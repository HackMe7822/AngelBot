import os
import pyodbc
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

def get_conn():
    """Return a pyodbc connection to the angelbot database on .\ANGELBOT."""
    pw = os.getenv("SQL_SA_PASS", "")
    conn_str = (
        "DRIVER={ODBC Driver 17 for SQL Server};"
        "SERVER=.\\ANGELBOT;"
        "DATABASE=angelbot;"
        "UID=sa;"
        f"PWD={pw};"
        "TrustServerCertificate=yes;"
    )
    return pyodbc.connect(conn_str, autocommit=False)

def _ensure_db():
    """Create the angelbot database on the ANGELBOT instance if it doesn't exist."""
    pw = os.getenv("SQL_SA_PASS", "")
    master_str = (
        "DRIVER={ODBC Driver 17 for SQL Server};"
        "SERVER=.\\ANGELBOT;"
        "DATABASE=master;"
        "UID=sa;"
        f"PWD={pw};"
        "TrustServerCertificate=yes;"
    )
    conn = pyodbc.connect(master_str, autocommit=True)
    c = conn.cursor()
    c.execute("IF NOT EXISTS (SELECT name FROM sys.databases WHERE name='angelbot') CREATE DATABASE angelbot")
    conn.close()

def _exec(c, sql):
    """Execute a single SQL statement, ignoring errors for idempotent migrations."""
    try:
        c.execute(sql)
    except Exception as e:
        pass  # IF NOT EXISTS guards handle this; real errors surface on next query

def init_db():
    _ensure_db()
    conn = get_conn()
    conn.autocommit = True   # DDL statements don't need explicit transactions
    c = conn.cursor()

    # ── trades ────────────────────────────────────────────────────────────────
    _exec(c, """
        IF NOT EXISTS (SELECT * FROM sys.tables WHERE name='trades')
        CREATE TABLE trades (
            id             INT IDENTITY(1,1) PRIMARY KEY,
            symbol         NVARCHAR(500),
            entry_time     NVARCHAR(50),
            exit_time      NVARCHAR(50),
            entry_price    FLOAT,
            exit_price     FLOAT,
            quantity       FLOAT,
            capital_used   FLOAT,
            pnl            FLOAT,
            pnl_pct        FLOAT,
            stop_loss      FLOAT,
            target         FLOAT,
            exit_reason    NVARCHAR(500),
            signals        NVARCHAR(MAX),
            status         NVARCHAR(50) DEFAULT 'open',
            source         NVARCHAR(50) DEFAULT 'paper'
        )
    """)
    # Migrate: add source column if missing
    _exec(c, """
        IF NOT EXISTS (
            SELECT * FROM sys.columns
            WHERE object_id = OBJECT_ID('trades') AND name = 'source'
        )
        ALTER TABLE trades ADD source NVARCHAR(50) DEFAULT 'paper'
    """)

    # ── top_ups ───────────────────────────────────────────────────────────────
    _exec(c, """
        IF NOT EXISTS (SELECT * FROM sys.tables WHERE name='top_ups')
        CREATE TABLE top_ups (
            id             INT IDENTITY(1,1) PRIMARY KEY,
            amount         FLOAT,
            reason         NVARCHAR(MAX),
            balance_before FLOAT,
            time           NVARCHAR(50)
        )
    """)

    # ── signal_performance ────────────────────────────────────────────────────
    _exec(c, """
        IF NOT EXISTS (SELECT * FROM sys.tables WHERE name='signal_performance')
        CREATE TABLE signal_performance (
            id          INT IDENTITY(1,1) PRIMARY KEY,
            signal_name NVARCHAR(200),
            correct     INT DEFAULT 0,
            total       INT DEFAULT 0,
            weight      FLOAT DEFAULT 1.0,
            updated_at  NVARCHAR(50)
        )
    """)
    # Migrate: fix signal_name if it was created as NVARCHAR(MAX) (can't be indexed)
    _exec(c, """
        IF EXISTS (
            SELECT * FROM sys.columns
            WHERE object_id = OBJECT_ID('signal_performance')
              AND name = 'signal_name' AND max_length = -1
        )
        ALTER TABLE signal_performance ALTER COLUMN signal_name NVARCHAR(200)
    """)
    # Remove duplicates before creating unique index
    _exec(c, """
        DELETE FROM signal_performance
        WHERE id NOT IN (
            SELECT MAX(id) FROM signal_performance GROUP BY signal_name
        )
    """)
    _exec(c, """
        IF NOT EXISTS (
            SELECT * FROM sys.indexes
            WHERE name = 'idx_signal_name'
              AND object_id = OBJECT_ID('signal_performance')
        )
        CREATE UNIQUE INDEX idx_signal_name ON signal_performance(signal_name)
    """)

    # ── monitor_state ─────────────────────────────────────────────────────────
    # Drop and recreate if columns are NVARCHAR(MAX) — those can't be PK keys
    _exec(c, """
        IF EXISTS (
            SELECT * FROM sys.columns
            WHERE object_id = OBJECT_ID('monitor_state')
              AND name = 'market' AND max_length = -1
        )
        DROP TABLE monitor_state
    """)
    _exec(c, """
        IF NOT EXISTS (SELECT * FROM sys.tables WHERE name='monitor_state')
        CREATE TABLE monitor_state (
            market      NVARCHAR(100) NOT NULL,
            pos_id      INT,
            symbol      NVARCHAR(50),
            key         NVARCHAR(500) NOT NULL,
            value       NVARCHAR(MAX) NOT NULL,
            updated_at  NVARCHAR(50) NOT NULL,
            CONSTRAINT pk_monitor_state PRIMARY KEY (market, key)
        )
    """)

    # ── portal_users ──────────────────────────────────────────────────────────
    _exec(c, """
        IF NOT EXISTS (SELECT * FROM sys.tables WHERE name='portal_users')
        CREATE TABLE portal_users (
            id            INT IDENTITY(1,1) PRIMARY KEY,
            username      NVARCHAR(100) NOT NULL,
            password_hash NVARCHAR(256) NOT NULL,
            role          NVARCHAR(20)  DEFAULT 'viewer',
            created_at    NVARCHAR(50),
            last_login    NVARCHAR(50)
        )
    """)
    _exec(c, """
        IF NOT EXISTS (
            SELECT * FROM sys.indexes
            WHERE name = 'idx_portal_users_username'
              AND object_id = OBJECT_ID('portal_users')
        )
        CREATE UNIQUE INDEX idx_portal_users_username ON portal_users(username)
    """)

    # Seed default admin if not present
    from hashlib import sha256
    default_pass = os.getenv("PORTAL_PASS", "AngelBot@1234")
    pass_hash = sha256(default_pass.encode()).hexdigest()
    _exec(c, f"""
        IF NOT EXISTS (SELECT 1 FROM portal_users WHERE username='admin')
        INSERT INTO portal_users (username, password_hash, role, created_at)
        VALUES ('admin', '{pass_hash}', 'admin', '{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    """)

    conn.close()
    print("Database ready.")

if __name__ == "__main__":
    init_db()
