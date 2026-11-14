"""Systemereignisse: eine Stelle für alles, was im Dashboard als Meldung/Warnung erscheinen und
(je nach Stufe) per Telegram rausgehen soll."""
import json
from datetime import datetime, timedelta, timezone

from . import notify
from .sources import redact

ICON = {"info": "ℹ️", "normal": "🔹", "hoch": "⚠️", "kritisch": "🛑"}


def emit(store, level, typ, title, detail="", key=None, agent=None, telegram=None, dedupe_h=6):
    """Ereignis speichern. Gleicher `key` innerhalb von dedupe_h Stunden zählt nur hoch statt neu zu melden."""
    title, detail = redact(title)[:300], redact(detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False))[:2000]
    now = datetime.now(timezone.utc)
    if key:
        since = (now - timedelta(hours=dedupe_h)).isoformat(timespec="seconds")
        row = store.q1("SELECT id FROM events WHERE key=? AND ts>=? ORDER BY id DESC LIMIT 1", (key, since))
        if row:
            store.x("UPDATE events SET count=count+1 WHERE id=?", (row["id"],))
            return None
    send = telegram if telegram is not None else level in ("normal", "hoch", "kritisch")
    sent = notify.push(level, f"{ICON.get(level, '')} <b>{title}</b>" + (f"\n{detail[:600]}" if detail else ""), key) if send else False
    cur = store.x("INSERT INTO events(ts, level, type, agent, key, title, detail, notified) VALUES(?,?,?,?,?,?,?,?)",
                  (now.isoformat(timespec="seconds"), level, typ, agent, key, title, detail, 1 if sent else 0))
    return cur.lastrowid


def recent(store, limit=50, min_level=None, hours=None):
    sql, args = "SELECT * FROM events", []
    cond = []
    if min_level:
        order = ["info", "normal", "hoch", "kritisch"]
        allowed = order[order.index(min_level):]
        cond.append("level IN (%s)" % ",".join("?" * len(allowed)))
        args += allowed
    if hours:
        cond.append("ts>=?")
        args.append((datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds"))
    if cond:
        sql += " WHERE " + " AND ".join(cond)
    return store.q(sql + " ORDER BY id DESC LIMIT ?", args + [limit])
