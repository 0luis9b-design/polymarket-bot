"""Speicher (SQLite). Auf Railway muss DATA_DIR auf einem Volume liegen, sonst ist nach jedem Neustart alles weg."""
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT, event_id TEXT, category TEXT, question TEXT, slug TEXT, event_slug TEXT,
    side INTEGER, side_name TEXT, shares REAL, price REAL, fee REAL, cost REAL,
    own_p REAL, market_p REAL, edge REAL, info TEXT,
    opened_at TEXT, end_date TEXT, status TEXT DEFAULT 'open',
    mark REAL, payout REAL, pnl REAL, closed_at TEXT
);
CREATE TABLE IF NOT EXISTS predictions (
    market_id TEXT PRIMARY KEY, category TEXT, question TEXT,
    own_p REAL, market_p REAL, created_at TEXT, end_date TEXT,
    outcome REAL, resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, level TEXT, msg TEXT
);
CREATE TABLE IF NOT EXISTS equity (ts TEXT PRIMARY KEY, equity REAL, cash REAL);
"""


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path):
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        with self.lock:
            self.db.executescript(SCHEMA)
            self.db.commit()

    # --- kv ---
    def kv_get(self, k, default=None):
        with self.lock:
            r = self.db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return r["v"] if r else default

    def kv_set(self, k, v):
        with self.lock:
            self.db.execute("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))
            self.db.commit()

    def kv_json(self, k, default=None):
        v = self.kv_get(k)
        try:
            return json.loads(v) if v else default
        except ValueError:
            return default

    # --- Konto ---
    def cash(self, start):
        v = self.kv_get("cash")
        if v is None:
            self.kv_set("cash", start)
            self.kv_set("started_at", now_iso())
            return float(start)
        return float(v)

    def add_cash(self, delta, start):
        with self.lock:
            c = self.cash(start) + delta
            self.kv_set("cash", round(c, 6))
            return c

    # --- Positionen ---
    def open_positions(self):
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM positions WHERE status='open' ORDER BY opened_at")]

    def closed_positions(self, limit=300):
        with self.lock:
            return [dict(r) for r in self.db.execute(
                "SELECT * FROM positions WHERE status!='open' ORDER BY closed_at DESC LIMIT ?", (limit,))]

    def has_position(self, market_id):
        with self.lock:
            return self.db.execute("SELECT 1 FROM positions WHERE market_id=? LIMIT 1", (market_id,)).fetchone() is not None

    def insert_position(self, p):
        cols = ",".join(p)
        with self.lock:
            cur = self.db.execute(f"INSERT INTO positions({cols}) VALUES({','.join('?' * len(p))})", list(p.values()))
            self.db.commit()
            return cur.lastrowid

    def update_position(self, pid, **kw):
        sets = ",".join(f"{k}=?" for k in kw)
        with self.lock:
            self.db.execute(f"UPDATE positions SET {sets} WHERE id=?", [*kw.values(), pid])
            self.db.commit()

    # --- Prognosen (für den Vergleich Bot vs. Markt) ---
    def add_prediction(self, market_id, category, question, own_p, market_p, end_date):
        with self.lock:
            self.db.execute(
                "INSERT OR IGNORE INTO predictions(market_id,category,question,own_p,market_p,created_at,end_date) "
                "VALUES(?,?,?,?,?,?,?)", (market_id, category, question, own_p, market_p, now_iso(), end_date))
            self.db.commit()

    def open_predictions(self):
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM predictions WHERE outcome IS NULL")]

    def resolve_prediction(self, market_id, outcome):
        with self.lock:
            self.db.execute("UPDATE predictions SET outcome=?, resolved_at=? WHERE market_id=?",
                            (outcome, now_iso(), market_id))
            self.db.commit()

    def resolved_predictions(self):
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM predictions WHERE outcome IS NOT NULL")]

    # --- Protokoll & Verlauf ---
    def log(self, msg, level="info"):
        with self.lock:
            self.db.execute("INSERT INTO log(ts,level,msg) VALUES(?,?,?)", (now_iso(), level, msg))
            self.db.execute("DELETE FROM log WHERE id < (SELECT MAX(id) - 2000 FROM log)")
            self.db.commit()

    def recent_log(self, n=150):
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM log ORDER BY id DESC LIMIT ?", (n,))]

    def add_equity(self, equity, cash):
        ts = now_iso()[:13] + ":00"  # ein Punkt pro Stunde
        with self.lock:
            self.db.execute("INSERT INTO equity(ts,equity,cash) VALUES(?,?,?) ON CONFLICT(ts) DO UPDATE SET "
                            "equity=excluded.equity, cash=excluded.cash", (ts, equity, cash))
            self.db.commit()

    def equity_history(self):
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM equity ORDER BY ts")]
