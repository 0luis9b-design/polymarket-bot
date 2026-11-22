"""Einstellungen, die im Dashboard geändert werden dürfen.

Nur Schlüssel aus SPEC, nur innerhalb fester Grenzen. Gespeichert in der Datenbank (kv „set:…“),
beim Start und nach jeder Änderung auf das config-Modul angewendet. Jede Änderung wird als Ereignis protokolliert.
Geheimnisse (Token, Passwörter) sind hier bewusst nicht einstellbar – die bleiben Umgebungsvariablen.
"""
from . import config

LEVELS = ["info", "normal", "hoch", "kritisch"]

# Schlüssel -> (Typ, Minimum, Maximum, Gruppe, Beschreibung)
SPEC = {
    "SCAN_INTERVAL_MIN": ("float", 5, 60, "Analyse", "Minuten zwischen zwei Läufen"),
    "MAX_DIVERGENCE": ("float", 0.10, 0.60, "Analyse", "Größte glaubwürdige Abweichung Modell/Markt (0,25 = 25 Punkte); darüber wird nicht gehandelt"),
    "TRADE_CRYPTO": ("bool", None, None, "Kategorien", "Paper-Trading Krypto"),
    "TRADE_SPORTS": ("bool", None, None, "Kategorien", "Paper-Trading Sport"),
    "TRADE_WEATHER": ("bool", None, None, "Kategorien", "Paper-Trading Wetter"),
    "SCANNER_ENABLED": ("bool", None, None, "Ressourcen", "Marktkatalog aller Kategorien (stündlich)"),
    "CATALOG_PER_CATEGORY": ("int", 50, 300, "Ressourcen", "Märkte je Kategorie im Katalog (die aktivsten)"),
    "NEWS_ENABLED": ("bool", None, None, "Ressourcen", "Schlagzeilen sammeln und zuordnen"),
    "BACKTEST_ENABLED": ("bool", None, None, "Ressourcen", "Datensatz entschiedener Märkte aufbauen und Strategien testen"),
    "HISTORY_PER_RUN": ("int", 0, 120, "Ressourcen", "Entschiedene Märkte pro Lauf nachladen (Datensatz für Backtests und Basisraten)"),
    "BASE_GUARD_Z": ("float", 0.5, 3.0, "Analyse", "Basisraten-Wächter: Thema sperren ab diesem z-Wert gegen den Markt (1,0 = wahrscheinlich schlechter, 1,64 = gesichert schlechter)"),
    "MIN_WIN_P": ("float", 0.0, 0.9, "Analyse", "Mindest-Gewinnchance der gewählten Seite (0,5 = nur Wetten, die eher gewinnen als verlieren)"),
    "RISK_PAUSE": ("bool", None, None, "Analyse", "Risikobremse pausiert Konten bei −8 % am Tag / −25 % seit Start (aus = nur Warnung)"),
    "HINDSIGHT": ("bool", None, None, "Analyse", "Rückblick: keine Modell-Wetten in Bereichen, in denen solche Wetten nachweislich Geld verloren hätten"),
    "HINDSIGHT_Z": ("float", 1.0, 3.0, "Analyse", "Rückblick sperrt ab diesem z-Wert (1,64 = Verlust gesichert)"),
    "TRADE_BASE": ("bool", None, None, "Kategorien", "Paper-Trading aller übrigen Märkte mit Basisraten (in den Konten des jeweiligen Themas)"),
    "MOVE_ALERT_PTS": ("float", 0.05, 0.50, "Benachrichtigungen", "Kursbewegung in einer Stunde, ab der ein Markt als auffällig gilt"),
    "NOTIFY_MIN_LEVEL": ("choice", LEVELS, None, "Benachrichtigungen", "Telegram ab dieser Stufe"),
    "NOTIFY_DAILY_CAP": ("int", 0, 200, "Benachrichtigungen", "Höchstzahl normaler Telegram-Meldungen pro Tag"),
    "QUIET_FROM": ("int", 0, 23, "Benachrichtigungen", "Ruhezeit ab (Stunde, deutsche Zeit) – nur Kritisches kommt durch"),
    "QUIET_TO": ("int", 0, 23, "Benachrichtigungen", "Ruhezeit bis (Stunde)"),
    "DASH_REFRESH_SEC": ("int", 20, 600, "Dashboard", "Automatisches Aktualisieren (Sekunden)"),
}


def _coerce(key, value):
    typ, lo, hi, _g, _d = SPEC[key]
    if typ == "bool":
        if isinstance(value, bool):
            return value
        if str(value).lower() in ("1", "true", "ja", "an", "on"):
            return True
        if str(value).lower() in ("0", "false", "nein", "aus", "off"):
            return False
        raise ValueError(f"{key}: an/aus erwartet")
    if typ == "choice":
        if value not in lo:
            raise ValueError(f"{key}: erlaubt sind {', '.join(lo)}")
        return value
    try:
        v = float(str(value).replace(",", "."))
    except ValueError:
        raise ValueError(f"{key}: Zahl erwartet") from None
    if v != v or not (lo <= v <= hi):
        raise ValueError(f"{key}: erlaubt ist {lo} bis {hi}")
    return int(round(v)) if typ == "int" else v


def load(store):
    """Gespeicherte Einstellungen auf config anwenden (beim Start)."""
    for key in SPEC:
        raw = store.kv_get(f"set:{key}")
        if raw is None or raw == "":
            continue
        try:
            setattr(config, key, _coerce(key, raw))
        except ValueError:
            pass


def current():
    out = []
    for key, (typ, lo, hi, group, desc) in SPEC.items():
        out.append({"key": key, "type": typ, "min": lo if typ != "choice" else None, "max": hi,
                    "choices": lo if typ == "choice" else None, "group": group, "desc": desc,
                    "value": getattr(config, key, None)})
    return out


def update(store, changes):
    """Prüft und übernimmt Änderungen. Gibt [(key, alt, neu)] zurück; wirft ValueError bei ungültigen Werten."""
    if not isinstance(changes, dict):
        raise ValueError("Ungültige Eingabe")
    cleaned = {}
    for key, value in changes.items():
        if key not in SPEC:
            raise ValueError(f"Unbekannte Einstellung: {str(key)[:40]}")
        cleaned[key] = _coerce(key, value)
    done = []
    for key, value in cleaned.items():
        old = getattr(config, key, None)
        if old == value:
            continue
        setattr(config, key, value)
        store.kv_set(f"set:{key}", value)
        done.append((key, old, value))
    return done
