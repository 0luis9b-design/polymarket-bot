import json
import math
import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("AUTOSTART", "0")

from polybot import config, polymarket, weather_model as wm  # noqa: E402
from polybot.engine import Engine, best_weight  # noqa: E402
from polybot.sports_model import OddsClient  # noqa: E402
from polybot.store import Store  # noqa: E402

from tests.test_engine import CLOSES, _end, _market  # noqa: E402


# ---------------------------------------------------------------- Wetter
def test_weather_parse():
    p = wm.parse_question("Will the highest temperature in New York City be between 42-43°F on November 13?")
    assert (p["kind"], p["lo"], p["hi"], p["unit"], p["month"], p["day"]) == ("high", 41.5, 43.5, "F", 11, 13)
    p = wm.parse_question("Will the lowest temperature in London be 0°C or below on November 12?")
    assert p["kind"] == "low" and p["lo"] == -math.inf and p["hi"] == 0.5
    p = wm.parse_question("Will the highest temperature in Munich be 12°C or higher on November 14?")
    assert p["lo"] == 11.5 and p["hi"] == math.inf
    p = wm.parse_question("Will the lowest temperature in Moscow be -3°C on November 14?")
    assert p["lo"] == -3.5 and p["hi"] == -2.5
    assert wm.parse_question("Will Bitcoin hit $100k?") is None


def test_weather_station_and_buckets_sum_to_one():
    m = {"description": "...reported at London City Airport (EGLC) on 12 Nov...", "event_title": "x"}
    assert wm.station_for(m) == "EGLC"
    vals = [10.0, 11.0, 12.0, 12.5, 13.0]
    edges = [-math.inf] + [x + 0.5 for x in range(5, 18)] + [math.inf]
    total = sum(wm.bucket_prob(vals, a, b, 1.2) for a, b in zip(edges, edges[1:]))
    assert abs(total - 1) < 1e-9


def test_weather_analyze_with_fake_forecast():
    now = datetime(2026, 11, 12, 20, 0, tzinfo=timezone.utc)
    hours = [f"2026-11-{d:02d}T{h:02d}:00" for d in (12, 13, 14) for h in range(24)]
    hourly = {"time": hours}
    for i in range(30):  # 30 Szenarien, Höchstwert am 13. zwischen 11 und 13 °C
        peak = 11 + (i % 3)
        hourly[f"temperature_2m_member{i:02d}_x"] = [5 + (peak - 5) * math.sin(math.pi * (j % 24) / 24) for j in range(len(hours))]
    wm._cache.clear()
    fetch = lambda url, params=None, **kw: {"hourly": hourly}  # noqa: E731
    m = {"question": "Will the highest temperature in London be 12°C on November 13?", "outcomes": ["Yes", "No"],
         "description": "London City Airport (EGLC)", "event_title": "Highest temperature in London on November 13?"}
    a = wm.analyze(m, now=now, fetch=fetch)
    assert "p_yes" in a, a
    assert 0.2 < a["p_yes"] < 0.6
    assert a["group"].startswith("wetter:EGLC:2026-11-13")


# ---------------------------------------------------------------- Konten-Strategien
def _eng(tmp_path, events_by_tag, books=None):
    store = Store(str(tmp_path / "t.sqlite"))
    eng = Engine(store, fetch_events=lambda tag, **kw: events_by_tag.get(tag, []),
                 fetch_by_ids=lambda ids: {}, closes_fn=lambda a: (CLOSES, "Test"),
                 odds_client=OddsClient(store, api_key=""), chain_fn=lambda a, n: None,
                 book_fn=(lambda t: books[t]) if books else None)
    if not books:
        eng.book_fn = None
    return store, eng


def test_longshot_account_buys_no(tmp_path):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    end = _end(now)
    m = _market("ls", 130000, end, 0.03, 0.04)  # Ja steht bei ~3,5 %
    store, eng = _eng(tmp_path, {"crypto": [{"id": "e", "markets": [m]}]})
    eng.run_cycle()
    pos = store.open_positions("aussenseiter")
    assert len(pos) == 1 and pos[0]["side"] == 1
    assert abs(pos[0]["cost"] - config.START_BALANCE * config.LONGSHOT_STAKE_PCT) < 0.01


def test_maker_order_placed_then_filled(tmp_path):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    end = _end(now)
    ev = {"id": "e", "markets": [_market("mk", 100000, end, 0.20, 0.22)]}  # Bot ~49 %
    store, eng = _eng(tmp_path, {"crypto": [ev]})
    eng.run_cycle()
    orders = store.open_orders("limit")
    assert len(orders) == 1 and orders[0]["side"] == 0
    assert orders[0]["price"] < 0.22  # unter dem Briefkurs
    assert store.open_positions("limit") == []
    # Kurs fällt bis auf das Gebot -> gefüllt, ohne Gebühr
    lim = orders[0]["price"]
    ev["markets"] = [_market("mk", 100000, end, round(lim - 0.01, 3), lim)]
    eng.run_cycle()
    pos = store.open_positions("limit")
    assert len(pos) == 1 and pos[0]["fee"] == 0 and abs(pos[0]["price"] - lim) < 1e-9
    assert store.open_orders("limit") == []


def test_risk_brake_day_loss(tmp_path):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    end = _end(now)
    store, eng = _eng(tmp_path, {"crypto": [{"id": "e", "markets": [_market("r", 100000, end, 0.20, 0.22)]}]})
    eng.check_risk(now)
    assert not eng.paused("haupt")
    store.add_cash(-100, config.START_BALANCE, "haupt")  # −10 % heute
    eng.check_risk(now)
    assert eng.paused("haupt")
    eng.run_cycle()
    assert store.open_positions("haupt") == []
    eng.resume("haupt")
    eng.check_risk(now)
    assert not eng.paused("haupt")


def test_best_weight_prefers_market_when_bot_is_noise():
    import random
    random.seed(3)
    rows = []
    for _ in range(400):
        p = random.uniform(0.1, 0.9)
        outcome = 1.0 if random.random() < p else 0.0
        bot = min(0.99, max(0.01, p + random.uniform(-0.35, 0.35)))
        rows.append({"own_p": bot, "market_p": p, "outcome": outcome})
    assert best_weight(rows) <= 0.4
    rows2 = [{"own_p": r["market_p"], "market_p": 0.5, "outcome": r["outcome"]} for r in rows]
    assert best_weight(rows2) >= 0.6


def test_resume_endpoint(tmp_path, monkeypatch):
    import importlib
    monkeypatch.setenv("AUTOSTART", "0")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DASHBOARD_USER", "")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "")
    importlib.reload(config)
    import app as app_module
    importlib.reload(app_module)
    c = app_module.app.test_client()
    assert c.post("/api/account/haupt/resume").status_code == 200
    assert c.post("/api/account/gibtsnicht/resume").status_code == 404


def test_error_alert_after_three_failed_runs(tmp_path, monkeypatch):
    from polybot import notify
    sent = []
    monkeypatch.setattr(notify, "send", lambda text, account=None: sent.append(text))
    store, eng = _eng(tmp_path, {})

    def boom():
        raise RuntimeError("Datenquelle hängt")
    monkeypatch.setattr(eng, "settle", boom)
    for _ in range(3):
        eng.run_cycle()
    assert len([t for t in sent if "Läufe hintereinander" in t]) == 1
    monkeypatch.setattr(eng, "settle", lambda: None)
    eng.run_cycle()
    assert any("klappt wieder" in t for t in sent)
