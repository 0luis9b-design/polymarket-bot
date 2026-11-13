"""Eigene Wahrscheinlichkeit für Temperatur-Märkte ("Highest temperature in NYC on November 14?").

Datenquelle: Open-Meteo-Ensemble (kostenlos, ohne Schlüssel). Drei Wettermodelle
(ECMWF, GFS, ICON) rechnen je 30–50 leicht unterschiedliche Szenarien – zusammen über 120.
Für jedes Szenario wird die Tages-Höchst- bzw. -Tiefsttemperatur am Flughafen bestimmt,
an dem Polymarket misst. Der Anteil der Szenarien, die in einen Temperaturbereich fallen,
ist die Wahrscheinlichkeit. Weil Wettermodelle die Unsicherheit eher unterschätzen und die
Station nicht exakt dem Modell-Gitterpunkt entspricht, wird jedes Szenario zusätzlich mit
einer Glockenkurve "verschmiert" (Breite wächst mit dem Vorlauf).
"""
import logging
import math
import re
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import config
from .http import HttpError, get_json

log = logging.getLogger("polybot.weather")
API = "https://ensemble-api.open-meteo.com/v1/ensemble"
MODELS = "ecmwf_ifs025,gfs025,icon_seamless"
# Open-Meteo zählt eine Ensemble-Abfrage (~120 Szenarien) wie ~12 normale Abfragen; gratis sind 10.000/Tag.
# Ensembles werden nur alle 6 h neu gerechnet -> 3 h Zwischenspeicher reicht und hält das Kontingent ein.
CACHE_SEC = 3 * 3600
FAIL_PAUSE_SEC = 30 * 60   # nach einem Fehler diese Station 30 min in Ruhe lassen (statt pro Markt neu zu versuchen)
status = {}
_cache = {}
_failed = {}

# Station (ICAO) -> (Breite, Länge, Zeitzone). Polymarket nennt die Station in der Beschreibung.
STATIONS = {
    "EHAM": (52.31, 4.76, "Europe/Amsterdam"), "LTAC": (40.128, 32.995, "Europe/Istanbul"),
    "KATL": (33.640, -84.427, "America/New_York"), "KAUS": (30.194, -97.670, "America/Chicago"),
    "ZBAA": (40.080, 116.585, "Asia/Shanghai"), "SAEZ": (-34.822, -58.536, "America/Argentina/Buenos_Aires"),
    "RKPK": (35.179, 128.938, "Asia/Seoul"), "FACT": (-33.965, 18.602, "Africa/Johannesburg"),
    "ZUUU": (30.578, 103.947, "Asia/Shanghai"), "KORD": (41.978, -87.905, "America/Chicago"),
    "ZUCK": (29.719, 106.642, "Asia/Shanghai"), "KDAL": (32.847, -96.852, "America/Chicago"),
    "KDEN": (39.856, -104.674, "America/Denver"), "ZGGG": (23.392, 113.299, "Asia/Shanghai"),
    "EFHK": (60.317, 24.963, "Europe/Helsinki"), "VHHH": (22.302, 114.174, "Asia/Hong_Kong"),
    "KHOU": (29.645, -95.279, "America/Chicago"), "LTFM": (41.262, 28.742, "Europe/Istanbul"),
    "OEJN": (21.680, 39.157, "Asia/Riyadh"), "OPKC": (24.907, 67.161, "Asia/Karachi"),
    "WMKK": (2.746, 101.710, "Asia/Kuala_Lumpur"), "EGLC": (51.505, 0.055, "Europe/London"),
    "KLAX": (33.942, -118.408, "America/Los_Angeles"), "VILK": (26.761, 80.889, "Asia/Kolkata"),
    "LEMD": (40.472, -3.561, "Europe/Madrid"), "RPLL": (14.509, 121.020, "Asia/Manila"),
    "MMMX": (19.436, -99.072, "America/Mexico_City"), "KMIA": (25.795, -80.290, "America/New_York"),
    "LIMC": (45.630, 8.723, "Europe/Rome"), "UUWW": (55.592, 37.261, "Europe/Moscow"),
    "EDDM": (48.354, 11.786, "Europe/Berlin"), "KLGA": (40.777, -73.874, "America/New_York"),
    "MPMG": (8.973, -79.556, "America/Panama"), "LFPB": (48.969, 2.441, "Europe/Paris"),
    "ZSQD": (36.361, 120.088, "Asia/Shanghai"), "KSFO": (37.619, -122.375, "America/Los_Angeles"),
    "SBGR": (-23.432, -46.469, "America/Sao_Paulo"), "KSEA": (47.449, -122.309, "America/Los_Angeles"),
    "RKSI": (37.469, 126.451, "Asia/Seoul"), "ZSPD": (31.143, 121.805, "Asia/Shanghai"),
    "ZGSZ": (22.639, 113.811, "Asia/Shanghai"), "WSSS": (1.359, 103.989, "Asia/Singapore"),
    "RCSS": (25.069, 121.552, "Asia/Taipei"), "LLBG": (32.011, 34.887, "Asia/Jerusalem"),
    "RJTT": (35.552, 139.780, "Asia/Tokyo"), "CYYZ": (43.677, -79.631, "America/Toronto"),
    "EPWA": (52.166, 20.967, "Europe/Warsaw"), "NZWN": (-41.327, 174.805, "Pacific/Auckland"),
    "ZHHH": (30.784, 114.208, "Asia/Shanghai"), "ZSJN": (36.857, 117.216, "Asia/Shanghai"),
    "ZHCC": (34.520, 113.841, "Asia/Shanghai"), "VIDP": (28.566, 77.103, "Asia/Kolkata"),
    "KBOS": (42.364, -71.005, "America/New_York"), "KPHX": (33.434, -112.012, "America/Phoenix"),
    "KDCA": (38.851, -77.040, "America/New_York"), "KPHL": (39.872, -75.241, "America/New_York"),
    "EDDB": (52.362, 13.501, "Europe/Berlin"), "LIRF": (41.800, 12.239, "Europe/Rome"),
    "LEBL": (41.297, 2.078, "Europe/Madrid"), "EIDW": (53.421, -6.270, "Europe/Dublin"),
    "YSSY": (-33.946, 151.177, "Australia/Sydney"), "OMDB": (25.253, 55.365, "Asia/Dubai"),
}
# Städte ohne ICAO in der Beschreibung (z. B. Hong Kong Observatory)
CITY_FALLBACK = {"hong kong": "VHHH"}

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], start=1)}


def _ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def parse_question(q):
    """'Will the highest temperature in NYC be between 42-43°F on November 13?' ->
    {'kind': 'high'|'low', 'lo': 41.5, 'hi': 43.5, 'unit': 'F', 'month': 11, 'day': 13, 'city': 'NYC'}"""
    t = q.replace("º", "°")
    m = re.search(r"(highest|lowest) temperature in (.+?) be (.+?) on (\w+) (\d{1,2})\??$", t, flags=re.I)
    if not m:
        return None
    kind = "high" if m.group(1).lower() == "highest" else "low"
    city, rng, mon, day = m.group(2), m.group(3), m.group(4).lower(), int(m.group(5))
    if mon not in MONTHS:
        return None
    unit_m = re.search(r"°\s*([CF])", rng)
    if not unit_m:
        return None
    unit = unit_m.group(1).upper()
    nums = [int(x) for x in re.findall(r"-?\d+", rng.replace("°", " "))]
    # "between 42-43°F": das Minus ist ein Bindestrich, keine negative Zahl
    b = re.search(r"(-?\d+)\s*-\s*(-?\d+)", rng)
    if b and "between" in rng.lower():
        lo, hi = int(b.group(1)), int(b.group(2))
        return {"kind": kind, "lo": lo - 0.5, "hi": hi + 0.5, "unit": unit, "month": MONTHS[mon], "day": day, "city": city}
    if not nums:
        return None
    n = nums[0]
    low_txt = rng.lower()
    if "or below" in low_txt or "or lower" in low_txt or "or less" in low_txt:
        return {"kind": kind, "lo": -math.inf, "hi": n + 0.5, "unit": unit, "month": MONTHS[mon], "day": day, "city": city}
    if "or higher" in low_txt or "or above" in low_txt or "or more" in low_txt:
        return {"kind": kind, "lo": n - 0.5, "hi": math.inf, "unit": unit, "month": MONTHS[mon], "day": day, "city": city}
    return {"kind": kind, "lo": n - 0.5, "hi": n + 0.5, "unit": unit, "month": MONTHS[mon], "day": day, "city": city}


def station_for(market):
    text = market.get("description") or ""
    m = re.search(r"\(([A-Z]{4})\)", text) or re.search(r"/([A-Z]{4})(?:[\s.)]|$)", text)
    if m and m.group(1) in STATIONS:
        return m.group(1)
    title = (market.get("event_title") or market.get("question") or "").lower()
    for city, icao in CITY_FALLBACK.items():
        if city in title:
            return icao
    return None


def ensemble(icao, unit, fetch=get_json):
    """{local_date: [max je Szenario]}, {local_date: [min je Szenario]}"""
    key = (icao, unit)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_SEC:
        return hit[1]
    if time.time() - _failed.get(key, 0) < FAIL_PAUSE_SEC:
        if hit:
            return hit[1]  # lieber etwas ältere Vorhersage als gar keine
        raise HttpError(f"Pause nach Fehler ({icao})")
    lat, lon, tz = STATIONS[icao]
    try:
        kw = {"retries": 0} if fetch is get_json else {}
        data = fetch(API, params={"latitude": lat, "longitude": lon, "hourly": "temperature_2m", "models": MODELS,
                                  "timezone": tz, "forecast_days": int(config.WEATHER_MAX_DAYS) + 2,
                                  "temperature_unit": "fahrenheit" if unit == "F" else "celsius"}, **kw)
    except HttpError as e:
        _failed[key] = time.time()
        status[icao] = f"Fehler: {str(e)[:100]}"
        if hit:
            return hit[1]
        raise
    hourly = data.get("hourly") or {}
    times = hourly.get("time") or []
    highs, lows = {}, {}
    for k, vals in hourly.items():
        if not k.startswith("temperature_2m") or not isinstance(vals, list):
            continue
        per_day = {}
        for t, v in zip(times, vals):
            if v is None:
                continue
            per_day.setdefault(t[:10], []).append(v)
        for d, vs in per_day.items():
            if len(vs) >= 20:  # nur fast vollständige Tage
                highs.setdefault(d, []).append(max(vs))
                lows.setdefault(d, []).append(min(vs))
    res = (highs, lows)
    _cache[key] = (time.time(), res)
    status[icao] = f"OK – {len(next(iter(highs.values()), []))} Szenarien"
    return res


def bucket_prob(values, lo, hi, spread):
    """Anteil der (verschmierten) Szenarien im Bereich [lo, hi)."""
    if not values:
        return None
    s = 0.0
    for x in values:
        a = 0.0 if lo == -math.inf else _ncdf((lo - x) / spread)
        b = 1.0 if hi == math.inf else _ncdf((hi - x) / spread)
        s += max(0.0, b - a)
    return s / len(values)


def analyze(market, now=None, fetch=get_json):
    now = now or datetime.now(timezone.utc)
    p = parse_question(market["question"])
    if not p:
        return {"skip": "Wetter-Fragetyp nicht unterstützt"}
    if market["outcomes"][0].lower() not in ("yes", "ja"):
        return {"skip": "kein Ja/Nein-Markt"}
    icao = station_for(market)
    if not icao:
        return {"skip": "Wetterstation unbekannt"}
    lat, lon, tz = STATIONS[icao]
    local_now = now.astimezone(ZoneInfo(tz))
    year = local_now.year + (1 if p["month"] < local_now.month - 6 else 0)
    try:
        day = datetime(year, p["month"], p["day"], tzinfo=ZoneInfo(tz))
    except ValueError:
        return {"skip": "Datum ungültig"}
    hours_to_start = (day - local_now).total_seconds() / 3600
    # Am Tag selbst kennt der Markt die bisher gemessenen Werte, das Modell nicht -> nur bis 6 Uhr Ortszeit
    if hours_to_start < -6:
        return {"skip": "Messtag läuft schon"}
    if hours_to_start > 24 * config.WEATHER_MAX_DAYS:
        return {"skip": "Wetter: zu weit in der Zukunft"}
    highs, lows = ensemble(icao, p["unit"], fetch=fetch)
    vals = (highs if p["kind"] == "high" else lows).get(day.strftime("%Y-%m-%d"))
    if not vals or len(vals) < 20:
        return {"skip": "keine Vorhersage für den Tag"}
    # Polymarket wertet die stündlichen METAR-Meldungen aus – das passt zu Stundenwerten, keine Korrektur nötig.
    # Verschmierung bewusst schmal: Ein Live-Vergleich (NYC, 12.11.) zeigte, dass der Markt deutlich schärfer liegt
    # als eine breite Verteilung – eine zu breite Kurve erzeugt falsche "Vorsprünge" bei den Rand-Bereichen.
    lead_days = max(0.0, hours_to_start / 24 + 0.5)
    base = config.WEATHER_SPREAD_C if p["unit"] == "C" else config.WEATHER_SPREAD_C * 1.8
    spread = base * (1 + 0.15 * lead_days)
    prob = bucket_prob(vals, p["lo"], p["hi"], spread)
    mean = sum(vals) / len(vals)
    u = "°" + p["unit"]
    rng = (f"≤ {p['hi'] - 0.5:.0f}{u}" if p["lo"] == -math.inf else f"≥ {p['lo'] + 0.5:.0f}{u}" if p["hi"] == math.inf
           else f"{p['lo'] + 0.5:.0f}{u}" if p["hi"] - p["lo"] == 1 else f"{p['lo'] + 0.5:.0f}–{p['hi'] - 0.5:.0f}{u}")
    info = (f"{'Höchst' if p['kind'] == 'high' else 'Tiefst'}wert {icao} am {day:%d.%m.} · Ziel {rng} · "
            f"Modelle Ø {mean:.1f}{u} (Spanne {min(vals):.0f}–{max(vals):.0f}) · {len(vals)} Szenarien")
    return {"p_yes": min(max(prob, 0.002), 0.998), "info": info, "model": "Wetter-Ensemble",
            "group": f"wetter:{icao}:{day:%Y-%m-%d}:{p['kind']}"}
