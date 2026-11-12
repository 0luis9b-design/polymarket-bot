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
import statistics
import threading
import time
import traceback
from collections import Counter
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import config, crypto_model, notify, polymarket, sports_model, weather_model
from .prices import hourly_closes, source_status

log = logging.getLogger("polybot.engine")
BERLIN = ZoneInfo("Europe/Berlin")
ACC = {a["id"]: a for a in config.ACCOUNTS}
MAIN = config.ACCOUNTS[0]["id"]


def edge_for(acc, cat):
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
                 closes_fn=hourly_closes, odds_client=None, chain_fn=None, book_fn=polymarket.fetch_book):
        self.store = store
        self.fetch_events = fetch_events
        self.fetch_by_ids = fetch_by_ids
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
        saved = store.kv_json("last_state", {})  # letzte Analyse nach Neustart weiter anzeigen
        for k in ("last_scan", "opportunities", "skips", "markets_seen", "duration", "arbs", "weights"):
            if k in saved:
                self.state[k] = saved[k]

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
            self.settle()
            self.update_weights()
            markets = self.collect_markets(now)
            self.refresh_closing_odds(markets, now)
            results = self.analyze_all(markets, now)
            by_market = {o["market"]["id"]: o for o in results}
            self.check_risk(now)
            for a in config.ACCOUNTS:
                if a["exit"]:
                    self.do_exits(a, by_market, now)
                if self.paused(a["id"]):
                    for o in results:
                        o["status"][a["id"]] = "Risikobremse aktiv"
                    continue
                if a.get("maker"):
                    self.do_maker(a, results, markets, by_market, now)
                elif a.get("longshot"):
                    self.do_longshots(a, markets, now)
                elif a["model"]:
                    self.do_entries(a, results)
            self.do_arbitrage(markets, now)
            self.update_closing(by_market)
            self.mark_positions(markets)
            for a in config.ACCOUNTS:
                pos = self.store.open_positions(a["id"])
                self.store.add_equity(round(self.equity(a["id"], pos), 2), round(self.cash(a["id"]), 2), a["id"])
            self.state["opportunities"] = [self._opp_view(o) for o in self._shown(results)]
            self.daily_summary(now)
            self.weekly_report(now)
            self._log_summary(results)
            if self._err_runs >= config.ERROR_ALERT_RUNS:
                notify.send("✅ <b>Polymarket-Bot</b>: Lauf klappt wieder ohne Fehler")
            self._err_runs = 0
            self.state["last_error"] = None
        except Exception as e:  # noqa: BLE001
            self.state["last_error"] = f"{type(e).__name__}: {e}"
            self.store.log(f"Fehler im Lauf: {e}", "error")
            log.error(traceback.format_exc())
            self._err_runs += 1
            if self._err_runs == config.ERROR_ALERT_RUNS:
                notify.send(f"⚠️ <b>Polymarket-Bot</b>: {self._err_runs} Läufe hintereinander mit Fehler\n{self.state['last_error'][:300]}")
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
        self.state["markets_seen"] = seen
        return markets

    def model(self, m, now):
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
            if "skip" in a:
                skips[a["skip"].split(" (")[0]] += 1
                continue
            mkt_p = polymarket.mid_yes(m)
            self.store.add_prediction(m["id"], m["category"], m["question"], a["p_yes"], mkt_p,
                                      m["end_date"].isoformat() if m["end_date"] else None)
            w = self.state["weights"].get(m["category"], {}).get("w")
            if w is not None and w < 1:  # Selbstkorrektur: mit dem Marktkurs mischen, so viel wie es früher geholfen hätte
                a["p_raw"] = a["p_yes"]
                a["p_yes"] = w * a["p_yes"] + (1 - w) * mkt_p
                a["info"] = f"{a.get('info', '')} · gemischt {w * 100:.0f} % Bot / {(1 - w) * 100:.0f} % Markt (roh {a['p_raw'] * 100:.1f} %)"
            ev = evaluate(m, a["p_yes"])
            spread = (m["best_ask"] - m["best_bid"]) if (m["best_ask"] and m["best_bid"]) else None
            base = []
            if not (config.MIN_PRICE <= ev["price"] <= config.MAX_PRICE):
                base.append("Kurs zu extrem")
            if spread is not None and spread > config.MAX_SPREAD:
                base.append("Spanne zu groß")
            if m["liquidity"] < config.MIN_LIQUIDITY:
                base.append("zu wenig Liquidität")
            grp = a.get("group") if m["category"] in ("crypto", "weather") else f"spiel:{m['event_id'] or m['id']}"
            out.append({"market": m, "analysis": a, "eval": ev, "market_p": mkt_p, "base": base,
                        "group": grp, "status": {}})
        out.sort(key=lambda o: o["eval"]["edge"], reverse=True)
        self.state["skips"] = dict(skips.most_common())
        return out

    @staticmethod
    def _shown(results):
        sports = [o for o in results if o["market"]["category"] == "sports"]
        crypto = [o for o in results if o["market"]["category"] == "crypto"][:100]
        weather = [o for o in results if o["market"]["category"] == "weather"][:100]
        return sorted(sports + crypto + weather, key=lambda o: o["eval"]["edge"], reverse=True)

    def _opp_view(self, o):
        m, ev = o["market"], o["eval"]
        bought = [k for k, v in o["status"].items() if v == "eröffnet"]
        return {
            "id": m["id"], "question": m["question"], "category": m["category"], "slug": m["event_slug"],
            "side_name": m["outcomes"][ev["side"]], "own_p": round(o["analysis"]["p_yes"], 4),
            "market_p": round(o["market_p"], 4), "price": round(ev["price"], 4), "edge": round(ev["edge"], 4),
            "info": o["analysis"].get("info", ""), "status": o["status"].get(MAIN, "–"), "bought": bought,
            "model": o["analysis"].get("model", ""),
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
        for o in results:
            m, ev = o["market"], o["eval"]
            aid = acc["id"]
            min_edge = edge_for(acc, m["category"])
            if ev["edge"] < min_edge:
                o["status"][aid] = "Vorsprung zu klein"
            elif o["base"]:
                o["status"][aid] = ", ".join(o["base"])
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
        if mode == "fest":
            return acc.get("fixed", 10.0), min(acc.get("fixed", 10.0), max(config.MIN_STAKE, 5.0)), conf
        pct = config.STAKE_MIN_PCT + (config.STAKE_MAX_PCT - config.STAKE_MIN_PCT) * conf
        return equity * pct, max(config.MIN_STAKE, equity * config.STAKE_MIN_PCT * 0.999), conf

    def open_position(self, acc, o, min_edge):
        """Gibt den Status zurück ('eröffnet' oder Grund)."""
        m, ev, aid = o["market"], o["eval"], acc["id"]
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
        self.store.insert_position({
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
        for m in markets:
            if not m["accepting_orders"] or not m["end_date"] or m["outcomes"][0].lower() not in ("yes", "ja"):
                continue
            days = (m["end_date"] - now).total_seconds() / 86400
            if days < 0.1 or days > config.LONGSHOT_MAX_DAYS:
                continue
            if m["category"] == "sports" and m.get("game_start") and m["game_start"] <= now:
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
            grp = (f"{m['category']}:{m['event_id'] or m['id']}")
            o = {"market": m, "eval": ev, "group": grp, "status": {}, "max_price": min(0.985, ask_no + 0.01),
                 "analysis": {"p_yes": mid, "info": f"Außenseiter-Regel: Ja steht bei {mid * 100:.1f} % → Nein gekauft"}}
            res = self.open_position(acc, o, min_edge=-1.0)
            if res == "Limit erreicht" and self.exposure(self.store.open_positions(aid)) > \
                    self.equity(aid) * config.MAX_EXPOSURE_PCT * 0.98:
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
        for o in results:
            m = o["market"]
            if o["base"] or self.store.has_position(m["id"], aid) or self.store.has_order(m["id"], aid):
                o["status"][aid] = "schon gehandelt" if not o["base"] else ", ".join(o["base"])
                continue
            p_yes = o["analysis"]["p_yes"]
            best = None
            for side in (0, 1):
                q = p_yes if side == 0 else 1 - p_yes
                ask, bid = polymarket.quote(m, side)
                limit = min(q - config.MAKER_EDGE, ask - 0.001)  # nie über/auf dem Briefkurs (sonst wäre es ein Sofortkauf)
                limit = math.floor(limit * 1000) / 1000
                if limit < max(bid, config.MIN_PRICE) or limit > config.MAX_PRICE:
                    continue
                if best is None or q - limit > best[2]:
                    best = (side, limit, q - limit, q)
            if not best:
                o["status"][aid] = "kein sinnvolles Gebot"
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
            self.store.insert_order({
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
            manual = self.store.kv_get(f"paused:{aid}")
            reason = None
            if manual:
                reason = manual
            elif eq <= float(self.store.kv_get(f"risk_base:{aid}") or config.START_BALANCE) * (1 - config.RISK_TOTAL_LOSS_PCT):
                reason = f"−{config.RISK_TOTAL_LOSS_PCT * 100:.0f} % seit Start – pausiert bis „Fortsetzen“"
                self.store.kv_set(f"paused:{aid}", reason)
            elif eq <= saved["equity"] * (1 - config.RISK_DAY_LOSS_PCT):
                reason = f"−{config.RISK_DAY_LOSS_PCT * 100:.0f} % heute – pausiert bis morgen"
            was = self.state["paused"].get(aid)
            self.state["paused"][aid] = reason
            if reason and reason != was:
                self.store.log(f"[{a['name']}] RISIKOBREMSE: {reason}", "error")
                notify.send(f"⛔ <b>Risikobremse</b> ({a['name']})\n{reason}\nKontowert {_usd(eq)}", aid)

    def resume(self, aid):
        # neue Bezugsgröße: −25 % zählen ab jetzt, sonst würde die Bremse sofort wieder greifen
        self.store.kv_set(f"risk_base:{aid}", round(self.equity(aid), 2))
        self.store.kv_set(f"paused:{aid}", "")
        self.store.kv_set(f"daystart:{aid}", json.dumps({}))
        self.state["paused"][aid] = None
        self.store.log(f"[{ACC.get(aid, {}).get('name', aid)}] wieder freigegeben (Fortsetzen)")

    # ------------------------------------------------------------------ Selbstkorrektur
    def update_weights(self):
        preds = self.store.resolved_predictions()
        out = {}
        for cat in ("crypto", "sports", "weather"):
            rows = [r for r in preds if r["category"] == cat and 0.05 <= r["market_p"] <= 0.95]
            if len(rows) >= config.CALIB_MIN_N:
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
            if not o:
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
            self.store.update_position(p["id"], status="sold", payout=round(proceeds, 4), pnl=round(pnl, 4),
                                       exit_price=round(avg, 4), mark=round(avg, 4),
                                       closed_at=now.isoformat(timespec="seconds"))
            self.store.add_cash(proceeds, config.START_BALANCE, acc["id"])
            why = "Gewinn mitgenommen" if pnl >= 0 else "Modell sieht keinen Vorsprung mehr"
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
                if acc["arb"]:
                    view["status"][acc["id"]] = self.execute_arb(acc, a, now)
        if arbs:
            log.info("%d Arbitrage-Chancen gefunden", len(arbs))

    def execute_arb(self, acc, a, now):
        aid = acc["id"]
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
                "account": aid, "kind": "arb", "grp": f"arb:{a['id']}", "token_id": self._token(m, side),
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
            payout = round(payout, 4)
            pnl = payout - p["cost"]
            acc = p.get("account") or MAIN
            self.store.update_position(p["id"], status=status, payout=round(payout, 4), pnl=round(pnl, 4),
                                       mark=1.0 if status == "won" else (0.5 if status == "void" else 0.0),
                                       closed_at=now.isoformat(timespec="seconds"))
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
        log.info("Lauf: Märkte %s · berechnet %d %s · neue Wetten %s · Arbitrage %s · Optionen %s · Quoten: %s",
                 self.state["markets_seen"], len(results), dict(models), dict(bought) or "keine", arbs or "keine",
                 dict(options.status), self.odds.status)

    def daily_summary(self, now):
        if not notify.enabled():
            return
        local = now.astimezone(BERLIN)
        key = local.strftime("%Y-%m-%d")
        if local.hour < config.DAILY_SUMMARY_HOUR or self.store.kv_get("last_daily") == key:
            return
        self.store.kv_set("last_daily", key)
        lines = [f"📊 <b>Tagesbilanz {local:%d.%m.}</b>"]
        for a in config.ACCOUNTS:
            if a["id"] not in config.NOTIFY_ACCOUNTS:
                continue
            closed = [c for c in self.store.closed_positions(500, a["id"])
                      if c["closed_at"] and polymarket.parse_dt(c["closed_at"]).astimezone(BERLIN).strftime("%Y-%m-%d") == key]
            pnl = sum(c["pnl"] or 0 for c in closed)
            lines.append(f"{a['name']}: heute {pnl:+.2f} $ ({len(closed)} abgerechnet) · Konto {_usd(self.equity(a['id']))}")
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
        lines = [f"🗓 <b>Wochenbericht bis {local:%d.%m.}</b>"]
        rows = []
        for a in config.ACCOUNTS:
            closed = [c for c in self.store.closed_positions(5000, a["id"])
                      if c["closed_at"] and polymarket.parse_dt(c["closed_at"]) >= since]
            pnl = sum(c["pnl"] or 0 for c in closed)
            st = [c for c in closed if c["status"] in ("won", "lost") and c.get("kind") != "arb"]
            wr = (sum(1 for c in st if c["status"] == "won") / len(st)) if st else None
            eq = self.equity(a["id"])
            rows.append((eq, f"{a['name']}: {pnl:+.2f} $ diese Woche · {len(closed)} abgerechnet"
                             f"{f' · Treffer {wr * 100:.0f} %' if wr is not None else ''} · Konto {_usd(eq)}"
                             f"{' · ⛔ pausiert' if self.paused(a['id']) else ''}"))
        rows.sort(key=lambda r: -r[0])
        lines += [r[1] for r in rows]
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
        settled = [c for c in model_closed if c["status"] in ("won", "lost")]
        wins = [c for c in settled if c["status"] == "won"]
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
        rows = []
        for a in config.ACCOUNTS:
            s = self.stats(a["id"])
            s.pop("calibration", None)
            s.pop("calibration_buckets", None)
            rows.append({**{k: a[k] for k in ("id", "name", "desc")}, **s,
                         "paused": self.state["paused"].get(a["id"]), "orders": len(self.store.open_orders(a["id"]))})
        return rows

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
