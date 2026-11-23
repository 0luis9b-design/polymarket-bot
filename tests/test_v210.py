"""Tests für 2.10: Testvarianten, die nur mitlaufen (Wetter schmaler, Krypto mehr Schwankung) und Sport-Quoten-Kontingent."""
import math
import os
from datetime import datetime, timezone

os.environ.setdefault("AUTOSTART", "0")

from polybot import metar, weather_model as wm  # noqa: E402
from tests.test_metar import _london_day, _obs  # noqa: E402


def _ens(day_peak_spread=3):
    hours = [f"2026-11-{d:02d}T{h:02d}:00" for d in (12, 13, 14) for h in range(24)]
    hourly = {"time": hours}
    for i in range(30):                    # Höchstwert am 13. zwischen 11 und 13 °C
        peak = 11 + (i % day_peak_spread)
        hourly[f"temperature_2m_member{i:02d}_x"] = [5 + (peak - 5) * math.sin(math.pi * (j % 24) / 24) for j in range(len(hours))]
    return {"hourly": hourly}


def _m(b, day=13):
    return {"question": f"Will the highest temperature in London be {b}°C on November {day}?", "outcomes": ["Yes", "No"],
            "description": "London City Airport (EGLC)", "event_title": f"Highest temperature in London on November {day}?"}


# ================================================================ Wetter: schmalere Testvarianten
def test_narrow_variants_on_forecast_days_concentrate_on_the_middle():
    wm._cache.clear()
    now = datetime(2026, 11, 12, 20, 0, tzinfo=timezone.utc)
    fetch = lambda url, params=None, **kw: _ens()           # noqa: E731
    mid = wm.analyze(_m(12), now=now, fetch=fetch)
    edge = wm.analyze(_m(14), now=now, fetch=fetch)
    v = mid["variants"]
    assert set(v) >= {"weather.ens@2", "weather.wide@1", "weather.narrow@85", "weather.narrow@70"}
    assert v["weather.narrow@70"] > v["weather.narrow@85"] > v["weather.ens@2"]            # Mitte bekommt mehr
    e = edge["variants"]
    assert e["weather.narrow@70"] < e["weather.narrow@85"] < e["weather.ens@2"]            # Rand bekommt weniger
    assert mid["model_id"] == "weather.ens@2" and mid["p_yes"] == v["weather.ens@2"]        # gehandelt wird weiter wie bisher
    total = sum(wm.analyze(_m(b), now=now, fetch=fetch)["variants"]["weather.narrow@70"] for b in range(4, 22))
    assert 0.95 <= total <= 1.05                                                           # Bereiche zusammen ≈ 100 %


def test_narrow_variants_on_measurement_day():
    wm._cache.clear()
    wm._failed.clear()
    metar.reset()
    now = datetime(2026, 11, 15, 10, 0, tzinfo=timezone.utc)
    temps = [round(5 + 6 * math.sin(math.pi * (h / 2) / 24)) for h in range(20)]          # 00:00 … 09:30, wie die Modelle
    obs = _obs("EGLC", datetime(2026, 11, 15, 0, tzinfo=timezone.utc), temps)
    a = wm.analyze(_m(11, 15), now=now, fetch=lambda url, params=None, **kw: _london_day("2026-11-15"),
                   metar_fetch=lambda url, params=None, **kw: obs)
    v = a["variants"]
    assert a["model_id"] == "weather.live@1" and {"weather.narrow@85", "weather.narrow@70"} <= set(v)
    assert v["weather.narrow@70"] >= v["weather.live@1"] - 1e-9 and 0 < v["weather.narrow@70"] <= 0.98


# ================================================================ Sport: Gratis-Kontingent klüger einteilen
def _lakers(now_start):
    from polybot.polymarket import normalize_market
    raw = {"id": "9", "question": "Lakers vs. Celtics", "outcomes": '["Lakers", "Celtics"]', "outcomePrices": '["0.33", "0.67"]',
           "bestBid": 0.32, "bestAsk": 0.34, "liquidityNum": 20000, "endDate": "2026-11-13T04:00:00Z",
           "gameStartTime": now_start, "sportsMarketType": "moneyline"}
    return normalize_market(raw, {"id": "e9", "title": "Lakers vs. Celtics", "slug": "nba-lal-bos-2026-11-12", "tags": [{"slug": "nba"}]},
                            "sports")


def test_no_new_odds_far_before_kickoff_but_cached_ones_are_used():
    import time as _t

    from polybot import sports_model as sm
    from tests.test_models import FakeStore, _odds_event
    calls = []

    def fetch(url, params=None, want_headers=False):
        calls.append(url)
        return [_odds_event("2026-11-13T00:30:00Z")], {"x-requests-remaining": "480"}
    client = sm.OddsClient(FakeStore(), api_key="x", fetch=fetch)
    m = _lakers("2026-11-13 00:30:00+00")
    far = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)                 # 12,5 h vor Anpfiff
    a = sm.analyze(m, client, now=far)
    assert a["skip"].startswith("Quoten werden erst 6 h vor Anpfiff geholt") and calls == []
    near = datetime(2026, 11, 12, 20, 0, tzinfo=timezone.utc)                # 4,5 h vor Anpfiff: jetzt abrufen
    assert "p_yes" in sm.analyze(m, client, now=near) and len(calls) == 1
    assert "p_yes" in sm.analyze(m, client, now=far) and len(calls) == 1      # frische gespeicherte Quoten gelten auch früher
    client.cache["basketball_nba"] = (_t.time() - 7 * 3600, client.cache["basketball_nba"][1])
    assert "skip" in sm.analyze(m, client, now=far) and len(calls) == 1      # veraltet und weit weg: kein Abruf


def test_odds_pacing_keeps_credits_for_the_evening(monkeypatch):
    from polybot import config
    from polybot import sports_model as sm
    from tests.test_models import FakeStore
    monkeypatch.setattr(config, "ODDS_DAILY_BUDGET", 30)
    client = sm.OddsClient(FakeStore(), api_key="x", fetch=lambda *a, **k: ([], {}))
    at = lambda h: datetime(2026, 11, 23, h, 0, tzinfo=timezone.utc)        # noqa: E731
    assert [client.paced_budget(at(h)) for h in (0, 6, 12, 18, 23)] == [13, 18, 22, 27, 30]
    client.store.kv_set(client._day_key(), 30)
    assert client.events("soccer_epl") == [] and "aufgebraucht" in client.status
    client.store.kv_set(client._day_key(), client.paced_budget())
    if client.paced_budget() < 30:
        assert client.events("soccer_epl") == [] and "spart Abrufe" in client.status
        assert client.events("soccer_epl", max_age=1800) == [] and "OK" in client.status    # Schlusskurse dürfen immer


# ================================================================ Krypto: mehr Schwankung als Testvariante
def _flat_chain(now, iv=50.0):
    from polybot import options
    rows = [{"instrument_name": f"BTC-{exp}-{k}-{kind}", "mark_iv": iv, "underlying_price": 100000.0}
            for exp in ("13NOV26", "14NOV26", "16NOV26") for k in range(90000, 110001, 2000) for kind in ("C", "P")]
    return options.Chain(rows, "BTC-", now)


def test_wider_crypto_variants_run_alongside_without_changing_the_trade():
    from polybot import crypto_model as cm
    now = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
    ch = _flat_chain(now)
    closes = [100000.0 * (1 + 0.004 * ((i % 2) * 2 - 1)) for i in range(720)] + [100000.0]
    end = datetime(2026, 11, 14, 17, 0, tzinfo=timezone.utc)                    # 14.11. 12 Uhr New York
    m = {"question": "Will the price of Bitcoin be between $98,000 and $102,000 on November 14?", "outcomes": ["Yes", "No"],
         "end_date": end}
    a = cm.analyze(m, now=now, closes_fn=lambda asset: (closes, "Test"), chain_fn=lambda asset, n: ch)
    v = a["variants"]
    assert a["model_id"] == "crypto.options@1" and a["p_yes"] == v["crypto.options@1"]          # gehandelt wird wie bisher
    assert v["crypto.options@1"] > v["crypto.options.wide@115"] > v["crypto.options.wide@130"]  # Bereich um den Kurs: weniger sicher
    far = cm.analyze(dict(m, question="Will the price of Bitcoin be above $110,000 on November 14?"), now=now,
                     closes_fn=lambda asset: (closes, "Test"), chain_fn=lambda asset, n: ch)["variants"]
    assert far["crypto.options@1"] < far["crypto.options.wide@115"] < far["crypto.options.wide@130"]  # ferner Kurs: wahrscheinlicher
    # die Optionsoberfläche selbst ist unverändert
    assert abs(ch.prob_above(100000, 102000, 48) - ch.prob_above(100000, 102000, 48, 1.0)) < 1e-12
    hist = cm.analyze(m, now=now, closes_fn=lambda asset: (closes, "Test"), chain_fn=lambda asset, n: None)["variants"]
    assert not any(k.startswith("crypto.options.wide") for k in hist)                         # ohne Optionen keine Testvariante
