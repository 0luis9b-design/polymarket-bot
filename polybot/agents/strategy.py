"""Strategy Research Agent: baut einen Datensatz entschiedener Märkte auf und testet einfache Strategien darauf.

Alles hier ist Simulation auf historischen Daten – es wird nichts gehandelt, auch nicht im Paper-Trading.

Datensatz (`resolved`)
  Je entschiedenem Markt: Ergebnis und der Ja-Kurs 1 h, 6 h, 24 h, 72 h und 7 Tage vor dem *geplanten* Ende.
  Bezugspunkt ist bewusst das geplante Ende (stand vorher fest) und nicht der tatsächliche Schluss (den kannte
  vorher niemand) – sonst flösse Wissen aus der Zukunft ein. War ein Markt zu einem dieser Zeitpunkte schon
  entschieden, bleibt der Kurs leer: Zu dem Zeitpunkt hätte man nicht mehr handeln können.
  Fehlt ein Kurs (kein Handel in den 3 h davor), bleibt er ebenfalls leer und wird als Datenlücke gezählt.

Backtests
  Zeitlich geteilt: die früheren 60 % der Tage sind der Entwicklungsteil, die späteren 40 % der Prüfteil.
  Kosten: Polymarket-Gebühr (Satz × p × (1 − p) je Anteil) plus 1 Cent halbe Spanne.
  Einsatz immer 1 $ je Wette. Sicherheit eines Ergebnisses über Tages-Gruppen (Wetten desselben Tages hängen zusammen).
"""
import json
import logging
import math
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from .. import config, events, polymarket, taxonomy
from ..http import HttpError
from . import Agent
from .scanner import CATEGORY_NAMES, CATEGORY_TAGS

HORIZONS = [("p_1h", 1), ("p_6h", 6), ("p_24h", 24), ("p_72h", 72), ("p_7d", 168)]
FEE = {"crypto": 0.07}
FEE_DEFAULT = 0.05
HALF_SPREAD = 0.01
MIN_BETS = 30
log = logging.getLogger("polybot.strategy")


def price_at(history, ts, tolerance=3 * 3600):
    """Letzter gehandelter Kurs am oder vor `ts` – höchstens `tolerance` Sekunden alt. Sonst None (Datenlücke)."""
    best = None
    for t, p in history:
        if t <= ts:
            best = (t, p)
        else:
            break
    if best is None or ts - best[0] > tolerance:
        return None
    return best[1]


def horizon_prices(history, end_ts, closed_ts=None):
    """Kurse zu den festen Abständen vor dem geplanten Ende. Kein Kurs, wenn der Markt da schon geschlossen war."""
    out = {}
    for col, hours in HORIZONS:
        ts = end_ts - hours * 3600
        out[col] = None if (closed_ts is not None and ts >= closed_ts) else price_at(history, ts)
    return out


def bet(price_yes, side, outcome, fee_rate):
    """Gewinn einer 1-$-Wette. side 0 = Ja kaufen, 1 = Nein kaufen. Gibt None zurück, wenn der Kurs unhandelbar ist."""
    p = price_yes if side == 0 else 1 - price_yes
    cost = p + HALF_SPREAD
    if cost <= 0.005 or cost >= 0.995:
        return None
    cost += fee_rate * p * (1 - p)
    won = outcome if side == 0 else 1 - outcome
    return won / cost - 1.0


def _stats(bets):
    """bets: Liste (Tag, Gewinn je 1 $). Gruppen = Tage."""
    if not bets:
        return {"n": 0}
    pnl = [b[1] for b in bets]
    by_day = defaultdict(list)
    for d, v in bets:
        by_day[d].append(v)
    days = [sum(v) / len(v) for v in by_day.values()]
    t = None
    if len(days) >= 3 and statistics.stdev(days) > 0:
        t = statistics.mean(days) / (statistics.stdev(days) / math.sqrt(len(days)))
    return {"n": len(bets), "tage": len(days), "roi": sum(pnl) / len(pnl), "summe": sum(pnl),
            "treffer": sum(1 for v in pnl if v > 0) / len(pnl), "t": t}


# ---------------------------------------------------------------- Strategien
# Jede Strategie: (row) -> Liste von (Kurs-Spalte, Seite). Benutzt nur Kurse aus der Zeile, nie das Ergebnis.
def s_longshot_no(r):
    p = r["p_24h"]
    return [("p_24h", 1)] if p is not None and 0.02 <= p <= 0.10 else []


def s_favorite_yes(r):
    p = r["p_24h"]
    return [("p_24h", 0)] if p is not None and 0.90 <= p <= 0.98 else []


def s_momentum(r):
    a, b = r["p_72h"], r["p_24h"]
    if a is None or b is None or not 0.05 <= b <= 0.95:
        return []
    return [("p_24h", 0)] if b - a >= 0.10 else [("p_24h", 1)] if a - b >= 0.10 else []


def s_reversal(r):
    return [(col, 1 - side) for col, side in s_momentum(r)]


def s_always_yes(r):
    p = r["p_24h"]
    return [("p_24h", 0)] if p is not None and 0.05 <= p <= 0.95 else []


def s_always_no(r):
    p = r["p_24h"]
    return [("p_24h", 1)] if p is not None and 0.05 <= p <= 0.95 else []


STRATEGIES = [
    ("always_yes", "Vergleich: immer Ja", "Kauft 24 h vor Ende immer „Ja“ (Kurs 5–95 %). Reine Vergleichsgröße.", s_always_yes, True),
    ("always_no", "Vergleich: immer Nein", "Kauft 24 h vor Ende immer „Nein“ (Kurs 5–95 %). Reine Vergleichsgröße.", s_always_no, True),
    ("longshot_no", "Außenseiter verkaufen", "Kauft „Nein“, wenn „Ja“ 24 h vor Ende nur 2–10 % kostet.", s_longshot_no, False),
    ("favorite_yes", "Favoriten kaufen", "Kauft „Ja“, wenn es 24 h vor Ende 90–98 % kostet.", s_favorite_yes, False),
    ("momentum", "Trend folgen", "Kurs hat sich zwischen 72 h und 24 h vor Ende um ≥ 10 Punkte bewegt – in dieselbe Richtung kaufen.", s_momentum, False),
    ("reversal", "Gegen den Trend", "Wie „Trend folgen“, aber in die Gegenrichtung kaufen.", s_reversal, False),
]


def split_by_time(rows, share=0.6):
    """Teilt an Tages-Grenzen: frühe Tage = Entwicklungsteil, späte = Prüfteil."""
    days = sorted({r["end_date"][:10] for r in rows})
    cut = set(days[:int(len(days) * share)])
    return [r for r in rows if r["end_date"][:10] in cut], [r for r in rows if r["end_date"][:10] not in cut]


def run_strategy(rows, fn):
    out = []
    for r in rows:
        for col, side in fn(r):
            v = bet(r[col], side, r["outcome"], FEE.get(r["category"], FEE_DEFAULT))
            if v is not None:
                out.append((r["end_date"][:10], v))
    return out


def calibration_map(rows, col="p_24h", bins=10):
    """Wie oft trat „Ja“ ein, wenn der Markt x % sagte? (nur Zeilen mit Kurs)"""
    out = []
    for i in range(bins):
        lo, hi = i / bins, (i + 1) / bins
        sel = [r for r in rows if r[col] is not None and (lo <= r[col] < hi or (i == bins - 1 and r[col] == 1.0))]
        if sel:
            out.append({"von": lo, "bis": hi, "n": len(sel), "p": sum(r[col] for r in sel) / len(sel),
                        "ist": sum(r["outcome"] for r in sel) / len(sel)})
    return out


def s_calib(mapping, min_n=40, min_gap=0.05):
    """Strategie aus einer Kalibrierungs-Karte (gelernt NUR im Entwicklungsteil)."""
    lookup = {round(b["von"], 2): b for b in mapping if b["n"] >= min_n}

    def fn(r):
        p = r["p_24h"]
        if p is None or not 0.03 <= p <= 0.97:
            return []
        b = lookup.get(round(math.floor(p * 10) / 10, 2))
        if not b:
            return []
        gap = b["ist"] - b["p"]
        return [("p_24h", 0)] if gap >= min_gap else [("p_24h", 1)] if gap <= -min_gap else []
    return fn


def evaluate_strategy(key, name, desc, fn_or_factory, rows, baseline=False, learns=False):
    early, late = split_by_time(rows)
    fn = fn_or_factory(calibration_map(early)) if learns else fn_or_factory
    i, o = _stats(run_strategy(early, fn)), _stats(run_strategy(late, fn))
    warn = []
    if not baseline:
        if o["n"] < MIN_BETS:
            warn.append(f"zu wenige Wetten im Prüfteil ({o['n']}/{MIN_BETS})")
        if i.get("roi") is not None and o.get("roi") is not None and i["roi"] > 0 >= o["roi"]:
            warn.append("Überanpassung: im Entwicklungsteil im Plus, im Prüfteil nicht")
        if o.get("roi") is not None and o["roi"] > 0 and (o.get("t") is None or o["t"] < 1.64):
            warn.append("Plus im Prüfteil ist statistisch nicht gesichert")
        if learns:
            warn.append("Regel wurde aus dem Entwicklungsteil gelernt – nur der Prüfteil zählt")
    passed = (not baseline and o["n"] >= MIN_BETS and (o.get("roi") or 0) > 0 and (o.get("t") or 0) >= 1.64
              and (i.get("roi") or 0) > 0)
    return {"strategy": key, "name": name, "desc": desc, "baseline": baseline, "is": i, "oos": o, "warnungen": warn, "bestanden": passed}


HISTORY_MAX_PAGES = 10


class StrategyAgent(Agent):
    id = "strategy"
    name = "Strategy Research"
    role = "Sammelt entschiedene Märkte samt Kursverlauf und prüft Strategien darauf (nur Simulation, zeitlich getrennt)"
    interval = 1800
    budget = 75
    priority = 6

    def __init__(self, store, fetch_markets=polymarket.fetch_markets, fetch_history=polymarket.fetch_price_history):
        super().__init__(store)
        self.fetch_markets = fetch_markets
        self.fetch_history = fetch_history

    # ------------------------------------------------------------------ Datensatz
    def collect(self, now, deadline, limit=None):
        limit = config.HISTORY_PER_RUN if limit is None else limit
        if limit <= 0:
            return {"neu": 0}
        cats = list(dict.fromkeys(c for c, _ in CATEGORY_TAGS))
        idx = int(self.store.kv_get("hist:rot") or 0)
        cat = cats[idx % len(cats)]
        self.store.kv_set("hist:rot", idx + 1)
        tag_id = next(t for c, t in CATEGORY_TAGS if c == cat)
        # abwechselnd: frisch entschiedene Märkte / weiter zurück in die Vergangenheit
        oldest = self.store.scalar("SELECT MIN(end_date) FROM resolved WHERE category=?", (cat,))
        backfill = bool(oldest) and (idx // len(cats)) % 2 == 1
        oldest_dt = polymarket.parse_dt(oldest) if backfill else None
        end_max = (oldest_dt or now).strftime("%Y-%m-%dT%H:%M:%SZ")
        backfill = bool(oldest_dt)
        added = skipped = gaps = seen = 0
        stop = None
        # Krypto: die zuletzt beendeten Märkte sind fast nur 5-Minuten-Märkte („Up or Down“) – deshalb mehrere Seiten
        for page in range(HISTORY_MAX_PAGES):
            if added >= limit or self.time_left(deadline) < 8:
                break
            params = {"tag_id": tag_id, "related_tags": "true", "closed": "true", "order": "endDate", "include_tag": "true",
                      "ascending": "false", "end_date_max": end_max, "volume_num_min": config.HISTORY_MIN_VOLUME,
                      "limit": 100, "offset": page * 100}
            try:
                raws = self.fetch_markets(params)
            except HttpError as e:
                # Live-Befund 17./18.11.: Polymarket antwortet bei manchen Abfragen mit „HTTP 500“ (Fehler auf deren Seite).
                # Dann diese Portion beenden statt den ganzen Agenten abbrechen – was schon gespeichert ist, bleibt, und
                # die Backtests laufen trotzdem. Die Abfrage wird mitgeschrieben, damit sich die Ursache eingrenzen lässt.
                if e.status is None or e.status < 500:
                    raise
                stop = (f"Polymarket-Fehler {e.status} bei Seite {page + 1} ({CATEGORY_NAMES.get(cat, cat)}, "
                        f"{'ältere' if backfill else 'neueste'} Märkte)")
                log.warning("Datensatz: %s · Abfrage %s", stop, {k: v for k, v in params.items() if k != "include_tag"})
                fails = self.store.kv_json("hist:gamma_fail", {})
                k = f"{cat}|{'alt' if backfill else 'neu'}|{page}"
                fails[k] = fails.get(k, 0) + 1
                self.store.kv_set("hist:gamma_fail", json.dumps(fails))
                break
            seen += len(raws)
            ids = [str(r.get("id")) for r in raws]
            have = set()
            if ids:
                have = {r["market_id"] for r in self.store.q("SELECT market_id FROM resolved WHERE market_id IN (%s)" % ",".join("?" * len(ids)), ids)}
            for raw in raws:
                if added >= limit or self.time_left(deadline) < 6:
                    break
                mid = str(raw.get("id"))
                q = raw.get("question") or ""
                if mid in have or "up or down" in q.lower():
                    continue
                res = polymarket.resolution(raw)
                end = polymarket.parse_dt(raw.get("endDate") or raw.get("endDateIso"))
                tokens = polymarket._loads(raw.get("clobTokenIds"), [])
                if res not in (0, 1) or not end or len(tokens) != 2 or end > now:
                    skipped += 1
                    continue
                closed = polymarket.parse_dt(raw.get("closedTime"))
                try:
                    hist = self.fetch_history(str(tokens[0]), end.timestamp() - 8 * 86400, end.timestamp() + 3600)
                except Exception:  # noqa: BLE001 – ein Markt darf fehlen; beim nächsten Durchlauf kommt er wieder dran
                    skipped += 1
                    continue
                hist.sort()
                ps = horizon_prices(hist, end.timestamp(), closed.timestamp() if closed else None)
                if all(v is None for v in ps.values()):
                    gaps += 1
                early = 1 if (closed and closed < end - timedelta(hours=1)) else 0
                # Einordnung nach den Schlagwörtern des Marktes (wie im Handel), sonst nach der Abfrage
                ev = raw["events"][0] if isinstance(raw.get("events"), list) and raw.get("events") else None
                nm = polymarket.normalize_market(raw, ev, "other") or {"question": q, "tags": []}
                c = taxonomy.classify(nm)
                if c["category"] == "other":
                    c = taxonomy.classify(nm, cat)
                self.store.x("INSERT OR IGNORE INTO resolved(market_id, question, category, sub, detail, end_date, closed_at, outcome, volume, "
                             "early, p_1h, p_6h, p_24h, p_72h, p_7d, n_points, fetched_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                             (mid, q[:300], c["category"], c["sub"], c["detail"], end.isoformat(timespec="seconds"),
                              closed.isoformat(timespec="seconds") if closed else None,
                              1.0 if res == 0 else 0.0, polymarket._num(raw.get("volumeNum"), 0.0), early,
                              ps["p_1h"], ps["p_6h"], ps["p_24h"], ps["p_72h"], ps["p_7d"], len(hist), now.isoformat(timespec="seconds")))
                added += 1
            if len(raws) < 100:
                break
        out = {"kategorie": CATEGORY_NAMES.get(cat, cat), "richtung": "ältere" if backfill else "neueste", "geprüft": seen,
               "neu": added, "übersprungen": skipped, "ohne_kurse": gaps}
        if stop:
            out["abgebrochen"] = stop
        return out

    # ------------------------------------------------------------------ Backtests
    def dataset(self):
        return self.store.q("SELECT * FROM resolved ORDER BY end_date")

    def backtest(self, now, rows=None):
        rows = self.dataset() if rows is None else rows
        usable = [r for r in rows if r["p_24h"] is not None]
        info = {"zeilen": len(rows), "mit_kurs_24h": len(usable),
                "von": rows[0]["end_date"][:10] if rows else None, "bis": rows[-1]["end_date"][:10] if rows else None,
                "lücken": {col: sum(1 for r in rows if r[col] is None) for col, _ in HORIZONS},
                "früh_entschieden": sum(1 for r in rows if r["early"]),
                "kategorien": {}}
        for r in rows:
            info["kategorien"][r["category"]] = info["kategorien"].get(r["category"], 0) + 1
        if len(usable) < 50:
            result = {"stand": now.isoformat(timespec="seconds"), "datensatz": info, "strategien": [], "markt": {},
                      "hinweis": f"Noch zu wenige Märkte mit Kursverlauf ({len(usable)}/50) – der Datensatz wächst mit jedem Lauf."}
            self.store.kv_set("strategy:latest", json.dumps(result, ensure_ascii=False))
            return result
        results = [evaluate_strategy(k, n, d, fn, usable, baseline=b) for k, n, d, fn, b in STRATEGIES]
        results.append(evaluate_strategy("calib_map", "Kalibrierungs-Karte",
                                         "Lernt im Entwicklungsteil, in welchen Kursbereichen der Markt zu hoch/zu niedrig lag (≥ 5 Punkte), "
                                         "und wettet im Prüfteil dagegen.", s_calib, usable, learns=True))
        market = {"gesamt": self._market_quality(rows)}
        for cat in sorted(info["kategorien"]):
            sub = [r for r in rows if r["category"] == cat]
            if len(sub) >= 30:
                market[cat] = self._market_quality(sub)
        result = {"stand": now.isoformat(timespec="seconds"), "datensatz": info, "strategien": results, "markt": market,
                  "kosten": {"gebühr": FEE_DEFAULT, "gebühr_krypto": FEE["crypto"], "halbe_spanne": HALF_SPREAD},
                  "kalibrierung": calibration_map(usable)}
        self.store.kv_set("strategy:latest", json.dumps(result, ensure_ascii=False))
        for r in results:
            self.store.x("INSERT INTO backtests(created_at, strategy, params, dataset, result) VALUES(?,?,?,?,?)",
                         (now.isoformat(timespec="seconds"), r["strategy"],
                          json.dumps({"gebühr": FEE_DEFAULT, "halbe_spanne": HALF_SPREAD, "teilung": 0.6, "einsatz": 1}),
                          json.dumps({"zeilen": info["zeilen"], "mit_kurs_24h": info["mit_kurs_24h"], "von": info["von"], "bis": info["bis"]}),
                          json.dumps(r, ensure_ascii=False)))
        self.store.x("DELETE FROM backtests WHERE id < (SELECT MAX(id) - 600 FROM backtests)")
        passed = [r for r in results if r["bestanden"]]
        events.emit(self.store, "info", "backtest", f"Backtest abgeschlossen: {len(results)} Strategien auf {len(usable)} Märkten",
                    ("Im Prüfteil bestanden: " + ", ".join(r["name"] for r in passed)) if passed else "Keine Strategie hat den Prüfteil bestanden.",
                    agent=self.id, telegram=False)
        for r in passed:
            events.emit(self.store, "normal", "backtest", f"Strategie besteht den Prüfteil: {r['name']}",
                        f"Prüfteil: {r['oos']['n']} Wetten, {r['oos']['roi'] * 100:+.1f} % je Wette (t = {r['oos']['t']:.1f}). "
                        "Nur Simulation – Details im Strategie-Labor.", key=f"bt:{r['strategy']}", agent=self.id, dedupe_h=24 * 7)
        return result

    @staticmethod
    def _market_quality(rows):
        """Wie gut war der Marktpreis selbst? Brier je Abstand zum Ende – die Messlatte für jedes Modell."""
        out = {}
        for col, hours in HORIZONS:
            sel = [r for r in rows if r[col] is not None]
            if sel:
                out[col] = {"n": len(sel), "brier": sum((r[col] - r["outcome"]) ** 2 for r in sel) / len(sel),
                            "treffer": sum(1 for r in sel if (r[col] >= 0.5) == (r["outcome"] >= 0.5)) / len(sel)}
        return out

    def run(self, now, deadline):
        if not config.BACKTEST_ENABLED:
            return {"aus": True}
        out = self.collect(now, deadline)
        n = self.store.scalar("SELECT COUNT(*) FROM resolved") or 0
        last = self.store.kv_json("strategy:last_bt", {})
        day = now.strftime("%Y-%m-%d")
        if n and (last.get("day") != day or n >= last.get("n", 0) * 1.25 + 20) and self.time_left(deadline) > 5:
            res = self.backtest(now)
            self.store.kv_set("strategy:last_bt", json.dumps({"day": day, "n": n}))
            out["backtest"] = {"strategien": len(res["strategien"]), "märkte": res["datensatz"]["mit_kurs_24h"]}
        out["datensatz"] = n
        return out

    def view(self):
        return {"latest": self.store.kv_json("strategy:latest", None),
                "datensatz": self.store.scalar("SELECT COUNT(*) FROM resolved") or 0,
                "läufe": [dict(id=b["id"], created_at=b["created_at"], strategy=b["strategy"], dataset=json.loads(b["dataset"] or "{}"),
                               result=json.loads(b["result"] or "{}"))
                          for b in self.store.q("SELECT * FROM backtests ORDER BY id DESC LIMIT 60")]}
