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
        f"DATABASE={os.getenv('ANGELBOT_DB','angelbot')};"
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
    db_name = os.getenv('ANGELBOT_DB', 'angelbot')
    c.execute(f"IF NOT EXISTS (SELECT name FROM sys.databases WHERE name='{db_name}') CREATE DATABASE [{db_name}]")
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
    # Drop old table if it has the reserved-word 'key' column (causes SQL Server parse error)
    _exec(c, """
        IF EXISTS (
            SELECT * FROM sys.columns
            WHERE object_id = OBJECT_ID('monitor_state') AND name = 'key'
        )
        DROP TABLE monitor_state
    """)
    _exec(c, """
        IF NOT EXISTS (SELECT * FROM sys.tables WHERE name='monitor_state')
        CREATE TABLE monitor_state (
            market      NVARCHAR(100) NOT NULL,
            pos_id      INT,
            symbol      NVARCHAR(50),
            state_key   NVARCHAR(500) NOT NULL,
            value       NVARCHAR(MAX) NOT NULL,
            updated_at  NVARCHAR(50) NOT NULL,
            CONSTRAINT pk_monitor_state PRIMARY KEY (market, state_key)
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

    # ── user_config ───────────────────────────────────────────────────────────
    # Per-user capital, risk settings and market toggles. New users start paused.
    _exec(c, """
        IF NOT EXISTS (SELECT * FROM sys.tables WHERE name='user_config')
        CREATE TABLE user_config (
            id              INT IDENTITY(1,1) PRIMARY KEY,
            user_id         INT NOT NULL,
            capital_india   FLOAT,
            capital_us      FLOAT,
            capital_crypto  FLOAT,
            risk_pct        FLOAT DEFAULT 2.0,
            max_positions   INT   DEFAULT 5,
            sl_pct          FLOAT DEFAULT 2.0,
            target_mult     FLOAT DEFAULT 2.0,
            enable_india    BIT   DEFAULT 1,
            enable_us       BIT   DEFAULT 1,
            enable_crypto   BIT   DEFAULT 1,
            paused          BIT   DEFAULT 1,
            created_at      NVARCHAR(50),
            updated_at      NVARCHAR(50)
        )
    """)
    _exec(c, """
        IF NOT EXISTS (
            SELECT * FROM sys.indexes
            WHERE name='idx_user_config_user_id' AND object_id=OBJECT_ID('user_config')
        )
        CREATE UNIQUE INDEX idx_user_config_user_id ON user_config(user_id)
    """)
    # Seed admin config row (user_id=1) — reads capitals from .env via config.py
    import config as _cfg
    _exec(c, f"""
        IF NOT EXISTS (SELECT 1 FROM user_config WHERE user_id=1)
        INSERT INTO user_config
            (user_id,capital_india,capital_us,capital_crypto,risk_pct,max_positions,
             sl_pct,target_mult,enable_india,enable_us,enable_crypto,paused,created_at)
        VALUES (1,{_cfg.CAPITAL},{_cfg.US_CAPITAL},{_cfg.CRYPTO_CAPITAL},2.0,5,2.0,2.0,1,1,1,0,'{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    """)

    # ── user_services ─────────────────────────────────────────────────────────
    # Tracks which NSSM services belong to which user.
    _exec(c, """
        IF NOT EXISTS (SELECT * FROM sys.tables WHERE name='user_services')
        CREATE TABLE user_services (
            id            INT IDENTITY(1,1) PRIMARY KEY,
            user_id       INT NOT NULL,
            service_name  NVARCHAR(200) NOT NULL,
            market        NVARCHAR(50),
            worker_script NVARCHAR(200),
            status        NVARCHAR(50) DEFAULT 'stopped'
        )
    """)
    # Seed admin's existing services so they appear in the UI
    for market, script, svc in [
        ('india',  'india_worker.py',  'AngelBot-India'),
        ('us',     'us_worker.py',     'AngelBot-US'),
        ('crypto', 'crypto_worker.py', 'AngelBot-Crypto'),
    ]:
        _exec(c, f"""
            IF NOT EXISTS (SELECT 1 FROM user_services WHERE service_name='{svc}')
            INSERT INTO user_services (user_id,service_name,market,worker_script,status)
            VALUES (1,'{svc}','{market}','{script}','running')
        """)

    # ── portal_instances ──────────────────────────────────────────────────────
    _exec(c, """
        IF NOT EXISTS (SELECT * FROM sys.tables WHERE name='portal_instances')
        CREATE TABLE portal_instances (
            id           INT IDENTITY(1,1) PRIMARY KEY,
            name         NVARCHAR(100) NOT NULL,
            db_name      NVARCHAR(100) NOT NULL,
            port         INT NOT NULL,
            instance_dir NVARCHAR(500),
            status       NVARCHAR(50) DEFAULT 'stopped',
            created_at   NVARCHAR(50)
        )
    """)
    _exec(c, """
        IF NOT EXISTS (
            SELECT * FROM sys.indexes
            WHERE name='idx_portal_instances_name' AND object_id=OBJECT_ID('portal_instances')
        )
        CREATE UNIQUE INDEX idx_portal_instances_name ON portal_instances(name)
    """)

    # ── Migrate trades: add user_id column (existing rows → admin = 1) ────────
    _exec(c, """
        IF NOT EXISTS (
            SELECT * FROM sys.columns
            WHERE object_id=OBJECT_ID('trades') AND name='user_id'
        )
        ALTER TABLE trades ADD user_id INT NOT NULL DEFAULT 1
    """)

    # ── One-time: normalize US trade timestamps ET → IST ─────────────────────
    # Before June 2026 fix, alpaca_trader stored entry/exit_time in ET (UTC-4).
    # US market hours in ET are 09:30–16:00, so any us_paper trade with time
    # component 01:00–16:59 is in ET and needs +570 min to become IST.
    # Trades already stored in IST have time 19:00–01:30, so hour >= 17 or == 0 → skip.
    _exec(c, """
        UPDATE trades
        SET
            entry_time = CONVERT(NVARCHAR(50),
                            DATEADD(minute, 570,
                                TRY_CAST(entry_time AS DATETIME2)), 20),
            exit_time  = CASE
                WHEN exit_time IS NOT NULL AND LEN(RTRIM(exit_time)) > 5
                THEN CONVERT(NVARCHAR(50),
                        DATEADD(minute, 570,
                            TRY_CAST(exit_time AS DATETIME2)), 20)
                ELSE exit_time
                END
        WHERE source = 'us_paper'
          AND TRY_CAST(entry_time AS DATETIME2) IS NOT NULL
          AND DATEPART(hour, TRY_CAST(entry_time AS DATETIME2)) BETWEEN 1 AND 16
    """)

    conn.close()
    print("Database ready.")

if __name__ == "__main__":
    init_db()
