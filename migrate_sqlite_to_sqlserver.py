"""
Migrate historical data from angelbot.db (SQLite) to SQL Server.
Run ONCE from an admin CMD on the VM:
    python migrate_sqlite_to_sqlserver.py

Safe to re-run — skips rows that already exist (by id).
Migrates: trades, top_ups, signal_performance
"""
import os, sys, sqlite3
sys.path.insert(0, os.path.dirname(__file__))
from dotenv import load_dotenv
load_dotenv()

SQLITE_DB = os.path.join(os.path.dirname(__file__), 'angelbot.db')

def get_sql_conn():
    import pyodbc
    pw = os.getenv("SQL_SA_PASS", "")
    return pyodbc.connect(
        "DRIVER={ODBC Driver 17 for SQL Server};"
        "SERVER=.\\ANGELBOT;DATABASE=angelbot;UID=sa;"
        f"PWD={pw};TrustServerCertificate=yes;",
        autocommit=False
    )

def migrate():
    if not os.path.exists(SQLITE_DB):
        print(f"[SKIP] angelbot.db not found at {SQLITE_DB}")
        return

    src = sqlite3.connect(SQLITE_DB)
    src.row_factory = sqlite3.Row
    dst = get_sql_conn()
    dc  = dst.cursor()

    # ── trades ────────────────────────────────────────────────────────────────
    print("Migrating trades...")
    rows = src.execute("SELECT * FROM trades").fetchall()
    cols = [d[0] for d in src.execute("SELECT * FROM trades LIMIT 0").description]
    inserted = skipped = 0
    for r in rows:
        row = dict(zip(cols, r))
        dc.execute("SELECT 1 FROM trades WHERE id=?", (row['id'],))
        if dc.fetchone():
            skipped += 1
            continue
        # Insert with explicit IDENTITY value — must use SET IDENTITY_INSERT
        dc.execute("SET IDENTITY_INSERT trades ON")
        dc.execute("""
            INSERT INTO trades (id,symbol,entry_time,exit_time,entry_price,exit_price,
                quantity,capital_used,pnl,pnl_pct,stop_loss,target,
                exit_reason,signals,status,source)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            row.get('id'),       row.get('symbol'),     row.get('entry_time'),
            row.get('exit_time'),row.get('entry_price'), row.get('exit_price'),
            row.get('quantity'), row.get('capital_used'),row.get('pnl'),
            row.get('pnl_pct'),  row.get('stop_loss'),   row.get('target'),
            row.get('exit_reason'), row.get('signals'),
            row.get('status','open'), row.get('source','paper')
        ))
        dc.execute("SET IDENTITY_INSERT trades OFF")
        inserted += 1
    dst.commit()
    print(f"  trades: {inserted} inserted, {skipped} already existed")

    # ── top_ups ───────────────────────────────────────────────────────────────
    print("Migrating top_ups...")
    try:
        rows = src.execute("SELECT * FROM top_ups").fetchall()
        cols = [d[0] for d in src.execute("SELECT * FROM top_ups LIMIT 0").description]
        ins2 = skip2 = 0
        for r in rows:
            row = dict(zip(cols, r))
            dc.execute("SELECT 1 FROM top_ups WHERE id=?", (row['id'],))
            if dc.fetchone():
                skip2 += 1
                continue
            dc.execute("SET IDENTITY_INSERT top_ups ON")
            dc.execute(
                "INSERT INTO top_ups (id,amount,reason,balance_before,time) VALUES (?,?,?,?,?)",
                (row.get('id'), row.get('amount'), row.get('reason'),
                 row.get('balance_before'), row.get('time'))
            )
            dc.execute("SET IDENTITY_INSERT top_ups OFF")
            ins2 += 1
        dst.commit()
        print(f"  top_ups: {ins2} inserted, {skip2} already existed")
    except Exception as e:
        print(f"  top_ups skipped: {e}")

    # ── signal_performance ────────────────────────────────────────────────────
    print("Migrating signal_performance...")
    try:
        rows = src.execute("SELECT * FROM signal_performance").fetchall()
        cols = [d[0] for d in src.execute("SELECT * FROM signal_performance LIMIT 0").description]
        ins3 = skip3 = 0
        for r in rows:
            row = dict(zip(cols, r))
            dc.execute("SELECT 1 FROM signal_performance WHERE signal_name=?", (row.get('signal_name'),))
            if dc.fetchone():
                skip3 += 1
                continue
            dc.execute(
                "INSERT INTO signal_performance (signal_name,correct,total,weight,updated_at) VALUES (?,?,?,?,?)",
                (row.get('signal_name'), row.get('correct',0), row.get('total',0),
                 row.get('weight',1.0),  row.get('updated_at'))
            )
            ins3 += 1
        dst.commit()
        print(f"  signal_performance: {ins3} inserted, {skip3} already existed")
    except Exception as e:
        print(f"  signal_performance skipped: {e}")

    src.close()
    dst.close()
    print("\nMigration complete.")

if __name__ == "__main__":
    migrate()
