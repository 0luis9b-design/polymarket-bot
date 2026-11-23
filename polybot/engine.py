"""Herzstück: Märkte holen, eigene Wahrscheinlichkeit berechnen, mit Spielgeld wetten, abrechnen.

Ablauf pro Lauf:
 1. Entschiedene Märkte abrechnen
 2. Märkte holen, eigene Wahrscheinlichkeit berechnen (einmal für alle Konten)
 3. Je Testkonto: erst Ausstiege prüfen, dann neue Wetten
 4. Widersprüche (Arbitrage) suchen und für Konten mit Arbitrage kaufen
 5. Schlusskurse für Sportwetten merken, Positionen bewerten, Kontoverlauf speichern
"""
import json
import logging
import math
import os
import re
import statistics
import threading
import time
import traceback
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import base_model, config, crypto_model, form_model, events, hindsight, notify, polymarket, sports_model, taxonomy, weather_model
from .paperview import hit as paperview_hit
from .store import DECIDED_SQL, HIT_SQL
from .agents.coordinator import Coordinator
from .prices import hourly_closes, source_status
from .sources import redact

log = logging.getLogger("polybot.engine")
BERLIN = ZoneInfo("Europe/Berlin")
ACC = {a["id"]: a for a in config.ACCOUNTS}
MAIN = config.ACCOUNTS[0]["id"]
CATS = tuple(config.MODEL_TOPICS)   # Bereiche mit eigenem Datenmodell
# Hauptkonto je Thema (für den Status in der Analyse und den Umzug alter Konten)
MAIN_BY_CAT = {a["topic"]: a["id"] for a in config.ACCOUNTS if a.get("strategy") == "haupt" and a.get("topic")}
LANE_NAMES = {"model": "eigenes Datenmodell", "base": "Basisraten", "rule": "feste Regel", "arb": "Arbitrage", "maker": "Limit-Order", "form": "Form-Modell (Elo)"}


def game_key(m):
    """Ein Spiel = ein Schlüssel – für Sieger, Unentschieden, genaues Ergebnis, Karten, Handicap, Über/Unter.

    Polymarket legt Zusatzmärkte eines Spiels teils in eigene Ereignisse; deren Kürzel beginnt aber wie das Hauptereignis
    und enthält das Datum (z. B. „cs2-tt3-bar2-2026-11-14“). Ohne Datum im Kürzel: das Ereignis selbst.
    """
    slug = (m.get("event_slug") or "").lower()
    hit = re.match(r"^(.+?-\d{4}-\d{2}-\d{2})", slug)
    return "spiel:" + (hit.group(1) if hit else str(m.get("event_id") or m.get("id")))


def acc_cats(acc):
    return acc.get("cats") or list(CATS)


def edge_for(acc, cat, lane="model"):
    if lane == "base":
        return acc.get("edge_b", config.MIN_EDGE_BASE)
    return {"crypto": acc["edge_c"], "weather": acc.get("edge_w", config.MIN_EDGE_WEATHER)}.get(cat, acc["edge_s"])


def best_weight(rows):
    """Mischgewicht w für p = w·Bot + (1−w)·Markt, das in der Vergangenheit am besten getroffen hätte."""
    best_w, best_l = 1.0, None
    for i in range(21):
        w = i / 20
        mixed = [{"p": w * r["own_p"] + (1 - w) * r["market_p"], "outcome": r["outcome"]} for r in rows]
        loss = logloss(mixed, "p")
        if best_l is None or loss < best_l - 1e-12:
            best_w, best_l = w, loss
    return best_w


def fee_per_share(rate, price):
    return rate * price * (1 - price)


def kelly_fraction(q, eff_price):
    """Anteil des Kontos nach Kelly für eine Wette, die 1 $ pro Anteil auszahlt."""
    if eff_price <= 0 or eff_price >= 1:
        return 0.0
    return max(0.0, (q - eff_price) / (1 - eff_price))


def confidence(q, edge, min_edge):
    """Sicherheit 0..1 aus Vorsprung (60 %) und Trefferchance (40 %)."""
    e = min(max((edge - min_edge) / config.EDGE_FULL_EXTRA, 0.0), 1.0)
    h = min(max((q - 0.35) / 0.50, 0.0), 1.0)
    return 0.6 * e + 0.4 * h


def evaluate(m, p_yes):
    """Beide Seiten prüfen und die bessere zurückgeben (nach Gebühren)."""
    best = None
    for side in (0, 1):
        ask, _bid = polymarket.quote(m, side)
        fee = fee_per_share(m["fee_rate"], ask)
        eff = ask + fee
        q = p_yes if side == 0 else 1 - p_yes
        cand = {"side": side, "price": ask, "fee_ps": fee, "eff": eff, "q": q, "edge": q - eff}
        if best is None or cand["edge"] > best["edge"]:
            best = cand
    return best


def brier(rows, key):
    return sum((r[key] - r["outcome"]) ** 2 for r in rows) / len(rows) if rows else None


def logloss(rows, key):
    if not rows:
        return None
    s = 0.0
    for r in rows:
        p = min(max(r[key], 0.001), 0.999)
        s += -(r["outcome"] * math.log(p) + (1 - r["outcome"]) * math.log(1 - p))
    return s / len(rows)


def _cent(v):
    return f"{v * 100:.1f} ¢".replace(".", ",")


def _usd(v):
    return f"{v:,.2f} $".replace(",", "X").replace(".", ",").replace("X", ".")


class Engine:
    def __init__(self, store, fetch_events=polymarket.fetch_events, fetch_by_ids=polymarket.fetch_markets_by_ids,
                 closes_fn=hourly_closes, odds_client=None, chain_fn=None, book_fn=polymarket.fetch_book, intel=None,
                 fetch_markets=None):
        self.store = store
        # Weitere Märkte (ohne eigenes Modell): im Betrieb von Polymarket; in Tests nur, wenn ausdrücklich übergeben
        self.fetch_markets = fetch_markets if fetch_markets is not None else (
            polymarket.fetch_markets if fetch_events is polymarket.fetch_events else (lambda params: []))
        self.base = base_model.BaseRates(store)
        self.intel = intel or Coordinator(store)   # Agenten: Katalog, Modell-Auswahl, Prüfung, Prognose-Historie
        self.intel.engine = self
        self.fetch_events = fetch_events
        self.fetch_by_ids = fetch_by_ids
        live = fetch_events is polymarket.fetch_events
        # Form-Modell (2.11): ClubElo und API-Football nur im Betrieb abrufen, in Tests nie ins Netz
        self.form = form_model.Form(store, fetch_markets=self.fetch_markets,
                                    fetch_text=form_model.get_text if live else (lambda url, **kw: b""),
                                    fetch_json=form_model.get_json if live else (lambda *a, **k: {}))
        self.form_map = {}
        self.closes_fn = closes_fn
        self.chain_fn = chain_fn
        self.book_fn = book_fn if config.USE_ORDERBOOK else None
        self.weather_fetch = None
        self.odds = odds_client or sports_model.OddsClient(store)
        self.lock = threading.Lock()
        self._books = {}
        self._err_runs = 0
        self.state = {"last_scan": None, "last_error": None, "running": False, "scans": 0,
                      "opportunities": [], "skips": {}, "markets_seen": {}, "duration": None, "arbs": [],
                      "weights": {}, "paused": {}}
        self.migrate_split()
        self.migrate_taxonomy()
        self.migrate_strategies()
        self.migrate_unpause()
        self.state["paused"] = self.store.kv_json("paused_state", {}) or {}
        self.migrate_mix()
        saved = store.kv_json("last_state", {})  # letzte Analyse nach Neustart weiter anzeigen
        for k in ("last_scan", "opportunities", "skips", "markets_seen", "duration", "arbs", "weights"):
            if k in saved:
                self.state[k] = saved[k]

    # ------------------------------------------------------------------ Umzug altes Hauptkonto
    def migrate_split(self):
        """13.11.: Das alte Konto "haupt" wird auf Haupt·Krypto/Sport/Wetter verteilt (einmalig).

        Jede Wette wandert in das Konto ihres Bereichs. Jedes neue Konto startet mit 1.000 $
        plus Gewinn/Verlust der übernommenen abgerechneten Wetten minus Einsatz der offenen.
        """
        st = self.store
        if st.kv_get("split_v1") or not any(a["id"] == "haupt_krypto" for a in config.ACCOUNTS):
            return
        with st.lock:
            rows = [dict(r) for r in st.db.execute("SELECT * FROM positions WHERE account='haupt'")]
            if rows:
                for r in rows:
                    st.db.execute("UPDATE positions SET account=? WHERE id=?",
                                  (MAIN_BY_CAT.get(r["category"], MAIN), r["id"]))
                st.db.execute("UPDATE orders SET account='haupt_krypto' WHERE account='haupt' AND category='crypto'")
                st.db.commit()
            started = st.kv_get("started_at")
            for c in CATS:
                aid = MAIN_BY_CAT[c]
                mine = [r for r in rows if MAIN_BY_CAT.get(r["category"], MAIN) == aid]
                if st.kv_get(f"cash:{aid}") is not None and not mine:
                    continue  # Konto gibt es schon, nichts zu übernehmen -> nicht anfassen
                cash = config.START_BALANCE + sum(r["pnl"] or 0 for r in mine if r["status"] != "open") \
                    - sum(r["cost"] for r in mine if r["status"] == "open")
                st.kv_set(f"cash:{aid}", round(cash, 6))
                if started:
                    st.kv_set(f"started_at:{aid}", started)
            st.kv_set("split_v1", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        if rows:
            st.log(f"Hauptkonto aufgeteilt: {len(rows)} Wetten auf Haupt·Krypto/Sport/Wetter verteilt (je 1.000 $ Start)")

    def migrate_strategies(self):
        """14.11.: Jede Strategie bekommt je Thema ein eigenes Konto (einmalig).

        Die Wetten der bisherigen Konten, die über alle Bereiche gewettet haben (Schwelle 4, Fest 10 $, …, Haupt·Weitere),
        wandern in das Konto ihrer Strategie und ihres Themas – auch die abgeschlossenen, damit die Geschichte erhalten
        bleibt. Jedes Zielkonto startet mit 1.000 $ (oder seinem bisherigen Bargeld, falls es das Konto schon gab),
        plus Gewinn/Verlust der übernommenen abgerechneten Wetten, minus Einsatz der übernommenen offenen.
        Die Verlustgrenze („−25 % seit Start“) zählt bei neuen Konten ab dem übernommenen Kontowert.
        """
        st = self.store
        if st.kv_get("split_v2"):
            return
        legacy = [k for k in config.LEGACY_ACCOUNTS if k not in ACC]
        by_strategy = {}
        for a in config.ACCOUNTS:
            by_strategy.setdefault(a.get("strategy"), []).append(a["id"])

        def target(old, cat):
            strat = config.LEGACY_ACCOUNTS[old]
            for aid in (f"{strat}_{config.TOPIC_CODE.get(cat, 'sonstiges')}", f"{strat}_sonstiges"):
                if aid in ACC:
                    return aid
            return (by_strategy.get(strat) or [MAIN])[0]

        ph = ",".join("?" * len(legacy))
        n_pos = st.scalar(f"SELECT COUNT(*) FROM positions WHERE account IN ({ph})", legacy) if legacy else 0
        n_ord = st.scalar(f"SELECT COUNT(*) FROM orders WHERE account IN ({ph})", legacy) if legacy else 0
        if n_pos or n_ord:
            try:  # Sicherung vor dem Umzug – falls etwas schiefgeht, lässt sich der alte Stand zurückholen
                if st.path and st.path != ":memory:":
                    from . import migrations
                    migrations.backup_file(st.db, os.path.join(os.path.dirname(st.path), "backups", "vor-kontoaufteilung.sqlite"))
            except Exception:  # noqa: BLE001
                log.exception("Sicherung vor der Kontoaufteilung fehlgeschlagen")
        delta, src, new = defaultdict(float), defaultdict(set), set()
        with st.lock:
            if n_pos or n_ord:
                rows = [dict(r) for r in st.db.execute(f"SELECT id, account, category, status, cost, pnl FROM positions "
                                                       f"WHERE account IN ({ph})", legacy)]
                for r in rows:
                    tgt = target(r["account"], r["category"])
                    st.db.execute("UPDATE positions SET account=? WHERE id=?", (tgt, r["id"]))
                    delta[tgt] += (r["pnl"] or 0) if r["status"] != "open" else -(r["cost"] or 0)
                    src[tgt].add(r["account"])
                for r in [dict(x) for x in st.db.execute(f"SELECT id, account, category FROM orders WHERE account IN ({ph})", legacy)]:
                    tgt = target(r["account"], r["category"])
                    st.db.execute("UPDATE orders SET account=? WHERE id=?", (tgt, r["id"]))
                    src[tgt].add(r["account"])
                st.db.commit()
            for tgt in src:
                cur = st.kv_get(f"cash:{tgt}")
                if cur is None:
                    new.add(tgt)
                    starts = [st.kv_get(f"started_at:{o}") for o in src[tgt]]
                    starts = [x for x in starts if x] or [st.kv_get("started_at")]
                    if starts[0]:
                        st.kv_set(f"started_at:{tgt}", min(x for x in starts if x))
                st.kv_set(f"cash:{tgt}", round((float(cur) if cur is not None else config.START_BALANCE) + delta[tgt], 6))
            # alte Konten aus der Pausen-Liste nehmen (es gibt sie nicht mehr)
            paused = st.kv_json("paused_state", {}) or {}
            if any(k in paused for k in legacy):
                st.kv_set("paused_state", json.dumps({k: v for k, v in paused.items() if k not in legacy}))
            st.kv_set("split_v2", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        for tgt in new:   # Verlustgrenze ab dem übernommenen Wert, sonst stünde ein geerbtes Minus sofort auf „pausiert“
            st.kv_set(f"risk_base:{tgt}", round(self.equity(tgt), 2))
        if n_pos or n_ord:
            msg = (f"Konten aufgeteilt: {n_pos} Wetten und {n_ord} Gebote aus {len({o for x in src.values() for o in x})} "
                   f"bisherigen Konten auf {len(src)} Konten (Strategie × Thema) verteilt")
            st.log(msg)
            log.info(msg)

    def migrate_taxonomy(self):
        """Einmalig: bestehende Wetten, Gebote und Katalog-Märkte in Bereich/Unterbereich/Detail einordnen."""
        if self.store.kv_get("taxonomy_v1"):
            return
        try:
            tags = {r["id"]: (r["tags"] or "").split(",") for r in self.store.q("SELECT id, tags FROM markets WHERE tags IS NOT NULL AND tags != ''")}
            for table in ("positions", "orders"):
                rows = self.store.q(f"SELECT id, market_id, category, question, slug, event_slug FROM {table} WHERE sub IS NULL")
                upd = []
                for r in rows:
                    c = taxonomy.classify({**r, "tags": tags.get(r["market_id"], [])}, r["category"])
                    upd.append((c["sub"], c["detail"], r["id"]))
                self.store.xmany(f"UPDATE {table} SET sub=?, detail=? WHERE id=?", upd)
            self.store.x("UPDATE positions SET lane = CASE WHEN kind='arb' THEN 'arb' WHEN account='aussenseiter' THEN 'rule' "
                         "WHEN account='limit' THEN 'maker' ELSE 'model' END WHERE lane IS NULL")
            rows = self.store.q("SELECT id, category, question, tags, slug FROM markets WHERE sub IS NULL")
            self.store.xmany("UPDATE markets SET sub=?, detail=? WHERE id=?",
                             [(c["sub"], c["detail"], r["id"]) for r in rows
                              for c in [taxonomy.classify({**r, "tags": (r["tags"] or "").split(","), "event_slug": r["slug"]}, r["category"])]])
        except Exception:  # noqa: BLE001 – die Einordnung ist Komfort; ohne sie läuft alles weiter
            log.exception("Einordnung alter Wetten fehlgeschlagen")
            return
        self.store.kv_set("taxonomy_v1", datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def migrate_mix(self):
        """14.11.: Bis v1.3 wurde die Mischung mit dem Marktpreis automatisch angewendet. Was zum Zeitpunkt des Updates
        galt, wird einmalig ins Modell-Register übernommen (damit sich das Verhalten nicht still ändert).
        Jede weitere Änderung läuft über einen Vorschlag mit Freigabe."""
        if self.store.kv_get("mix_v2"):
            return
        try:
            self.update_weights()
            pa = self.intel.probability
            for cat, x in self.state["weights"].items():
                reg = pa.reg.get(cat)
                if reg and x.get("w") is not None and x["w"] < 0.999 and (reg["mix_w"] is None or reg["mix_w"] >= 0.999):
                    pa.activate(cat, reg["model"], reg["version"], float(x["w"]),
                                f"übernommen aus v1.3: automatische Mischung ({x['w'] * 100:.0f} % Modell, gemessen an {x['n']} Märkten)")
        except Exception:  # noqa: BLE001 – eine Übernahme darf den Start nie verhindern; ohne sie gilt das reine Modell
            log.exception("Übernahme der Mischgewichte fehlgeschlagen")
            self.store.log("Übernahme der Mischgewichte aus v1.3 fehlgeschlagen – es gilt das reine Modell", "error")
        self.store.kv_set("mix_v2", datetime.now(timezone.utc).isoformat(timespec="seconds"))

    # ------------------------------------------------------------------ Konto
    def cash(self, acc=MAIN):
        return self.store.cash(config.START_BALANCE, acc)

    @staticmethod
    def exposure(positions):
        return sum(p["cost"] for p in positions)

    def equity(self, acc=MAIN, positions=None):
        positions = positions if positions is not None else self.store.open_positions(acc)
        return self.cash(acc) + sum(p["shares"] * (p["mark"] if p["mark"] is not None else p["price"]) for p in positions)

    # ------------------------------------------------------------------ Ablauf
    def run_cycle(self, now=None):
        if not self.lock.acquire(blocking=False):
            return False
        t0 = time.time()
        self.state["running"] = True
        self._books = {}
        try:
            now = now or datetime.now(timezone.utc)
            for a in config.ACCOUNTS:  # Konten anlegen
                self.cash(a["id"])
            if notify.enabled() and self.store.kv_get("telegram_hello") != config.TELEGRAM_CHAT_ID:
                self.store.kv_set("telegram_hello", config.TELEGRAM_CHAT_ID)
                notify.send("✅ <b>Polymarket-Bot verbunden</b>\nAb jetzt kommt hier eine Nachricht bei jedem Kauf, Verkauf "
                            f"und jeder Abrechnung im Hauptkonto, dazu um {config.DAILY_SUMMARY_HOUR} Uhr die Tagesbilanz.")
            tm = self._timer = {}
            t = time.time()
            self.settle()
            tm["abrechnen"] = time.time() - t
            self.update_weights()
            t = time.time()
            markets = self.collect_markets(now)
            modeled = [m for m in markets if m.get("lane") not in ("base", "alles")]
            tm["märkte"] = time.time() - t
            self.intel.on_markets(markets, now)
            self.refresh_closing_odds(markets, now)
            t = time.time()
            results = self.analyze_all([m for m in markets if m.get("lane") != "alles"], now)
            tm["berechnen"] = time.time() - t
            t = time.time()
            by_market = {o["market"]["id"]: o for o in results}
            t = time.time()
            self.form_step(markets, now)
            tm["form"] = time.time() - t
            self.refile_all(markets)
            self.check_risk(now)
            for a in config.ACCOUNTS:
                if a["exit"]:
                    self.do_exits(a, by_market, now)
                if self.paused(a["id"]):
                    cats = acc_cats(a)
                    for o in results:
                        if o["market"]["category"] in cats:
                            o["status"][a["id"]] = "Risikobremse aktiv"
                    continue
                if a.get("maker"):
                    self.do_maker(a, results, markets, by_market, now)
                elif a.get("longshot"):
                    self.do_longshots(a, markets, now)
                elif a.get("all"):
                    if config.ALL_TIPS:
                        self.do_all(a, markets, now)
                elif a.get("form"):
                    if config.FORM_TIPS:
                        self.do_form(a, now)
                elif a["model"]:
                    self.do_entries(a, results)
            tm["konten"] = time.time() - t
            self.do_arbitrage(modeled, now)   # nur vollständige Ereignisse (weitere Märkte kommen einzeln, ohne Geschwister)
            self.update_closing(by_market)
            self.mark_positions(markets)
            self.store.add_equity_many([(a["id"], round(self.equity(a["id"]), 2), round(self.cash(a["id"]), 2)) for a in config.ACCOUNTS])
            self.state["opportunities"] = [self._opp_view(o) for o in self._shown(results)]
            self.daily_summary(now)
            self.weekly_report(now)
            self._log_summary(results)
            if self._err_runs >= config.ERROR_ALERT_RUNS:
                notify.send("✅ <b>Polymarket-Bot</b>: Lauf klappt wieder ohne Fehler", level="hoch")
            self._err_runs = 0
            self.state["last_error"] = None
        except Exception as e:  # noqa: BLE001
            self.state["last_error"] = redact(f"{type(e).__name__}: {e}")[:400]
            self.store.log(f"Fehler im Lauf: {self.state['last_error']}", "error")
            log.error(redact(traceback.format_exc()))
            self._err_runs += 1
            if self._err_runs == config.ERROR_ALERT_RUNS:
                notify.send(f"⚠️ <b>Polymarket-Bot</b>: {self._err_runs} Läufe hintereinander mit Fehler\n{self.state['last_error'][:300]}",
                            level="hoch", key="fehlläufe")
        finally:
            self.state["last_scan"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self.state["duration"] = round(time.time() - t0, 1)
            self.state["scans"] += 1
            self.state["running"] = False
            try:
                self.store.kv_set("last_state", json.dumps(
                    {k: self.state[k] for k in ("last_scan", "opportunities", "skips", "markets_seen", "duration", "arbs",
                                                "weights")}))
            except (TypeError, ValueError) as e:
                log.warning("Zustand nicht speicherbar: %s", e)
            self.lock.release()
        return True

    def collect_markets(self, now):
        markets = []
        seen = {}
        try:
            # Nur Märkte, die in den nächsten ~2 Wochen enden, und ohne die hunderten stündlichen
            # "Up or Down"-Märkte – sonst sind es >2.100 Ereignisse und die Abfrage schneidet ab.
            days = max(config.CRYPTO_MAX_DAYS, config.LONGSHOT_MAX_DAYS) + 1
            extra = {"end_date_min": (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "end_date_max": (now + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")}
            ud = polymarket.tag_id("up-or-down") if self.fetch_events is polymarket.fetch_events else ""
            if ud:
                extra["exclude_tag_id"] = ud
            evs = self.fetch_events("crypto", extra=extra)
            ms = polymarket.markets_from_events(evs, "crypto")
            seen["crypto"] = len(ms)
            markets += ms
        except Exception as e:  # noqa: BLE001
            self.store.log(f"Krypto-Märkte nicht abrufbar: {e}", "error")
        if self.odds.enabled:
            try:
                extra = {"end_date_min": (now - timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                         "end_date_max": (now + timedelta(hours=config.SPORTS_LOOKAHEAD_H + 48)).strftime("%Y-%m-%dT%H:%M:%SZ")}
                evs, ids = [], set()
                for tag in config.SPORTS_LEAGUES:  # nur die gewählten Ligen abrufen
                    try:
                        for e in self.fetch_events(tag, extra=extra):
                            if e.get("id") not in ids:
                                ids.add(e.get("id"))
                                evs.append(e)
                    except Exception as e:  # noqa: BLE001
                        self.store.log(f"Liga {tag} nicht abrufbar: {e}", "error")
                ms = [m for m in polymarket.markets_from_events(evs, "sports")
                      if (m["sports_market_type"] or "moneyline").lower() == "moneyline"]
                far = datetime.max.replace(tzinfo=timezone.utc)
                ms.sort(key=lambda m: m["game_start"] or far)  # bald beginnende Spiele zuerst (Quoten-Budget)
                seen["sports"] = len(ms)
                markets += ms
            except Exception as e:  # noqa: BLE001
                self.store.log(f"Sport-Märkte nicht abrufbar: {e}", "error")
        if config.WEATHER_ENABLED:
            try:
                extra = {"end_date_min": (now - timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                         "end_date_max": (now + timedelta(days=config.WEATHER_MAX_DAYS + 1)).strftime("%Y-%m-%dT%H:%M:%SZ")}
                evs = self.fetch_events("daily-temperature", extra=extra)
                ms = polymarket.markets_from_events(evs, "weather")
                for m in ms:  # Station steht in der Beschreibung des Ereignisses
                    if not m["description"]:
                        ev = next((e for e in evs if str(e.get("id")) == m["event_id"]), {})
                        m["description"] = ev.get("description") or ""
                seen["weather"] = len(ms)
                markets += ms
            except Exception as e:  # noqa: BLE001
                self.store.log(f"Wetter-Märkte nicht abrufbar: {e}", "error")
        for m in markets:
            m["lane"] = "model"
            m.update({k: v for k, v in taxonomy.classify(m, m["category"]).items() if k != "category"})
        if config.TRADE_BASE:
            try:
                others = self.collect_others(now, {m["id"] for m in markets})
                seen["weitere"] = len(others)
                markets += others
            except Exception as e:  # noqa: BLE001
                self.store.log(f"Weitere Märkte nicht abrufbar: {e}", "error")
        if config.ALL_TIPS or config.FORM_TIPS:   # alle Spiele: für „Alles tippen“ und das Form-Modell
            try:
                extra = self.collect_all(now, {m["id"] for m in markets})
                seen["alle Spiele"] = len(extra)
                markets += extra
            except Exception as e:  # noqa: BLE001
                self.store.log(f"Spiele für „Alles tippen“ nicht abrufbar: {e}", "error")
        self.state["markets_seen"] = seen
        return markets

    def collect_all(self, now, known):
        """Für „Alles tippen“: die Sieger-Märkte (moneyline) aller Sportarten und E-Sport, die bald beginnen.

        Nur der flache Sieger-Markt je Spiel (nicht die 20–30 Zusatzmärkte), höchstens alle ALL_REFRESH_MIN neu geholt –
        gekauft wird ohnehin zum Kurs aus dem aktuellen Orderbuch. Tischtennis-Ereignisse enden bei Polymarket oft erst
        7 Tage nach Spielbeginn, deshalb je Tag ein eigenes Zeitfenster (ALL_TAGS: „tag:tage“).
        """
        cache = getattr(self, "_all_cache", None)
        if cache is None or (now - cache[0]).total_seconds() > config.ALL_REFRESH_MIN * 60:
            raws, ids = [], set()
            for item in config.ALL_TAGS:
                slug, _, days = item.partition(":")
                tid = polymarket.tag_id(slug) if self.fetch_markets is polymarket.fetch_markets else slug
                if not tid:
                    continue
                params = {"tag_id": tid, "sports_market_types": "moneyline", "closed": "false", "active": "true", "limit": 100,
                          "include_tag": "true",
                          "end_date_min": (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                          "end_date_max": (now + timedelta(days=float(days or 3))).strftime("%Y-%m-%dT%H:%M:%SZ")}
                for page in range(config.ALL_MAX_PAGES):
                    got = self.fetch_markets({**params, "offset": page * 100})
                    for raw in got:
                        if raw.get("id") not in ids:
                            ids.add(raw.get("id"))
                            raws.append((slug, raw))
                    if len(got) < 100:
                        break
            self._all_cache = cache = (now, raws)
        out = []
        for slug, raw in cache[1]:
            ev = raw["events"][0] if isinstance(raw.get("events"), list) and raw.get("events") else None
            m = polymarket.normalize_market(raw, ev, "other")
            if not m or m["id"] in known or m["closed"]:
                continue
            c = taxonomy.classify(m)   # Sport oder E-Sport nach den Tags
            if c["category"] not in config.GAME_TOPICS:
                # ohne passende Tags (live 23.11.: Sportspiele landeten unter „Sonstiges“) gilt der Tag, über den geholt wurde
                c = taxonomy.classify(m, "esports" if slug == "esports" else "sports")
            m.update(c)
            m["fee_rate"] = polymarket.fee_rate_for(raw, c["category"])
            m["lane"] = "alles"
            known.add(m["id"])
            out.append(m)
        return out

    def collect_others(self, now, known):
        """Die aktivsten Märkte aller übrigen Bereiche, die in den nächsten 8 Tagen enden (dafür gibt es Basisraten)."""
        out = []
        params = {"closed": "false", "active": "true", "order": "volume24hr", "ascending": "false", "limit": 100,
                  "include_tag": "true", "liquidity_num_min": config.MIN_LIQUIDITY,
                  "end_date_min": (now + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                  "end_date_max": (now + timedelta(hours=base_model.MAX_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")}
        for page in range(config.OTHER_PAGES):
            raws = self.fetch_markets({**params, "offset": page * 100})
            for raw in raws:
                ev = raw["events"][0] if isinstance(raw.get("events"), list) and raw.get("events") else None
                m = polymarket.normalize_market(raw, ev, "other")
                if not m or m["id"] in known or m["closed"] or "up or down" in m["question"].lower():
                    continue
                known.add(m["id"])
                c = taxonomy.classify(m)
                m.update(c)
                m["fee_rate"] = polymarket.fee_rate_for(raw, c["category"])
                m["lane"] = "base"
                out.append(m)
            if len(raws) < 100:
                break
        # Märkte mit offenen Basisraten-Wetten, die aus der Liste gefallen sind: trotzdem bewerten (Wert, früher Ausstieg)
        held = sorted({p["market_id"] for p in self.store.open_positions() if p.get("lane") == "base" and p["market_id"] not in known})
        if held:
            for mid, raw in (self.fetch_by_ids(held[:100]) or {}).items():
                ev = raw["events"][0] if isinstance(raw.get("events"), list) and raw.get("events") else None
                m = polymarket.normalize_market(raw, ev, "other")
                if not m or m["closed"]:
                    continue
                c = taxonomy.classify(m)
                m.update(c)
                m["fee_rate"] = polymarket.fee_rate_for(raw, c["category"])
                m["lane"] = "base"
                known.add(m["id"])
                out.append(m)
        return out

    def model(self, m, now):
        if m.get("lane") == "base":
            return self.base.analyze(m, now)
        if m["category"] == "crypto":
            return crypto_model.analyze(m, now=now, closes_fn=self.closes_fn, chain_fn=self.chain_fn)
        if m["category"] == "sports":
            return sports_model.analyze(m, self.odds, now=now)
        if m["category"] == "weather":
            return weather_model.analyze(m, now=now, fetch=self.weather_fetch) if self.weather_fetch else \
                weather_model.analyze(m, now=now)
        return {"skip": "Kategorie nicht unterstützt"}

    def analyze_all(self, markets, now):
        skips = Counter()
        out = []
        for m in markets:
            if not m["accepting_orders"]:
                skips["nimmt keine Orders an"] += 1
                continue
            try:
                a = self.model(m, now)
            except Exception as e:  # noqa: BLE001
                skips[f"Modellfehler: {type(e).__name__}"] += 1
                log.debug("Modellfehler %s: %s", m["question"], e)
                continue
            if "skip" in a and m.get("lane") != "base" and config.TRADE_BASE and \
                    not (m["category"] == "sports" and m.get("game_start") and m["game_start"] <= now):
                # Eigenes Modell hat für diesen Markt nichts (z. B. Spiel in 3 Tagen, unbekannte Station):
                # dann die Basisraten – gehandelt von den Konten des jeweiligen Themas
                b = self.base.analyze(m, now)
                if "skip" not in b:
                    skips[f"{a['skip'].split(' (')[0]} → Basisraten"] += 1
                    a = b
            if "skip" in a:
                skips[a["skip"].split(" (")[0]] += 1
                continue
            lane = a.get("lane", "model")
            mkt_p = polymarket.mid_yes(m)
            # Aktives Modell laut Register wählen (Wechsel und Mischung mit dem Markt nur nach Freigabe im Lernzentrum –
            # früher wurde das Mischgewicht automatisch angewendet; das ist jetzt nur noch eine Messgröße).
            a = self.intel.select(m, a, mkt_p)
            if lane == "model":  # alte Treffsicherheits-Tabelle nur für die eigenen Datenmodelle
                self.store.add_prediction(m["id"], m["category"], m["question"], a["p_yes"], mkt_p,
                                          m["end_date"].isoformat() if m["end_date"] else None)
            ev = evaluate(m, a["p_yes"])
            spread = (m["best_ask"] - m["best_bid"]) if (m["best_ask"] and m["best_bid"]) else None
            base = []
            if not (config.MIN_PRICE <= ev["price"] <= config.MAX_PRICE):
                base.append("Kurs zu extrem")
            if spread is not None and spread > config.MAX_SPREAD:
                base.append("Spanne zu groß")
            if m["liquidity"] < config.MIN_LIQUIDITY:
                base.append("zu wenig Liquidität")
            if not getattr(config, f"TRADE_{m['category'].upper()}", True) or (lane == "base" and not config.TRADE_BASE):
                base.append("Bereich im Paper-Trading ausgeschaltet")
            if m["category"] in config.GAME_TOPICS:
                grp = game_key(m)       # alle Märkte eines Spiels zählen zusammen (höchstens eine Wette je Konto)
            else:
                grp = a.get("group") or f"{m['category']}:{m['event_id'] or m['id']}"
            out.append({"market": m, "analysis": a, "eval": ev, "market_p": mkt_p, "base": base,
                        "group": grp, "status": {}})
        self.intel.on_results(out, now)   # Prüfung (sperrt Unplausibles über o["base"]) + Prognose-Historie
        out.sort(key=lambda o: o["eval"]["edge"], reverse=True)
        self.state["skips"] = dict(skips.most_common())
        return out

    @staticmethod
    def main_for(o):
        """Hauptkonto, dessen Entscheidung zu dieser Analyse angezeigt wird (das Hauptkonto des Themas)."""
        return MAIN_BY_CAT.get(o["market"]["category"], MAIN_BY_CAT.get("other", MAIN))

    @staticmethod
    def _shown(results):
        model = [o for o in results if o["analysis"].get("lane", "model") == "model"]
        sports = [o for o in model if o["market"]["category"] == "sports"]
        crypto = [o for o in model if o["market"]["category"] == "crypto"][:100]
        weather = [o for o in model if o["market"]["category"] == "weather"][:100]
        others = [o for o in results if o["analysis"].get("lane") == "base"][:150]
        return sorted(sports + crypto + weather + others, key=lambda o: o["eval"]["edge"], reverse=True)

    def _opp_view(self, o):
        m, ev = o["market"], o["eval"]
        bought = [k for k, v in o["status"].items() if v == "eröffnet"]
        return {
            "id": m["id"], "question": m["question"], "category": m["category"], "slug": m["event_slug"],
            "side_name": m["outcomes"][ev["side"]], "own_p": round(o["analysis"]["p_yes"], 4),
            "market_p": round(o["market_p"], 4), "price": round(ev["price"], 4), "edge": round(ev["edge"], 4),
            "info": o["analysis"].get("info", ""), "status": o["status"].get(self.main_for(o), "–"),
            "bought": bought, "sub": m.get("sub"), "detail": m.get("detail"), "lane": o["analysis"].get("lane", "model"),
            "model": o["analysis"].get("model", ""), "valid": o["analysis"].get("valid", True),
            "end": m["end_date"].isoformat() if m["end_date"] else None,
        }

    # ------------------------------------------------------------------ Orderbuch
    def book(self, token_id):
        if not self.book_fn or not token_id:
            return None
        if token_id not in self._books:
            try:
                self._books[token_id] = self.book_fn(token_id)
            except Exception as e:  # noqa: BLE001
                log.info("Orderbuch %s nicht abrufbar: %s", token_id[:12], e)
                self._books[token_id] = None
        return self._books[token_id]

    @staticmethod
    def _token(m, side):
        t = m.get("token_ids") or []
        return t[side] if len(t) == 2 else None

    # ------------------------------------------------------------------ Einstieg
    def do_entries(self, acc, results):
        lanes = acc.get("lanes") or ["model"]
        cats = acc.get("cats")
        aid = acc["id"]
        for o in results:
            m, ev = o["market"], o["eval"]
            lane = o["analysis"].get("lane", "model")
            if lane not in lanes or (cats and m["category"] not in cats) or \
                    (lane == "model" and not cats and m["category"] not in CATS):
                continue
            min_edge = edge_for(acc, m["category"], lane)
            if ev["edge"] < min_edge:
                o["status"][aid] = "Vorsprung zu klein"
            elif o["base"]:
                o["status"][aid] = ", ".join(o["base"])
            elif lane == "base" and abs(o["analysis"].get("z") or 0) < config.BASE_MIN_Z:
                o["status"][aid] = "Abweichung statistisch nicht gesichert"
            elif self.store.has_position(m["id"], aid):
                o["status"][aid] = "schon gehandelt"
            else:
                o["status"][aid] = self.open_position(acc, o, min_edge)

    def _stake(self, acc, ev, min_edge, equity):
        conf = confidence(ev["q"], ev["edge"], min_edge)
        mode = acc.get("stake", "sicherheit")
        if mode == "kelly":
            return equity * config.KELLY_FRACTION * kelly_fraction(ev["q"], ev["eff"]), config.MIN_STAKE, conf
        if mode == "longshot":
            return equity * config.LONGSHOT_STAKE_PCT, config.MIN_STAKE, conf
        if acc.get("all"):   # „Alles tippen“: fester Einsatz, unter Einstellungen änderbar
            return config.ALL_STAKE, min(config.ALL_STAKE, config.MIN_STAKE), conf
        if acc.get("form"):
            return config.FORM_STAKE, min(config.FORM_STAKE, config.MIN_STAKE), conf
        if mode == "fest":
            return acc.get("fixed", 10.0), min(acc.get("fixed", 10.0), max(config.MIN_STAKE, 5.0)), conf
        pct = config.STAKE_MIN_PCT + (config.STAKE_MAX_PCT - config.STAKE_MIN_PCT) * conf
        return equity * pct, max(config.MIN_STAKE, equity * config.STAKE_MIN_PCT * 0.999), conf

    @staticmethod
    def entry_snapshot(acc, o, min_edge, now=None):
        """Alles, was beim Kauf bekannt war – für die Detailansicht und spätere Auswertungen.

        Gibt (Spalten, JSON-Text) zurück. Spalten sind zum Filtern/Sortieren gedacht, das JSON für die Detailseite."""
        m, a, ev = o["market"], o["analysis"], o["eval"]
        now = now or datetime.now(timezone.utc)
        side = ev["side"]
        ask, bid = polymarket.quote(m, side)
        yes_mid = polymarket.mid_yes(m)
        side_mid = yes_mid if side == 0 else 1 - yes_mid
        spread = (m["best_ask"] - m["best_bid"]) if (m.get("best_ask") and m.get("best_bid")) else None
        hours = a.get("hours")
        if hours is None and m.get("end_date"):
            hours = (m["end_date"] - now).total_seconds() / 3600
        lane = a.get("lane", "model")
        data_ts = a.get("data_ts")
        rnd = lambda v, d=4: None if v is None else round(v, d)  # noqa: E731
        cols = {"sub": m.get("sub"), "detail": m.get("detail"), "model": a.get("model_id") or a.get("model"), "lane": lane,
                "p_mid": rnd(side_mid), "uncertainty": rnd(a.get("uncertainty")), "hours_left": rnd(hours, 2),
                "spread": rnd(spread), "liquidity": rnd(m.get("liquidity"), 0), "volume24h": rnd(m.get("volume24h"), 0)}
        entry = {
            "konto": acc["name"], "spur": LANE_NAMES.get(lane, lane), "modell": a.get("model"), "modell_id": a.get("model_id"),
            "p_unser_ausgang": rnd(ev["q"]), "p_ja_modell": rnd(a.get("p_yes")), "p_ja_modell_roh": rnd(a.get("p_model")),
            "p_ja_markt": rnd(yes_mid), "p_unser_ausgang_markt": rnd(side_mid), "kaufkurs": rnd(ask), "verkaufskurs": rnd(bid),
            "gebuehr_je_anteil": rnd(ev.get("fee_ps")), "vorsprung": rnd(ev["edge"]), "mindest_vorsprung": rnd(min_edge),
            "unsicherheit": rnd(a.get("uncertainty")), "z": rnd(a.get("z"), 2), "restlaufzeit_h": rnd(hours, 2),
            "spanne": rnd(spread), "liquiditaet": rnd(m.get("liquidity"), 0), "volumen_24h": rnd(m.get("volume24h"), 0),
            "datenalter_s": round(now.timestamp() - data_ts) if data_ts else None, "quellen": a.get("sources"),
            "pruefung": [[n, bool(ok)] for n, ok in (a.get("checks") or [])], "gueltig": a.get("valid", True),
            "begruendung": a.get("info"), "merkmale": a.get("features"), "varianten": {k: rnd(v) for k, v in (a.get("variants") or {}).items()},
            "ereignis": m.get("event_title"), "gruppe": o.get("group"), "spielbeginn": m["game_start"].isoformat() if m.get("game_start") else None,
            "einordnung": taxonomy.path({"category": m["category"], "sub": m.get("sub"), "detail": m.get("detail")}),
        }
        return cols, json.dumps({k: v for k, v in entry.items() if v not in (None, [], {})}, ensure_ascii=False, default=str)

    def refuse(self, acc, o):
        """Grundregeln für jede Wette (außer Arbitrage): kein genaues Ergebnis, Gewinnchance ≥ MIN_WIN_P, eine Wette je Spiel."""
        m, ev, aid = o["market"], o["eval"], acc["id"]
        if polymarket.is_exact_score(m):
            return "genaues Ergebnis – wird nie gewettet"
        if ev and ev.get("q") is not None and ev["q"] < config.MIN_WIN_P:
            return f"Gewinnchance {ev['q'] * 100:.0f} % – unter {config.MIN_WIN_P * 100:.0f} %"
        if m["category"] in config.GAME_TOPICS and self.store.has_game(aid, o["group"], m.get("event_id")):
            return "schon eine Wette auf dieses Spiel"
        if ev and ev.get("side") is not None:
            return self.hindsight_block(o, ev["side"] == 0)
        return None

    def hindsight_table(self):
        """Rückblick-Tabelle (rechnet der Lern-Agent stündlich), höchstens alle 5 Minuten neu gelesen."""
        now = time.monotonic()
        if getattr(self, "_hs", None) is None or now - self._hs[0] > 300:
            self._hs = (now, hindsight.Table(hindsight.load(self.store)))
        return self._hs[1]

    def hindsight_block(self, o, side_yes):
        """Nur Modell-Wetten: Hätten Wetten in diesem Bereich nachweislich verloren? Dann Grund als Text."""
        a, m = o.get("analysis") or {}, o["market"]
        if a.get("lane", "model") != "model" or a.get("p_yes") is None or o.get("market_p") is None:
            return None
        diff = (a["p_yes"] - o["market_p"]) if side_yes else (o["market_p"] - a["p_yes"])
        bt = taxonomy.bet_type(m.get("question"), m["category"], m.get("detail"))
        return self.hindsight_table().block(m["category"], bt, side_yes, diff)

    def open_position(self, acc, o, min_edge):
        """Gibt den Status zurück ('eröffnet' oder Grund)."""
        m, ev, aid = o["market"], o["eval"], acc["id"]
        refuse = self.refuse(acc, o)
        if refuse:
            return refuse
        pos = self.store.open_positions(aid)
        equity = self.equity(aid, pos)
        cash = self.cash(aid)
        stake, floor, conf = self._stake(acc, ev, min_edge, equity)
        stake = min(stake, equity * config.MAX_STAKE_PCT)
        grp_exp = sum(p["cost"] for p in pos if p.get("grp") and p["grp"] == o["group"])
        stake = min(stake, equity * config.MAX_GROUP_PCT - grp_exp)
        stake = min(stake, equity * config.MAX_EXPOSURE_PCT - self.exposure(pos))
        stake = min(stake, cash)
        if stake < floor:
            return "Gruppen-Limit erreicht" if equity * config.MAX_GROUP_PCT - grp_exp < floor else "Limit erreicht"
        token = self._token(m, ev["side"])
        bk = self.book(token)
        if bk is not None:
            asks, _ = bk
            shares, cost, fee, avg = polymarket.fill_buy(asks, stake, m["fee_rate"], ev["q"], min_edge,
                                                         max_price=o.get("max_price"))
            if not shares or cost < floor or shares < (m.get("min_size") or 5):
                return "Orderbuch zu dünn"
            note = f"Orderbuch: Ø {_cent(avg)} (bester {_cent(asks[0][0])})"
            price = avg
        else:
            price = ev["price"]
            shares = stake / ev["eff"]
            fee = shares * ev["fee_ps"]
            cost = stake
            note = "ohne Orderbuch (bester Kurs angenommen)"
        cost = round(cost, 4)
        q = ev["q"]
        edge = q - (cost / shares)
        side_name = m["outcomes"][ev["side"]]
        cols, entry = self.entry_snapshot(acc, o, min_edge)
        e = json.loads(entry)
        e.update({"sicherheit": round(conf, 3), "einsatz_anteil": round(cost / equity, 4) if equity else None,
                  "kontowert": round(equity, 2), "fuellung": note})
        self.store.insert_position({**cols, "entry": json.dumps(e, ensure_ascii=False, default=str),
            "account": aid, "kind": "model", "grp": o["group"], "token_id": token,
            "market_id": m["id"], "event_id": m["event_id"], "category": m["category"], "question": m["question"],
            "slug": m["slug"], "event_slug": m["event_slug"], "side": ev["side"], "side_name": side_name,
            "shares": round(shares, 4), "price": round(price, 4), "fee": round(fee, 4), "cost": round(cost, 4),
            "own_p": round(q, 4), "market_p": round(ev["price"], 4), "edge": round(edge, 4),
            "info": f"{o['analysis'].get('info', '')} · Sicherheit {conf * 100:.0f} % → {cost / equity * 100:.1f} % Einsatz",
            "fill_note": note, "opened_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "end_date": m["end_date"].isoformat() if m["end_date"] else None,
            "game_start": m["game_start"].isoformat() if m.get("game_start") else None,
            "mark": round(polymarket.quote(m, ev["side"])[1], 4),
        })
        self.store.add_cash(-cost, config.START_BALANCE, aid)
        self.store.log(f"[{acc['name']}] KAUF {side_name} @ {_cent(price)} für {_usd(cost)} · eigene W. {q * 100:.1f} % "
                       f"· Vorsprung {edge * 100:.1f} Pkt · {m['question']}")
        notify.send(f"🟢 <b>Kauf</b> ({acc['name']})\n{m['question']}\n<b>{side_name}</b> zu {_cent(price)} · Einsatz {_usd(cost)}\n"
                    f"Bot: {q * 100:.0f} % · Vorsprung {edge * 100:.1f} Pkt", aid)
        return "eröffnet"

    # ------------------------------------------------------------------ Außenseiter-Konto
    def do_longshots(self, acc, markets, now):
        """Ohne Modell: "Nein" kaufen, wenn "Ja" nur 2–5 % kostet (Märkte überschätzen oft Außenseiter)."""
        aid = acc["id"]
        cats = acc_cats(acc)
        for m in markets:
            if m["category"] not in cats or m.get("lane") == "alles":
                continue
            if not m["accepting_orders"] or not m["end_date"] or m["outcomes"][0].lower() not in ("yes", "ja"):
                continue
            days = (m["end_date"] - now).total_seconds() / 86400
            if days < 0.1 or days > config.LONGSHOT_MAX_DAYS:
                continue
            if m["category"] in config.GAME_TOPICS and m.get("game_start") and m["game_start"] <= now:
                continue
            mid = polymarket.mid_yes(m)
            if not (config.LONGSHOT_MIN <= mid <= config.LONGSHOT_MAX):
                continue
            if m["liquidity"] < config.MIN_LIQUIDITY or self.store.has_position(m["id"], aid):
                continue
            if not m["best_bid"] or not m["best_ask"] or m["best_ask"] - m["best_bid"] > config.MAX_SPREAD:
                continue  # ohne echtes, enges Orderbuch kein fairer Kurs
            ask_no, _ = polymarket.quote(m, 1)
            if ask_no > 0.985:
                continue
            fee = fee_per_share(m["fee_rate"], ask_no)
            ev = {"side": 1, "price": ask_no, "fee_ps": fee, "eff": ask_no + fee, "q": 1 - mid, "edge": (1 - mid) - ask_no - fee}
            grp = game_key(m) if m["category"] in config.GAME_TOPICS else f"{m['category']}:{m['event_id'] or m['id']}"
            o = {"market": m, "eval": ev, "group": grp, "status": {}, "max_price": min(0.985, ask_no + 0.01),
                 "analysis": {"p_yes": mid, "lane": "rule", "model": "Außenseiter-Regel", "model_id": "rule.longshot@1",
                              "info": f"Außenseiter-Regel: Ja steht bei {mid * 100:.1f} % → Nein gekauft (ohne eigenes Modell: "
                                      "unsere Wahrscheinlichkeit ist hier der Marktpreis)"}}
            res = self.open_position(acc, o, min_edge=-1.0)
            if res == "Limit erreicht" and self.exposure(self.store.open_positions(aid)) > \
                    self.equity(aid) * config.MAX_EXPOSURE_PCT * 0.98:
                break

    # ------------------------------------------------------------------ Alles tippen
    @staticmethod
    def all_pick(ms, now):
        """Je Spiel/Ereignis die eine Wette für „Alles tippen“: der Favorit laut Markt.

        Vorrang: Sieger-Markt vor Zusatzmärkten, ein Sieger-Tipp („Ja“ oder ein Name) vor „Nein“, dann die höhere
        Gewinnchance. Nur mit echtem Orderbuch und Kaufkurs zwischen ALL_MIN_PRICE und ALL_MAX_PRICE.
        Gibt (Markt, Seite, Kaufkurs, Gewinnchance) oder None zurück."""
        best = None
        for m in ms:
            mid = polymarket.mid_yes(m)
            outs = [o.lower() for o in m["outcomes"]]
            for side in (0, 1):
                q = mid if side == 0 else 1 - mid
                ask, _ = polymarket.quote(m, side)
                if q < config.ALL_MIN_PRICE or not (config.ALL_MIN_PRICE <= ask <= config.ALL_MAX_PRICE):
                    continue
                winner = outs[side] not in ("no", "nein", "over", "under")
                money = (m.get("sports_market_type") or "moneyline").lower() == "moneyline"
                key = (money, winner, q)
                if best is None or key > best[0]:
                    best = (key, m, side, ask, q)
        return best[1:] if best else None

    def refile_all(self, markets):
        """Einmalig (2.10.1): Am 23.11. landeten die ersten Sport-Tipps ohne Tags in „Alles tippen · Sonstiges“.
        Offene Wetten auf Spiele ins richtige Konto (Sport/E-Sport) umbuchen – samt Einsatz, damit die Bilanzen stimmen."""
        if self.store.kv_get("alles_refile_v1"):
            return
        by_id = {m["id"]: m for m in markets if m.get("lane") == "alles"}
        if not by_id:
            return   # Spiele-Liste noch nicht geladen – nächster Lauf
        moved = 0
        for p in self.store.open_positions("alles_sonstiges"):
            m = by_id.get(p["market_id"])
            if not m or m["category"] not in config.GAME_TOPICS:
                continue
            target = f"alles_{config.TOPIC_CODE[m['category']]}"
            self.store.update_position(p["id"], account=target, category=m["category"], sub=m.get("sub"), detail=m.get("detail"))
            self.store.add_cash(p["cost"], config.START_BALANCE, "alles_sonstiges")
            self.store.add_cash(-p["cost"], config.START_BALANCE, target)
            moved += 1
        self.store.kv_set("alles_refile_v1", str(moved))
        if moved:
            self.store.log(f"„Alles tippen“: {moved} Wetten aus „Sonstiges“ ins richtige Konto umgebucht", "info")

    def do_all(self, acc, markets, now):
        """Ohne Modell: bei jedem Spiel und Ereignis eine Wette auf den Favoriten laut Markt.

        Zweck (Wunsch 23.11.): Daten aus allen Bereichen – auch Tennis, Tischtennis, E-Sport –, um zu sehen, wo Wetten
        Plus oder Minus machen. Es gelten die festen Regeln jeder Wette: nie ein genaues Ergebnis, Gewinnchance ≥ 50 %,
        eine Wette je Spiel, nicht nach Spielbeginn."""
        aid = acc["id"]
        cats = acc_cats(acc)
        groups = {}
        for m in markets:
            if m["category"] not in cats or not m["accepting_orders"] or not m["end_date"] or polymarket.is_exact_score(m):
                continue
            if not getattr(config, f"TRADE_{m['category'].upper()}", True):
                continue   # Thema unter Einstellungen ausgeschaltet
            game = m["category"] in config.GAME_TOPICS
            if game and m.get("game_start"):
                if m["game_start"] <= now or m["game_start"] > now + timedelta(hours=config.ALL_LOOKAHEAD_H):
                    continue
            elif not (0.05 <= (m["end_date"] - now).total_seconds() / 86400 <= config.ALL_MAX_DAYS):
                continue
            if (m["liquidity"] or 0) < config.ALL_MIN_LIQUIDITY or not m["best_bid"] or not m["best_ask"] \
                    or m["best_ask"] - m["best_bid"] > config.ALL_MAX_SPREAD:
                continue  # leeres oder sehr weites Orderbuch: kein fairer Kurs (viele Tennis-/Tischtennis-Märkte)
            grp = game_key(m) if game else f"{m['category']}:{m['event_id'] or m['id']}"
            groups.setdefault(grp, []).append(m)
        far = now + timedelta(days=365)
        new = 0
        # je Sportart/Unterbereich höchstens ALL_MAX_PER_SUB offene Wetten – sonst füllen die vielen Tischtennis-Spiele
        # (alle 30 min eins) das Konto, und Fußball, Basketball, Tennis kämen kaum noch dran (live 23.11.)
        per_sub = Counter(p.get("sub") for p in self.store.open_positions(aid))
        # bald beginnende Spiele zuerst; je Lauf höchstens ALL_MAX_NEW neue Wetten (jede holt das aktuelle Orderbuch)
        for grp, ms in sorted(groups.items(), key=lambda g: min((m.get("game_start") or m["end_date"] or far) for m in g[1])):
            if new >= config.ALL_MAX_NEW:
                break
            if self.store.has_game(aid, grp, ms[0].get("event_id")):
                continue
            pick = self.all_pick(ms, now)
            if not pick:
                continue
            m, side, ask, q = pick
            if per_sub[m.get("sub")] >= config.ALL_MAX_PER_SUB:
                continue
            fee = fee_per_share(m["fee_rate"], ask)
            ev = {"side": side, "price": ask, "fee_ps": fee, "eff": ask + fee, "q": q, "edge": q - ask - fee}
            mid = polymarket.mid_yes(m)
            o = {"market": m, "eval": ev, "group": grp, "status": {}, "max_price": min(0.99, ask + 0.01), "market_p": mid,
                 "analysis": {"p_yes": mid, "lane": "rule", "model": "Favoriten-Regel", "model_id": "rule.favorit@1",
                              "info": f"Alles tippen: Favorit laut Markt – {m['outcomes'][side]} mit {q * 100:.0f} % "
                                      "(ohne eigenes Modell: unsere Wahrscheinlichkeit ist hier der Marktpreis)"}}
            res = self.open_position(acc, o, min_edge=-1.0)
            if res == "eröffnet":
                new += 1
                per_sub[m.get("sub")] += 1
            elif res == "Limit erreicht":
                break

    # ------------------------------------------------------------------ Form-Modell (Elo)
    def form_step(self, markets, now):
        """Ergebnisse sammeln (stündlich), Ausfälle holen, Prognose für jedes Spiel – und als „form.elo“ mitschreiben,
        damit gemessen wird, ob das Modell besser ist als der Markt. Fehler hier halten den Lauf nie an."""
        self.form_map = {}
        try:
            last = self.store.kv_get("form:collected")
            added = None
            if not last or (now - datetime.fromisoformat(last)).total_seconds() >= 3600:
                self.store.kv_set("form:collected", now.isoformat(timespec="seconds"))
                added = self.form.collect(now)
            inj = self.form.injuries_refresh(now)
            pseudo = []
            for m in markets:
                if m["category"] not in config.GAME_TOPICS:
                    continue
                a = self.form.analyze(m, now)
                if not a:
                    continue
                self.form_map[m["id"]] = (m, a)
                pseudo.append({"market": m, "market_p": polymarket.mid_yes(m), "group": game_key(m),
                               "analysis": {"variants": dict(a["variants"]), "model_id": None, "hours": a["hours"],
                                            "features": a["features"]}})
            if pseudo:
                self.intel.probability.log_extra(pseudo, now)
            if added is not None:   # einmal je Stunde ein Lebenszeichen im Log
                fx = self.store.kv_json("form:clubelo", None) or {}
                cur = self.store.kv_json("form:cursor", None) or {}
                log.info("Form-Modell: +%s Ergebnisse (gesamt %s, gesammelt ab %s) · ClubElo %s Spiele · Prognosen jetzt %s "
                         "(Fußball %s, Duelle %s) · Ausfälle %s", added, self.store.scalar("SELECT COUNT(*) FROM match_results"),
                         (cur.get("lo") or "–")[:10], fx.get("spiele", 0), len(self.form_map),
                         sum(1 for m, _ in self.form_map.values() if m["outcomes"][0].lower() in ("yes", "no")),
                         sum(1 for m, _ in self.form_map.values() if m["outcomes"][0].lower() not in ("yes", "no")),
                         "aus (kein Schlüssel)" if not config.API_FOOTBALL_KEY else inj)
        except Exception as e:  # noqa: BLE001
            log.warning("Form-Modell: %s", e)
            self.store.log(f"Form-Modell: {type(e).__name__}: {e}", "error")

    def do_form(self, acc, now):
        """Eigene Konten „Form (Elo)“: Wette auf die Seite, die das Form-Modell für wahrscheinlicher hält, wenn es
        mindestens FORM_MIN_EDGE mehr sieht als der Kaufpreis. Feste Regeln wie überall."""
        aid = acc["id"]
        cats = acc_cats(acc)
        groups = {}
        for m, a in self.form_map.values():
            if m["category"] not in cats or not getattr(config, f"TRADE_{m['category'].upper()}", True):
                continue
            if (m["liquidity"] or 0) < config.ALL_MIN_LIQUIDITY or not m["best_bid"] or not m["best_ask"] \
                    or m["best_ask"] - m["best_bid"] > config.ALL_MAX_SPREAD:
                continue
            side = 0 if a["p_yes"] >= 0.5 else 1
            q = a["p_yes"] if side == 0 else 1 - a["p_yes"]
            ask, _ = polymarket.quote(m, side)
            if not ask or ask > 0.95:
                continue
            fee = fee_per_share(m["fee_rate"], ask)
            edge = q - ask - fee
            if edge < config.FORM_MIN_EDGE:
                continue
            grp = game_key(m)
            if grp not in groups or edge > groups[grp][0]:
                groups[grp] = (edge, m, a, side, ask, q, fee)
        per_sub = Counter(p.get("sub") for p in self.store.open_positions(aid))
        new = 0
        for grp, (edge, m, a, side, ask, q, fee) in sorted(groups.items(), key=lambda g: g[1][1]["game_start"]):
            if new >= config.ALL_MAX_NEW:
                break
            if self.store.has_game(aid, grp, m.get("event_id")) or per_sub[m.get("sub")] >= config.ALL_MAX_PER_SUB:
                continue
            ev = {"side": side, "price": ask, "fee_ps": fee, "eff": ask + fee, "q": q, "edge": edge}
            o = {"market": m, "eval": ev, "group": grp, "status": {}, "max_price": min(0.99, ask + 0.01),
                 "market_p": polymarket.mid_yes(m), "analysis": dict(a)}
            res = self.open_position(acc, o, min_edge=config.FORM_MIN_EDGE)
            if res == "eröffnet":
                new += 1
                per_sub[m.get("sub")] += 1
            elif res == "Limit erreicht":
                break

    # ------------------------------------------------------------------ Limit-Orders (Maker)
    def do_maker(self, acc, results, markets, by_market, now):
        """Gebote unter den fairen Wert legen. Gefüllt gilt ein Gebot, wenn der Briefkurs es erreicht.

        Achtung: Das ist eine Annäherung – in Wirklichkeit stehen andere Gebote vor einem in der Schlange,
        und gefüllt wird man oft gerade dann, wenn der Kurs gegen einen läuft.
        """
        aid = acc["id"]
        by_id = {m["id"]: m for m in markets}
        # 1) offene Gebote prüfen: gefüllt? zurückziehen?
        for od in self.store.open_orders(aid):
            m = by_id.get(od["market_id"])
            created = polymarket.parse_dt(od["created_at"])
            if not m or (created and (now - created).total_seconds() > config.MAKER_MAX_HOURS * 3600):
                self.store.update_order(od["id"], status="cancelled", done_at=now.isoformat(timespec="seconds"),
                                        note="abgelaufen" if m else "Markt nicht mehr offen")
                continue
            ask, _bid = polymarket.quote(m, od["side"])
            if ask <= od["price"] + 1e-9:
                self._fill_order(acc, od, m, now)
                continue
            o = by_market.get(od["market_id"])
            if not o:  # z. B. Spiel hat begonnen oder Messtag läuft – alter Preis wäre gefährlich
                self.store.update_order(od["id"], status="cancelled", done_at=now.isoformat(timespec="seconds"),
                                        note="Markt wird nicht mehr bewertet")
                continue
            q = o["analysis"]["p_yes"] if od["side"] == 0 else 1 - o["analysis"]["p_yes"]
            if q - od["price"] < config.MAKER_EDGE / 2:
                self.store.update_order(od["id"], status="cancelled", done_at=now.isoformat(timespec="seconds"),
                                        note="Vorsprung weg")
        # 2) neue Gebote
        pos = self.store.open_positions(aid)
        orders = self.store.open_orders(aid)
        equity = self.equity(aid, pos)
        committed = self.exposure(pos) + sum(o_["stake"] for o_ in orders)
        cats, lanes = acc_cats(acc), acc.get("lanes") or ["model"]
        for o in results:
            m = o["market"]
            lane = o["analysis"].get("lane", "model")
            if m["category"] not in cats or lane not in lanes:
                continue
            if lane == "base" and abs(o["analysis"].get("z") or 0) < config.BASE_MIN_Z:
                o["status"][aid] = "Abweichung statistisch nicht gesichert"
                continue
            if o["base"] or self.store.has_position(m["id"], aid) or self.store.has_order(m["id"], aid):
                o["status"][aid] = "schon gehandelt" if not o["base"] else ", ".join(o["base"])
                continue
            refuse = self.refuse(acc, {**o, "eval": None})      # Gewinnchance prüft das Gebot selbst (je Seite)
            if refuse:
                o["status"][aid] = refuse
                continue
            p_yes = o["analysis"]["p_yes"]
            best = None
            hs_reason = None
            for side in (0, 1):
                q = p_yes if side == 0 else 1 - p_yes
                if q < config.MIN_WIN_P:
                    continue
                hs = self.hindsight_block(o, side == 0)
                if hs:
                    hs_reason = hs
                    continue
                ask, bid = polymarket.quote(m, side)
                limit = min(q - config.MAKER_EDGE, ask - 0.001)  # nie über/auf dem Briefkurs (sonst wäre es ein Sofortkauf)
                limit = math.floor(limit * 1000) / 1000
                if limit < max(bid, config.MIN_PRICE) or limit > config.MAX_PRICE:
                    continue
                if best is None or q - limit > best[2]:
                    best = (side, limit, q - limit, q)
            if not best:
                o["status"][aid] = hs_reason or "kein sinnvolles Gebot"
                continue
            side, limit, edge, q = best
            conf = confidence(q, edge, config.MAKER_EDGE)
            stake = equity * (config.STAKE_MIN_PCT + (config.STAKE_MAX_PCT - config.STAKE_MIN_PCT) * conf)
            grp_exp = sum(p["cost"] for p in pos if p.get("grp") == o["group"]) + \
                sum(x["stake"] for x in orders if x.get("grp") == o["group"])
            stake = min(stake, equity * config.MAX_GROUP_PCT - grp_exp, equity * config.MAX_EXPOSURE_PCT - committed,
                        self.cash(aid) - sum(x["stake"] for x in orders))
            if stake < equity * config.STAKE_MIN_PCT * 0.999:
                o["status"][aid] = "Limit erreicht"
                continue
            shares = stake / limit
            if shares < (m.get("min_size") or 5):
                o["status"][aid] = "zu klein"
                continue
            ev_ = {"side": side, "q": q, "edge": edge, "fee_ps": 0.0}
            cols, entry = self.entry_snapshot(acc, {**o, "eval": ev_}, config.MAKER_EDGE, now)
            self.store.insert_order({
                "sub": cols["sub"], "detail": cols["detail"], "model": cols["model"], "entry": entry,
                "account": aid, "market_id": m["id"], "event_id": m["event_id"], "category": m["category"],
                "question": m["question"], "slug": m["slug"], "event_slug": m["event_slug"], "side": side,
                "side_name": m["outcomes"][side], "token_id": self._token(m, side), "price": limit,
                "shares": round(shares, 4), "stake": round(stake, 4), "q": round(q, 4), "grp": o["group"],
                "info": o["analysis"].get("info", ""), "created_at": now.isoformat(timespec="seconds"),
                "end_date": m["end_date"].isoformat() if m["end_date"] else None,
                "game_start": m["game_start"].isoformat() if m.get("game_start") else None,
            })
            orders = self.store.open_orders(aid)
            committed += stake
            o["status"][aid] = "Gebot gestellt"

    def _fill_order(self, acc, od, m, now):
        aid = acc["id"]
        cost = round(od["shares"] * od["price"], 4)  # Maker zahlen keine Gebühr
        if cost > self.cash(aid):
            self.store.update_order(od["id"], status="cancelled", done_at=now.isoformat(timespec="seconds"), note="kein Bargeld")
            return
        self.store.update_order(od["id"], status="filled", done_at=now.isoformat(timespec="seconds"), note="gefüllt")
        self.store.insert_position({
            "account": aid, "kind": "model", "grp": od["grp"], "token_id": od["token_id"],
            "market_id": od["market_id"], "event_id": od["event_id"], "category": od["category"], "question": od["question"],
            "slug": od["slug"], "event_slug": od["event_slug"], "side": od["side"], "side_name": od["side_name"],
            "shares": od["shares"], "price": od["price"], "fee": 0.0, "cost": cost, "own_p": od["q"],
            "market_p": od["price"], "edge": round(od["q"] - od["price"], 4), "info": od["info"],
            "sub": od.get("sub"), "detail": od.get("detail"), "model": od.get("model"), "lane": "maker", "entry": od.get("entry"),
            "fill_note": f"Limit-Order zu {_cent(od['price'])} gefüllt (ohne Gebühr)",
            "opened_at": now.isoformat(timespec="seconds"), "end_date": od["end_date"], "game_start": od["game_start"],
            "mark": od["price"],
        })
        self.store.add_cash(-cost, config.START_BALANCE, aid)
        self.store.log(f"[{acc['name']}] GEBOT GEFÜLLT {od['side_name']} @ {_cent(od['price'])} für {_usd(cost)} · {od['question']}")
        notify.send(f"🟢 <b>Gebot gefüllt</b> ({acc['name']})\n{od['question']}\n<b>{od['side_name']}</b> zu {_cent(od['price'])} · {_usd(cost)}", aid)

    # ------------------------------------------------------------------ Risikobremse
    def paused(self, aid):
        return bool(self.state["paused"].get(aid))

    def check_risk(self, now):
        day = now.astimezone(BERLIN).strftime("%Y-%m-%d")
        for a in config.ACCOUNTS:
            aid = a["id"]
            eq = self.equity(aid)
            k = f"daystart:{aid}"
            saved = self.store.kv_json(k, {})
            if saved.get("day") != day:
                saved = {"day": day, "equity": eq}
                self.store.kv_set(k, json.dumps(saved))
            if not config.RISK_PAUSE:
                self._risk_warn(a, eq, saved)
                continue
            manual = self.store.kv_get(f"paused:{aid}")
            reason = None
            if manual:
                reason = manual
            elif eq <= float(self.store.kv_get(f"risk_base:{aid}") or config.START_BALANCE) * (1 - config.RISK_TOTAL_LOSS_PCT):
                reason = f"−{config.RISK_TOTAL_LOSS_PCT * 100:.0f} % seit Start – pausiert bis „Fortsetzen“"
                self.store.kv_set(f"paused:{aid}", reason)
            elif saved.get("locked") or eq <= saved["equity"] * (1 - config.RISK_DAY_LOSS_PCT):
                reason = f"−{config.RISK_DAY_LOSS_PCT * 100:.0f} % heute – pausiert bis morgen"
                if not saved.get("locked"):  # bleibt für den Rest des Tages, auch wenn sich der Kontowert erholt
                    saved["locked"] = True
                    self.store.kv_set(k, json.dumps(saved))
            was = self.state["paused"].get(aid)
            self.state["paused"][aid] = reason
            if reason != was:  # Stand merken: nach einem Neustart ist eine bekannte Pause keine neue Meldung
                self.store.kv_set("paused_state", json.dumps(self.state["paused"]))
            if reason and reason != was:
                self.store.log(f"[{a['name']}] RISIKOBREMSE: {reason}", "error")
                events.emit(self.store, "hoch", "risikobremse", f"Risikobremse: {a['name']}", f"{reason} · Kontowert {_usd(eq)}",
                            key=f"bremse:{aid}:{reason[:12]}", agent="trader", telegram=config.notify_account(aid), dedupe_h=20)

    def _risk_warn(self, a, eq, saved):
        """Ohne Pausen (Standard seit 17.11.): große Verluste nur melden, das Konto handelt weiter."""
        aid = a["id"]
        if self.state["paused"].get(aid):
            self.state["paused"][aid] = None
        start = float(self.store.kv_get(f"risk_base:{aid}") or config.START_BALANCE)
        if eq <= start * (1 - config.RISK_TOTAL_LOSS_PCT):
            what, key = f"−{config.RISK_TOTAL_LOSS_PCT * 100:.0f} % seit Start", "gesamt"
        elif eq <= saved["equity"] * (1 - config.RISK_DAY_LOSS_PCT):
            what, key = f"−{config.RISK_DAY_LOSS_PCT * 100:.0f} % heute", "tag"
        else:
            return
        events.emit(self.store, "normal", "risikobremse", f"Großer Verlust: {a['name']}",
                    f"{what} · Kontowert {_usd(eq)} · Konto handelt weiter (keine Pause eingestellt)",
                    key=f"verlust:{aid}:{key}:{saved.get('day')}", agent="trader", telegram=False, dedupe_h=20)

    def migrate_unpause(self):
        """17.11.: Alle Pausen aufheben (Konten werden nicht mehr pausiert). Einmalig."""
        st = self.store
        if st.kv_get("unpause_v1") or config.RISK_PAUSE:
            return
        n = 0
        for a in config.ACCOUNTS:
            if st.kv_get(f"paused:{a['id']}"):
                st.kv_set(f"paused:{a['id']}", "")
                n += 1
            d = st.kv_json(f"daystart:{a['id']}", {}) or {}
            if d.get("locked"):
                d.pop("locked")
                st.kv_set(f"daystart:{a['id']}", json.dumps(d))
                n += 1
            # Verlustgrenze für eine spätere Wieder-Einschaltung ab dem jetzigen Stand
            st.kv_set(f"risk_base:{a['id']}", round(self.equity(a["id"]), 2))
        st.kv_set("paused_state", json.dumps({}))
        st.kv_set("unpause_v1", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        if n:
            st.log("Alle Pausen aufgehoben – Konten werden nicht mehr pausiert, große Verluste nur noch gemeldet")

    def resume(self, aid):
        # neue Bezugsgröße: −25 % zählen ab jetzt, sonst würde die Bremse sofort wieder greifen
        self.store.kv_set(f"risk_base:{aid}", round(self.equity(aid), 2))
        self.store.kv_set(f"paused:{aid}", "")
        self.store.kv_set(f"daystart:{aid}", json.dumps({}))
        self.state["paused"][aid] = None
        self.store.kv_set("paused_state", json.dumps(self.state["paused"]))
        self.store.log(f"[{ACC.get(aid, {}).get('name', aid)}] wieder freigegeben (Fortsetzen)")
        events.emit(self.store, "info", "risikobremse", f"Konto fortgesetzt: {ACC.get(aid, {}).get('name', aid)}",
                    "Im Dashboard freigegeben; die Verlustgrenze zählt ab dem jetzigen Kontowert neu.", agent="trader", telegram=False)

    # ------------------------------------------------------------------ Selbstkorrektur
    def update_weights(self):
        preds = self.store.resolved_predictions()
        out = {}
        for cat in ("crypto", "sports", "weather"):
            rows = [r for r in preds if r["category"] == cat and r["market_p"] is not None and r["own_p"] is not None
                    and r["outcome"] is not None and 0.05 <= r["market_p"] <= 0.95]
            if len(rows) >= config.CALIB_MIN_N:  # nur Messgröße; angewendet wird, was im Lernzentrum freigegeben ist
                out[cat] = {"w": best_weight(rows), "n": len(rows)}
            else:
                out[cat] = {"w": None, "n": len(rows)}
        self.state["weights"] = out

    # ------------------------------------------------------------------ Ausstieg
    def do_exits(self, acc, by_market, now):
        for p in self.store.open_positions(acc["id"]):
            if p.get("kind") == "arb":
                continue
            o = by_market.get(p["market_id"])
            if not o or o["analysis"].get("valid") is False:  # auf eine abgelehnte Prognose hin wird auch nicht verkauft
                continue
            opened = polymarket.parse_dt(p["opened_at"])
            if opened and (now - opened).total_seconds() < config.MIN_HOLD_MIN * 60:
                continue
            m = o["market"]
            q_now = o["analysis"]["p_yes"] if p["side"] == 0 else 1 - o["analysis"]["p_yes"]
            _ask, bid = polymarket.quote(m, p["side"])
            if bid - fee_per_share(m["fee_rate"], bid) < q_now - config.EXIT_BUFFER:
                continue
            bk = self.book(p.get("token_id") or self._token(m, p["side"]))
            if bk is not None:
                sold, proceeds, fee, avg = polymarket.fill_sell(bk[1], p["shares"], m["fee_rate"],
                                                                min_price=q_now - config.EXIT_BUFFER)
                if sold < p["shares"] * 0.999:
                    continue  # Buch zu dünn, um alles zum fairen Kurs zu verkaufen
            else:
                avg = bid
                fee = p["shares"] * fee_per_share(m["fee_rate"], bid)
                proceeds = p["shares"] * bid - fee
            proceeds = round(proceeds, 4)
            pnl = proceeds - p["cost"]
            why = "Gewinn mitgenommen" if pnl >= 0 else "Modell sieht keinen Vorsprung mehr"
            self.store.update_position(p["id"], status="sold", payout=round(proceeds, 4), pnl=round(pnl, 4),
                                       exit_price=round(avg, 4), mark=round(avg, 4), closed_at=now.isoformat(timespec="seconds"),
                                       exit_reason=f"Früher Ausstieg: {why} – Verkaufserlös {_cent(bid)} je Anteil erreichte die "
                                                   f"eigene Wahrscheinlichkeit von {q_now * 100:.1f} %")
            self.store.add_cash(proceeds, config.START_BALANCE, acc["id"])
            self.store.log(f"[{acc['name']}] VERKAUF {p['side_name']} @ {_cent(avg)} ({why}) {pnl:+.2f} $ · {p['question']}")
            notify.send(f"🔵 <b>Verkauf</b> ({acc['name']}) – {why}\n{p['question']}\n{p['side_name']}: {_cent(p['price'])} → "
                        f"{_cent(avg)} · <b>{pnl:+.2f} $</b>", acc["id"])

    # ------------------------------------------------------------------ Arbitrage
    def find_arbs(self, markets, now):
        """Widersprüche zwischen zusammenhängenden Polymarket-Märkten."""
        out = []

        def yes_cost(m):
            a = m["best_ask"]
            return None if not a or a <= 0 or a >= 1 else a + fee_per_share(m["fee_rate"], a)

        def no_cost(m):
            b = m["best_bid"]
            if not b or b <= 0 or b >= 1:
                return None
            p = 1 - b
            return p + fee_per_share(m["fee_rate"], p)

        # 1) Ereignisse mit sich ausschließenden Antworten (genau eine gewinnt): Summe muss 100 % sein
        by_ev = {}
        for m in markets:
            if m.get("neg_risk") and m["event_id"]:
                by_ev.setdefault(m["event_id"], []).append(m)
        for eid, ms in by_ev.items():
            n = len(ms)
            if n < 2 or n != ms[0].get("event_n_markets"):
                continue
            yc = [yes_cost(m) for m in ms]
            if None not in yc and sum(yc) < 1 - config.ARB_MIN_PROFIT:
                out.append({"id": f"ja:{eid}", "type": "Alle Ja kaufen", "legs": [(m, 0) for m in ms],
                            "cost": sum(yc), "payout": 1.0, "title": ms[0]["event_title"] or ms[0]["question"]})
            nc = [no_cost(m) for m in ms]
            if None not in nc and sum(nc) < (n - 1) - config.ARB_MIN_PROFIT:
                out.append({"id": f"nein:{eid}", "type": "Alle Nein kaufen", "legs": [(m, 1) for m in ms],
                            "cost": sum(nc), "payout": float(n - 1), "title": ms[0]["event_title"] or ms[0]["question"]})
        # 2) Stufen-Leitern "über K": höherer Ziel-Preis darf nie wahrscheinlicher sein
        ladders = {}
        for m in markets:
            if m["category"] != "crypto":
                continue
            p = crypto_model.parse_question(m["question"])
            if not p or p["kind"] != "above" or not m["end_date"]:
                continue
            tt = crypto_model.target_time(m["question"], m["end_date"])
            ladders.setdefault((p["asset"], tt.isoformat()), []).append((p["k1"], m))
        for (asset, tt), rows in ladders.items():
            rows.sort(key=lambda r: r[0])
            best = None
            for i, (k1, m1) in enumerate(rows):
                for k2, m2 in rows[i + 1:]:
                    c1, c2 = yes_cost(m1), no_cost(m2)
                    if c1 is None or c2 is None:
                        continue
                    c = c1 + c2
                    if c < 1 - config.ARB_MIN_PROFIT and (best is None or c < best["cost"]):
                        best = {"id": f"leiter:{m1['id']}:{m2['id']}", "type": "Stufen-Widerspruch",
                                "legs": [(m1, 0), (m2, 1)], "cost": c, "payout": 1.0,
                                "title": f"{asset} über {k1:,.0f} (Ja) + über {k2:,.0f} (Nein)"}
            if best:
                out.append(best)
        return out

    def do_arbitrage(self, markets, now):
        arbs = self.find_arbs(markets, now)
        self.state["arbs"] = [{"type": a["type"], "title": a["title"], "legs": len(a["legs"]),
                               "cost": round(a["cost"], 4), "payout": a["payout"],
                               "profit": round(a["payout"] - a["cost"], 4), "status": {}} for a in arbs]
        for a, view in zip(arbs, self.state["arbs"]):
            for acc in config.ACCOUNTS:
                if acc["arb"] and all(m["category"] in acc_cats(acc) for m, _ in a["legs"]):
                    view["status"][acc["id"]] = self.execute_arb(acc, a, now)
        if arbs:
            log.info("%d Arbitrage-Chancen gefunden", len(arbs))

    def execute_arb(self, acc, a, now):
        aid = acc["id"]
        if any(m["category"] not in acc_cats(acc) for m, _ in a["legs"]):
            return "anderer Bereich"
        if any(self.store.has_position(m["id"], aid) for m, _ in a["legs"]):
            return "schon gehandelt"
        pos = self.store.open_positions(aid)
        equity = self.equity(aid, pos)
        budget = min(equity * config.ARB_MAX_PCT, self.cash(aid), equity * config.MAX_EXPOSURE_PCT - self.exposure(pos))
        n_sets = budget / a["cost"]
        # Tiefe: pro Bein nur so viele Anteile, wie zum angenommenen Kurs wirklich im Buch liegen
        legs = []
        for m, side in a["legs"]:
            px = m["best_ask"] if side == 0 else 1 - m["best_bid"]
            bk = self.book(self._token(m, side))
            if bk is None:
                return "kein Orderbuch"
            avail = sum(s for p_, s in bk[0] if p_ <= px + 1e-9)
            n_sets = min(n_sets, avail)
            legs.append((m, side, px))
        min_size = max((m.get("min_size") or 5) for m, _ in a["legs"])
        profit = n_sets * (a["payout"] - a["cost"])
        if n_sets < min_size or profit < 0.5:
            return "zu wenig Tiefe"
        total = 0.0
        for m, side, px in legs:
            fee = n_sets * fee_per_share(m["fee_rate"], px)
            cost = round(n_sets * px + fee, 4)
            total += cost
            self.store.insert_position({
                "account": aid, "kind": "arb", "lane": "arb", "model": "arbitrage", "sub": m.get("sub"), "detail": m.get("detail"),
                "grp": f"arb:{a['id']}", "token_id": self._token(m, side),
                "market_id": m["id"], "event_id": m["event_id"], "category": m["category"], "question": m["question"],
                "slug": m["slug"], "event_slug": m["event_slug"], "side": side, "side_name": m["outcomes"][side],
                "shares": round(n_sets, 4), "price": round(px, 4), "fee": round(fee, 4), "cost": round(cost, 4),
                "own_p": round(px, 4), "market_p": round(px, 4), "edge": 0.0,
                "info": f"Arbitrage ({a['type']}): {len(legs)} Teile, Auszahlung sicher {_usd(n_sets * a['payout'])}",
                "fill_note": "Arbitrage-Paket", "opened_at": now.isoformat(timespec="seconds"),
                "end_date": m["end_date"].isoformat() if m["end_date"] else None,
                "game_start": m["game_start"].isoformat() if m.get("game_start") else None, "mark": round(px, 4),
            })
            self.store.add_cash(-cost, config.START_BALANCE, aid)
        self.store.log(f"[{acc['name']}] ARBITRAGE {a['type']}: {a['title']} · Einsatz {_usd(total)} → sicher "
                       f"{_usd(n_sets * a['payout'])} (+{_usd(n_sets * a['payout'] - total)})")
        notify.send(f"🟣 <b>Arbitrage</b> ({acc['name']})\n{a['title']}\n{a['type']} · Einsatz {_usd(total)} → "
                    f"sicher {_usd(n_sets * a['payout'])}", aid)
        return "eröffnet"

    # ------------------------------------------------------------------ Schlusskurse (Sport)
    def refresh_closing_odds(self, markets, now):
        """Kurz vor Anpfiff frische Quoten holen, damit der Schlusskurs stimmt (kostet 1 Credit pro Liga)."""
        if not self.odds.enabled:
            return
        by_id = {m["id"]: m for m in markets}
        sports = set()
        for p in self.store.open_positions():
            if p["category"] != "sports" or not p.get("game_start"):
                continue
            gs = polymarket.parse_dt(p["game_start"])
            if gs and timedelta(0) < gs - now <= timedelta(minutes=75) and p["market_id"] in by_id:
                sp = sports_model.league_for(by_id[p["market_id"]])
                if sp:
                    sports.add(sp)
        for sp in sports:
            self.odds.events(sp, max_age=30 * 60)

    def update_closing(self, by_market):
        for p in self.store.open_positions():
            if p["category"] != "sports" or p.get("kind") == "arb":
                continue
            o = by_market.get(p["market_id"])
            if o:  # Analyse gibt es nur bis zum Anpfiff – der letzte Wert ist der Schlusskurs
                q = o["analysis"]["p_yes"] if p["side"] == 0 else 1 - o["analysis"]["p_yes"]
                self.store.update_position(p["id"], close_p=round(q, 4))

    # ------------------------------------------------------------------ Bewerten & Abrechnen
    def mark_positions(self, markets):
        by_id = {m["id"]: m for m in markets}
        for p in self.store.open_positions():
            m = by_id.get(p["market_id"])
            if m:
                _ask, bid = polymarket.quote(m, p["side"])
                self.store.update_position(p["id"], mark=round(bid, 4))

    def settle(self):
        pos = self.store.open_positions()
        # Prognosen (für die Treffsicherheit) reicht es jeden 3. Lauf zu prüfen – das spart Abrufe
        preds = self.store.open_predictions() if self.state["scans"] % 3 == 0 else []
        now = datetime.now(timezone.utc)

        def due(end):
            d = polymarket.parse_dt(end)
            return d is None or d <= now + timedelta(hours=1)

        def played(start):
            # Spiele sind oft lange vor dem offiziellen Marktende entschieden (Tischtennis: Ende = Beginn + 7 Tage)
            d = polymarket.parse_dt(start) if start else None
            return d is not None and d <= now - timedelta(hours=2)

        ids = {p["market_id"] for p in pos if due(p["end_date"]) or played(p.get("game_start"))} | \
            {r["market_id"] for r in preds if due(r["end_date"])}
        if self.state["scans"] % 3 == 0:
            # Ausgang auch für vorzeitig verkaufte Wetten festhalten (2.9): Sonst fehlen in der Auswertung genau die Gewinner,
            # die mit „Gewinn mitgenommen“ kurz vor Schluss verkauft wurden – Bericht #4: E-Sport 0 von 83 „gewonnen“.
            cut = (now - timedelta(days=30)).isoformat(timespec="seconds")
            for r in self.store.q("SELECT DISTINCT p.market_id, p.end_date FROM positions p LEFT JOIN outcomes o ON o.market_id=p.market_id "
                                  "WHERE p.status='sold' AND o.market_id IS NULL AND p.closed_at>=? ORDER BY RANDOM() LIMIT 200", (cut,)):
                if due(r["end_date"]):
                    ids.add(r["market_id"])
        if not ids:
            return
        raws = self.fetch_by_ids(sorted(ids))
        for mid, raw in raws.items():  # Ergebnis dauerhaft festhalten (Grundlage aller Auswertungen)
            r_ = polymarket.resolution(raw)
            if r_ in (0, 1):
                self.store.add_outcome(mid, 1.0 if r_ == 0 else 0.0)
        for p in pos:
            res = polymarket.resolution(raws.get(p["market_id"]))
            if res is None:
                continue
            if res == "void":
                payout, status = p["shares"] * 0.5, "void"
            elif res == p["side"]:
                payout, status = p["shares"], "won"
            else:
                payout, status = 0.0, "lost"
            payout = round(payout, 4)
            pnl = payout - p["cost"]
            acc = p.get("account") or MAIN
            raw = raws.get(p["market_id"]) or {}
            outs = polymarket._loads(raw.get("outcomes"), [])
            winner = outs[res] if res in (0, 1) and len(outs) == 2 else None
            self.store.update_position(p["id"], status=status, payout=round(payout, 4), pnl=round(pnl, 4),
                                       mark=1.0 if status == "won" else (0.5 if status == "void" else 0.0),
                                       closed_at=now.isoformat(timespec="seconds"),
                                       exit_reason=("Markt storniert (50/50)" if status == "void" else
                                                    f"Markt entschieden: „{winner or ('Ja' if res == 0 else 'Nein')}“"))
            self.store.add_cash(payout, config.START_BALANCE, acc)
            word = {"won": "GEWONNEN", "lost": "VERLOREN", "void": "STORNIERT"}[status]
            name = ACC.get(acc, {}).get("name", acc)
            self.store.log(f"[{name}] {word} {pnl:+.2f} $ · {p['side_name']} · {p['question']}")
            if p.get("kind") != "arb":
                icon = {"won": "✅", "lost": "❌", "void": "↩️"}[status]
                notify.send(f"{icon} <b>{word.capitalize()}</b> ({name})\n{p['question']}\n{p['side_name']} · <b>{pnl:+.2f} $</b>", acc)
        for r in preds:
            res = polymarket.resolution(raws.get(r["market_id"]))
            if res in (0, 1):
                self.store.resolve_prediction(r["market_id"], 1.0 if res == 0 else 0.0)

    def _log_summary(self, results):
        """Kurze Zusammenfassung ins Railway-Log (ohne Anmeldung prüfbar)."""
        from . import options
        models = Counter(o["analysis"].get("model", o["market"]["category"]) for o in results)
        bought = Counter(k for o in results for k, v in o["status"].items() if v == "eröffnet")
        arbs = [(a["type"], a["profit"], a["status"]) for a in self.state.get("arbs", [])]
        wfail = sum(1 for v in weather_model.status.values() if not str(v).startswith("OK"))
        timing = " ".join(f"{k} {v:.0f}s" for k, v in getattr(self, "_timer", {}).items())
        hs_now = self.count_hindsight_blocks(results)
        log.info("Lauf: Märkte %s · berechnet %d %s · neue Wetten %s · Arbitrage %s · Optionen %s · Quoten: %s · "
                 "Wetter-Stationen mit Fehler: %d · Rückblick gesperrt: %d Märkte · Dauer: %s",
                 self.state["markets_seen"], len(results), dict(models), dict(bought) or "keine", arbs or "keine",
                 dict(options.status), self.odds.status, wfail, hs_now, timing)

    def count_hindsight_blocks(self, results, now=None):
        """Wie viele Märkte hat der Rückblick heute vom Wetten abgehalten? (2.9, für den Vorher-Nachher-Vergleich im Bericht)

        Gezählt werden verschiedene Märkte je Tag (deutsche Zeit), nicht jede Prüfung – derselbe Markt kommt alle 10 Minuten
        wieder. Ablage in kv `rueckblick:gesperrt` als {Tag: Anzahl} (30 Tage), die Markt-Kennungen des Tages in
        `rueckblick:heute`. Gibt die Zahl der in diesem Lauf gesperrten Märkte zurück."""
        ids = {o["market"]["id"] for o in results
               if any(isinstance(v, str) and v.startswith("Rückblick:") for v in (o.get("status") or {}).values())}
        now = now or datetime.now(timezone.utc)
        day = now.astimezone(BERLIN).date().isoformat()
        today = self.store.kv_json("rueckblick:heute", None) or {}
        seen = set(today.get("ids") or []) if today.get("tag") == day else set()
        if ids - seen or today.get("tag") != day:
            seen |= ids
            self.store.kv_set("rueckblick:heute", json.dumps({"tag": day, "ids": sorted(seen)}))
            per_day = self.store.kv_json("rueckblick:gesperrt", None) or {}
            per_day[day] = len(seen)
            keep = sorted(per_day)[-30:]
            self.store.kv_set("rueckblick:gesperrt", json.dumps({d: per_day[d] for d in keep}))
        return len(ids)

    def _outcomes(self, market_ids):
        """Ausgang je Markt (1.0 = erste Antwort, 0.0 = zweite) aus `outcomes`."""
        out, ids = {}, sorted(set(market_ids))
        for i in range(0, len(ids), 800):
            chunk = ids[i:i + 800]
            for r in self.store.q("SELECT market_id, outcome FROM outcomes WHERE market_id IN (%s)" % ",".join("?" * len(chunk)), chunk):
                out[r["market_id"]] = r["outcome"]
        return out

    def _pnl_by_account(self, since_iso, until_iso=None):
        """Abgerechneter G/V und Anzahl je Konto seit einem Zeitpunkt (eine Abfrage für alle Konten)."""
        q = (f"SELECT account, SUM(pnl) gv, COUNT(*) n, SUM(CASE WHEN {HIT_SQL} AND COALESCE(kind,'')!='arb' THEN 1 ELSE 0 END) w, "
             f"SUM(CASE WHEN {DECIDED_SQL} AND COALESCE(kind,'')!='arb' THEN 1 ELSE 0 END) s "
             "FROM positions WHERE status!='open' AND closed_at>=?")
        args = [since_iso]
        if until_iso:
            q += " AND closed_at<?"
            args.append(until_iso)
        return {r["account"]: r for r in self.store.q(q + " GROUP BY account", args)}

    def strategy_rows(self, per_acc=None):
        """Je Strategie zusammengefasst: Kontowert, G/V seit Start und im Zeitraum (per_acc), über alle Themen."""
        rows = self.strategy_summary(self.accounts_overview())
        for r in rows:
            r.update({"wert": r["equity"], "seit_start": r["pnl_total"], "pausiert": r["paused"], "gv_zeitraum": 0.0, "n": 0, "w": 0, "s": 0})
        by = {r["id"]: r for r in rows}
        for aid, x in (per_acc or {}).items():
            r = by.get((ACC.get(aid) or {}).get("strategy"))
            if r:
                r["gv_zeitraum"] += x["gv"] or 0
                r["n"] += x["n"] or 0
                r["w"] += x["w"] or 0
                r["s"] += x["s"] or 0
        return rows

    def daily_summary(self, now):
        if not notify.enabled():
            return
        local = now.astimezone(BERLIN)
        key = local.strftime("%Y-%m-%d")
        if local.hour < config.DAILY_SUMMARY_HOUR or self.store.kv_get("last_daily") == key:
            return
        self.store.kv_set("last_daily", key)
        start = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat(timespec="seconds")
        per = self._pnl_by_account(start)
        lines = [f"📊 <b>Tagesbilanz {local:%d.%m.}</b>"]
        for a in config.ACCOUNTS:
            if not config.notify_account(a["id"]):
                continue
            x = per.get(a["id"]) or {}
            eq = self.equity(a["id"])
            if not x and abs(eq - config.START_BALANCE) < 0.005 and not self.store.open_positions(a["id"]):
                continue   # noch unberührtes Konto – nicht jeden Tag eine leere Zeile
            lines.append(f"{a['name']}: heute {(x.get('gv') or 0):+.2f} $ ({x.get('n') or 0} abgerechnet) · Konto {_usd(eq)}")
        rows = [r for r in self.strategy_rows(per) if r["n"]]
        if rows:
            lines.append("")
            lines.append("<b>Alle Strategien heute</b> (alle Themen zusammen):")
            for r in sorted(rows, key=lambda r: -r["gv_zeitraum"]):
                lines.append(f"{r['name']}: {r['gv_zeitraum']:+.2f} $ ({r['n']} abgerechnet)")
        notify.send("\n".join(lines))

    def weekly_report(self, now):
        if not notify.enabled():
            return
        local = now.astimezone(BERLIN)
        key = local.strftime("%G-W%V")
        if local.weekday() != 6 or local.hour < config.WEEKLY_REPORT_HOUR or self.store.kv_get("last_weekly") == key:
            return
        self.store.kv_set("last_weekly", key)
        since = now - timedelta(days=7)
        per = self._pnl_by_account(since.isoformat(timespec="seconds"))
        lines = [f"🗓 <b>Wochenbericht bis {local:%d.%m.}</b>", "<b>Strategien</b> (je Strategie alle Themen zusammen):"]
        for r in sorted(self.strategy_rows(per), key=lambda r: -r["seit_start"]):
            wr = f" · Treffer {r['w'] / r['s'] * 100:.0f} %" if r["s"] else ""
            stop = f" · ⛔ {r['pausiert']} von {r['konten']} pausiert" if r["pausiert"] else ""
            lines.append(f"{r['name']}: {r['gv_zeitraum']:+.2f} $ diese Woche · {r['n']} abgerechnet{wr} · "
                         f"seit Start {r['seit_start']:+.2f} $ ({r['konten']} Konten){stop}")
        ranked = sorted(((x["gv"] or 0, ACC.get(aid, {}).get("name", aid)) for aid, x in per.items() if aid in ACC), reverse=True)
        best = [f"{n} {v:+.2f} $" for v, n in ranked[:3] if v > 0]
        worst = [f"{n} {v:+.2f} $" for v, n in ranked[::-1][:3] if v < 0]
        if best:
            lines.append("<b>Beste Konten der Woche:</b> " + " · ".join(best))
        if worst:
            lines.append("<b>Schwächste Konten:</b> " + " · ".join(worst))
        paused = [a["name"] for a in config.ACCOUNTS if self.paused(a["id"])]
        if paused:
            lines.append(f"⛔ Pausiert ({len(paused)}): " + ", ".join(paused[:8]) + (" …" if len(paused) > 8 else ""))
        cal = self.calibration()
        for cat, name in (("crypto", "Krypto"), ("sports", "Sport"), ("weather", "Wetter")):
            c = cal.get(cat, {})
            if c.get("n"):
                better = "Bot genauer" if c["brier_bot"] < c["brier_market"] else "Markt genauer"
                lines.append(f"Treffsicherheit {name}: {better} (Brier {c['brier_bot']:.3f} vs. {c['brier_market']:.3f}, {c['n']} Märkte)")
        lines.append("Mehr im Dashboard – bis zur Besprechung um 19 Uhr!")
        notify.send("\n".join(lines))

    # ------------------------------------------------------------------ Auswertung
    def stats(self, acc=MAIN):
        pos = self.store.open_positions(acc)
        closed = self.store.closed_positions(100000, acc)
        cash = self.cash(acc)
        equity = self.equity(acc, pos)
        model_closed = [c for c in closed if c.get("kind") != "arb"]
        outs = self._outcomes([c["market_id"] for c in model_closed if c["status"] == "sold"])
        hits = {c["id"]: paperview_hit({**c, "mkt_outcome": outs.get(c["market_id"])}) for c in model_closed}
        settled = [c for c in model_closed if hits[c["id"]] is not None]       # mit bekanntem Ausgang, auch vorher verkaufte
        wins = [c for c in settled if hits[c["id"]] == 1]
        staked = sum(c["cost"] for c in closed)
        pnl = sum(c["pnl"] or 0 for c in closed)
        clv_rows = [c for c in closed + pos if c["category"] == "sports" and c.get("close_p") is not None and c.get("kind") != "arb"]
        sold = [c for c in closed if c["status"] == "sold"]
        out = {
            "account": acc, "start": config.START_BALANCE, "cash": cash, "equity": equity, "exposure": self.exposure(pos),
            "pnl_total": equity - config.START_BALANCE, "pnl_realized": pnl,
            "open_count": len(pos), "closed_count": len(closed), "wins": len(wins), "settled": len(settled),
            "winrate": len(wins) / len(settled) if settled else None,
            "expected_winrate": (sum(c["own_p"] for c in settled) / len(settled)) if settled else None,
            "roi": pnl / staked if staked else None, "staked": staked,
            "sold": len(sold), "sold_pnl": sum(c["pnl"] or 0 for c in sold),
            "arb_pnl": sum(c["pnl"] or 0 for c in closed if c.get("kind") == "arb"),
            "clv": statistics.mean(c["close_p"] - c["price"] for c in clv_rows) if clv_rows else None,
            "clv_n": len(clv_rows),
            "started_at": self.store.kv_get(f"started_at:{acc}") or self.store.kv_get("started_at"),
        }
        if acc == MAIN:
            out["calibration"] = self.calibration()
            out["calibration_buckets"] = self.buckets(self.store.resolved_predictions())
        return out

    def calibration(self):
        preds = self.store.resolved_predictions()
        out = {}
        for cat in ("all", "crypto", "sports", "weather"):
            rows = preds if cat == "all" else [r for r in preds if r["category"] == cat]
            mid = [r for r in rows if 0.05 <= r["market_p"] <= 0.95]
            out[cat] = {
                "n": len(mid), "n_all": len(rows),
                "brier_bot": brier(mid, "own_p"), "brier_market": brier(mid, "market_p"),
                "logloss_bot": logloss(mid, "own_p"), "logloss_market": logloss(mid, "market_p"),
            }
        return out

    def accounts_overview(self):
        """Alle Konten mit Kennzahlen – in wenigen Abfragen statt mehrerer je Konto (es gibt rund 90 Konten)."""
        st = self.store
        kv = {r["k"]: r["v"] for r in st.q("SELECT k, v FROM kv WHERE k='cash' OR k LIKE 'cash:%'")}
        openv = {r["account"]: r for r in st.q(
            "SELECT account, COUNT(*) n, SUM(shares*COALESCE(mark, price)) wert, SUM(cost) gebunden FROM positions "
            "WHERE status='open' GROUP BY account")}
        closed = {r["account"]: r for r in st.q(
            "SELECT account, COUNT(*) n, SUM(pnl) gv, SUM(cost) einsatz, "
            f"SUM(CASE WHEN {HIT_SQL} AND COALESCE(kind,'')!='arb' THEN 1 ELSE 0 END) w, "
            f"SUM(CASE WHEN {DECIDED_SQL} AND COALESCE(kind,'')!='arb' THEN 1 ELSE 0 END) s, "
            f"SUM(CASE WHEN {DECIDED_SQL} AND COALESCE(kind,'')!='arb' THEN own_p END) erwartet "
            "FROM positions WHERE status!='open' GROUP BY account")}
        orders = {r["account"]: r["n"] for r in st.q("SELECT account, COUNT(*) n FROM orders WHERE status='open' GROUP BY account")}
        rows = []
        for a in config.ACCOUNTS:
            aid = a["id"]
            v = kv.get(st._ck(aid))
            cash = float(v) if v is not None else config.START_BALANCE
            o, x = openv.get(aid) or {}, closed.get(aid) or {}
            eq = cash + (o.get("wert") or 0)
            settled = x.get("s") or 0
            rows.append({"id": aid, "name": a["name"], "desc": a.get("desc", ""), "strategy": a.get("strategy"), "topic": a.get("topic"),
                         "start": config.START_BALANCE, "cash": cash, "equity": eq, "pnl_total": eq - config.START_BALANCE,
                         "exposure": o.get("gebunden") or 0, "open_count": o.get("n") or 0, "closed_count": x.get("n") or 0,
                         "pnl_realized": x.get("gv") or 0, "staked": x.get("einsatz") or 0, "wins": x.get("w") or 0, "settled": settled,
                         "winrate": (x.get("w") or 0) / settled if settled else None,
                         "expected_winrate": (x.get("erwartet") or 0) / settled if settled else None,
                         "roi": (x.get("gv") or 0) / x["einsatz"] if x.get("einsatz") else None,
                         "paused": self.state["paused"].get(aid), "orders": orders.get(aid, 0)})
        return rows

    @staticmethod
    def strategy_summary(accounts):
        """Konten je Strategie zusammengefasst (alle Themen)."""
        out = {}
        for a in accounts:
            sid = a.get("strategy") or a["id"]
            r = out.setdefault(sid, {"id": sid, "name": config.STRATEGY_NAME.get(sid, a["name"]), "konten": 0, "equity": 0.0, "pnl_total": 0.0,
                                     "open_count": 0, "wins": 0, "settled": 0, "pnl_realized": 0.0, "staked": 0.0, "paused": 0, "aktiv": 0})
            r["konten"] += 1
            for k in ("equity", "pnl_total", "open_count", "wins", "settled", "pnl_realized", "staked"):
                r[k] += a.get(k) or 0
            r["paused"] += 1 if a.get("paused") else 0
            r["aktiv"] += 1 if (a.get("open_count") or a.get("closed_count")) else 0
        for r in out.values():
            r["winrate"] = r["wins"] / r["settled"] if r["settled"] else None
            r["roi"] = r["pnl_realized"] / r["staked"] if r["staked"] else None
            r["desc"] = next((x.get("rules") for x in config.STRATEGIES if x["id"] == r["id"]), "")
        return [out[k] for k in [x["id"] for x in config.STRATEGIES] if k in out] + [v for k, v in out.items()
                                                                                  if k not in {x["id"] for x in config.STRATEGIES}]

    @staticmethod
    def buckets(preds):
        """Kalibrierung: Wenn der Bot 30 % sagt – tritt es auch in ~30 % der Fälle ein?"""
        edges = [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0001]
        out = []
        for lo, hi in zip(edges[:-1], edges[1:]):
            rows = [r for r in preds if lo <= r["own_p"] < hi and 0.02 <= r["market_p"] <= 0.98]
            if rows:
                out.append({"range": f"{lo * 100:.0f}–{min(hi, 1) * 100:.0f} %", "n": len(rows),
                            "bot": sum(r["own_p"] for r in rows) / len(rows),
                            "market": sum(r["market_p"] for r in rows) / len(rows),
                            "actual": sum(r["outcome"] for r in rows) / len(rows)})
        return out

    @staticmethod
    def _telegram_status():
        if notify.enabled():
            return f"aktiv ({notify.last_status})"
        if config.TELEGRAM_BOT_TOKEN:
            cid = notify.find_chat_id_cached()
            return (f"Token gesetzt, Chat-ID fehlt – gefunden: {cid} → als TELEGRAM_CHAT_ID eintragen" if cid else
                    f"Token gesetzt, Chat-ID fehlt – {notify.last_status}")
        return notify.last_status

    def status(self):
        from . import options
        return {
            "prices": dict(source_status),
            "options": dict(options.status),
            "weather": dict(weather_model.status),
            "odds": self.odds.status,
            "odds_credits": self.odds.credits_left(),
            "telegram": self._telegram_status(),
        }
