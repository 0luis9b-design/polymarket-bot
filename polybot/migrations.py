"""Nummerierte Datenbank-Migrationen.

Jede Migration läuft genau einmal, in einer Transaktion, und wird in `schema_version` vermerkt.
Vor der ersten neuen Migration auf einer bestehenden Datenbank wird eine Sicherungskopie angelegt.
Migrationen fügen nur hinzu – bestehende Tabellen und Daten bleiben unverändert.
"""
import logging
import os
import sqlite3
from datetime import datetime, timezone

log = logging.getLogger("polybot.migrations")

MIGRATIONS = [
    (1, "Ausgangsstand (Konten, Wetten, Gebote, Prognosen alt, Protokoll)", []),
    (2, "Marktkatalog und Preisverlauf", [
        """CREATE TABLE markets (
            id TEXT PRIMARY KEY, question TEXT, category TEXT, tags TEXT, event_id TEXT, event_title TEXT, slug TEXT,
            end_date TEXT, first_seen TEXT, last_seen TEXT, price REAL, bid REAL, ask REAL, liquidity REAL,
            volume REAL, volume24h REAL, move_1h REAL, move_24h REAL, quality TEXT, priority REAL DEFAULT 0,
            modeled INTEGER DEFAULT 0, status TEXT DEFAULT 'open', news_48h INTEGER DEFAULT 0)""",
        "CREATE INDEX idx_markets_cat ON markets(category, status)",
        "CREATE INDEX idx_markets_prio ON markets(status, priority DESC)",
        "CREATE INDEX idx_markets_first ON markets(first_seen)",
        """CREATE TABLE snapshots (market_id TEXT, ts TEXT, price REAL, bid REAL, ask REAL, volume24h REAL, liquidity REAL,
            PRIMARY KEY (market_id, ts)) WITHOUT ROWID""",
    ]),
    (3, "Prognose-Historie (unveränderlich), Ergebnisse, Modell-Register", [
        """CREATE TABLE forecasts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, market_id TEXT NOT NULL, category TEXT, model TEXT NOT NULL,
            version TEXT NOT NULL, horizon TEXT NOT NULL, created_at TEXT NOT NULL, p REAL NOT NULL, market_p REAL,
            uncertainty REAL, hours_left REAL, champion INTEGER DEFAULT 0, valid INTEGER DEFAULT 1, reject_reason TEXT,
            cluster TEXT, sources TEXT, features TEXT, prev_hash TEXT, hash TEXT,
            UNIQUE (market_id, model, version, horizon))""",
        "CREATE INDEX idx_fc_market ON forecasts(market_id)",
        "CREATE INDEX idx_fc_cat ON forecasts(category, model)",
        "CREATE TRIGGER forecasts_kein_update BEFORE UPDATE ON forecasts BEGIN SELECT RAISE(ABORT, 'Prognose-Historie ist unveraenderlich'); END",
        "CREATE TRIGGER forecasts_kein_delete BEFORE DELETE ON forecasts BEGIN SELECT RAISE(ABORT, 'Prognose-Historie ist unveraenderlich'); END",
        "CREATE TABLE outcomes (market_id TEXT PRIMARY KEY, outcome REAL NOT NULL, resolved_at TEXT NOT NULL, source TEXT)",
        """CREATE TABLE model_registry (category TEXT PRIMARY KEY, model TEXT, version TEXT, mix_w REAL DEFAULT 1.0,
            prev_model TEXT, prev_version TEXT, prev_mix_w REAL, activated_at TEXT, note TEXT)""",
        """CREATE TABLE model_versions (id INTEGER PRIMARY KEY AUTOINCREMENT, model TEXT, version TEXT, params TEXT,
            n_samples INTEGER, created_at TEXT, note TEXT, UNIQUE (model, version))""",
        """CREATE TABLE model_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, category TEXT, action TEXT,
            model TEXT, version TEXT, mix_w REAL, reason TEXT)""",
    ]),
    (4, "Lernen: Experimente und Vorschläge", [
        """CREATE TABLE experiments (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, category TEXT, champion TEXT,
            challenger TEXT, n_is INTEGER, n_oos INTEGER, clusters_oos INTEGER, brier_champ_oos REAL, brier_chal_oos REAL,
            brier_market_oos REAL, z REAL, passed INTEGER, detail TEXT, UNIQUE (category, champion, challenger))""",
        """CREATE TABLE proposals (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, kind TEXT, category TEXT, key TEXT,
            title TEXT, detail TEXT, status TEXT DEFAULT 'offen', decided_at TEXT, note TEXT)""",
        "CREATE INDEX idx_prop_status ON proposals(status)",
    ]),
    (5, "Strategie-Labor: entschiedene Märkte und Backtests", [
        """CREATE TABLE resolved (market_id TEXT PRIMARY KEY, question TEXT, category TEXT, end_date TEXT, closed_at TEXT,
            outcome REAL, volume REAL, early INTEGER DEFAULT 0, p_1h REAL, p_6h REAL, p_24h REAL, p_72h REAL, p_7d REAL,
            n_points INTEGER, fetched_at TEXT)""",
        "CREATE INDEX idx_resolved_cat ON resolved(category, end_date)",
        """CREATE TABLE backtests (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, strategy TEXT, params TEXT,
            dataset TEXT, result TEXT)""",
    ]),
    (6, "Betrieb: Agenten-Läufe, Ereignisse, Quellen-Statistik", [
        """CREATE TABLE agent_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, agent TEXT, started_at TEXT, duration REAL,
            ok INTEGER, summary TEXT, error TEXT)""",
        "CREATE INDEX idx_runs_agent ON agent_runs(agent, id)",
        """CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, level TEXT, type TEXT, agent TEXT, key TEXT,
            title TEXT, detail TEXT, count INTEGER DEFAULT 1, notified INTEGER DEFAULT 0)""",
        "CREATE INDEX idx_events_ts ON events(ts)",
        "CREATE INDEX idx_events_key ON events(key, ts)",
        """CREATE TABLE source_stats (source TEXT, day TEXT, calls INTEGER, errors INTEGER, limited INTEGER, blocked INTEGER,
            units INTEGER, ms REAL, last_ok TEXT, last_error TEXT, last_msg TEXT, PRIMARY KEY (source, day))""",
    ]),
    (7, "Nachrichten", [
        """CREATE TABLE news (id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT, title TEXT, url TEXT UNIQUE, summary TEXT,
            published TEXT, fetched_at TEXT, flags TEXT)""",
        "CREATE INDEX idx_news_pub ON news(published)",
        "CREATE TABLE news_links (news_id INTEGER, market_id TEXT, score REAL, PRIMARY KEY (news_id, market_id)) WITHOUT ROWID",
        "CREATE INDEX idx_newslinks_market ON news_links(market_id)",
    ]),
    (8, "Claude-Berichte und Empfehlungen", [
        """CREATE TABLE reports (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, kind TEXT, period_from TEXT,
            period_to TEXT, title TEXT, markdown TEXT, data TEXT, status TEXT DEFAULT 'offen', note TEXT)""",
        """CREATE TABLE recommendations (id INTEGER PRIMARY KEY AUTOINCREMENT, report_id INTEGER, text TEXT,
            status TEXT DEFAULT 'offen', note TEXT, updated_at TEXT)""",
    ]),
    (9, "Einordnung in Bereich/Unterbereich/Detail; Einstiegs-Daten je Wette", [
        "ALTER TABLE positions ADD COLUMN sub TEXT",
        "ALTER TABLE positions ADD COLUMN detail TEXT",
        "ALTER TABLE positions ADD COLUMN model TEXT",          # Modell@Version bzw. Regel, nach der gekauft wurde
        "ALTER TABLE positions ADD COLUMN lane TEXT",           # model = eigenes Datenmodell, base = Basisraten, rule = Regel, arb
        "ALTER TABLE positions ADD COLUMN p_mid REAL",          # Markt-Wahrscheinlichkeit für unsere Seite (Mitte) beim Kauf
        "ALTER TABLE positions ADD COLUMN uncertainty REAL",
        "ALTER TABLE positions ADD COLUMN hours_left REAL",
        "ALTER TABLE positions ADD COLUMN spread REAL",
        "ALTER TABLE positions ADD COLUMN liquidity REAL",
        "ALTER TABLE positions ADD COLUMN volume24h REAL",
        "ALTER TABLE positions ADD COLUMN entry TEXT",          # alles Weitere zum Zeitpunkt des Kaufs (JSON)
        "ALTER TABLE positions ADD COLUMN exit_reason TEXT",
        "ALTER TABLE orders ADD COLUMN sub TEXT",
        "ALTER TABLE orders ADD COLUMN detail TEXT",
        "ALTER TABLE orders ADD COLUMN model TEXT",
        "ALTER TABLE orders ADD COLUMN entry TEXT",
        "ALTER TABLE markets ADD COLUMN sub TEXT",
        "ALTER TABLE markets ADD COLUMN detail TEXT",
        "CREATE INDEX idx_pos_opened ON positions(opened_at)",
        "CREATE INDEX idx_pos_closed ON positions(closed_at)",
        "CREATE INDEX idx_pos_tax ON positions(category, sub, detail)",
        "CREATE INDEX idx_markets_tax ON markets(category, sub, detail)",
        "ALTER TABLE resolved ADD COLUMN sub TEXT",
        "ALTER TABLE resolved ADD COLUMN detail TEXT",
    ]),
]
LATEST = MIGRATIONS[-1][0]


def current_version(db):
    db.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at TEXT, note TEXT)")
    row = db.execute("SELECT MAX(version) FROM schema_version").fetchone()
    return row[0] or 0


def backup_file(db, path):
    """Konsistente Kopie der laufenden Datenbank (SQLite-Backup-API)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    dst = sqlite3.connect(path)
    try:
        db.backup(dst)
    finally:
        dst.close()
    return path


def migrate(db, db_path=None):
    """Fehlende Migrationen anwenden. Gibt die Liste der angewendeten Versionsnummern zurück."""
    have = current_version(db)
    todo = [m for m in MIGRATIONS if m[0] > have]
    if not todo:
        return []
    has_data = db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='positions'").fetchone()[0] and \
        db.execute("SELECT COUNT(*) FROM positions").fetchone()[0] > 0
    if has_data and db_path and db_path != ":memory:" and any(m[2] for m in todo):
        target = os.path.join(os.path.dirname(db_path), "backups", f"vor-migration-v{todo[0][0]}.sqlite")
        try:
            backup_file(db, target)
            log.info("Sicherung vor Migration: %s", target)
        except Exception as e:  # noqa: BLE001
            log.warning("Sicherung vor Migration fehlgeschlagen: %s", e)
    done = []
    for version, note, statements in todo:
        try:
            db.execute("BEGIN")
            for sql in statements:
                db.execute(sql)
            db.execute("INSERT INTO schema_version(version, applied_at, note) VALUES(?,?,?)",
                       (version, datetime.now(timezone.utc).isoformat(timespec="seconds"), note))
            db.execute("COMMIT")
            done.append(version)
        except Exception:
            db.execute("ROLLBACK")
            log.exception("Migration %s fehlgeschlagen – zurückgerollt", version)
            raise
    return done
