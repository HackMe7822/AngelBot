import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'angelbot.db')

def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            entry_time TEXT,
            exit_time TEXT,
            entry_price REAL,
            exit_price REAL,
            quantity REAL,
            capital_used REAL,
            pnl REAL,
            pnl_pct REAL,
            stop_loss REAL,
            target REAL,
            exit_reason TEXT,
            signals TEXT,
            status TEXT DEFAULT 'open',
            source TEXT DEFAULT 'paper'
        )
    ''')
    # Migrate existing DB — add source column if it doesn't exist yet
    try:
        c.execute("ALTER TABLE trades ADD COLUMN source TEXT DEFAULT 'paper'")
    except Exception:
        pass
    c.execute('''
        CREATE TABLE IF NOT EXISTS top_ups (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            amount         REAL,
            reason         TEXT,
            balance_before REAL,
            time           TEXT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS signal_performance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_name TEXT UNIQUE,
            correct INTEGER DEFAULT 0,
            total INTEGER DEFAULT 0,
            weight REAL DEFAULT 1.0,
            updated_at TEXT
        )
    ''')
    # Migration: remove duplicate signal_name rows (keep highest id), then add unique index
    c.execute("""
        DELETE FROM signal_performance
        WHERE id NOT IN (
            SELECT MAX(id) FROM signal_performance GROUP BY signal_name
        )
    """)
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_signal_name ON signal_performance (signal_name)")

    # Monitor state — persists trailing stops, cooldowns, SL counts across restarts
    c.execute('''
        CREATE TABLE IF NOT EXISTS monitor_state (
            market      TEXT NOT NULL,
            pos_id      INTEGER,
            symbol      TEXT,
            key         TEXT NOT NULL,
            value       TEXT NOT NULL,
            updated_at  TEXT NOT NULL,
            PRIMARY KEY (market, key)
        )
    ''')

    conn.commit()
    conn.close()
    print("Database ready.")

if __name__ == "__main__":
    init_db()
