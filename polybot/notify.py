"""Benachrichtigungen per Telegram – mit Prioritäten, damit Wichtiges nicht untergeht.

Stufen:  info < normal < hoch < kritisch
  kritisch  immer sofort (System steht, Datenbank beschädigt)
  hoch      sofort, aber dasselbe Thema höchstens alle 6 h (Quelle fällt aus, Risikobremse)
  normal    sofort bis zum Tageslimit, nicht in der Ruhezeit (Käufe, Vorschläge, Berichte)
  info      nur wenn die Mindeststufe auf „info“ gestellt ist

Einrichten: in Telegram @BotFather -> /newbot -> Token als TELEGRAM_BOT_TOKEN in Railway eintragen,
dem neuen Bot einmal „Hallo“ schreiben, Chat-ID (zeigt das Dashboard) als TELEGRAM_CHAT_ID eintragen.
Token und Chat-ID stehen nur in Umgebungsvariablen und werden nie geloggt.
"""
import logging
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from . import config
from .http import HttpError, SESSION, post_json
from .sources import redact

log = logging.getLogger("polybot.notify")
BERLIN = ZoneInfo("Europe/Berlin")
LEVELS = {"info": 0, "normal": 1, "hoch": 2, "kritisch": 3}
DEDUPE_SEC = 6 * 3600
last_status = "aus – TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID nicht gesetzt"
_lock = threading.Lock()
_sent_keys = {}                      # key -> Zeitpunkt
_day = {"day": "", "normal": 0, "capped": False}
stats = {"gesendet": 0, "unterdrückt": 0}


def enabled():
    return bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID)


_send_lock = threading.Lock()         # eine Nachricht nach der anderen (Reihenfolge bleibt, keine gleichzeitigen Verbindungen)


def _send(text):
    global last_status
    with _send_lock:
        for attempt in (0, 1):
            try:
                post_json(f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
                          {"chat_id": config.TELEGRAM_CHAT_ID, "text": text[:3900], "parse_mode": "HTML",
                           "disable_web_page_preview": True})
                last_status = "OK"
                return True
            except HttpError as e:
                last_status = redact(f"Fehler: {e}")[:160]
                # nur ein Verbindungsfehler wird einmal wiederholt; eine Ablehnung (4xx) oder ein leeres Budget nicht
                if attempt == 0 and e.status is None and not e.blocked:
                    time.sleep(3)
                    continue
                log.warning("Telegram: %s", last_status)
                return False


def _in_quiet_hours(now):
    a, b = int(getattr(config, "QUIET_FROM", 23)), int(getattr(config, "QUIET_TO", 7))
    if a == b:
        return False
    h = now.hour
    return (a <= h < b) if a < b else (h >= a or h < b)


def decide(level, key=None, now=None):
    """Soll diese Meldung jetzt per Telegram raus? Gibt (ja/nein, Grund) zurück. Ohne Nebenwirkung auf das Netz."""
    now = now or datetime.now(BERLIN)
    lv = LEVELS.get(level, 1)
    if lv < LEVELS.get(getattr(config, "NOTIFY_MIN_LEVEL", "normal"), 1):
        return False, "unter Mindeststufe"
    with _lock:
        if key and lv < 3:
            last = _sent_keys.get(key)
            if last and time.time() - last < DEDUPE_SEC:
                return False, "schon gemeldet"
        if lv < 3 and _in_quiet_hours(now):
            return False, "Ruhezeit"
        day = now.strftime("%Y-%m-%d")
        if _day["day"] != day:
            _day.update(day=day, normal=0, capped=False)
        if lv <= 1:
            cap = int(getattr(config, "NOTIFY_DAILY_CAP", 40))
            if _day["normal"] >= cap:
                first = not _day["capped"]
                _day["capped"] = True
                return False, "Tageslimit" + (" (erste)" if first else "")
            _day["normal"] += 1
        if key:
            _sent_keys[key] = time.time()
            for k in [k for k, t in _sent_keys.items() if time.time() - t > DEDUPE_SEC]:
                del _sent_keys[k]
    return True, "ok"


def push(level, text, key=None):
    """Meldung mit Priorität. Blockiert den Bot nie (Versand im Hintergrund)."""
    if not enabled():
        return False
    ok, why = decide(level, key)
    if not ok:
        stats["unterdrückt"] += 1
        if why == "Tageslimit (erste)":
            threading.Thread(target=_send, args=("🔕 Tageslimit für normale Meldungen erreicht – der Rest steht im Dashboard. "
                                                 "Wichtiges kommt weiter durch.",), daemon=True).start()
        return False
    stats["gesendet"] += 1
    threading.Thread(target=_send, args=(text,), daemon=True).start()
    return True


def send(text, account=None, level="normal", key=None):
    """Bisherige Schnittstelle (Käufe, Verkäufe, Abrechnungen); `account` filtert auf die gemeldeten Konten."""
    if account is not None and not config.notify_account(account):
        return False
    return push(level, text, key)


_chat_cache = {"t": 0, "v": None}


def find_chat_id_cached():
    if time.time() - _chat_cache["t"] > 20 or (_chat_cache["v"] is None and time.time() - _chat_cache["t"] > 10):
        _chat_cache["t"] = time.time()
        _chat_cache["v"] = find_chat_id()
    return _chat_cache["v"]


def find_chat_id():
    """Hilfe beim Einrichten: letzte Chat-ID aus getUpdates lesen (nur Token nötig)."""
    global last_status
    if not config.TELEGRAM_BOT_TOKEN:
        return None
    try:
        r = SESSION.get(f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/getUpdates", timeout=config.HTTP_TIMEOUT)
        data = r.json()
        if not data.get("ok"):
            last_status = redact(f"Telegram meldet: {data.get('description', r.status_code)}")
            if r.status_code in (401, 404):
                last_status += " – der Token ist vermutlich falsch kopiert (ohne Leerzeichen, komplett mit Doppelpunkt)"
            return None
        res = data.get("result") or []
        for u in reversed(res):
            for k in ("message", "edited_message", "channel_post", "my_chat_member"):
                chat = (u.get(k) or {}).get("chat") or {}
                if chat.get("id"):
                    return str(chat["id"])
        last_status = (f"Token ok, aber noch keine Nachricht an den Bot gefunden ({len(res)} Updates) – "
                       "in Telegram den eigenen Bot öffnen und „Hallo“ schicken")
    except Exception as e:  # noqa: BLE001
        last_status = f"Telegram nicht erreichbar: {type(e).__name__}"
    return None


def reset():
    """Nur für Tests."""
    with _lock:
        _sent_keys.clear()
        _day.update(day="", normal=0, capped=False)
