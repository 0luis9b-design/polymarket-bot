"""Herzstück: Märkte holen, eigene Wahrscheinlichkeit berechnen, mit Spielgeld wetten, abrechnen."""
import json
import logging
import math
import threading
import time
import traceback
from collections import Counter
from datetime import datetime, timedelta, timezone

from . import config, crypto_model, polymarket, sports_model
from .prices import hourly_closes, source_status

log = logging.getLogger("polybot.engine")


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


class Engine:
    def __init__(self, store, fetch_events=polymarket.fetch_events, fetch_by_ids=polymarket.fetch_markets_by_ids,
                 closes_fn=hourly_closes, odds_client=None):
        self.store = store
        self.fetch_events = fetch_events
        self.fetch_by_ids = fetch_by_ids
        self.closes_fn = closes_fn
        self.odds = odds_client or sports_model.OddsClient(store)
        self.lock = threading.Lock()
        self.state = {"last_scan": None, "last_error": None, "running": False, "scans": 0,
                      "opportunities": [], "skips": {}, "markets_seen": {}, "duration": None}
        saved = store.kv_json("last_state", {})  # letzte Analyse nach Neustart weiter anzeigen
        for k in ("last_scan", "opportunities", "skips", "markets_seen", "duration"):
            if k in saved:
                self.state[k] = saved[k]

    # ------------------------------------------------------------------ Konto
    def cash(self):
        return self.store.cash(config.START_BALANCE)

    def exposure(self, positions=None):
        return sum(p["cost"] for p in (positions if positions is not None else self.store.open_positions()))

    def equity(self, positions=None):
        positions = positions if positions is not None else self.store.open_positions()
        return self.cash() + sum(p["shares"] * (p["mark"] if p["mark"] is not None else p["price"]) for p in positions)

    # ------------------------------------------------------------------ Ablauf
    def run_cycle(self, now=None):
        if not self.lock.acquire(blocking=False):
            return False
        t0 = time.time()
        self.state["running"] = True
        try:
            now = now or datetime.now(timezone.utc)
            self.settle()
            markets = self.collect_markets(now)
            self.analyze_and_trade(markets, now)
            self.mark_positions(markets)
            pos = self.store.open_positions()
            self.store.add_equity(round(self.equity(pos), 2), round(self.cash(), 2))
            self.state["last_error"] = None
        except Exception as e:  # noqa: BLE001
            self.state["last_error"] = f"{type(e).__name__}: {e}"
            self.store.log(f"Fehler im Lauf: {e}", "error")
            log.error(traceback.format_exc())
        finally:
            self.state["last_scan"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self.state["duration"] = round(time.time() - t0, 1)
            self.state["scans"] += 1
            self.state["running"] = False
            try:
                self.store.kv_set("last_state", json.dumps(
                    {k: self.state[k] for k in ("last_scan", "opportunities", "skips", "markets_seen", "duration")}))
            except (TypeError, ValueError) as e:
                log.warning("Zustand nicht speicherbar: %s", e)
            self.lock.release()
        return True

    def collect_markets(self, now):
        markets = []
        seen = {}
        try:
            evs = self.fetch_events("crypto")
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
                # Bald beginnende Spiele zuerst, damit das knappe Quoten-Budget dort landet
                far = datetime.max.replace(tzinfo=timezone.utc)
                ms.sort(key=lambda m: m["game_start"] or far)
                seen["sports"] = len(ms)
                markets += ms
            except Exception as e:  # noqa: BLE001
                self.store.log(f"Sport-Märkte nicht abrufbar: {e}", "error")
        self.state["markets_seen"] = seen
        return markets

    def model(self, m, now):
        if m["category"] == "crypto":
            return crypto_model.analyze(m, now=now, closes_fn=self.closes_fn)
        if m["category"] == "sports":
            return sports_model.analyze(m, self.odds, now=now)
        return {"skip": "Kategorie nicht unterstützt"}

    def analyze_and_trade(self, markets, now):
        skips = Counter()
        opps = []
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
            if "skip" in a:
                skips[a["skip"].split(" (")[0]] += 1
                continue
            p_yes = a["p_yes"]
            mkt_p = polymarket.mid_yes(m)
            self.store.add_prediction(m["id"], m["category"], m["question"], p_yes, mkt_p,
                                      m["end_date"].isoformat() if m["end_date"] else None)
            ev = evaluate(m, p_yes)
            spread = (m["best_ask"] - m["best_bid"]) if (m["best_ask"] and m["best_bid"]) else None
            reasons = []
            min_edge = config.MIN_EDGE_CRYPTO if m["category"] == "crypto" else config.MIN_EDGE_SPORTS
            if ev["edge"] < min_edge:
                reasons.append("Vorsprung zu klein")
            if not (config.MIN_PRICE <= ev["price"] <= config.MAX_PRICE):
                reasons.append("Kurs zu extrem")
            if spread is not None and spread > config.MAX_SPREAD:
                reasons.append("Spanne zu groß")
            if m["liquidity"] < config.MIN_LIQUIDITY:
                reasons.append("zu wenig Liquidität")
            if self.store.has_position(m["id"]):
                reasons.append("schon gehandelt")
            opps.append({
                "market": m, "analysis": a, "eval": ev, "market_p": mkt_p, "reasons": reasons,
            })
        opps.sort(key=lambda o: o["eval"]["edge"], reverse=True)
        for o in opps:
            if not o["reasons"]:
                res = self.open_position(o)
                if res:
                    o["reasons"] = [res]
        self.state["skips"] = dict(skips.most_common())
        self.state["opportunities"] = [self._opp_view(o) for o in opps[:150]]

    def _opp_view(self, o):
        m, ev = o["market"], o["eval"]
        return {
            "id": m["id"], "question": m["question"], "category": m["category"], "slug": m["event_slug"],
            "side_name": m["outcomes"][ev["side"]], "own_p": round(o["analysis"]["p_yes"], 4),
            "market_p": round(o["market_p"], 4), "price": round(ev["price"], 4), "edge": round(ev["edge"], 4),
            "info": o["analysis"].get("info", ""), "status": ", ".join(o["reasons"]) or "–",
            "end": m["end_date"].isoformat() if m["end_date"] else None,
        }

    def open_position(self, o):
        """Gibt einen Grund zurück, falls nicht eröffnet, sonst 'eröffnet'."""
        m, ev = o["market"], o["eval"]
        pos = self.store.open_positions()
        equity = self.equity(pos)
        cash = self.cash()
        min_edge = config.MIN_EDGE_CRYPTO if m["category"] == "crypto" else config.MIN_EDGE_SPORTS
        conf = confidence(ev["q"], ev["edge"], min_edge)
        if config.STAKE_MODE == "kelly":
            stake = equity * config.KELLY_FRACTION * kelly_fraction(ev["q"], ev["eff"])
            floor = config.MIN_STAKE
        else:
            pct = config.STAKE_MIN_PCT + (config.STAKE_MAX_PCT - config.STAKE_MIN_PCT) * conf
            stake = equity * pct
            floor = max(config.MIN_STAKE, equity * config.STAKE_MIN_PCT * 0.999)
        stake = min(stake, equity * config.MAX_STAKE_PCT)
        ev_exposure = sum(p["cost"] for p in pos if p["event_id"] and p["event_id"] == m["event_id"])
        stake = min(stake, equity * config.MAX_EVENT_PCT - ev_exposure)
        stake = min(stake, equity * config.MAX_EXPOSURE_PCT - self.exposure(pos))
        stake = min(stake, cash)
        if stake < floor:
            return "Limit erreicht"
        stake = round(stake, 4)
        shares = stake / ev["eff"]
        fee = shares * ev["fee_ps"]
        side_name = m["outcomes"][ev["side"]]
        self.store.insert_position({
            "market_id": m["id"], "event_id": m["event_id"], "category": m["category"], "question": m["question"],
            "slug": m["slug"], "event_slug": m["event_slug"], "side": ev["side"], "side_name": side_name,
            "shares": round(shares, 4), "price": round(ev["price"], 4), "fee": round(fee, 4), "cost": round(stake, 4),
            "own_p": round(ev["q"], 4), "market_p": round(ev["price"], 4), "edge": round(ev["edge"], 4),
            "info": f"{o['analysis'].get('info', '')} · Sicherheit {conf * 100:.0f} % → {stake / equity * 100:.1f} % Einsatz",
            "opened_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "end_date": m["end_date"].isoformat() if m["end_date"] else None, "mark": round(ev["price"], 4),
        })
        self.store.add_cash(-stake, config.START_BALANCE)
        self.store.log(f"KAUF {side_name} @ {ev['price']:.3f} für {stake:.2f} $ · eigene W. {ev['q'] * 100:.1f} % "
                       f"· Vorsprung {ev['edge'] * 100:.1f} Pkt · {m['question']}")
        return "eröffnet"

    def mark_positions(self, markets):
        by_id = {m["id"]: m for m in markets}
        for p in self.store.open_positions():
            m = by_id.get(p["market_id"])
            if m:
                _ask, bid = polymarket.quote(m, p["side"])
                self.store.update_position(p["id"], mark=round(bid, 4))

    def settle(self):
        pos = self.store.open_positions()
        preds = self.store.open_predictions()
        now = datetime.now(timezone.utc)

        def due(end):
            d = polymarket.parse_dt(end)
            return d is None or d <= now + timedelta(hours=1)

        ids = {p["market_id"] for p in pos if due(p["end_date"])} | {r["market_id"] for r in preds if due(r["end_date"])}
        if not ids:
            return
        raws = self.fetch_by_ids(sorted(ids))
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
            pnl = payout - p["cost"]
            self.store.update_position(p["id"], status=status, payout=round(payout, 4), pnl=round(pnl, 4),
                                       mark=1.0 if status == "won" else (0.5 if status == "void" else 0.0),
                                       closed_at=now.isoformat(timespec="seconds"))
            self.store.add_cash(payout, config.START_BALANCE)
            word = {"won": "GEWONNEN", "lost": "VERLOREN", "void": "STORNIERT"}[status]
            self.store.log(f"{word} {pnl:+.2f} $ · {p['side_name']} · {p['question']}")
        for r in preds:
            res = polymarket.resolution(raws.get(r["market_id"]))
            if res in (0, 1):
                self.store.resolve_prediction(r["market_id"], 1.0 if res == 0 else 0.0)

    # ------------------------------------------------------------------ Auswertung
    def stats(self):
        pos = self.store.open_positions()
        closed = self.store.closed_positions(100000)
        cash = self.cash()
        equity = self.equity(pos)
        settled = [c for c in closed if c["status"] in ("won", "lost")]
        wins = [c for c in settled if c["status"] == "won"]
        staked = sum(c["cost"] for c in closed)
        pnl = sum(c["pnl"] or 0 for c in closed)
        out = {
            "start": config.START_BALANCE, "cash": cash, "equity": equity, "exposure": self.exposure(pos),
            "pnl_total": equity - config.START_BALANCE, "pnl_realized": pnl,
            "open_count": len(pos), "closed_count": len(closed), "wins": len(wins),
            "winrate": len(wins) / len(settled) if settled else None,
            "expected_winrate": (sum(c["own_p"] for c in settled) / len(settled)) if settled else None,
            "roi": pnl / staked if staked else None,
            "started_at": self.store.kv_get("started_at"),
            "by_cat": {},
            "calibration": {},
        }
        for cat in ("crypto", "sports"):
            cc = [c for c in closed if c["category"] == cat]
            st = [c for c in cc if c["status"] in ("won", "lost")]
            out["by_cat"][cat] = {
                "count": len(cc), "pnl": sum(c["pnl"] or 0 for c in cc),
                "winrate": (sum(1 for c in st if c["status"] == "won") / len(st)) if st else None,
                "open": sum(1 for p in pos if p["category"] == cat),
            }
        preds = self.store.resolved_predictions()
        for cat in ("all", "crypto", "sports"):
            rows = preds if cat == "all" else [r for r in preds if r["category"] == cat]
            # Nur interessante Fälle: Markt zwischen 5 % und 95 % (sonst ist es trivial)
            mid = [r for r in rows if 0.05 <= r["market_p"] <= 0.95]
            out["calibration"][cat] = {
                "n": len(mid), "n_all": len(rows),
                "brier_bot": brier(mid, "own_p"), "brier_market": brier(mid, "market_p"),
                "logloss_bot": logloss(mid, "own_p"), "logloss_market": logloss(mid, "market_p"),
            }
        out["calibration_buckets"] = self.buckets(preds)
        return out

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

    def status(self):
        return {
            "prices": dict(source_status),
            "odds": self.odds.status,
            "odds_credits": self.odds.credits_left(),
        }
