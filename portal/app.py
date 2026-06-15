"""
AngelBot Management Portal — FastAPI backend
============================================
Provides REST API + serves the web UI.
Run: uvicorn portal.app:app --host 0.0.0.0 --port 8080 --reload

Default admin credentials:  admin / AngelBot@1234
Change via PORTAL_PASS in .env (sets initial admin password on first run)
"""

import os, sys, json, subprocess, hashlib, hmac, secrets
from datetime import datetime, timezone, timedelta
from hashlib import sha256
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from fastapi import FastAPI, HTTPException, Depends, status, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel
from typing import Optional, List

from data.database import get_conn, init_db

app = FastAPI(title="AngelBot Portal", version="1.0.0")

# ── Auth ──────────────────────────────────────────────────────────────────────
_security = HTTPBasic()

def _hash_password(pw: str) -> str:
    return sha256(pw.encode()).hexdigest()

def require_auth(credentials: HTTPBasicCredentials = Depends(_security)):
    """Authenticate against the portal_users table in SQL Server."""
    username = credentials.username
    pw_hash  = _hash_password(credentials.password)
    conn = get_conn()
    c    = conn.cursor()
    c.execute(
        "SELECT username, role FROM portal_users WHERE username=? AND password_hash=? AND role IS NOT NULL",
        (username, pw_hash)
    )
    row = c.fetchone()
    if not row:
        conn.close()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    # Update last_login
    c.execute(
        "UPDATE portal_users SET last_login=? WHERE username=?",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), username)
    )
    conn.commit()
    conn.close()
    return username

def _require_admin(username: str):
    """Raise 403 if username is not an admin."""
    conn = get_conn()
    c    = conn.cursor()
    c.execute("SELECT role FROM portal_users WHERE username=?", (username,))
    row = c.fetchone()
    conn.close()
    if not row or row[0] != 'admin':
        raise HTTPException(status_code=403, detail="Admin access required.")

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
    """Filter on entry_time using rolling windows — avoids IST/ET midnight-crossing issues."""
    now_ist = datetime.now(_IST)
    fmt = "%Y-%m-%d %H:%M:%S"
    if date_filter == 'today':
        cutoff = (now_ist - timedelta(hours=24)).strftime(fmt)
        return "AND entry_time >= ?", [cutoff]
    elif date_filter == 'yesterday':
        t_from = (now_ist - timedelta(hours=48)).strftime(fmt)
        t_to   = (now_ist - timedelta(hours=24)).strftime(fmt)
        return "AND entry_time >= ? AND entry_time < ?", [t_from, t_to]
    elif date_filter == 'week':
        cutoff = (now_ist - timedelta(days=7)).strftime(fmt)
        return "AND entry_time >= ?", [cutoff]
    elif date_filter == 'month':
        cutoff = (now_ist - timedelta(days=30)).strftime(fmt)
        return "AND entry_time >= ?", [cutoff]
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
                f"WHERE status='closed' AND {source_clause} "
                f"AND TRY_CAST(TRY_CAST(exit_time AS DATETIME2) AS DATE) = ?",
                (today_ist,)
            )
            trades, day_pnl = c.fetchone()
            c.execute(
                f"SELECT COUNT(*) FROM trades WHERE status='closed' AND {source_clause} AND pnl>0 "
                f"AND TRY_CAST(TRY_CAST(exit_time AS DATETIME2) AS DATE) = ?",
                (today_ist,)
            )
            wins = c.fetchone()[0]

        c.execute(f"SELECT COUNT(*) FROM trades WHERE status='open' AND {source_clause}")
        open_pos = c.fetchone()[0]
        c.execute(f"SELECT COALESCE(SUM(pnl),0) FROM trades WHERE status='closed' AND {source_clause}")
        total_pnl = c.fetchone()[0]
        c.execute(f"SELECT COALESCE(SUM(capital_used),0) FROM trades WHERE status='open' AND {source_clause}")
        deployed = c.fetchone()[0] or 0.0
        return {
            "today_trades": trades, "today_pnl": round(day_pnl or 0, dec),
            "today_wins": wins, "today_losses": (trades or 0) - (wins or 0),
            "open_positions": open_pos, "total_pnl": round(total_pnl or 0, dec),
            "deployed": round(deployed, dec),
            "currency": cur_sym,
        }

    import config as _cfg
    india  = _market_summary("(source='paper' OR source IS NULL)", "₹", 2)
    india["capital"]   = _cfg.CAPITAL
    india["balance"]   = round(_cfg.CAPITAL + india["total_pnl"] - india["deployed"], 2)

    us     = _market_summary("source='us_paper'",  "$", 2)
    us["capital"]      = _cfg.US_CAPITAL
    us["balance"]      = round(_cfg.US_CAPITAL + us["total_pnl"] - us["deployed"], 2)

    crypto = _market_summary("source='crypto_paper'", "$", 4)
    crypto["capital"]  = _cfg.CRYPTO_CAPITAL
    crypto["balance"]  = round(_cfg.CRYPTO_CAPITAL + crypto["total_pnl"] - crypto["deployed"], 4)

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
    sort_by: Optional[str] = None,
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

    _sort_map = {
        'date_desc': 'entry_time DESC',
        'date_asc':  'entry_time ASC',
        'pnl_desc':  'pnl DESC',
        'pnl_asc':   'pnl ASC',
        'sym_asc':   'symbol ASC',
    }
    order_clause = _sort_map.get(sort_by or '', 'id DESC')

    sql = "SELECT id,symbol,entry_time,exit_time,entry_price,exit_price,quantity,capital_used,pnl,pnl_pct,exit_reason,status,source FROM trades"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY {order_clause} OFFSET ? ROWS FETCH NEXT ? ROWS ONLY"
    params += [offset, limit]

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


# ── Force Sell ────────────────────────────────────────────────────────────────

def _get_live_price_for_source(symbol: str, source: str) -> Optional[float]:
    """Fetch best current price. Always returns float or None — never raises."""
    import math
    try:
        if source == 'us_paper':
            from data.alpaca_client import get_us_live_price
            price = get_us_live_price(symbol)
        elif source == 'crypto_paper':
            import yfinance as yf
            df = yf.Ticker(symbol + '-USD').history(period='1d', interval='1m')
            if df.empty:
                df = yf.Ticker(symbol).history(period='1d', interval='1m')
            price = float(df['Close'].iloc[-1]) if not df.empty else None
        else:
            import yfinance as yf
            sym = symbol if symbol.endswith('.NS') else symbol + '.NS'
            df  = yf.Ticker(sym).history(period='1d', interval='1m')
            price = float(df['Close'].iloc[-1]) if not df.empty else None
    except Exception:
        return None
    if price is None:
        return None
    try:
        f = float(price)
        if math.isnan(f) or f <= 0:
            return None
        return round(f, 6)
    except Exception:
        return None


@app.post("/api/positions/force_sell")
def force_sell(trade_id: int, user: str = Depends(require_auth)):
    """Force-close an open position at current market price. Pass trade_id as query param."""
    conn = get_conn()
    c    = conn.cursor()
    c.execute(
        "SELECT id, symbol, source, entry_price, quantity, status FROM trades WHERE id=?",
        (trade_id,)
    )
    row = c.fetchone()
    if not row:
        c.execute("SELECT COUNT(*) FROM trades WHERE status='open'")
        open_count = c.fetchone()[0]
        conn.close()
        raise HTTPException(404, f"Trade id={trade_id} not found. Open trades in DB: {open_count}")
    tid, symbol, source, entry_price, quantity, trade_status = row
    if trade_status == 'closed':
        conn.close()
        raise HTTPException(400, f"{symbol} (id={tid}) is already closed.")

    if not entry_price or entry_price <= 0:
        conn.close()
        raise HTTPException(400, f"Invalid entry_price ({entry_price}) for trade {tid}.")

    price = _get_live_price_for_source(symbol, source or 'paper')
    if price is None:
        conn.close()
        raise HTTPException(502, f"Could not fetch live price for {symbol}. Market may be closed — try again later.")

    pnl     = round((price - entry_price) * quantity, 4)
    pnl_pct = round(((price - entry_price) / entry_price) * 100, 2)
    now     = datetime.now(_IST).strftime("%Y-%m-%d %H:%M:%S")

    c.execute(
        "UPDATE trades SET exit_time=?, exit_price=?, pnl=?, pnl_pct=?, exit_reason=?, status=? WHERE id=?",
        (now, price, pnl, pnl_pct, 'force_sell', 'closed', tid)
    )
    conn.commit()
    conn.close()
    return {"ok": True, "id": tid, "symbol": symbol, "exit_price": price, "pnl": pnl, "pnl_pct": pnl_pct}


@app.post("/api/positions/cancel_all")
def cancel_all_open(market: Optional[str] = None, user: str = Depends(require_auth)):
    """Mark all open positions as cancelled — no live price needed."""
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
    now = datetime.now(_IST).strftime("%Y-%m-%d %H:%M:%S")
    c.execute(
        f"UPDATE trades SET status='closed', exit_reason='cancelled', exit_time=?, pnl=0, pnl_pct=0 "
        f"WHERE status='open' AND {where}",
        (now,)
    )
    affected = c.rowcount
    conn.commit()
    conn.close()
    return {"ok": True, "cancelled": affected}


@app.post("/api/positions/force_sell_all")
def force_sell_all(market: Optional[str] = None, user: str = Depends(require_auth)):
    """Force-close all open positions (optionally filtered by market)."""
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
    c.execute(f"SELECT id, symbol, source, entry_price, quantity FROM trades WHERE status='open' AND {where}")
    rows = c.fetchall()
    conn.close()

    results = []
    for tid, symbol, source, entry_price, quantity in rows:
        try:
            price = _get_live_price_for_source(symbol, source or 'paper')
            if price is None or not entry_price or entry_price <= 0:
                results.append({"id": tid, "symbol": symbol, "ok": False, "error": "price unavailable"})
                continue
            pnl     = round((price - entry_price) * quantity, 4)
            pnl_pct = round(((price - entry_price) / entry_price) * 100, 2)
            now     = datetime.now(_IST).strftime("%Y-%m-%d %H:%M:%S")
            conn2 = get_conn()
            c2    = conn2.cursor()
            c2.execute(
                "UPDATE trades SET exit_time=?, exit_price=?, pnl=?, pnl_pct=?, exit_reason=?, status=? WHERE id=? AND status='open'",
                (now, price, pnl, pnl_pct, 'force_sell', 'closed', tid)
            )
            conn2.commit()
            conn2.close()
            results.append({"id": tid, "symbol": symbol, "ok": True, "exit_price": price, "pnl": pnl})
        except Exception as e:
            results.append({"id": tid, "symbol": symbol, "ok": False, "error": str(e)})

    sold = sum(1 for r in results if r.get('ok'))
    failed = len(results) - sold
    return {"sold": sold, "failed": failed, "results": results}


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
        'US_SCALP_TARGET_PCT', 'US_SCALP_SL_PCT', 'US_MAX_DAILY_LOSS_PCT',
        'US_MAX_DEPLOYED_PCT', 'US_PEAK_DRAWDOWN_PCT',
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
    """Change own username and/or password. Updates the portal_users table."""
    if len(body.new_password) < 8:
        raise HTTPException(400, "New password must be at least 8 characters.")
    current_hash = _hash_password(body.current_password)
    conn = get_conn()
    c    = conn.cursor()
    c.execute("SELECT id FROM portal_users WHERE username=? AND password_hash=?", (user, current_hash))
    if not c.fetchone():
        conn.close()
        raise HTTPException(400, "Current password is incorrect.")
    new_hash = _hash_password(body.new_password.strip())
    new_username = body.new_username.strip()
    c.execute(
        "UPDATE portal_users SET username=?, password_hash=? WHERE username=?",
        (new_username, new_hash, user)
    )
    conn.commit()
    conn.close()
    return {"ok": True, "message": "Credentials updated. Re-login with your new password."}


# ── User management ───────────────────────────────────────────────────────────
class NewUser(BaseModel):
    username: str
    password: str
    role: str = "viewer"

class UserPatch(BaseModel):
    role: Optional[str] = None
    new_password: Optional[str] = None

@app.get("/api/users")
def list_users(user: str = Depends(require_auth)):
    """Return all portal users. Admin only."""
    _require_admin(user)
    conn = get_conn()
    c    = conn.cursor()
    c.execute("SELECT id, username, role, created_at, last_login FROM portal_users ORDER BY id")
    cols = ['id', 'username', 'role', 'created_at', 'last_login']
    rows = [dict(zip(cols, r)) for r in c.fetchall()]
    conn.close()
    return {"users": rows}

@app.post("/api/users")
def create_user(body: NewUser, user: str = Depends(require_auth)):
    """Create a new portal user. Admin only."""
    _require_admin(user)
    if body.role not in ('admin', 'viewer'):
        raise HTTPException(400, "Role must be 'admin' or 'viewer'.")
    if len(body.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters.")
    pw_hash = _hash_password(body.password)
    conn = get_conn()
    c    = conn.cursor()
    try:
        c.execute(
            "INSERT INTO portal_users (username, password_hash, role, created_at) VALUES (?,?,?,?)",
            (body.username.strip(), pw_hash, body.role, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        conn.commit()
    except Exception as e:
        conn.close()
        raise HTTPException(400, f"Could not create user: {e}")
    conn.close()
    return {"ok": True, "message": f"User '{body.username}' created."}

@app.delete("/api/users/{username}")
def delete_user(username: str, user: str = Depends(require_auth)):
    """Delete a portal user. Admin only. Cannot delete own account."""
    _require_admin(user)
    if username == user:
        raise HTTPException(400, "Cannot delete your own account.")
    conn = get_conn()
    c    = conn.cursor()
    c.execute("DELETE FROM portal_users WHERE username=?", (username,))
    if c.rowcount == 0:
        conn.close()
        raise HTTPException(404, f"User '{username}' not found.")
    conn.commit()
    conn.close()
    return {"ok": True, "message": f"User '{username}' deleted."}

@app.patch("/api/users/{username}")
def patch_user(username: str, body: UserPatch, user: str = Depends(require_auth)):
    """Change role or reset password for a user. Admin only."""
    _require_admin(user)
    conn = get_conn()
    c    = conn.cursor()
    if body.role is not None:
        if body.role not in ('admin', 'viewer'):
            conn.close()
            raise HTTPException(400, "Role must be 'admin' or 'viewer'.")
        c.execute("UPDATE portal_users SET role=? WHERE username=?", (body.role, username))
    if body.new_password is not None:
        if len(body.new_password) < 8:
            conn.close()
            raise HTTPException(400, "Password must be at least 8 characters.")
        pw_hash = _hash_password(body.new_password)
        c.execute("UPDATE portal_users SET password_hash=? WHERE username=?", (pw_hash, username))
    conn.commit()
    conn.close()
    return {"ok": True, "message": f"User '{username}' updated."}


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


def _resolve_log_path(market: str) -> Path:
    """Find the most recent log file for a market — falls back if today's file not yet created."""
    log_dir = Path(__file__).parent.parent / 'logs'
    if market == 'watchdog':
        return log_dir / 'watchdog.log'
    if market == 'portal':
        return log_dir / 'AngelBot-Portal.log'
    # Try today then the 3 previous days (service may have started before midnight)
    for days_ago in range(4):
        d = (datetime.now() - timedelta(days=days_ago)).strftime('%Y%m%d')
        p = log_dir / f'{market}_{d}.log'
        if p.exists():
            return p
    return log_dir / f'{market}_{datetime.now().strftime("%Y%m%d")}.log'


# ── Logs (historical) ─────────────────────────────────────────────────────────
@app.get("/api/logs/{market}")
def get_log(market: str, lines: int = 200, user: str = Depends(require_auth)):
    if market not in ('india', 'us', 'crypto', 'watchdog', 'portal'):
        raise HTTPException(400, "Invalid market. Use: india, us, crypto, watchdog, portal")
    log_path = _resolve_log_path(market)
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
    log_path = _resolve_log_path(market)
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
    """Query a Windows service status via sc.exe."""
    try:
        result = subprocess.run(
            ['sc', 'query', name],
            capture_output=True, text=True, timeout=10
        )
        out = result.stdout.upper()
        if 'RUNNING' in out:
            return 'running'
        elif 'PAUSED' in out:
            return 'paused'
        elif 'STOPPED' in out:
            return 'stopped'
        elif 'START_PENDING' in out or 'STOP_PENDING' in out:
            return 'pending'
        elif result.returncode != 0 or 'FAILED' in out or 'does not exist' in result.stdout:
            return 'not_installed'
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


@app.post("/api/services/{name}/start")
def start_service(name: str, user: str = Depends(require_auth)):
    """Start a stopped Windows service."""
    if name not in _SERVICE_NAMES:
        raise HTTPException(400, f"Unknown service '{name}'.")
    try:
        r = subprocess.run(['net', 'start', name], capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            raise HTTPException(500, f"Failed to start {name}: {(r.stderr or r.stdout).strip()}")
        return {"ok": True, "name": name, "message": f"{name} started."}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/services/{name}/stop")
def stop_service(name: str, user: str = Depends(require_auth)):
    """Stop a running Windows service."""
    if name not in _SERVICE_NAMES:
        raise HTTPException(400, f"Unknown service '{name}'.")
    try:
        r = subprocess.run(['net', 'stop', name], capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            raise HTTPException(500, f"Failed to stop {name}: {(r.stderr or r.stdout).strip()}")
        return {"ok": True, "name": name, "message": f"{name} stopped."}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/services/{name}/restart")
def restart_service(name: str, user: str = Depends(require_auth)):
    """Stop then start a named Windows service."""
    if name not in _SERVICE_NAMES:
        raise HTTPException(400, f"Unknown service '{name}'.")
    try:
        if name == "AngelBot-Portal":
            # Restarting own process synchronously would kill this response mid-flight.
            # Spawn a detached cmd.exe that waits 5s then issues the restart.
            DETACHED_PROCESS      = 0x00000008
            CREATE_NEW_PROC_GROUP = 0x00000200
            subprocess.Popen(
                ['cmd', '/c', f'timeout /t 5 /nobreak >nul & nssm restart {name}'],
                creationflags=DETACHED_PROCESS | CREATE_NEW_PROC_GROUP,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return {"ok": True, "name": name, "message": f"{name} restarting in ~5s — page will reload."}
        subprocess.run(['nssm', 'stop', name], capture_output=True, text=True,
                       encoding='utf-8', errors='replace', timeout=30)
        start = subprocess.run(['nssm', 'start', name], capture_output=True, text=True,
                               encoding='utf-8', errors='replace', timeout=30)
        if start.returncode != 0:
            raise HTTPException(500, f"Failed to start {name}: {(start.stderr or start.stdout).strip()}")
        return {"ok": True, "name": name, "message": f"{name} restarted."}
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
    """Pull latest code from GitHub and restart all workers, then the portal.
    Streams NDJSON — one line per step — so the browser sees live progress."""
    _BOT_DIR = str(Path(__file__).parent.parent)

    def _stream():
        def emit(step, ok, detail=""):
            yield json.dumps({"step": step, "ok": ok, "detail": detail}) + "\n"

        # 1. git pull
        try:
            r = subprocess.run(
                ['git', 'pull', 'origin', 'main'],
                capture_output=True, text=True, timeout=60, cwd=_BOT_DIR
            )
            ok     = r.returncode == 0
            detail = (r.stdout.strip().splitlines()[-1] if ok and r.stdout.strip() else r.stderr.strip()) or "up to date"
            yield from emit("git pull", ok, detail)
            if not ok:
                return
        except Exception as e:
            yield from emit("git pull", False, str(e))
            return

        # 2. Restart workers: explicit stop → poll STOPPED → start → poll RUNNING.
        # Emit ok=None progress lines so the browser updates the row in-place while waiting.
        import time
        for svc in ["AngelBot-India", "AngelBot-US", "AngelBot-Crypto"]:
            try:
                # -- Stop phase --
                yield from emit(f"restart {svc}", None, "stopping…")
                subprocess.run(['nssm', 'stop', svc],
                               capture_output=True, text=True, encoding='utf-8',
                               errors='replace', timeout=30)
                deadline = time.time() + 30
                while time.time() < deadline:
                    if _sc_status(svc) in ('stopped', 'not_installed'):
                        break
                    time.sleep(2)

                # -- Start phase --
                yield from emit(f"restart {svc}", None, "starting…")
                subprocess.run(['nssm', 'start', svc],
                               capture_output=True, text=True, encoding='utf-8',
                               errors='replace', timeout=30)

                # Poll until RUNNING — up to 120 s; emit progress every 10 s so screen stays alive
                deadline = time.time() + 120
                waited = 0
                while time.time() < deadline:
                    st = _sc_status(svc)
                    if st == 'running':
                        break
                    time.sleep(3)
                    waited += 3
                    if waited % 10 == 0:
                        yield from emit(f"restart {svc}", None, f"waiting for RUNNING… ({waited}s)")

                final = _sc_status(svc)
                ok = final == 'running'
                detail = "running" if ok else f"status still '{final}' after 120 s"
                yield from emit(f"restart {svc}", ok, detail)
            except Exception as e:
                yield from emit(f"restart {svc}", False, str(e))

        # 3. Portal restarts via detached cmd.exe — 30s delay lets this response fully reach browser
        # DETACHED_PROCESS=0x8, CREATE_NEW_PROCESS_GROUP=0x200 (NOT 0x10 which is CREATE_NEW_CONSOLE
        # and conflicts with DETACHED_PROCESS causing WinError 87)
        try:
            DETACHED_PROCESS      = 0x00000008
            CREATE_NEW_PROC_GROUP = 0x00000200
            subprocess.Popen(
                ['cmd', '/c',
                 'timeout /t 30 /nobreak >nul & nssm restart AngelBot-Portal'],
                creationflags=DETACHED_PROCESS | CREATE_NEW_PROC_GROUP,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            yield from emit("restart portal", True, "portal restarts in ~30s — page will auto-reconnect")
        except Exception as e:
            yield from emit("restart portal", False, str(e))

    return StreamingResponse(_stream(), media_type="text/plain")


# ── Credentials (API keys / tokens stored in .env) ────────────────────────────
_CREDENTIAL_KEYS = [
    'ANGEL_API_KEY', 'ANGEL_SECRET', 'ANGEL_CLIENT_ID', 'ANGEL_PIN', 'ANGEL_TOTP_SECRET',
    'ALPACA_KEY', 'ALPACA_SECRET',
    'BINANCE_KEY', 'BINANCE_SECRET',
    'TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID',
    'PORTAL_PASS', 'SQL_SA_PASS',
]

@app.get("/api/credentials")
def get_credentials(user: str = Depends(require_auth)):
    _require_admin(user)
    env_path = Path(__file__).parent.parent / '.env'
    env_vals = {}
    if env_path.exists():
        for line in env_path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if '=' in line and not line.startswith('#'):
                k, _, v = line.partition('=')
                env_vals[k.strip()] = v.strip()
    return {key: env_vals.get(key, '') for key in _CREDENTIAL_KEYS}

@app.post("/api/credentials")
def update_credential(update: ConfigUpdate, user: str = Depends(require_auth)):
    _require_admin(user)
    env_path = Path(__file__).parent.parent / '.env'
    key   = update.key.strip()
    value = update.value.strip()
    if key not in _CREDENTIAL_KEYS:
        raise HTTPException(400, f"'{key}' is not an editable credential.")
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
    return {"ok": True, "message": f"{key} saved. Restart workers to apply."}


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
        SELECT DATEPART(hour, TRY_CAST(exit_time AS DATETIME2)) as hour,
               COUNT(*) as trades,
               ROUND(SUM(pnl), 4) as total_pnl,
               SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END) as wins
        FROM trades
        WHERE status='closed' AND exit_time IS NOT NULL {where}
        GROUP BY DATEPART(hour, TRY_CAST(exit_time AS DATETIME2))
        ORDER BY DATEPART(hour, TRY_CAST(exit_time AS DATETIME2))
    """)
    cols = ['hour','trades','total_pnl','wins']
    rows = [dict(zip(cols, r)) for r in c.fetchall()]
    conn.close()
    return {"hourly": rows}


# ── Debug ─────────────────────────────────────────────────────────────────────
@app.get("/api/debug/trades")
def debug_trades(user: str = Depends(require_auth)):
    """Return raw open trade IDs + timestamps for debugging force-sell issues."""
    conn = get_conn()
    c    = conn.cursor()
    c.execute("SELECT id, symbol, source, entry_time, status FROM trades ORDER BY id DESC OFFSET 0 ROWS FETCH NEXT 50 ROWS ONLY")
    cols = ['id', 'symbol', 'source', 'entry_time', 'status']
    rows = [dict(zip(cols, r)) for r in c.fetchall()]
    c.execute("SELECT COUNT(*) FROM trades WHERE status='open'")
    open_count = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM trades")
    total_count = c.fetchone()[0]
    conn.close()
    import sys
    return {
        "open_count": open_count,
        "total_count": total_count,
        "portal_version": "173b926",
        "last_50_trades": rows
    }


# ── Health ────────────────────────────────────────────────────────────────────
@app.get("/api/health")
def health():
    return {"status": "ok", "time": datetime.now(_IST).isoformat(), "version": "dec5594"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("portal.app:app", host="0.0.0.0", port=8080, reload=True)
