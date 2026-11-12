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
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT, market_id TEXT, event_id TEXT, category TEXT, question TEXT,
    slug TEXT, event_slug TEXT, side INTEGER, side_name TEXT, token_id TEXT, price REAL, shares REAL, stake REAL,
    q REAL, grp TEXT, info TEXT, created_at TEXT, end_date TEXT, game_start TEXT, status TEXT DEFAULT 'open',
    done_at TEXT, note TEXT
);
CREATE TABLE IF NOT EXISTS equity_acc (account TEXT, ts TEXT, equity REAL, cash REAL, PRIMARY KEY (account, ts));
CREATE INDEX IF NOT EXISTS idx_pos_status ON positions(status);
CREATE INDEX IF NOT EXISTS idx_pred_outcome ON predictions(outcome);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
"""

# Spalten, die nach dem ersten Start dazugekommen sind (werden bei alten Datenbanken ergänzt)
NEW_COLUMNS = {
    "account": "TEXT DEFAULT 'haupt'",
    "grp": "TEXT",
    "kind": "TEXT DEFAULT 'model'",
    "game_start": "TEXT",
    "close_p": "REAL",
    "exit_price": "REAL",
    "token_id": "TEXT",
    "fill_note": "TEXT",
}

MAIN = "haupt"


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
            have = {r["name"] for r in self.db.execute("PRAGMA table_info(positions)")}
            for col, typ in NEW_COLUMNS.items():
                if col not in have:
                    self.db.execute(f"ALTER TABLE positions ADD COLUMN {col} {typ}")
            self.db.execute("CREATE INDEX IF NOT EXISTS idx_pos_acc_mkt ON positions(account, market_id)")
            self.db.execute("UPDATE positions SET account='haupt' WHERE account IS NULL")
            self.db.execute("UPDATE positions SET kind='model' WHERE kind IS NULL")
            # alter Kontoverlauf -> Hauptkonto
            if self.db.execute("SELECT COUNT(*) FROM equity_acc").fetchone()[0] == 0:
                self.db.execute("INSERT OR IGNORE INTO equity_acc(account,ts,equity,cash) "
                                "SELECT 'haupt', ts, equity, cash FROM equity")
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

    # --- Konten ---
    @staticmethod
    def _ck(account):
        return "cash" if account == MAIN else f"cash:{account}"

    def cash(self, start, account=MAIN):
        with self.lock:
            v = self.kv_get(self._ck(account))
            if v is None:
                self.kv_set(self._ck(account), start)
                if self.kv_get("started_at") is None:
                    self.kv_set("started_at", now_iso())
                if self.kv_get(f"started_at:{account}") is None:
                    self.kv_set(f"started_at:{account}", now_iso())
                return float(start)
            return float(v)

    def add_cash(self, delta, start, account=MAIN):
        with self.lock:
            c = self.cash(start, account) + delta
            self.kv_set(self._ck(account), round(c, 6))
            return c

    # --- Positionen ---
    def open_positions(self, account=None):
        q, args = "SELECT * FROM positions WHERE status='open'", []
        if account:
            q, args = q + " AND account=?", [account]
        with self.lock:
            return [dict(r) for r in self.db.execute(q + " ORDER BY opened_at", args)]

    def closed_positions(self, limit=300, account=None):
        q, args = "SELECT * FROM positions WHERE status!='open'", []
        if account:
            q, args = q + " AND account=?", [account]
        with self.lock:
            return [dict(r) for r in self.db.execute(q + " ORDER BY closed_at DESC LIMIT ?", args + [limit])]

    def has_position(self, market_id, account=MAIN):
        with self.lock:
            return self.db.execute("SELECT 1 FROM positions WHERE market_id=? AND account=? LIMIT 1",
                                   (market_id, account)).fetchone() is not None

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

    # --- Limit-Orders ---
    def open_orders(self, account=None):
        q, args = "SELECT * FROM orders WHERE status='open'", []
        if account:
            q, args = q + " AND account=?", [account]
        with self.lock:
            return [dict(r) for r in self.db.execute(q + " ORDER BY created_at", args)]

    def recent_orders(self, limit=200):
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM orders ORDER BY id DESC LIMIT ?", (limit,))]

    def has_order(self, market_id, account):
        with self.lock:
            return self.db.execute("SELECT 1 FROM orders WHERE market_id=? AND account=? AND status='open' LIMIT 1",
                                   (market_id, account)).fetchone() is not None

    def insert_order(self, o):
        cols = ",".join(o)
        with self.lock:
            cur = self.db.execute(f"INSERT INTO orders({cols}) VALUES({','.join('?' * len(o))})", list(o.values()))
            self.db.commit()
            return cur.lastrowid

    def update_order(self, oid, **kw):
        sets = ",".join(f"{k}=?" for k in kw)
        with self.lock:
            self.db.execute(f"UPDATE orders SET {sets} WHERE id=?", [*kw.values(), oid])
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
        import logging
        logging.getLogger("polybot.protokoll").log(logging.ERROR if level == "error" else logging.INFO, msg)
        with self.lock:
            self.db.execute("INSERT INTO log(ts,level,msg) VALUES(?,?,?)", (now_iso(), level, msg))
            self.db.execute("DELETE FROM log WHERE id < (SELECT MAX(id) - 3000 FROM log)")
            self.db.commit()

    def recent_log(self, n=150):
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM log ORDER BY id DESC LIMIT ?", (n,))]

    def add_equity(self, equity, cash, account=MAIN):
        ts = now_iso()[:13] + ":00"  # ein Punkt pro Stunde
        with self.lock:
            self.db.execute("INSERT INTO equity_acc(account,ts,equity,cash) VALUES(?,?,?,?) ON CONFLICT(account,ts) "
                            "DO UPDATE SET equity=excluded.equity, cash=excluded.cash", (account, ts, equity, cash))
            self.db.commit()

    def equity_history(self, account=MAIN):
        with self.lock:
            return [dict(r) for r in self.db.execute(
                "SELECT ts, equity, cash FROM equity_acc WHERE account=? ORDER BY ts", (account,))]
