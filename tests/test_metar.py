"""Wetter am Messtag: gemessene Werte (METAR) + Vorhersage für die restlichen Stunden."""
import math
import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("AUTOSTART", "0")

from polybot import metar, weather_model as wm  # noqa: E402


def _obs(icao, start, temps, step_min=30):
    return [{"icaoId": icao, "obsTime": int((start + timedelta(minutes=i * step_min)).timestamp()), "temp": t, "metarType": "METAR"}
            for i, t in enumerate(temps)]


def test_metar_batches_stations_and_caches(monkeypatch):
    metar.reset()
    calls = []
    t0 = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)

    def fetch(url, params=None, **kw):
        calls.append(params["ids"])
        return [o for i in params["ids"].split(",") for o in _obs(i, t0, [10.0, 11.5])]
    now = t0.timestamp()
    assert metar.observations("EGLC", fetch=fetch, now=now) == [(int(t0.timestamp()), 10.0), (int(t0.timestamp()) + 1800, 11.5)]
    metar.observations("KLGA", fetch=fetch, now=now + 60)
    metar.observations("EGLC", fetch=fetch, now=now + 120)                      # noch frisch: kein Abruf
    assert calls == ["EGLC", "KLGA"]
    metar.observations("EGLC", fetch=fetch, now=now + metar.CACHE_SEC + 100)     # veraltet: beide gebündelt
    assert calls[-1] == "EGLC,KLGA"


def test_metar_failure_pauses_and_day_extremes_use_local_day():
    metar.reset()
    from polybot.http import HttpError

    def broken(url, params=None, **kw):
        raise HttpError("HTTP 503", 503)
    assert metar.observations("EGLC", fetch=broken, now=1000.0) is None
    assert metar.observations("EGLC", fetch=lambda *a, **k: [], now=1100.0) is None    # 15 min Pause nach Fehler
    # New York: 03:30 UTC am 16. ist noch der 15. Ortszeit
    obs = [(int(datetime(2026, 11, 15, 18, tzinfo=timezone.utc).timestamp()), 10.0),
           (int(datetime(2026, 11, 16, 3, 30, tzinfo=timezone.utc).timestamp()), 5.0),
           (int(datetime(2026, 11, 16, 6, tzinfo=timezone.utc).timestamp()), 2.0)]
    e = metar.day_extremes(obs, "America/New_York", "2026-11-15", "F")
    assert e["n"] == 2 and abs(e["max"] - 50.0) < 1e-9 and abs(e["min"] - 41.0) < 1e-9


def test_final_probability_respects_what_was_already_measured():
    inf = math.inf
    assert wm._final_prob("high", 13.0, 10.0, 0.6, 11.5, 12.5) == 0.0              # schon 13 gemessen: 12 ist vorbei
    assert wm._final_prob("high", 12.0, 9.0, 0.6, 11.5, 12.5) > 0.99               # 12 gemessen, Rest viel kälter
    assert abs(wm._final_prob("high", 10.0, 12.0, 0.6, 11.5, 12.5) - (wm._ncdf(0.5 / 0.6) - wm._ncdf(-0.5 / 0.6))) < 1e-12
    assert wm._final_prob("high", 14.0, None, 0.6, 13.5, inf) == 1.0               # Tag vorbei
    assert wm._final_prob("low", 2.0, 5.0, 0.6, 2.5, 3.5) == 0.0                   # Tiefstwert kann nur fallen
    assert wm._final_prob("low", 3.0, 6.0, 0.6, 2.5, 3.5) > 0.99


def _london_day(day, members=30):
    """Ensemble mit Höchstwert 11 °C um 12 Uhr an `day` und dem Folgetag."""
    hours = [f"{d}T{h:02d}:00" for d in (day, (datetime.fromisoformat(day) + timedelta(days=1)).strftime("%Y-%m-%d")) for h in range(24)]
    hourly = {"time": hours}
    for i in range(members):
        hourly[f"temperature_2m_member{i:02d}"] = [5 + 6 * math.sin(math.pi * (j % 24) / 24) + (i % 3 - 1) * 0.2 for j in range(len(hours))]
    return {"hourly": hourly}


def test_measurement_day_uses_observations_and_corrects_models():
    wm._cache.clear()
    wm._failed.clear()
    metar.reset()
    now = datetime(2026, 11, 15, 16, 0, tzinfo=timezone.utc)                     # London = UTC im November
    # gemessen: 2 °C wärmer als die Modelle, Höchstwert 13 °C um 12 Uhr
    temps = [round(5 + 6 * math.sin(math.pi * (h / 2) / 24) + 2) for h in range(32)]   # 00:00 … 15:30
    obs = _obs("EGLC", datetime(2026, 11, 15, 0, tzinfo=timezone.utc), temps)
    fetch_ens = lambda url, params=None, **kw: _london_day("2026-11-15")   # noqa: E731
    fetch_metar = lambda url, params=None, **kw: obs                       # noqa: E731
    m = lambda b: {"question": f"Will the highest temperature in London be {b}°C on November 15?", "outcomes": ["Yes", "No"],  # noqa: E731
                   "description": "London City Airport (EGLC)", "event_title": "Highest temperature in London on November 15?"}
    a = wm.analyze(m(13), now=now, fetch=fetch_ens, metar_fetch=fetch_metar)
    assert a["model_id"] == "weather.live@1" and a["p_yes"] == 0.98, a["info"]
    assert a["features"]["p_ohne_messwerte"] < 0.05                                # ohne Messwerte läge das Modell daneben
    assert "weather.ens@2" not in a["variants"]                                   # das aktive Modell darf die Messwerte nicht überstimmen
    assert 1.5 < a["features"]["bias"] < 2.5 and a["features"]["obs"] == 13
    assert "bisher Höchstwert 13.0°C gemessen" in a["info"] and a["hours"] < 9
    assert wm.analyze(m(12), now=now, fetch=fetch_ens, metar_fetch=fetch_metar)["p_yes"] == 0.005
    # alle Bereiche eines Tages zusammen: etwa 100 % (sonst sperrt die Prüfung „Summe“)
    total = sum(wm.analyze(dict(m(b), question=m(b)["question"].replace(f"be {b}°C", f"be {b}°C")), now=now, fetch=fetch_ens,
                           metar_fetch=fetch_metar)["p_yes"] for b in range(6, 17))
    assert 0.85 <= total <= 1.15
    # ohne Messwerte am späten Messtag: wie bisher überspringen
    metar.reset()
    assert wm.analyze(m(13), now=now, fetch=fetch_ens, metar_fetch=lambda *a, **k: [])["skip"].startswith("Messtag läuft schon")
    # Tests ohne METAR-Abruf holen nie echte Daten
    assert wm.analyze(m(13), now=now, fetch=fetch_ens)["skip"].startswith("Messtag läuft schon")
    assert wm.analyze(m(13), now=now + timedelta(days=1), fetch=fetch_ens)["skip"] == "Messtag vorbei"
