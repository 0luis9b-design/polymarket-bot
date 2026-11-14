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
    pos = store.open_positions("aussenseiter_krypto")
    assert len(pos) == 1 and pos[0]["side"] == 1
    assert abs(pos[0]["cost"] - config.START_BALANCE * config.LONGSHOT_STAKE_PCT) < 0.01


def test_maker_order_placed_then_filled(tmp_path):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    end = _end(now)
    ev = {"id": "e", "markets": [_market("mk", 100000, end, 0.20, 0.22)]}  # Bot ~49 %
    store, eng = _eng(tmp_path, {"crypto": [ev]})
    eng.run_cycle()
    orders = store.open_orders("limit_krypto")
    assert len(orders) == 1 and orders[0]["side"] == 0
    assert orders[0]["price"] < 0.22  # unter dem Briefkurs
    assert store.open_positions("limit_krypto") == []
    # Kurs fällt bis auf das Gebot -> gefüllt, ohne Gebühr
    lim = orders[0]["price"]
    ev["markets"] = [_market("mk", 100000, end, round(lim - 0.01, 3), lim)]
    eng.run_cycle()
    pos = store.open_positions("limit_krypto")
    assert len(pos) == 1 and pos[0]["fee"] == 0 and abs(pos[0]["price"] - lim) < 1e-9
    assert store.open_orders("limit_krypto") == []


def test_risk_brake_day_loss(tmp_path):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    end = _end(now)
    store, eng = _eng(tmp_path, {"crypto": [{"id": "e", "markets": [_market("r", 100000, end, 0.20, 0.22)]}]})
    eng.check_risk(now)
    assert not eng.paused("haupt_krypto")
    store.add_cash(-100, config.START_BALANCE, "haupt_krypto")  # −10 % heute
    eng.check_risk(now)
    assert eng.paused("haupt_krypto")
    eng.run_cycle()
    assert store.open_positions("haupt_krypto") == []
    eng.resume("haupt_krypto")
    eng.check_risk(now)
    assert not eng.paused("haupt_krypto")


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
    hdr = {"X-Requested-With": "polybot"}
    assert c.post("/api/account/haupt_krypto/resume", headers=hdr).status_code == 200
    assert c.post("/api/account/gibtsnicht/resume", headers=hdr).status_code == 404
    assert c.post("/api/account/haupt_krypto/resume").status_code == 403   # ohne den Kopf des Dashboards: abgewiesen


def test_error_alert_after_three_failed_runs(tmp_path, monkeypatch):
    from polybot import notify
    sent = []
    monkeypatch.setattr(notify, "send", lambda text, account=None, **kw: sent.append(text))
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


# ---------------------------------------------------------------- Prüfung 12.11. abends
def test_settlement_fetch_asks_only_closed_and_no_single_calls(monkeypatch):
    calls = []

    def fake(url, params=None, **kw):
        calls.append((url, params))
        return [{"id": "1", "closed": True}]
    monkeypatch.setattr(polymarket, "get_json", fake)
    res = polymarket.fetch_markets_by_ids(["1", "2", "3"])
    assert list(res) == ["1"]
    assert len(calls) == 1 and ("closed", "true") in calls[0][1]


def test_crypto_fetch_uses_time_window(tmp_path):
    seen = {}

    def fe(tag, **kw):
        seen[tag] = kw.get("extra")
        return []
    store = Store(str(tmp_path / "t.sqlite"))
    eng = Engine(store, fetch_events=fe, fetch_by_ids=lambda ids: {}, closes_fn=lambda a: (CLOSES, "T"),
                 odds_client=OddsClient(store, api_key=""), chain_fn=lambda a, n: None, book_fn=None)
    eng.collect_markets(datetime.now(timezone.utc))
    assert "end_date_max" in seen["crypto"] and "end_date_min" in seen["crypto"]


def test_maker_cancels_order_when_market_no_longer_rated(tmp_path):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    end = _end(now)
    ev = {"id": "e", "markets": [_market("mk", 100000, end, 0.20, 0.22)]}
    store, eng = _eng(tmp_path, {"crypto": [ev]})
    eng.run_cycle()
    assert len(store.open_orders("limit_krypto")) == 1
    # Markt läuft gleich ab -> wird nicht mehr bewertet -> Gebot weg
    # (Frage nennt den Vortag: sonst hinge der Test von der Uhrzeit ab, zu der er läuft)
    past = now - timedelta(days=1)
    q = f"Will the price of Bitcoin be above $100,000 on {past:%B} {past.day}?"
    ev["markets"] = [_market("mk", 100000, now + timedelta(minutes=30), 0.20, 0.22, q=q)]
    eng.run_cycle()
    assert store.open_orders("limit_krypto") == []


def test_resume_after_total_loss_does_not_retrigger(tmp_path):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    store, eng = _eng(tmp_path, {})
    store.add_cash(-300, config.START_BALANCE, "kelly_sport")  # −30 %
    eng.check_risk(now)
    assert eng.paused("kelly_sport")
    eng.resume("kelly_sport")
    eng.check_risk(now)
    assert not eng.paused("kelly_sport")


def test_longshot_skips_wide_spread_and_caps_price(tmp_path):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    end = _end(now)
    wide = _market("w", 130000, end, 0.001, 0.08)  # Mitte ~4 %, aber riesige Spanne
    store, eng = _eng(tmp_path, {"crypto": [{"id": "e", "markets": [wide]}]})
    eng.run_cycle()
    assert store.open_positions("aussenseiter_krypto") == []
    asks = [(0.97, 5.0), (0.999, 1000.0)]
    sh, cost, fee, avg = polymarket.fill_buy(asks, 100, 0.0, max_price=0.98)
    assert sh == 5.0 and avg == 0.97


def test_weather_failure_is_not_retried_per_market():
    calls = {"n": 0}

    def failing(url, params=None, **kw):
        calls["n"] += 1
        raise wm.HttpError("HTTP 429 Too Many Requests", 429)
    wm._cache.clear()
    wm._failed.clear()
    m = {"question": "Will the highest temperature in London be 12°C on November 20?", "outcomes": ["Yes", "No"],
         "description": "London City Airport (EGLC)", "event_title": "x"}
    now = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
    for _ in range(50):  # 50 Märkte derselben Station
        try:
            wm.analyze(m, now=now, fetch=failing)
        except wm.HttpError:
            pass
    assert calls["n"] == 1


def _london_forecast():
    hours = [f"2026-11-{d:02d}T{h:02d}:00" for d in (19, 20, 21) for h in range(24)]
    hourly = {"time": hours}
    for i in range(30):
        hourly[f"temperature_2m_member{i:02d}"] = [5 + (11 + i % 3 - 5) * math.sin(math.pi * (j % 24) / 24) for j in range(len(hours))]
    return {"hourly": hourly}


def test_weather_budget_block_is_remembered_for_the_run_and_not_a_station_error():
    calls = {"n": 0}

    def blocked(url, params=None, **kw):
        calls["n"] += 1
        raise wm.HttpError("Minutenbudget erreicht", blocked=True)
    wm._cache.clear()
    wm._failed.clear()
    m = {"question": "Will the highest temperature in London be 12°C on November 20?", "outcomes": ["Yes", "No"],
         "description": "London City Airport (EGLC)", "event_title": "x"}
    now = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
    for _ in range(40):  # 40 Märkte derselben Station im selben Lauf
        try:
            wm.analyze(m, now=now, fetch=blocked)
        except wm.HttpError as e:
            assert e.blocked
    assert calls["n"] == 1 and ("EGLC", "C") not in wm._failed          # ein Versuch, und die Station gilt nicht als gestört


def test_weather_cache_survives_restart(tmp_path):
    from polybot.store import Store
    st = Store(str(tmp_path / "w.sqlite"))
    wm._cache.clear()
    wm._failed.clear()
    old = wm._store
    try:
        assert wm.attach(st) == 0
        m = {"question": "Will the highest temperature in London be 12°C on November 20?", "outcomes": ["Yes", "No"],
             "description": "London City Airport (EGLC)", "event_title": "x"}
        now = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
        first = wm.analyze(m, now=now, fetch=lambda url, params=None, **kw: _london_forecast())
        assert st.kv_get("wx:EGLC:C")
        wm._cache.clear()                                                  # „Neustart“: Arbeitsspeicher leer
        assert wm.attach(st) == 1

        def must_not_fetch(url, params=None, **kw):
            raise AssertionError("nach dem Neustart darf nicht neu abgerufen werden")
        again = wm.analyze(m, now=now, fetch=must_not_fetch)
        assert abs(again["p_yes"] - first["p_yes"]) < 1e-9
        st.kv_set("wx:EGLC:C", "kaputt")                                   # ein beschädigter Eintrag stört nicht
        wm._cache.clear()
        assert wm.attach(st) == 0
    finally:
        wm._store = old
        wm._cache.clear()


def test_known_pause_is_not_reported_again_after_restart(tmp_path):
    """Live-Befund 14.11.: Nach jedem Neustart standen sechs alte Risikobremsen erneut als Fehler im Protokoll."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    store, eng = _eng(tmp_path, {})
    store.add_cash(-300, config.START_BALANCE, "haupt_wetter")           # −30 % seit Start
    eng.check_risk(now)
    assert eng.paused("haupt_wetter")
    errors = lambda: [l for l in store.recent_log(100) if l["level"] == "error" and "RISIKOBREMSE" in l["msg"]]  # noqa: E731
    assert len(errors()) == 1
    eng2 = Engine(store, fetch_events=lambda tag, **kw: [], fetch_by_ids=lambda ids: {}, closes_fn=lambda a: (CLOSES, "Test"),
                  odds_client=OddsClient(store, api_key=""), chain_fn=lambda a, n: None, book_fn=None)
    assert eng2.paused("haupt_wetter")                                    # Pause gilt sofort wieder, nicht erst nach dem ersten Lauf
    eng2.check_risk(now)
    assert len(errors()) == 1                                             # keine zweite Meldung
    eng2.resume("haupt_wetter")
    eng3 = Engine(store, fetch_events=lambda tag, **kw: [], fetch_by_ids=lambda ids: {}, closes_fn=lambda a: (CLOSES, "Test"),
                  odds_client=OddsClient(store, api_key=""), chain_fn=lambda a, n: None, book_fn=None)
    assert not eng3.paused("haupt_wetter")
