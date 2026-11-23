"""Quellen-Verwaltung: Budget, Mindestabstand, Fehler-Pause und Statistik je Datenquelle.

Jeder HTTP-Abruf läuft über http.get_json / http.get_text und wird hier gezählt. Ist ein Budget
aufgebraucht oder eine Quelle nach Fehlern pausiert, wird gar nicht erst abgerufen (Blocked).
So kann keine kostenlose Quelle mehr „leergefragt“ werden wie Open-Meteo am 13.11.2026.

Gewicht: Open-Meteo zählt eine Ensemble-Abfrage (~120 Szenarien) wie ~12 normale Abrufe.
"""
import os
import re
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

SOURCES = {
    "polymarket_gamma": {"name": "Polymarket Gamma", "hosts": ["gamma-api.polymarket.com"], "use": "Märkte, Ereignisse, Ergebnisse",
                         "day_budget": 30000, "limit": "kein veröffentlichtes Limit – Budget als Schutz"},
    "polymarket_clob": {"name": "Polymarket CLOB", "hosts": ["clob.polymarket.com"], "use": "Orderbuch, Preisverlauf",
                        "day_budget": 30000, "limit": "kein veröffentlichtes Limit – Budget als Schutz"},
    "binance": {"name": "Binance", "hosts": ["api.binance.com", "data-api.binance.vision"], "use": "Krypto-Kurse",
                "day_budget": 6000, "limit": "frei"},
    "coinbase": {"name": "Coinbase", "hosts": ["api.exchange.coinbase.com"], "use": "Krypto-Kurse (Ersatz)", "day_budget": 3000, "limit": "frei"},
    "kraken": {"name": "Kraken", "hosts": ["api.kraken.com"], "use": "Krypto-Kurse (Ersatz)", "day_budget": 3000, "limit": "frei"},
    "deribit": {"name": "Deribit", "hosts": ["www.deribit.com"], "use": "Optionsdaten (implizite Volatilität)",
                "day_budget": 2000, "limit": "frei"},
    "odds_api": {"name": "The Odds API", "hosts": ["api.the-odds-api.com"], "use": "Buchmacher-Quoten",
                 "day_budget": 40, "limit": "500 Abrufe/Monat gratis"},
    "open_meteo": {"name": "Open-Meteo Ensemble", "hosts": ["ensemble-api.open-meteo.com"], "use": "Wetter-Vorhersagen",
                   "day_budget": 9000, "minute_budget": 480, "weight": 12, "pause_429": 3600,
                   "limit": "10.000 gewichtete Abrufe/Tag, 600/min"},
    "aviationweather": {"name": "METAR (aviationweather.gov)", "hosts": ["aviationweather.gov"], "use": "Gemessene Temperaturen am Messtag",
                        "day_budget": 1500, "minute_budget": 6, "limit": "keine feste Zahl; kleine, seltene Abfragen erbeten"},
    "clubelo": {"name": "ClubElo", "hosts": ["api.clubelo.com"], "use": "Stärke-Wertung europäischer Fußballklubs (Spielplan mit Wahrscheinlichkeiten)",
                "day_budget": 60, "limit": "frei, ohne Schlüssel"},
    "api_football": {"name": "API-Football", "hosts": ["v3.football.api-sports.io"], "use": "Verletzte und gesperrte Spieler (nur mit eigenem Schlüssel)",
                     "day_budget": 90, "limit": "Gratis-Plan 100 Abrufe/Tag"},
    "bbc_rss": {"name": "BBC RSS", "hosts": ["feeds.bbci.co.uk"], "use": "Schlagzeilen", "day_budget": 600, "limit": "öffentlicher Feed"},
    "telegram": {"name": "Telegram", "hosts": ["api.telegram.org"], "use": "Benachrichtigungen", "day_budget": 600, "limit": "frei"},
    "sonstige": {"name": "Sonstige", "hosts": [], "use": "nicht zugeordnete Abrufe", "day_budget": 2000, "limit": "–"},
}
_HOSTS = {h: sid for sid, s in SOURCES.items() for h in s["hosts"]}
_lock = threading.Lock()
_stats = {}       # (source, day) -> dict
_runtime = {}     # source -> {"last_call", "pause_until", "streak", "minute": [(ts, units)]}

_SECRET_PATTERNS = [
    (re.compile(r"(bot)\d{4,}:[A-Za-z0-9_-]{6,}"), r"\1<geheim>"),
    (re.compile(r"((?:api_?key|apikey|token|key|secret|password)=)[^&\s'\"]+", re.I), r"\1<geheim>"),
]


class Blocked(Exception):
    """Abruf wurde nicht gemacht (Budget leer oder Quelle pausiert)."""


def redact(text):
    """Entfernt Schlüssel/Token aus Fehlertexten, bevor sie gespeichert oder angezeigt werden."""
    s = str(text)
    for pat, rep in _SECRET_PATTERNS:
        s = pat.sub(rep, s)
    for env in ("API_FOOTBALL_KEY", "ODDS_API_KEY"):   # Schlüssel, die im Kopf einer Anfrage stehen
        v = os.environ.get(env, "").strip()
        if len(v) >= 8:
            s = s.replace(v, "<geheim>")
    return s


def today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def source_for(url):
    return _HOSTS.get((urlparse(url).hostname or "").lower(), "sonstige")


def _row(sid, day=None):
    key = (sid, day or today())
    if key not in _stats:
        _stats[key] = {"calls": 0, "errors": 0, "limited": 0, "blocked": 0, "units": 0, "ms": 0.0,
                       "last_ok": None, "last_error": None, "last_msg": ""}
    return _stats[key]


def _rt(sid):
    return _runtime.setdefault(sid, {"last_call": 0.0, "pause_until": 0.0, "streak": 0, "minute": []})


def before(sid, weight=None):
    """Vor jedem Abruf: wirft Blocked, wenn Budget leer oder Quelle pausiert ist."""
    cfg = SOURCES.get(sid, SOURCES["sonstige"])
    w = weight if weight is not None else cfg.get("weight", 1)
    now = time.time()
    with _lock:
        row, rt = _row(sid), _rt(sid)
        if now < rt["pause_until"]:
            row["blocked"] += 1
            raise Blocked(f"{cfg['name']}: pausiert noch {int(rt['pause_until'] - now)} s nach Fehlern")
        if row["units"] + w > cfg["day_budget"]:
            row["blocked"] += 1
            raise Blocked(f"{cfg['name']}: Tagesbudget aufgebraucht ({row['units']}/{cfg['day_budget']})")
        mb = cfg.get("minute_budget")
        if mb:
            rt["minute"] = [(t, u) for t, u in rt["minute"] if now - t < 60]
            if sum(u for _, u in rt["minute"]) + w > mb:
                row["blocked"] += 1
                raise Blocked(f"{cfg['name']}: Minuten-Limit, weiter im nächsten Lauf")
            rt["minute"].append((now, w))
        row["calls"] += 1
        row["units"] += w
        rt["last_call"] = now
    return w


def after(sid, ok, status=None, ms=0.0, msg=""):
    cfg = SOURCES.get(sid, SOURCES["sonstige"])
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _lock:
        row, rt = _row(sid), _rt(sid)
        row["ms"] += ms
        if ok:
            row["last_ok"] = stamp
            rt["streak"] = 0
            return
        row["errors"] += 1
        row["last_error"] = stamp
        row["last_msg"] = redact(msg)[:200]
        rt["streak"] += 1
        if status == 429:
            row["limited"] += 1
            rt["pause_until"] = time.time() + cfg.get("pause_429", 900)
        elif rt["streak"] >= 5:
            rt["pause_until"] = time.time() + 300


def streak(sid):
    return _rt(sid)["streak"]


def paused(sid):
    return max(0, int(_rt(sid)["pause_until"] - time.time()))


def snapshot():
    """Stand je Quelle für heute (für das Dashboard)."""
    out = []
    with _lock:
        for sid, cfg in SOURCES.items():
            row = dict(_row(sid))
            calls = row["calls"]
            out.append({"id": sid, "name": cfg["name"], "use": cfg["use"], "limit": cfg["limit"],
                        "day_budget": cfg["day_budget"], **row,
                        "error_rate": (row["errors"] / calls) if calls else None,
                        "avg_ms": (row["ms"] / calls) if calls else None,
                        "paused_s": max(0, int(_rt(sid)["pause_until"] - time.time())), "streak": _rt(sid)["streak"]})
    return out


def flush(store):
    """Zähler in die Datenbank schreiben (einmal pro Lauf)."""
    with _lock:
        rows = [(sid, day, r["calls"], r["errors"], r["limited"], r["blocked"], r["units"], round(r["ms"], 1),
                 r["last_ok"], r["last_error"], r["last_msg"]) for (sid, day), r in _stats.items()]
        for key in [k for k in _stats if k[1] != today()]:
            del _stats[key]
    store.xmany("INSERT INTO source_stats(source,day,calls,errors,limited,blocked,units,ms,last_ok,last_error,last_msg) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source,day) DO UPDATE SET calls=excluded.calls, errors=excluded.errors, "
                "limited=excluded.limited, blocked=excluded.blocked, units=excluded.units, ms=excluded.ms, last_ok=excluded.last_ok, "
                "last_error=excluded.last_error, last_msg=excluded.last_msg", rows)


def load(store):
    """Nach einem Neustart die heutigen Zähler wiederherstellen – sonst wäre jedes Budget nach jedem Deploy wieder voll."""
    with _lock:
        for r in store.q("SELECT * FROM source_stats WHERE day=?", (today(),)):
            _stats[(r["source"], r["day"])] = {"calls": r["calls"], "errors": r["errors"], "limited": r["limited"],
                                               "blocked": r["blocked"], "units": r["units"], "ms": r["ms"],
                                               "last_ok": r["last_ok"], "last_error": r["last_error"], "last_msg": r["last_msg"] or ""}


def reset():
    """Nur für Tests."""
    with _lock:
        _stats.clear()
        _runtime.clear()
