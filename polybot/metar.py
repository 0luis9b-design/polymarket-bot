"""Gemessene Temperaturen (METAR) der Flughafen-Stationen – für Wetter-Märkte am Messtag.

Quelle: aviationweather.gov Data API (US-Wetterdienst, kostenlos, ohne Schlüssel). Laut Betreiber sollen Abfragen
klein und selten sein – deshalb: höchstens 5 Stationen je Abruf, 30 Minuten Zwischenspeicher, Budget in sources.py.
Polymarket entscheidet die Temperatur-Märkte nach den Werten derselben Stationen (Weather Underground zeigt die METAR-Meldungen).
"""
import logging
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .http import HttpError, get_json

log = logging.getLogger(__name__)
API = "https://aviationweather.gov/api/data/metar"
CACHE_SEC = 30 * 60
BATCH = 5
_cache = {}      # icao -> (abgerufen, [(zeit, temp_c)])
_wanted = {}     # icao -> zuletzt angefragt (für gebündelte Abrufe)
_failed = {}


def _fetch(icaos, fetch):
    data = fetch(API, params={"ids": ",".join(icaos), "format": "json", "hours": 26})
    out = {i: [] for i in icaos}
    for m in data if isinstance(data, list) else []:
        i, t, temp = m.get("icaoId"), m.get("obsTime"), m.get("temp")
        if i in out and isinstance(t, (int, float)) and isinstance(temp, (int, float)):
            out[i].append((int(t), float(temp)))
    return {i: sorted(set(v)) for i, v in out.items()}


def observations(icao, fetch=get_json, now=None):
    """Meldungen der letzten 26 Stunden als Liste (Unix-Zeit, °C), oder None, wenn nicht abrufbar."""
    now = now or time.time()
    _wanted[icao] = now
    hit = _cache.get(icao)
    if hit and now - hit[0] < CACHE_SEC:
        return hit[1]
    if now - _failed.get(icao, 0) < 15 * 60:
        return hit[1] if hit else None
    # gebündelt: diese Station plus weitere zuletzt gefragte, deren Stand veraltet ist
    stale = [i for i, t in sorted(_wanted.items(), key=lambda x: -x[1])
             if i != icao and now - t < 3 * 3600 and (i not in _cache or now - _cache[i][0] >= CACHE_SEC)]
    batch = [icao] + stale[:BATCH - 1]
    try:
        res = _fetch(batch, fetch)
    except HttpError as e:
        if not getattr(e, "blocked", False):
            for i in batch:
                _failed[i] = now
        log.info("METAR nicht abrufbar (%s): %s", ",".join(batch), e)
        return hit[1] if hit else None
    for i, obs in res.items():
        _cache[i] = (now, obs)
        _failed.pop(i, None)
    return res.get(icao)


def day_extremes(obs, tz, date, unit="C"):
    """Höchst- und Tiefstwert der Meldungen eines Ortstages (bis jetzt) in der Einheit des Marktes."""
    zone = ZoneInfo(tz)
    vals = []
    for t, c in obs or []:
        d = datetime.fromtimestamp(t, timezone.utc).astimezone(zone)
        if d.strftime("%Y-%m-%d") == date:
            vals.append((t, c * 9 / 5 + 32 if unit == "F" else c))
    if not vals:
        return None
    temps = [v for _, v in vals]
    return {"max": max(temps), "min": min(temps), "n": len(vals), "last_ts": vals[-1][0], "last": vals[-1][1],
            "series": vals}


def reset():
    _cache.clear()
    _wanted.clear()
    _failed.clear()
