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
# Einsatz: 2–6 % des Kontos, je nach Sicherheit der Wette
#   Sicherheit = 60 % Vorsprung (Schwelle → +10 Punkte darüber = voll) + 40 % Trefferchance (35 % → 85 % = voll)
STAKE_MODE = os.environ.get("STAKE_MODE", "sicherheit")  # "sicherheit" oder "kelly"
STAKE_MIN_PCT = _f("STAKE_MIN_PCT", 0.02)
STAKE_MAX_PCT = _f("STAKE_MAX_PCT", 0.06)
EDGE_FULL_EXTRA = _f("EDGE_FULL_EXTRA", 0.10)
KELLY_FRACTION = _f("KELLY_FRACTION", 0.25)         # nur bei STAKE_MODE=kelly
MAX_STAKE_PCT = _f("MAX_STAKE_PCT", 0.06)           # max. 6 % des Kontos pro Wette
MAX_EVENT_PCT = _f("MAX_EVENT_PCT", 0.10)           # max. 10 % pro Ereignis (z. B. alle BTC-Stufen eines Tages)
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
# Polymarket-Ligen (Tag), getrennt durch Komma
SPORTS_LEAGUES = [x.strip().lower() for x in os.environ.get(
    "SPORTS_LEAGUES",
    # NBA/NHL entfernt (Polymarket dort fast gleich wie Buchmacher) – dafür kleinere Fußball-Ligen
    "bundesliga,bundesliga-2,epl,la-liga,serie-a,ligue-1,ucl,uel,"
    "efl-championship,la-liga-2,ligue-2,serie-b,scottish-premiership,soccer-el1,nfl,ufc").split(",") if x.strip()]
ODDS_REFRESH_MIN = _f("ODDS_REFRESH_MIN", 360)      # Quoten pro Liga höchstens alle 6 h holen (Gratis-Kontingent)
ODDS_CREDIT_RESERVE = _i("ODDS_CREDIT_RESERVE", 15)
ODDS_DAILY_BUDGET = _i("ODDS_DAILY_BUDGET", 0)      # 0 = automatisch: Restguthaben / Resttage im Monat
SPORTS_LOOKAHEAD_H = _f("SPORTS_LOOKAHEAD_H", 24)   # nur Spiele in den nächsten 24 h (Quoten nah am Spiel sind genauer)
SPORTS_MIN_BOOKS = _i("SPORTS_MIN_BOOKS", 3)

# --- Gebühren (Polymarket: Gebühr = Anteile × Satz × p × (1 − p)) ---
FEE_RATE_CRYPTO = _f("FEE_RATE_CRYPTO", 0.07)
FEE_RATE_SPORTS = _f("FEE_RATE_SPORTS", 0.05)
FEE_RATE_WEATHER = _f("FEE_RATE_WEATHER", 0.05)

# --- Wetter ---
WEATHER_ENABLED = os.environ.get("WEATHER_ENABLED", "1") == "1"
MIN_EDGE_WEATHER = _f("MIN_EDGE_WEATHER", 0.10)  # Wetter-Modell ist neu -> vorsichtigere Schwelle
WEATHER_MAX_DAYS = _f("WEATHER_MAX_DAYS", 4)
WEATHER_SPREAD_C = _f("WEATHER_SPREAD_C", 0.6)  # Grund-Unschärfe je Szenario in °C (°F: × 1,8)

# --- Risikobremse (pro Konto) ---
RISK_DAY_LOSS_PCT = _f("RISK_DAY_LOSS_PCT", 0.08)     # −8 % an einem Tag -> Pause bis morgen
RISK_TOTAL_LOSS_PCT = _f("RISK_TOTAL_LOSS_PCT", 0.25) # −25 % gesamt -> Pause bis „Fortsetzen“ im Dashboard

# --- Limit-Orders (Maker, nur Konto „Limit-Orders“) ---
MAKER_EDGE = _f("MAKER_EDGE", 0.05)          # Gebot so legen, dass mind. 5 Pkt Vorsprung bleiben
MAKER_MAX_HOURS = _f("MAKER_MAX_HOURS", 6)    # Gebot nach 6 h zurückziehen

# --- Selbstkorrektur ---
CALIB_MIN_N = _i("CALIB_MIN_N", 200)          # ab so vielen entschiedenen Märkten pro Bereich anwenden

# --- Außenseiter-Konto ---
LONGSHOT_MIN = _f("LONGSHOT_MIN", 0.02)
LONGSHOT_MAX = _f("LONGSHOT_MAX", 0.05)
LONGSHOT_MAX_DAYS = _f("LONGSHOT_MAX_DAYS", 14)
LONGSHOT_STAKE_PCT = _f("LONGSHOT_STAKE_PCT", 0.02)

# --- Ausführung, Ausstieg, Arbitrage ---
USE_ORDERBOOK = os.environ.get("USE_ORDERBOOK", "1") == "1"   # echten Durchschnittskurs aus dem Orderbuch rechnen
EXIT_BUFFER = _f("EXIT_BUFFER", 0.01)        # verkaufen, sobald Verkaufserlös ≥ eigene W. − 1 Punkt
MIN_HOLD_MIN = _f("MIN_HOLD_MIN", 30)        # frühestens 30 min nach Kauf verkaufen
MAX_GROUP_PCT = _f("MAX_GROUP_PCT", 0.10)    # max. 10 % für alles, was am selben Coin+Stichtag bzw. Spiel hängt
ARB_MIN_PROFIT = _f("ARB_MIN_PROFIT", 0.01)  # Arbitrage erst ab 1 Cent sicherem Gewinn pro Paket
ARB_MAX_PCT = _f("ARB_MAX_PCT", 0.10)        # max. 10 % des Kontos pro Arbitrage-Paket

# --- Testkonten: alle sehen dieselben Märkte, entscheiden aber mit eigenen Regeln ---
ACCOUNTS = [
    # Hauptkonto, aufgeteilt nach Bereich (13.11.): jedes mit eigenen 1.000 $, damit sich die Bereiche nicht vermischen
    {"id": "haupt_krypto", "name": "Haupt · Krypto", "cats": ["crypto"],
     "desc": "nur Krypto · Schwelle 6 Pkt · Einsatz 2–6 % nach Sicherheit · früher Ausstieg · Arbitrage",
     "edge_c": MIN_EDGE_CRYPTO, "edge_s": MIN_EDGE_SPORTS, "edge_w": MIN_EDGE_WEATHER, "stake": "sicherheit", "exit": True, "arb": True, "model": True},
    {"id": "haupt_sport", "name": "Haupt · Sport", "cats": ["sports"],
     "desc": "nur Sport · Schwelle 3 Pkt · Einsatz 2–6 % nach Sicherheit · früher Ausstieg · Arbitrage",
     "edge_c": MIN_EDGE_CRYPTO, "edge_s": MIN_EDGE_SPORTS, "edge_w": MIN_EDGE_WEATHER, "stake": "sicherheit", "exit": True, "arb": True, "model": True},
    {"id": "haupt_wetter", "name": "Haupt · Wetter", "cats": ["weather"],
     "desc": "nur Wetter · Schwelle 10 Pkt · Einsatz 2–6 % nach Sicherheit · früher Ausstieg · Arbitrage",
     "edge_c": MIN_EDGE_CRYPTO, "edge_s": MIN_EDGE_SPORTS, "edge_w": MIN_EDGE_WEATHER, "stake": "sicherheit", "exit": True, "arb": True, "model": True},
    {"id": "schwelle4", "name": "Schwelle 4/2/7", "desc": "alle Bereiche, Regeln wie Haupt, aber schon ab 4 Pkt (Krypto) / 2 Pkt (Sport) / 7 Pkt (Wetter)",
     "edge_c": 0.04, "edge_s": 0.02, "edge_w": 0.07, "stake": "sicherheit", "exit": True, "arb": False, "model": True},
    {"id": "schwelle8", "name": "Schwelle 8/4/14", "desc": "wie Hauptkonto, aber erst ab 8 Pkt (Krypto) / 4 Pkt (Sport) / 14 Pkt (Wetter)",
     "edge_c": 0.08, "edge_s": 0.04, "edge_w": 0.14, "stake": "sicherheit", "exit": True, "arb": False, "model": True},
    {"id": "fest10", "name": "Fest 10 $", "desc": "wie Hauptkonto, aber immer 10 $ Einsatz",
     "edge_c": MIN_EDGE_CRYPTO, "edge_s": MIN_EDGE_SPORTS, "edge_w": MIN_EDGE_WEATHER, "stake": "fest", "fixed": 10.0, "exit": True, "arb": False, "model": True},
    {"id": "halten", "name": "Ohne Ausstieg", "desc": "wie Hauptkonto, hält aber jede Wette bis zur Entscheidung",
     "edge_c": MIN_EDGE_CRYPTO, "edge_s": MIN_EDGE_SPORTS, "edge_w": MIN_EDGE_WEATHER, "stake": "sicherheit", "exit": False, "arb": False, "model": True},
    {"id": "kelly", "name": "Viertel-Kelly", "desc": "wie Hauptkonto, Einsatz aber nach Viertel-Kelly (meist 1–3 %)",
     "edge_c": MIN_EDGE_CRYPTO, "edge_s": MIN_EDGE_SPORTS, "edge_w": MIN_EDGE_WEATHER, "stake": "kelly", "exit": True, "arb": False, "model": True},
    {"id": "limit", "name": "Limit-Orders", "desc": "stellt Kaufgebote unter den fairen Wert (keine Gebühr) statt sofort zu kaufen – Füllung nur angenähert simuliert",
     "edge_c": MAKER_EDGE, "edge_s": MAKER_EDGE, "edge_w": MAKER_EDGE, "stake": "sicherheit", "exit": True, "arb": False, "model": False, "maker": True},
    {"id": "aussenseiter", "name": "Außenseiter verkaufen", "desc": "ohne Modell: kauft „Nein“, wenn ein Markt nur 2–5 % für „Ja“ gibt (fester Einsatz 2 %)",
     "edge_c": 1.0, "edge_s": 1.0, "edge_w": 1.0, "stake": "longshot", "exit": False, "arb": False, "model": False, "longshot": True},
    {"id": "arbitrage", "name": "Nur Arbitrage", "desc": "kauft nur Widersprüche in Polymarket (sicherer Gewinn), kein Modell",
     "edge_c": 1.0, "edge_s": 1.0, "edge_w": 1.0, "stake": "sicherheit", "exit": False, "arb": True, "model": False},
]

# --- Benachrichtigungen (Telegram) ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
NOTIFY_ACCOUNTS = [x.strip() for x in os.environ.get("NOTIFY_ACCOUNTS", "haupt_krypto,haupt_sport,haupt_wetter").split(",")
                   if x.strip()]
DAILY_SUMMARY_HOUR = _i("DAILY_SUMMARY_HOUR", 22)   # Tageszusammenfassung um 22 Uhr (deutsche Zeit)
WEEKLY_REPORT_HOUR = _i("WEEKLY_REPORT_HOUR", 18)   # Wochenbericht sonntags um 18 Uhr (vor der Besprechung um 19 Uhr)

# --- Version 2: Agenten, Validierung, Katalog, Lernen (im Dashboard einstellbar: siehe settings.py) ---
MAX_DIVERGENCE = _f("MAX_DIVERGENCE", 0.25)          # größere Abweichung Modell/Markt gilt als Modellfehler -> kein Handel
TRADE_CRYPTO = os.environ.get("TRADE_CRYPTO", "1") == "1"
TRADE_SPORTS = os.environ.get("TRADE_SPORTS", "1") == "1"
TRADE_WEATHER = os.environ.get("TRADE_WEATHER", "1") == "1"
SCANNER_ENABLED = os.environ.get("SCANNER_ENABLED", "1") == "1"
CATALOG_PER_CATEGORY = _i("CATALOG_PER_CATEGORY", 100)
NEWS_ENABLED = os.environ.get("NEWS_ENABLED", "1") == "1"
NEWS_FEEDS = [  # (Name, Adresse) – alle am 14.11.2026 als Standard-RSS geprüft
    ("BBC Welt", "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("BBC USA", "https://feeds.bbci.co.uk/news/world/us_and_canada/rss.xml"),
    ("BBC Politik", "https://feeds.bbci.co.uk/news/politics/rss.xml"),
    ("BBC Wirtschaft", "https://feeds.bbci.co.uk/news/business/rss.xml"),
    ("BBC Technik", "https://feeds.bbci.co.uk/news/technology/rss.xml"),
    ("BBC Sport", "https://feeds.bbci.co.uk/sport/rss.xml"),
]
BACKTEST_ENABLED = os.environ.get("BACKTEST_ENABLED", "1") == "1"
HISTORY_PER_RUN = _i("HISTORY_PER_RUN", 25)           # entschiedene Märkte pro Lauf nachladen (je 1 Abruf Preisverlauf)
HISTORY_MIN_VOLUME = _f("HISTORY_MIN_VOLUME", 2000)
MOVE_ALERT_PTS = _f("MOVE_ALERT_PTS", 0.10)
NOTIFY_MIN_LEVEL = os.environ.get("NOTIFY_MIN_LEVEL", "normal")
NOTIFY_DAILY_CAP = _i("NOTIFY_DAILY_CAP", 40)
QUIET_FROM = _i("QUIET_FROM", 23)
QUIET_TO = _i("QUIET_TO", 7)
DASH_REFRESH_SEC = _i("DASH_REFRESH_SEC", 60)
EXPERIMENT_MIN_OOS = _i("EXPERIMENT_MIN_OOS", 100)    # Mindestzahl späterer Prognosen für einen Freigabe-Vorschlag
EXPERIMENT_MIN_CLUSTERS = _i("EXPERIMENT_MIN_CLUSTERS", 30)
EXPERIMENT_MIN_GAIN = _f("EXPERIMENT_MIN_GAIN", 0.02) # Brier mindestens 2 % besser
EXPERIMENT_MIN_Z = _f("EXPERIMENT_MIN_Z", 1.64)
REPORT_DAYS = [int(x) for x in os.environ.get("REPORT_DAYS", "2,6").split(",") if x.strip().isdigit()]  # 0=Mo … 6=So
REPORT_HOUR = _i("REPORT_HOUR", 17)
BACKUP_KEEP = _i("BACKUP_KEEP", 7)
CYCLE_BUDGET_SEC = _f("CYCLE_BUDGET_SEC", 240)        # so lange darf ein ganzer Takt höchstens dauern
SELF_RESTART_MIN = _f("SELF_RESTART_MIN", 45)         # hängt ein Takt länger, beendet sich der Prozess (Railway startet neu)
RETAIN_SNAPSHOT_DAYS = _i("RETAIN_SNAPSHOT_DAYS", 14)
RETAIN_EVENT_DAYS = _i("RETAIN_EVENT_DAYS", 90)
RETAIN_RUN_DAYS = _i("RETAIN_RUN_DAYS", 14)

# --- Ablauf ---
SCAN_INTERVAL_MIN = _f("SCAN_INTERVAL_MIN", 10)
AUTOSTART = os.environ.get("AUTOSTART", "1") == "1"
WATCHDOG_MIN = _f("WATCHDOG_MIN", 30)          # Telegram-Warnung, wenn so lange kein Lauf fertig wurde
ERROR_ALERT_RUNS = _i("ERROR_ALERT_RUNS", 3)    # Telegram-Warnung nach so vielen Fehl-Läufen hintereinander
HTTP_TIMEOUT = _f("HTTP_TIMEOUT", 15)
