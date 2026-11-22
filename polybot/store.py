"""Speicher (SQLite). Auf Railway muss DATA_DIR auf einem Volume liegen, sonst ist nach jedem Neustart alles weg."""
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone

# Treffer nach Markt-Ausgang (2.9/2.9.1): gewonnen/verloren – oder verkauft, sobald der Markt entschieden ist.
# Vorher fehlten die kurz vor Schluss mit Gewinn verkauften Wetten, die Trefferquote wirkte viel zu niedrig.
_MKT_OUT = "(SELECT o.outcome FROM outcomes o WHERE o.market_id=positions.market_id)"
DECIDED_SQL = f"(status IN ('won','lost') OR (status='sold' AND side IN (0,1) AND {_MKT_OUT} IS NOT NULL))"
HIT_SQL = f"(status='won' OR (status='sold' AND side IN (0,1) AND {_MKT_OUT} = CASE WHEN side=0 THEN 1.0 ELSE 0.0 END))"

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
        self.path = path
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)  # Autocommit; Transaktionen explizit
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute("PRAGMA busy_timeout=5000")
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
            from . import migrations
            self.migrated = migrations.migrate(self.db, path)
            self.schema_version = migrations.current_version(self.db)

    # --- allgemeine Helfer (für die Agenten) ---
    def q(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args)]

    def q1(self, sql, args=()):
        with self.lock:
            r = self.db.execute(sql, args).fetchone()
        return dict(r) if r else None

    def scalar(self, sql, args=()):
        with self.lock:
            r = self.db.execute(sql, args).fetchone()
        return r[0] if r else None

    def x(self, sql, args=()):
        with self.lock:
            return self.db.execute(sql, args)

    def xmany(self, sql, rows):
        """Viele Zeilen in einer Transaktion (schnell und alles-oder-nichts)."""
        rows = list(rows)
        if not rows:
            return 0
        with self.lock:
            self.db.execute("BEGIN")
            try:
                self.db.executemany(sql, rows)
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise
        return len(rows)

    def backup(self, keep=7):
        """Tägliche Sicherung neben der Datenbank; behält die letzten `keep`."""
        from . import migrations
        folder = os.path.join(os.path.dirname(self.path), "backups")
        name = os.path.join(folder, f"polybot-{datetime.now(timezone.utc):%Y%m%d}.sqlite")
        with self.lock:
            migrations.backup_file(self.db, name)
        files = sorted(f for f in os.listdir(folder) if f.startswith("polybot-") and f.endswith(".sqlite"))
        for old in files[:-keep]:
            os.remove(os.path.join(folder, old))
        return name, os.path.getsize(name)

    def add_outcome(self, market_id, outcome, source="polymarket"):
        with self.lock:
            self.db.execute("INSERT OR IGNORE INTO outcomes(market_id, outcome, resolved_at, source) VALUES(?,?,?,?)",
                            (market_id, outcome, now_iso(), source))

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

    def has_game(self, account, grp, event_id=None):
        """Hat das Konto schon eine offene Wette oder ein offenes Gebot auf dieses Spiel (gleiche Gruppe oder Ereignis)?"""
        cond, args = "grp=?", [grp]
        if event_id:
            cond += " OR event_id=?"
            args.append(str(event_id))
        with self.lock:
            for t in ("positions", "orders"):
                if self.db.execute(f"SELECT 1 FROM {t} WHERE account=? AND status='open' AND ({cond}) LIMIT 1",
                                   [account] + args).fetchone():
                    return True
        return False

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

    def add_equity_many(self, rows):
        """Kontowert vieler Konten auf einmal (account, equity, cash) – ein Schreibvorgang statt einer je Konto."""
        ts = now_iso()[:13] + ":00"
        with self.lock:
            self.db.executemany("INSERT INTO equity_acc(account,ts,equity,cash) VALUES(?,?,?,?) ON CONFLICT(account,ts) "
                                "DO UPDATE SET equity=excluded.equity, cash=excluded.cash", [(a, ts, e, c) for a, e, c in rows])
            self.db.commit()

    def equity_history(self, account=MAIN):
        with self.lock:
            return [dict(r) for r in self.db.execute(
                "SELECT ts, equity, cash FROM equity_acc WHERE account=? ORDER BY ts", (account,))]
