"""
AngelBot Management Portal — FastAPI backend
============================================
Provides REST API + serves the web UI.
Run: uvicorn portal.app:app --host 0.0.0.0 --port 8080 --reload

Default admin credentials:  admin / AngelBot@1234
Change via PORTAL_USER / PORTAL_PASS in .env
"""

import os, sys, json, subprocess, hashlib, hmac, secrets
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from fastapi import FastAPI, HTTPException, Depends, status, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel
from typing import Optional, List

from data.database import get_conn, init_db

app = FastAPI(title="AngelBot Portal", version="1.0.0")

# ── Auth ──────────────────────────────────────────────────────────────────────
_security = HTTPBasic()

def _get_credentials():
    from dotenv import load_dotenv
    load_dotenv()
    return (
        os.getenv("PORTAL_USER", "admin"),
        os.getenv("PORTAL_PASS", "AngelBot@1234"),
    )

def require_auth(credentials: HTTPBasicCredentials = Depends(_security)):
    user, pw = _get_credentials()
    ok_user = secrets.compare_digest(credentials.username.encode(), user.encode())
    ok_pass = secrets.compare_digest(credentials.password.encode(), pw.encode())
    if not (ok_user and ok_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username

_IST = timezone(timedelta(hours=5, minutes=30))

# ── Static files ──────────────────────────────────────────────────────────────
_STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


# ── HTML UI ───────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def root():
    index = _STATIC_DIR / "index.html"
    return HTMLResponse(content=index.read_text(encoding="utf-8"))


# ── Dashboard ─────────────────────────────────────────────────────────────────
def _build_date_clause(date_filter: Optional[str]) -> tuple:
    """Return (sql_fragment, params_list) for a date_filter on exit_time column."""
    now_ist = datetime.now(_IST)
    if date_filter == 'today':
        d = now_ist.strftime("%Y-%m-%d")
        return "AND date(exit_time) = ?", [d]
    elif date_filter == 'yesterday':
        d = (now_ist - timedelta(days=1)).strftime("%Y-%m-%d")
        return "AND date(exit_time) = ?", [d]
    elif date_filter == 'week':
        return "AND date(exit_time) >= date('now','-7 days')", []
    elif date_filter == 'month':
        return "AND date(exit_time) >= date('now','-30 days')", []
    return "", []


@app.get("/api/dashboard")
def dashboard(date_filter: Optional[str] = None, user: str = Depends(require_auth)):
    conn = get_conn()
    c    = conn.cursor()
    today_ist = datetime.now(_IST).strftime("%Y-%m-%d")

    date_sql, date_params = _build_date_clause(date_filter)

    def _market_summary(source_clause, cur_sym, dec):
        # For the main display period (respects date_filter, falls back to today for labels)
        if date_filter:
            c.execute(
                f"SELECT COUNT(*), COALESCE(SUM(pnl),0) FROM trades "
                f"WHERE status='closed' AND {source_clause} {date_sql}",
                date_params
            )
            trades, day_pnl = c.fetchone()
            c.execute(
                f"SELECT COUNT(*) FROM trades WHERE status='closed' AND {source_clause} "
                f"AND pnl>0 {date_sql}",
                date_params
            )
            wins = c.fetchone()[0]
        else:
            c.execute(
                f"SELECT COUNT(*), COALESCE(SUM(pnl),0) FROM trades "
                f"WHERE status='closed' AND {source_clause} AND date(exit_time)=?",
                (today_ist,)
            )
            trades, day_pnl = c.fetchone()
            c.execute(
                f"SELECT COUNT(*) FROM trades WHERE status='closed' AND {source_clause} AND pnl>0 "
                f"AND date(exit_time)=?",
                (today_ist,)
            )
            wins = c.fetchone()[0]

        c.execute(f"SELECT COUNT(*) FROM trades WHERE status='open' AND {source_clause}")
        open_pos = c.fetchone()[0]
        c.execute(f"SELECT COALESCE(SUM(pnl),0) FROM trades WHERE status='closed' AND {source_clause}")
        total_pnl = c.fetchone()[0]
        return {
            "today_trades": trades, "today_pnl": round(day_pnl or 0, dec),
            "today_wins": wins, "today_losses": (trades or 0) - (wins or 0),
            "open_positions": open_pos, "total_pnl": round(total_pnl or 0, dec),
            "currency": cur_sym,
        }

    india  = _market_summary("(source='paper' OR source IS NULL)", "₹", 2)
    us     = _market_summary("source='us_paper'",                 "$", 2)
    crypto = _market_summary("source='crypto_paper'",             "$", 4)

    # Bot status
    paused = os.path.exists(os.path.join(os.path.dirname(__file__), '..', 'paused.flag'))

    conn.close()
    return {
        "timestamp": datetime.now(_IST).isoformat(),
        "bot_paused": paused,
        "date_filter": date_filter,
        "india": india,
        "us": us,
        "crypto": crypto,
    }


# ── Trades ────────────────────────────────────────────────────────────────────
@app.get("/api/trades")
def get_trades(
    market: Optional[str] = None,
    status_filter: Optional[str] = None,
    date_filter: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    user: str = Depends(require_auth)
):
    conn = get_conn()
    c    = conn.cursor()

    where = []
    params = []
    if market == 'india':
        where.append("(source='paper' OR source IS NULL)")
    elif market == 'us':
        where.append("source='us_paper'")
    elif market == 'crypto':
        where.append("source='crypto_paper'")

    if status_filter in ('open', 'closed'):
        where.append("status=?")
        params.append(status_filter)

    # Date filter
    date_sql, date_params = _build_date_clause(date_filter)
    if date_sql:
        # Strip leading "AND " since we handle it via where list
        where.append(date_sql.lstrip("AND ").strip())
        params.extend(date_params)

    sql = "SELECT id,symbol,entry_time,exit_time,entry_price,exit_price,quantity,capital_used,pnl,pnl_pct,exit_reason,status,source FROM trades"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    params += [limit, offset]

    c.execute(sql, params)
    cols = ['id','symbol','entry_time','exit_time','entry_price','exit_price',
            'quantity','capital_used','pnl','pnl_pct','exit_reason','status','source']
    rows = [dict(zip(cols, r)) for r in c.fetchall()]

    # Total count and total P&L for current filter
    count_params = params[:-2]  # exclude limit/offset
    count_sql = "SELECT COUNT(*), COALESCE(SUM(pnl),0) FROM trades"
    if where:
        count_sql += " WHERE " + " AND ".join(where)
    c.execute(count_sql, count_params)
    row = c.fetchone()
    total = row[0]
    total_pnl = round(row[1] or 0, 4)

    conn.close()
    return {"trades": rows, "total": total, "total_pnl": total_pnl, "limit": limit, "offset": offset}


# ── Open positions ────────────────────────────────────────────────────────────
@app.get("/api/positions")
def get_positions(market: Optional[str] = None, user: str = Depends(require_auth)):
    conn = get_conn()
    c    = conn.cursor()
    if market == 'india':
        where = "(source='paper' OR source IS NULL)"
    elif market == 'us':
        where = "source='us_paper'"
    elif market == 'crypto':
        where = "source='crypto_paper'"
    else:
        where = "1=1"
    c.execute(f"SELECT id,symbol,entry_time,entry_price,quantity,capital_used,stop_loss,target,source "
              f"FROM trades WHERE status='open' AND {where} ORDER BY entry_time DESC")
    cols = ['id','symbol','entry_time','entry_price','quantity','capital_used','stop_loss','target','source']
    rows = [dict(zip(cols, r)) for r in c.fetchall()]
    conn.close()
    return {"positions": rows}


# ── Config ────────────────────────────────────────────────────────────────────
@app.get("/api/config")
def get_config(user: str = Depends(require_auth)):
    """Return editable config values from config.py."""
    import config as cfg
    editable = [
        'CAPITAL', 'MAX_DAILY_TRADES', 'MAX_DEPLOYED_PCT', 'PEAK_DRAWDOWN_PCT',
        'SCALP_TARGET_PCT', 'SCALP_SL_PCT', 'SL_CONFIRM_POLLS',
        'MAX_DAILY_LOSS_PCT', 'MIN_STOCK_PRICE', 'ENTRY_START_MIN', 'ENTRY_END_MIN',
        'US_CAPITAL', 'US_MAX_DAILY_TRADES', 'US_MIN_STOCK_PRICE',
        'CRYPTO_CAPITAL', 'CRYPTO_MAX_DAILY_TRADES', 'CRYPTO_TARGET_PCT', 'CRYPTO_SL_PCT',
        'CRYPTO_BTC_MIN_CHANGE', 'SLIPPAGE_PCT',
        'PAPER_MODE', 'ALPACA_PAPER', 'BINANCE_PAPER',
    ]
    result = {}
    for key in editable:
        val = getattr(cfg, key, None)
        if val is not None:
            result[key] = val
    return result


class ConfigUpdate(BaseModel):
    key: str
    value: str

@app.post("/api/config")
def update_config(update: ConfigUpdate, user: str = Depends(require_auth)):
    """Update a value in the .env file. Restarts are required for changes to take effect."""
    _BOT_DIR = Path(__file__).parent.parent
    env_path = _BOT_DIR / '.env'

    key   = update.key.strip()
    value = update.value.strip()

    # Safety: never allow disabling paper mode via API
    if key in ('PAPER_MODE', 'ALPACA_PAPER', 'BINANCE_PAPER') and value.lower() == 'false':
        raise HTTPException(400, "Cannot disable paper mode via the portal. Edit .env manually.")

    # Read current .env
    lines = env_path.read_text(encoding='utf-8').splitlines() if env_path.exists() else []
    found = False
    new_lines = []
    for line in lines:
        if line.startswith(f"{key}="):
            new_lines.append(f"{key}={value}")
            found = True
        else:
            new_lines.append(line)
    if not found:
        new_lines.append(f"{key}={value}")

    env_path.write_text('\n'.join(new_lines) + '\n', encoding='utf-8')
    return {"ok": True, "message": f"{key} updated. Restart bot workers for changes to take effect."}


# ── Auth management ───────────────────────────────────────────────────────────
class PasswordChange(BaseModel):
    current_password: str
    new_username: str
    new_password: str

@app.post("/api/auth/change-password")
def change_password(body: PasswordChange, user: str = Depends(require_auth)):
    """Change portal username and/or password. Writes PORTAL_USER/PORTAL_PASS to .env."""
    _, current_pw = _get_credentials()
    if not secrets.compare_digest(body.current_password.encode(), current_pw.encode()):
        raise HTTPException(400, "Current password is incorrect.")
    if len(body.new_password) < 8:
        raise HTTPException(400, "New password must be at least 8 characters.")

    _BOT_DIR = Path(__file__).parent.parent
    env_path = _BOT_DIR / '.env'
    lines = env_path.read_text(encoding='utf-8').splitlines() if env_path.exists() else []

    updates = {'PORTAL_USER': body.new_username.strip(), 'PORTAL_PASS': body.new_password.strip()}
    new_lines = []
    seen = set()
    for line in lines:
        matched = False
        for k, v in updates.items():
            if line.startswith(f"{k}="):
                new_lines.append(f"{k}={v}")
                seen.add(k)
                matched = True
                break
        if not matched:
            new_lines.append(line)
    for k, v in updates.items():
        if k not in seen:
            new_lines.append(f"{k}={v}")

    env_path.write_text('\n'.join(new_lines) + '\n', encoding='utf-8')
    return {"ok": True, "message": "Credentials updated. Re-login with your new password."}


# ── Bot controls ──────────────────────────────────────────────────────────────
@app.post("/api/bot/pause")
def bot_pause(user: str = Depends(require_auth)):
    flag = Path(__file__).parent.parent / 'paused.flag'
    flag.touch()
    return {"ok": True, "paused": True}

@app.post("/api/bot/resume")
def bot_resume(user: str = Depends(require_auth)):
    flag = Path(__file__).parent.parent / 'paused.flag'
    if flag.exists():
        flag.unlink()
    return {"ok": True, "paused": False}

@app.get("/api/bot/status")
def bot_status(user: str = Depends(require_auth)):
    flag = Path(__file__).parent.parent / 'paused.flag'
    return {"paused": flag.exists()}


# ── Logs (historical) ─────────────────────────────────────────────────────────
@app.get("/api/logs/{market}")
def get_log(market: str, lines: int = 200, user: str = Depends(require_auth)):
    if market not in ('india', 'us', 'crypto', 'watchdog', 'portal'):
        raise HTTPException(400, "Invalid market. Use: india, us, crypto, watchdog, portal")
    today = datetime.now().strftime('%Y%m%d')
    if market == 'watchdog':
        log_path = Path(__file__).parent.parent / 'logs' / 'watchdog.log'
    elif market == 'portal':
        log_path = Path(__file__).parent.parent / 'logs' / 'portal.log'
    else:
        log_path = Path(__file__).parent.parent / 'logs' / f'{market}_{today}.log'
    if not log_path.exists():
        return {"lines": [], "path": str(log_path)}
    with open(log_path, encoding='utf-8', errors='replace') as f:
        all_lines = f.readlines()
    tail = all_lines[-lines:]
    return {"lines": [l.rstrip() for l in tail], "path": str(log_path)}


# ── Logs (live — returns size for change detection) ───────────────────────────
@app.get("/api/logs/live/{market}")
def get_log_live(market: str, lines: int = 200, user: str = Depends(require_auth)):
    """Return last N lines of a log file plus the file's current byte size.
    The frontend compares size to detect new content without re-fetching unchanged data."""
    if market not in ('india', 'us', 'crypto', 'watchdog', 'portal'):
        raise HTTPException(400, "Invalid market. Use: india, us, crypto, watchdog, portal")
    today = datetime.now().strftime('%Y%m%d')
    if market == 'watchdog':
        log_path = Path(__file__).parent.parent / 'logs' / 'watchdog.log'
    elif market == 'portal':
        log_path = Path(__file__).parent.parent / 'logs' / 'portal.log'
    else:
        log_path = Path(__file__).parent.parent / 'logs' / f'{market}_{today}.log'
    if not log_path.exists():
        return {"lines": [], "size": 0, "path": str(log_path)}
    size = log_path.stat().st_size
    with open(log_path, encoding='utf-8', errors='replace') as f:
        all_lines = f.readlines()
    tail = all_lines[-lines:]
    return {"lines": [l.rstrip() for l in tail], "size": size, "path": str(log_path)}


# ── Services ──────────────────────────────────────────────────────────────────
_SERVICE_NAMES = ['AngelBot-India', 'AngelBot-US', 'AngelBot-Crypto', 'AngelBot-Portal']

def _sc_status(name: str) -> str:
    """Query a Windows service status via sc.exe. Returns 'running', 'stopped', or 'unknown'."""
    try:
        result = subprocess.run(
            ['sc', 'query', name],
            capture_output=True, text=True, timeout=10
        )
        out = result.stdout.lower()
        if 'running' in out:
            return 'running'
        elif 'stopped' in out:
            return 'stopped'
        return 'unknown'
    except Exception:
        return 'unknown'


@app.get("/api/services")
def get_services(user: str = Depends(require_auth)):
    """Return status of the 4 AngelBot Windows services."""
    services = []
    for name in _SERVICE_NAMES:
        services.append({"name": name, "status": _sc_status(name)})
    return {"services": services}


@app.post("/api/services/{name}/restart")
def restart_service(name: str, user: str = Depends(require_auth)):
    """Stop then start a named Windows service."""
    if name not in _SERVICE_NAMES:
        raise HTTPException(400, f"Unknown service '{name}'. Valid: {_SERVICE_NAMES}")
    try:
        stop = subprocess.run(['net', 'stop', name], capture_output=True, text=True, timeout=30)
        start = subprocess.run(['net', 'start', name], capture_output=True, text=True, timeout=30)
        if start.returncode != 0:
            raise HTTPException(500, f"Failed to start {name}: {start.stderr or start.stdout}")
        return {"ok": True, "name": name, "message": f"{name} restarted successfully."}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


# ── GitHub update check ───────────────────────────────────────────────────────
@app.get("/api/update/check")
def check_update(user: str = Depends(require_auth)):
    """Check if there are updates available on GitHub main branch."""
    try:
        result = subprocess.run(
            ['git', 'fetch', 'origin', 'main'],
            capture_output=True, text=True, timeout=15,
            cwd=str(Path(__file__).parent.parent)
        )
        result2 = subprocess.run(
            ['git', 'rev-list', 'HEAD..origin/main', '--count'],
            capture_output=True, text=True, timeout=10,
            cwd=str(Path(__file__).parent.parent)
        )
        commits_behind = int(result2.stdout.strip() or '0')
        return {"update_available": commits_behind > 0, "commits_behind": commits_behind}
    except Exception as e:
        return {"update_available": False, "error": str(e)}

@app.post("/api/update/apply")
def apply_update(user: str = Depends(require_auth)):
    """Pull latest code from GitHub. Workers must be restarted after."""
    try:
        result = subprocess.run(
            ['git', 'pull', 'origin', 'main'],
            capture_output=True, text=True, timeout=60,
            cwd=str(Path(__file__).parent.parent)
        )
        if result.returncode != 0:
            raise HTTPException(500, f"git pull failed: {result.stderr}")
        return {"ok": True, "output": result.stdout, "message": "Update applied. Restart bot workers to activate."}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


# ── Stats ─────────────────────────────────────────────────────────────────────
@app.get("/api/stats/leaderboard")
def leaderboard(market: Optional[str] = None, user: str = Depends(require_auth)):
    """Per-symbol win/loss stats — all-time."""
    conn = get_conn()
    c    = conn.cursor()
    where = ""
    if market == 'india':
        where = "AND (source='paper' OR source IS NULL)"
    elif market == 'us':
        where = "AND source='us_paper'"
    elif market == 'crypto':
        where = "AND source='crypto_paper'"

    c.execute(f"""
        SELECT symbol,
               COUNT(*) as trades,
               SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END) as wins,
               ROUND(SUM(pnl), 4) as total_pnl,
               ROUND(AVG(pnl), 4) as avg_pnl,
               ROUND(MAX(pnl), 4) as best,
               ROUND(MIN(pnl), 4) as worst
        FROM trades
        WHERE status='closed' {where}
        GROUP BY symbol
        ORDER BY total_pnl DESC
    """)
    cols = ['symbol','trades','wins','total_pnl','avg_pnl','best','worst']
    rows = [dict(zip(cols, r)) for r in c.fetchall()]
    for r in rows:
        r['losses'] = r['trades'] - r['wins']
        r['win_rate'] = round(r['wins'] / r['trades'] * 100, 1) if r['trades'] > 0 else 0
    conn.close()
    return {"leaderboard": rows}


@app.get("/api/stats/hourly")
def hourly_pnl(market: Optional[str] = None, user: str = Depends(require_auth)):
    """P&L grouped by hour of day — all-time."""
    conn = get_conn()
    c    = conn.cursor()
    where = ""
    if market == 'india':
        where = "AND (source='paper' OR source IS NULL)"
    elif market == 'us':
        where = "AND source='us_paper'"
    elif market == 'crypto':
        where = "AND source='crypto_paper'"

    c.execute(f"""
        SELECT CAST(strftime('%H', exit_time) AS INTEGER) as hour,
               COUNT(*) as trades,
               ROUND(SUM(pnl), 4) as total_pnl,
               SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END) as wins
        FROM trades
        WHERE status='closed' AND exit_time IS NOT NULL {where}
        GROUP BY hour
        ORDER BY hour
    """)
    cols = ['hour','trades','total_pnl','wins']
    rows = [dict(zip(cols, r)) for r in c.fetchall()]
    conn.close()
    return {"hourly": rows}


# ── Health ────────────────────────────────────────────────────────────────────
@app.get("/api/health")
def health():
    return {"status": "ok", "time": datetime.now(_IST).isoformat()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("portal.app:app", host="0.0.0.0", port=8080, reload=True)
