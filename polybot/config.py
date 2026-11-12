"""Alle Einstellungen an einem Ort. Jede Zahl kann auf Railway per Variable überschrieben werden."""
import os


def _f(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


def _i(name, default):
    try:
        return int(float(os.environ.get(name, default)))
    except ValueError:
        return int(default)


DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data"))

# --- Spielgeld-Konto ---
START_BALANCE = _f("START_BALANCE", 1000)          # $ Spielgeld
KELLY_FRACTION = _f("KELLY_FRACTION", 0.25)         # Viertel-Kelly
MAX_STAKE_PCT = _f("MAX_STAKE_PCT", 0.04)           # max. 4 % des Kontos pro Wette
MAX_EVENT_PCT = _f("MAX_EVENT_PCT", 0.08)           # max. 8 % pro Ereignis (z. B. alle BTC-Stufen eines Tages)
MAX_EXPOSURE_PCT = _f("MAX_EXPOSURE_PCT", 0.60)     # max. 60 % des Kontos gleichzeitig im Markt
MIN_STAKE = _f("MIN_STAKE", 2.0)                    # kleinere Einsätze lohnen nicht

# --- Filter ---
MIN_EDGE_CRYPTO = _f("MIN_EDGE_CRYPTO", 0.06)       # 6 Prozentpunkte Vorsprung nach Gebühren
MIN_EDGE_SPORTS = _f("MIN_EDGE_SPORTS", 0.03)       # 3 Prozentpunkte Vorsprung nach Gebühren
MIN_PRICE = _f("MIN_PRICE", 0.04)                   # extreme Kurse meiden, dort ist jedes Modell unsicher
MAX_PRICE = _f("MAX_PRICE", 0.96)
MAX_SPREAD = _f("MAX_SPREAD", 0.06)
MIN_LIQUIDITY = _f("MIN_LIQUIDITY", 500)            # $ im Orderbuch

# --- Krypto ---
CRYPTO_MIN_HOURS = _f("CRYPTO_MIN_HOURS", 2)        # kurz vor Ablauf nicht mehr einsteigen
CRYPTO_MAX_DAYS = _f("CRYPTO_MAX_DAYS", 10)
VOL_MULTIPLIER = _f("VOL_MULTIPLIER", 1.0)          # >1 = vorsichtigeres Modell

# --- Sport ---
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "").strip()
ODDS_REGIONS = os.environ.get("ODDS_REGIONS", "eu")
ODDS_REFRESH_MIN = _f("ODDS_REFRESH_MIN", 360)      # Quoten pro Liga höchstens alle 6 h holen (Gratis-Kontingent)
ODDS_CREDIT_RESERVE = _i("ODDS_CREDIT_RESERVE", 15)
ODDS_DAILY_BUDGET = _i("ODDS_DAILY_BUDGET", 0)      # 0 = automatisch: Restguthaben / Resttage im Monat
SPORTS_LOOKAHEAD_H = _f("SPORTS_LOOKAHEAD_H", 24)   # nur Spiele in den nächsten 24 h (Quoten nah am Spiel sind genauer)
SPORTS_MIN_BOOKS = _i("SPORTS_MIN_BOOKS", 3)

# --- Gebühren (Polymarket: Gebühr = Anteile × Satz × p × (1 − p)) ---
FEE_RATE_CRYPTO = _f("FEE_RATE_CRYPTO", 0.07)
FEE_RATE_SPORTS = _f("FEE_RATE_SPORTS", 0.05)

# --- Ablauf ---
SCAN_INTERVAL_MIN = _f("SCAN_INTERVAL_MIN", 10)
AUTOSTART = os.environ.get("AUTOSTART", "1") == "1"
HTTP_TIMEOUT = _f("HTTP_TIMEOUT", 15)
