"""Benachrichtigungen per Telegram (optional).

Einrichten: in Telegram @BotFather anschreiben -> /newbot -> Token kopieren -> als TELEGRAM_BOT_TOKEN
in Railway eintragen. Dann dem neuen Bot einmal "Hallo" schreiben; die Chat-ID zeigt das Dashboard
unter Status an (oder https://api.telegram.org/bot<TOKEN>/getUpdates) -> als TELEGRAM_CHAT_ID eintragen.
"""
import logging
import threading

from . import config
from .http import SESSION

log = logging.getLogger("polybot.notify")
last_status = "aus – TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID nicht gesetzt"


def enabled():
    return bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID)


def _send(text):
    global last_status
    try:
        r = SESSION.post(f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
                         json={"chat_id": config.TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML",
                               "disable_web_page_preview": True}, timeout=config.HTTP_TIMEOUT)
        last_status = "OK" if r.status_code == 200 else f"Fehler {r.status_code}: {r.text[:120]}"
    except Exception as e:  # noqa: BLE001
        last_status = f"Fehler: {e}"
        log.warning("Telegram: %s", e)


def send(text, account=None):
    """Schickt im Hintergrund, blockiert den Bot nie."""
    if not enabled():
        return
    if account is not None and account not in config.NOTIFY_ACCOUNTS:
        return
    threading.Thread(target=_send, args=(text,), daemon=True).start()


_chat_cache = {"t": 0, "v": None}


def find_chat_id_cached():
    import time
    if time.time() - _chat_cache["t"] > 60:
        _chat_cache["t"] = time.time()
        _chat_cache["v"] = find_chat_id()
    return _chat_cache["v"]


def find_chat_id():
    """Hilfe beim Einrichten: letzte Chat-ID aus getUpdates lesen (nur Token nötig)."""
    if not config.TELEGRAM_BOT_TOKEN:
        return None
    try:
        r = SESSION.get(f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/getUpdates", timeout=config.HTTP_TIMEOUT)
        res = r.json().get("result") or []
        for u in reversed(res):
            msg = u.get("message") or u.get("channel_post") or {}
            if msg.get("chat", {}).get("id"):
                return str(msg["chat"]["id"])
    except Exception as e:  # noqa: BLE001
        log.warning("getUpdates: %s", e)
    return None
