"""Tests für 2.9: Befunde aus dem Claude-Review vom 22.11.2026 (Bericht #4).

1. Treffer zählen auch vorher verkaufte Wetten, sobald ihr Ausgang bekannt ist (E-Sport „0 von 83 gewonnen“).
2. Wirkung des Rückblicks messbar: vorher/nachher und gesperrte Märkte je Tag.
3. Wetter: Ist die Vorhersage zu breit?
"""
import json
import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("AUTOSTART", "0")

from polybot import paperview, reports, weather_check  # noqa: E402
from polybot.engine import Engine  # noqa: E402
from polybot.sports_model import OddsClient  # noqa: E402
from polybot.store import Store  # noqa: E402

NOW = datetime(2026, 11, 22, 16, 0, tzinfo=timezone.utc)


def _store(tmp_path):
    return Store(str(tmp_path / "v29.sqlite"))


def _pos(st, mid, status, side=0, pnl=0.0, lane="model", cat="esports", price=0.4, opened=NOW - timedelta(days=1), outcome=None):
    st.insert_position({"account": "haupt_esport", "kind": "model", "market_id": mid, "event_id": "e", "category": cat, "lane": lane,
                        "question": "Counter-Strike: A vs B (BO3)", "side": side, "side_name": "A" if side == 0 else "B",
                        "shares": 10, "price": price, "fee": 0, "cost": price * 10, "own_p": 0.55, "market_p": price, "edge": 0.1,
                        "status": status, "pnl": pnl, "opened_at": opened.isoformat(timespec="seconds"),
                        "closed_at": (opened + timedelta(hours=3)).isoformat(timespec="seconds") if status != "open" else None,
                        "end_date": (opened + timedelta(hours=4)).isoformat(timespec="seconds")})
    if outcome is not None:
        st.add_outcome(mid, outcome)


# ================================================================ 1. Treffer mit verkauften Gewinnern
def test_hit_counts_sold_bets_by_market_outcome():
    assert paperview.hit({"status": "won"}) == 1 and paperview.hit({"status": "lost"}) == 0
    assert paperview.hit({"status": "sold", "side": 0, "mkt_outcome": 1.0}) == 1     # Seite 0 gekauft, Seite 0 gewann
    assert paperview.hit({"status": "sold", "side": 1, "mkt_outcome": 1.0}) == 0
    assert paperview.hit({"status": "sold", "side": 1, "mkt_outcome": 0.0}) == 1
    assert paperview.hit({"status": "sold", "side": 0, "mkt_outcome": None}) is None  # Ausgang noch unbekannt
    assert paperview.hit({"status": "open", "side": 0, "mkt_outcome": 1.0}) is None


def test_breakdown_no_longer_hides_winners_sold_before_the_end(tmp_path):
    """Live bis 22.11.: Gewinner wurden kurz vor Schluss mit „Gewinn mitgenommen“ verkauft und fehlten in „Treffer“ –
    übrig blieben nur die Verlierer (E-Sport 0 von 83)."""
    st = _store(tmp_path)
    for i in range(4):
        _pos(st, f"w{i}", "sold", side=0, pnl=5.0, outcome=1.0)       # Gewinner, vorher verkauft
    for i in range(4):
        _pos(st, f"l{i}", "lost", side=0, pnl=-4.0, outcome=0.0)      # Verlierer, bis zum Ende gehalten
    _pos(st, "x", "sold", side=0, pnl=1.0)                            # Ausgang unbekannt: zählt nicht
    s = paperview.breakdown(st, {})["summe"]
    assert (s["n"], s["gewonnen"], s["verkauft"], s["verkauft_mit_ausgang"]) == (8, 4, 5, 4) and s["treffer"] == 0.5
    lines, _ = reports.bets_review(st, (NOW - timedelta(days=3)).isoformat())
    assert "| E-Sport | eigenes Modell | 8 | 55 % | 40 % | 50 % | -16 $ | 5 / +21 $ |" in "\n".join(lines)


def test_settle_fetches_outcome_of_sold_bets(tmp_path):
    st = _store(tmp_path)
    now = datetime.now(timezone.utc)
    _pos(st, "s1", "sold", side=0, pnl=3.0, opened=now - timedelta(days=1))
    asked = []

    def fetch(ids):
        asked.extend(ids)
        return {"s1": {"id": "s1", "closed": True, "outcomes": '["A", "B"]', "outcomePrices": '["1", "0"]', "umaResolutionStatus": "resolved"}}
    eng = Engine(st, fetch_events=lambda tag, **kw: [], fetch_by_ids=fetch, closes_fn=lambda a: ([1.0] * 721, "Test"),
                 odds_client=OddsClient(st, api_key=""), chain_fn=lambda a, n: None)
    eng.state["scans"] = 3
    eng.settle()
    assert asked == ["s1"] and st.q1("SELECT outcome FROM outcomes WHERE market_id='s1'")["outcome"] == 1.0
    assert st.q1("SELECT status, pnl FROM positions WHERE market_id='s1'") == {"status": "sold", "pnl": 3.0}   # Verkauf bleibt
    asked.clear()
    eng.settle()                                                           # Ausgang bekannt: nicht noch einmal fragen
    assert asked == []


# ================================================================ 2. Wirkung des Rückblicks
def _result(mid, status):
    return {"market": {"id": mid}, "status": {"haupt_wetter": status}}


def test_hindsight_blocks_counted_once_per_market_and_day(tmp_path):
    st = _store(tmp_path)
    eng = Engine(st, fetch_events=lambda tag, **kw: [], fetch_by_ids=lambda ids: {}, closes_fn=lambda a: ([1.0] * 721, "Test"),
                 odds_client=OddsClient(st, api_key=""), chain_fn=lambda a, n: None)
    rb = "Rückblick: „Nein“ mit 15–25 Pkt Abweichung hätte verloren"
    assert eng.count_hindsight_blocks([_result("a", rb), _result("b", rb), _result("c", "Vorsprung zu klein")], NOW) == 2
    assert eng.count_hindsight_blocks([_result("a", rb)], NOW + timedelta(minutes=10)) == 1   # derselbe Markt: zählt nicht doppelt
    assert st.kv_json("rueckblick:gesperrt") == {"2026-11-22": 2}
    eng.count_hindsight_blocks([_result("a", rb)], NOW + timedelta(days=1))
    assert st.kv_json("rueckblick:gesperrt") == {"2026-11-22": 2, "2026-11-23": 1}


def test_hindsight_effect_compares_before_and_after(tmp_path):
    st = _store(tmp_path)
    assert "Keine Daten" in reports.hindsight_effect(st, NOW)[0]
    seit = NOW - timedelta(days=2)
    st.kv_set("rueckblick:seit", seit.isoformat(timespec="seconds"))
    for i in range(4):                                     # vorher: 4 Wetten, 1 für uns ausgegangen
        _pos(st, f"v{i}", "lost" if i else "won", cat="weather", pnl=-5.0 if i else 6.0, opened=seit - timedelta(hours=10 + i))
    _pos(st, "n1", "sold", side=1, cat="weather", pnl=2.0, opened=seit + timedelta(hours=5), outcome=0.0)   # nachher: verkauft, gewonnen
    _pos(st, "n2", "open", cat="weather", opened=seit + timedelta(hours=6))
    _pos(st, "b1", "lost", cat="weather", lane="base", opened=seit + timedelta(hours=7))                    # Basisraten zählen nicht
    st.kv_set("rueckblick:gesperrt", json.dumps({"2026-11-21": 7}))
    md = "\n".join(reports.hindsight_effect(st, NOW))
    assert "| Wetter | 2.0 | 1/4 · Preis 40 % | -9 $ | 1.0 | 1/1 · Preis 40 % | +2 $ |" in md
    assert "abgehaltene Märkte je Tag: 21.11. 7" in md


# ================================================================ 3. Wetter: zu breit?
def _wx(st, i, lo, hi, ens_mean, ens_sd, p, market_p, outcome, lead_h=20.0, champion=0, model="weather.ens", version="2"):
    mid = f"wx{i}-{lo}-{model}-{champion}"
    f = {"icao": "EDDM", "date": "2026-11-20", "kind": "high", "unit": "C", "ens_mean": ens_mean, "ens_sd": ens_sd,
         "lead_h": lead_h, "lo": lo, "hi": hi, "n": 120}
    st.x("INSERT INTO forecasts(market_id, category, model, version, horizon, created_at, p, market_p, hours_left, champion, valid, "
         "cluster, features) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
         (mid, "weather", model, version, "24-72h", NOW.isoformat(), p, market_p, lead_h + 24, champion, 1, f"wetter:EDDM:{i}:high",
          json.dumps(f)))
    st.add_outcome(mid, outcome)


def test_weather_spread_detects_too_wide_forecast(tmp_path):
    st = _store(tmp_path)
    for i in range(120):        # die Temperatur trifft fast immer das Ensemble-Mittel, das Modell rechnet aber mit ±2 Grad
        _wx(st, i, 11.5, 12.5, 12.0 + (0.3 if i % 2 else -0.3), 2.0, 0.2, 0.5, 1.0)
    rows = weather_check.spread(st)
    assert rows[0]["vorlauf"] == "etwa 1 Tag" and rows[0]["n"] == 120
    assert rows[0]["innen50"] == 1.0 and rows[0]["streuung"] < 0.3
    assert "zu breit" in weather_check.verdict(rows)
    assert weather_check.verdict(rows[:0]) is None


def test_weather_spread_fits_when_errors_match_model_width(tmp_path):
    st = _store(tmp_path)
    sd = weather_check.model_sd({"unit": "C", "lead_h": 20.0, "ens_sd": 1.0})
    sd = (sd ** 2 + 1 / 12) ** 0.5
    for i in range(200):        # Abweichungen wie aus einer Normalverteilung mit genau der Modellbreite
        z = [-1.6, -0.9, -0.4, -0.1, 0.1, 0.4, 0.9, 1.6][i % 8] * 1.07
        _wx(st, i, 11.5 + z * sd, 12.5 + z * sd, 12.0, 1.0, 0.2, 0.5, 1.0)
    v = weather_check.verdict(weather_check.spread(st))
    assert "passt" in v, v


def test_weather_favourite_bin(tmp_path):
    st = _store(tmp_path)
    for i in range(30):         # Favorit (Markt 60 %) trifft 2 von 3 Tagen ein, das Modell gab ihm nur 30 %
        hit = i % 3 != 0
        _wx(st, i, 11.5, 12.5, 12.0, 1.0, 0.30, 0.60, 1.0 if hit else 0.0, champion=1, model="weather.live", version="1")
        _wx(st, i, 12.5, 13.5, 12.0, 1.0, 0.40, 0.25, 0.0 if hit else 1.0, champion=1, model="weather.live", version="1")
        _wx(st, i, 13.5, 14.5, 12.0, 1.0, 0.30, 0.15, 0.0, champion=1, model="weather.live", version="1")
    rows = weather_check.favourite(st)
    assert len(rows) == 1
    r = rows[0]
    assert (r["variante"], r["vorlauf"], r["n"]) == ("gehandelt", "etwa 1 Tag", 30)
    assert abs(r["modell"] - 0.30) < 1e-9 and abs(r["markt"] - 0.60) < 1e-9 and abs(r["eingetroffen"] - 2 / 3) < 1e-9
