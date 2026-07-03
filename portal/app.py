"""
AngelBot Management Portal — FastAPI backend
============================================
Provides REST API + serves the web UI.
Run: uvicorn portal.app:app --host 0.0.0.0 --port 8080 --reload

Default admin credentials:  admin / AngelBot@1234
Change via PORTAL_PASS in .env (sets initial admin password on first run)
"""

import os, sys, json, subprocess, hashlib, hmac, secrets, asyncio, re as _re
from datetime import datetime, timezone, timedelta
from hashlib import sha256
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from fastapi import FastAPI, HTTPException, Depends, status, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import base64
from pydantic import BaseModel
from typing import Optional, List

from data.database import get_conn, init_db

app = FastAPI(title="AngelBot Portal", version="1.0.0")

_BOT_DIR = Path(__file__).parent.parent  # C:\AngelBot in production


def _ensure_settings_table():
    """Create bot_settings table in SQL Server if it doesn't already exist."""
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("""
            IF NOT EXISTS (
                SELECT * FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME='bot_settings'
            )
            CREATE TABLE bot_settings (
                setting_key   NVARCHAR(200) NOT NULL,
                setting_value NVARCHAR(MAX),
                updated_at    DATETIME2     DEFAULT GETDATE(),
                updated_by    NVARCHAR(100) DEFAULT 'portal',
                CONSTRAINT PK_bot_settings PRIMARY KEY (setting_key)
            )
        """)
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[Portal] bot_settings init: {e}")


def _sync_db_to_env():
    """Restore DB-stored settings to .env on every portal start.
    Workers pick up changes after their next restart — even if .env was reset by git or setup."""
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT setting_key, setting_value FROM bot_settings")
        rows = c.fetchall()
        conn.close()
        if not rows:
            return
        env_path = _BOT_DIR / '.env'
        lines = env_path.read_text(encoding='utf-8').splitlines() if env_path.exists() else []
        for key, value in rows:
            if value is None:
                continue
            for i, line in enumerate(lines):
                if line.split('#')[0].strip().startswith(f"{key}="):
                    lines[i] = f"{key}={value}"
                    break
            else:
                lines.append(f"{key}={value}")
        env_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        print(f"[Portal] Synced {len(rows)} setting(s) from DB → .env")
    except Exception as e:
        print(f"[Portal] DB→.env sync: {e}")


@app.on_event("startup")
async def _on_startup():
    _ensure_settings_table()
    _sync_db_to_env()


# ── Auth ──────────────────────────────────────────────────────────────────────
# Manual Basic-auth parsing — intentionally omits WWW-Authenticate: Basic so
# the browser never shows its native credential dialog.

def _hash_password(pw: str) -> str:
    return sha256(pw.encode()).hexdigest()

def require_auth(request: Request):
    """Authenticate against the portal_users table.
    Parses Authorization: Basic header manually so browsers never see
    WWW-Authenticate: Basic and never pop up their native login dialog.
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Basic "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    try:
        decoded  = base64.b64decode(auth[6:]).decode("utf-8", errors="replace")
        username, _, password = decoded.partition(":")
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    pw_hash = _hash_password(password)
    conn = get_conn()
    c    = conn.cursor()
    c.execute(
        "SELECT username, role FROM portal_users WHERE username=? AND password_hash=? AND role IS NOT NULL",
        (username, pw_hash)
    )
    row = c.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
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

def _get_user_id(username: str) -> int:
    """Return the integer user_id for a username (admin always = 1)."""
    conn = get_conn()
    c    = conn.cursor()
    c.execute("SELECT id FROM portal_users WHERE username=?", (username,))
    row  = c.fetchone()
    conn.close()
    return row[0] if row else 1

_IST = timezone(timedelta(hours=5, minutes=30))

# ── Static files ──────────────────────────────────────────────────────────────
_STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


# ── HTML UI — serve index.html for root and all SPA page routes ──────────────
_SPA_PAGES = {
    'dashboard', 'positions', 'trades', 'leaderboard',
    'settings', 'apikeys', 'users', 'logs', 'monitor', 'symbols', 'help', 'portals',
}

@app.get("/", response_class=HTMLResponse)
async def root():
    index = _STATIC_DIR / "index.html"
    return HTMLResponse(content=index.read_text(encoding="utf-8"))

@app.get("/{page_name}", response_class=HTMLResponse)
async def spa_page(page_name: str):
    """Serve the SPA shell for any known page so browser history/deep-links work."""
    if page_name in _SPA_PAGES:
        index = _STATIC_DIR / "index.html"
        return HTMLResponse(content=index.read_text(encoding="utf-8"))
    raise HTTPException(status_code=404, detail="Not found")


# ── Dashboard ─────────────────────────────────────────────────────────────────
def _build_date_clause(date_filter: Optional[str], col: str = "entry_time") -> tuple:
    """Filter on `col` using IST calendar-day boundaries.
    Use col='exit_time' for closed-trade period counts (dashboard P&L).
    Use col='entry_time' for open-position / trade-history filtering.
    Rolling 24h windows break US trades: US session runs 7:30 PM–1:25 AM IST,
    so 'yesterday' trades entered 7:30 PM–midnight IST land outside a rolling 24–48h window
    when checked the next morning. Midnight-anchored boundaries capture them correctly.
    """
    now_ist = datetime.now(_IST)
    today_start = now_ist.replace(hour=0, minute=0, second=0, microsecond=0)
    fmt = "%Y-%m-%d %H:%M:%S"
    if date_filter == 'today':
        return f"AND {col} >= ?", [today_start.strftime(fmt)]
    elif date_filter == 'yesterday':
        yesterday_start = (today_start - timedelta(days=1)).strftime(fmt)
        return f"AND {col} >= ? AND {col} < ?", [yesterday_start, today_start.strftime(fmt)]
    elif date_filter == 'week':
        return f"AND {col} >= ?", [(today_start - timedelta(days=7)).strftime(fmt)]
    elif date_filter == 'month':
        return f"AND {col} >= ?", [(today_start - timedelta(days=30)).strftime(fmt)]
    return "", []


def _build_us_date_clause(date_filter: Optional[str]) -> tuple:
    """Build date clause for US closed trades using Eastern Time (ET) calendar day.
    Stored exit_time is IST. IST→EDT = -570 min, IST→EST = -630 min.
    Uses SQL: CAST(DATEADD(minute, offset, TRY_CAST(exit_time AS DATETIME2)) AS DATE)
    so 'today' means the current US trading day regardless of IST midnight boundaries.
    """
    utc_now = datetime.now(timezone.utc)
    y = utc_now.year
    # DST starts 2nd Sunday of March at 2 AM UTC, ends 1st Sunday of Nov at 2 AM UTC
    mar = datetime(y, 3, 1, tzinfo=timezone.utc)
    nov = datetime(y, 11, 1, tzinfo=timezone.utc)
    dst_start = mar + timedelta(days=(6 - mar.weekday()) % 7) + timedelta(weeks=1, hours=7)
    dst_end   = nov + timedelta(days=(6 - nov.weekday()) % 7) + timedelta(hours=6)
    is_dst = dst_start <= utc_now < dst_end
    ist_to_et = -570 if is_dst else -630  # IST→ET offset in minutes

    # Compute current ET date and neighbours
    now_et       = utc_now + timedelta(minutes=(60 * (-4 if is_dst else -5)))
    today_et     = now_et.strftime("%Y-%m-%d")
    yesterday_et = (now_et - timedelta(days=1)).strftime("%Y-%m-%d")
    week_et      = (now_et - timedelta(days=7)).strftime("%Y-%m-%d")
    month_et     = (now_et - timedelta(days=30)).strftime("%Y-%m-%d")

    conv = f"CAST(DATEADD(minute,{ist_to_et},TRY_CAST(exit_time AS DATETIME2)) AS DATE)"

    if date_filter == 'today':
        return f"AND {conv} = ?", [today_et]
    elif date_filter == 'yesterday':
        return f"AND {conv} = ?", [yesterday_et]
    elif date_filter == 'week':
        return f"AND {conv} >= ?", [week_et]
    elif date_filter == 'month':
        return f"AND {conv} >= ?", [month_et]
    return "", []


_watchlist_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'watchlist.json')

class SymbolsUpdate(BaseModel):
    india: List[str] = []
    us: List[str] = []
    crypto: List[str] = []

@app.get("/api/symbols")
def get_symbols(user: str = Depends(require_auth)):
    if os.path.exists(_watchlist_path):
        with open(_watchlist_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"india": [], "us": [], "crypto": []}

@app.post("/api/symbols")
def save_symbols(req: SymbolsUpdate, user: str = Depends(require_auth)):
    data = {"india": req.india, "us": req.us, "crypto": req.crypto}
    os.makedirs(os.path.dirname(_watchlist_path), exist_ok=True)
    with open(_watchlist_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
    return {"ok": True}


@app.get("/api/dashboard")
def dashboard(date_filter: Optional[str] = None, user: str = Depends(require_auth)):
    conn = get_conn()
    c    = conn.cursor()
    uid  = _get_user_id(user)
    today_ist = datetime.now(_IST).strftime("%Y-%m-%d")

    # India/Crypto: filter closed trades by exit_time using IST calendar day
    ist_closed_sql, ist_closed_params = _build_date_clause(date_filter, col="exit_time")
    # US: filter closed trades by exit_time converted to ET calendar day
    us_closed_sql, us_closed_params = _build_us_date_clause(date_filter)

    def _market_summary(source_clause, cur_sym, dec, period_sql, period_params):
        # Period data — caller supplies the right date clause for this market's timezone
        c.execute(
            f"SELECT COUNT(*), COALESCE(SUM(pnl),0), "
            f"COALESCE(SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END),0) FROM trades "
            f"WHERE status='closed' AND {source_clause} AND user_id=? {period_sql}",
            [uid] + period_params
        )
        trades, day_pnl, wins = c.fetchone()

        # All-time totals (always, regardless of filter) — include profit/loss split
        c.execute(
            f"SELECT COALESCE(SUM(pnl),0), COUNT(*), "
            f"COALESCE(SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END),0), "
            f"COALESCE(SUM(CASE WHEN pnl>0 THEN pnl ELSE 0 END),0), "
            f"COALESCE(SUM(CASE WHEN pnl<0 THEN pnl ELSE 0 END),0) FROM trades "
            f"WHERE status='closed' AND {source_clause} AND user_id=?",
            [uid]
        )
        total_pnl, total_trades, total_wins, total_profit, total_loss = c.fetchone()

        c.execute(f"SELECT COUNT(*) FROM trades WHERE status='open' AND {source_clause} AND user_id=?", (uid,))
        open_pos = c.fetchone()[0]
        c.execute(f"SELECT COALESCE(SUM(capital_used),0) FROM trades WHERE status='open' AND {source_clause} AND user_id=?", (uid,))
        deployed = c.fetchone()[0] or 0.0
        return {
            "today_trades": trades or 0, "today_pnl": round(day_pnl or 0, dec),
            "today_wins": wins or 0, "today_losses": (trades or 0) - (wins or 0),
            "open_positions": open_pos, "total_pnl": round(total_pnl or 0, dec),
            "total_trades": total_trades or 0, "total_wins": total_wins or 0,
            "total_profit": round(total_profit or 0, dec),
            "total_loss":   round(total_loss   or 0, dec),
            "deployed": round(deployed, dec),
            "currency": cur_sym,
        }

    # Load per-user capital settings
    c.execute(
        "SELECT capital_india, capital_us, capital_crypto, paused "
        "FROM user_config WHERE user_id=?", (uid,)
    )
    cfg_row = c.fetchone()
    if cfg_row:
        cap_india, cap_us, cap_crypto, user_paused = cfg_row
    else:
        import config as _cfg
        cap_india, cap_us, cap_crypto = _cfg.CAPITAL, _cfg.US_CAPITAL, _cfg.CRYPTO_CAPITAL
        user_paused = 0

    india  = _market_summary("(source='paper' OR source IS NULL)", "₹", 2, ist_closed_sql, ist_closed_params)
    india["capital"]   = cap_india
    india["balance"]   = round(cap_india + india["total_pnl"] - india["deployed"], 2)

    us     = _market_summary("source='us_paper'", "$", 2, us_closed_sql, us_closed_params)
    us["capital"]      = cap_us
    us["balance"]      = round(cap_us + us["total_pnl"] - us["deployed"], 2)

    crypto = _market_summary("source='crypto_paper'", "$", 4, ist_closed_sql, ist_closed_params)
    crypto["capital"]  = cap_crypto
    crypto["balance"]  = round(cap_crypto + crypto["total_pnl"] - crypto["deployed"], 4)

    # Per-user bot paused status
    paused = bool(user_paused)

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
    pnl_filter: Optional[str] = None,
    date_filter: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    sort_by: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    user: str = Depends(require_auth)
):
    conn = get_conn()
    c    = conn.cursor()
    uid  = _get_user_id(user)

    where  = ["user_id=?"]
    params = [uid]
    if market == 'india':
        where.append("(source='paper' OR source IS NULL)")
    elif market == 'us':
        where.append("source='us_paper'")
    elif market == 'crypto':
        where.append("source='crypto_paper'")

    if status_filter in ('open', 'closed'):
        where.append("status=?")
        params.append(status_filter)

    if pnl_filter == 'wins':
        where.append("pnl > 0")
    elif pnl_filter == 'losses':
        where.append("pnl < 0")

    # Date filter (preset ranges)
    date_sql, date_params = _build_date_clause(date_filter)
    if date_sql:
        where.append(date_sql.lstrip("AND ").strip())
        params.extend(date_params)

    # Custom date range (YYYY-MM-DD) — used by report generator
    if date_from:
        where.append("entry_time >= ?")
        params.append(date_from + ' 00:00:00')
    if date_to:
        where.append("entry_time <= ?")
        params.append(date_to + ' 23:59:59')

    _sort_map = {
        'date_desc':  'entry_time DESC',
        'date_asc':   'entry_time ASC',
        'pnl_desc':   'pnl DESC',
        'pnl_asc':    'pnl ASC',
        'pnl_pct_desc': 'pnl_pct DESC',
        'pnl_pct_asc':  'pnl_pct ASC',
        'sym_asc':    'symbol ASC',
        'sym_desc':   'symbol DESC',
        'mkt_asc':    'source ASC',
        'mkt_desc':   'source DESC',
        'entry_desc': 'entry_price DESC',
        'entry_asc':  'entry_price ASC',
        'qty_desc':   'quantity DESC',
        'qty_asc':    'quantity ASC',
    }
    order_clause = _sort_map.get(sort_by or '', 'entry_time DESC')

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
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    count_sql = f"SELECT COUNT(*), COALESCE(SUM(pnl),0) FROM trades{where_sql}"
    c.execute(count_sql, count_params)
    row = c.fetchone()
    total = row[0]
    total_pnl = round(row[1] or 0, 4)

    # Per-currency summary for INR / USD split display
    summary_sql = f"""
        SELECT
            CASE WHEN source IN ('us_paper','crypto_paper') THEN 'usd' ELSE 'inr' END AS grp,
            COALESCE(SUM(CASE WHEN pnl>0 THEN pnl ELSE 0 END), 0),
            COALESCE(SUM(CASE WHEN pnl<0 THEN pnl ELSE 0 END), 0),
            COUNT(*)
        FROM trades{where_sql}
        GROUP BY CASE WHEN source IN ('us_paper','crypto_paper') THEN 'usd' ELSE 'inr' END
    """
    c.execute(summary_sql, count_params)
    summary = {'inr': {'total_profit': 0.0, 'total_loss': 0.0, 'trade_count': 0},
               'usd': {'total_profit': 0.0, 'total_loss': 0.0, 'trade_count': 0}}
    for grp, profit, loss, cnt in c.fetchall():
        summary[grp] = {'total_profit': round(profit or 0, 2),
                        'total_loss':   round(loss   or 0, 2),
                        'trade_count':  cnt or 0}

    conn.close()
    return {"trades": rows, "total": total, "total_pnl": total_pnl,
            "summary": summary, "limit": limit, "offset": offset}


# ── Open positions ────────────────────────────────────────────────────────────
@app.get("/api/positions")
def get_positions(market: Optional[str] = None, user: str = Depends(require_auth)):
    conn = get_conn()
    c    = conn.cursor()
    uid  = _get_user_id(user)
    if market == 'india':
        src_where = "(source='paper' OR source IS NULL)"
    elif market == 'us':
        src_where = "source='us_paper'"
    elif market == 'crypto':
        src_where = "source='crypto_paper'"
    else:
        src_where = "1=1"
    c.execute(f"SELECT id,symbol,entry_time,entry_price,quantity,capital_used,stop_loss,target,source "
              f"FROM trades WHERE status='open' AND {src_where} AND user_id=? ORDER BY entry_time DESC",
              (uid,))
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


class PriceItem(BaseModel):
    symbol: str
    source: str = 'paper'

class PricesRequest(BaseModel):
    items: List[PriceItem]

@app.post("/api/prices")
def get_live_prices(req: PricesRequest, user: str = Depends(require_auth)):
    """Batch live-price fetch. Returns {prices: {'SYMBOL|source': float_or_null}}."""
    from concurrent.futures import ThreadPoolExecutor
    def fetch(item):
        price = _get_live_price_for_source(item.symbol, item.source)
        return (item.symbol + '|' + item.source, price)
    results = {}
    if req.items:
        with ThreadPoolExecutor(max_workers=min(len(req.items), 6)) as ex:
            for key, price in ex.map(fetch, req.items):
                results[key] = price
    return {"prices": results}


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
    """Return editable config values — DB values take precedence over config.py defaults."""
    import config as cfg
    editable = [
        'CAPITAL', 'MAX_DAILY_TRADES', 'MAX_DEPLOYED_PCT', 'PEAK_DRAWDOWN_PCT',
        'SCALP_TARGET_PCT', 'SCALP_SL_PCT', 'SL_CONFIRM_POLLS',
        'MAX_DAILY_LOSS_PCT', 'MIN_STOCK_PRICE', 'ENTRY_START_MIN', 'ENTRY_END_MIN',
        'US_CAPITAL', 'US_MAX_DAILY_TRADES', 'US_MIN_STOCK_PRICE',
        'US_MAX_CONCURRENT_POSITIONS',
        'US_SCALP_TARGET_PCT', 'US_SCALP_SL_PCT', 'US_MAX_DAILY_LOSS_PCT',
        'US_MAX_DEPLOYED_PCT', 'US_PEAK_DRAWDOWN_PCT',
        'US_MAX_BUYS_PER_SCAN', 'US_LOSS_BURST_COUNT', 'US_LOSS_BURST_WINDOW',
        'US_LOSS_BURST_COOLDOWN', 'US_CANDLE_CONFIRM_REENTRY',
        'CRYPTO_CAPITAL', 'CRYPTO_MAX_DAILY_TRADES', 'CRYPTO_MAX_CONCURRENT',
        'CRYPTO_MAX_DEPLOYED_PCT', 'CRYPTO_PEAK_DRAWDOWN_PCT', 'CRYPTO_MAX_DAILY_LOSS_PCT',
        'CRYPTO_TARGET_PCT', 'CRYPTO_SL_PCT', 'CRYPTO_BTC_MIN_CHANGE', 'SLIPPAGE_PCT',
        'PAPER_MODE', 'ALPACA_PAPER', 'BINANCE_PAPER',
        'MIN_SIGNAL_SCORE', 'MAX_CONCURRENT_POSITIONS', 'USE_TIME_EXIT', 'MAX_HOLD_MINUTES',
        'USE_PROFIT_TIMER', 'PROFIT_TIMER_MINUTES',
        'USE_LOSS_TIMER', 'LOSS_TIMER_MINUTES',
        'ALLOW_OVERNIGHT',
        'USE_MOOD_FILTER', 'MOOD_FILTER_THRESHOLD', 'USE_SECTOR_CAP', 'MAX_SECTOR_POSITIONS',
        'TELEGRAM_ALERTS_ENABLED','TELEGRAM_ALERT_BUY','TELEGRAM_ALERT_SELL',
        'TELEGRAM_ALERT_DAILY','TELEGRAM_ALERT_ERRORS','TELEGRAM_ALERT_BOT_START',
        'TELEGRAM_ALERT_BURST',
        'TELEGRAM_ALERT_INDIA','TELEGRAM_ALERT_US','TELEGRAM_ALERT_CRYPTO',
        'TELEGRAM_MIN_BUY_CAPITAL','TELEGRAM_MIN_PNL_ALERT','TELEGRAM_ALERT_TUNNEL_URL',
        'WHATSAPP_ALERTS_ENABLED','WHATSAPP_ALERT_BUY','WHATSAPP_ALERT_SELL',
        'WHATSAPP_ALERT_DAILY','WHATSAPP_ALERT_ERRORS','WHATSAPP_ALERT_BOT_START',
        'WHATSAPP_ALERT_BURST',
        'WHATSAPP_ALERT_INDIA','WHATSAPP_ALERT_US','WHATSAPP_ALERT_CRYPTO',
        'WHATSAPP_MIN_BUY_CAPITAL','WHATSAPP_MIN_PNL_ALERT','WHATSAPP_ALERT_TUNNEL_URL',
        'NTFY_TOPIC','NTFY_SERVER','NTFY_TOKEN',
        'NTFY_ALERTS_ENABLED','NTFY_ALERT_BUY','NTFY_ALERT_SELL',
        'NTFY_ALERT_DAILY','NTFY_ALERT_ERRORS','NTFY_ALERT_BOT_START',
        'NTFY_ALERT_BURST',
        'NTFY_ALERT_INDIA','NTFY_ALERT_US','NTFY_ALERT_CRYPTO',
        'NTFY_MIN_BUY_CAPITAL','NTFY_MIN_PNL_ALERT','NTFY_ALERT_TUNNEL_URL',
        'PORTAL_DASH_REFRESH','PORTAL_POS_REFRESH','PORTAL_LB_REFRESH',
        'PORTAL_LOG_REFRESH','PORTAL_MONITOR_REFRESH',
    ]
    # DB values override config.py (DB = user's last saved choice, survives git pulls)
    db = {}
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT setting_key, setting_value FROM bot_settings")
        for row in c.fetchall():
            if row[1] is not None:
                db[row[0]] = row[1]
        conn.close()
    except Exception:
        pass
    result = {}
    for key in editable:
        if key in db:
            result[key] = db[key]
        else:
            val = getattr(cfg, key, None)
            if val is not None:
                result[key] = val
    return result


class ConfigUpdate(BaseModel):
    key: str
    value: str

@app.post("/api/config")
def update_config(update: ConfigUpdate, user: str = Depends(require_auth)):
    """Save a setting to the database (primary) and .env (so workers read it on restart).
    DB storage survives git pulls and .env resets; portal syncs DB→.env on every startup."""
    key   = update.key.strip()
    value = update.value.strip()

    if key in ('PAPER_MODE', 'ALPACA_PAPER', 'BINANCE_PAPER') and value.lower() == 'false':
        raise HTTPException(400, "Cannot disable paper mode via the portal. Edit .env manually.")

    # Primary: save to DB so setting survives git pulls and .env resets
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute(
            "IF EXISTS (SELECT 1 FROM bot_settings WHERE setting_key=?) "
            "  UPDATE bot_settings SET setting_value=?, updated_at=GETDATE(), updated_by=? WHERE setting_key=? "
            "ELSE "
            "  INSERT INTO bot_settings (setting_key, setting_value, updated_by) VALUES (?,?,?)",
            (key, value, user, key,  key, value, user)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        raise HTTPException(500, f"Database save failed: {e}")

    # Secondary: write to .env so workers pick it up on next restart
    env_path = _BOT_DIR / '.env'
    lines = env_path.read_text(encoding='utf-8').splitlines() if env_path.exists() else []
    found = False
    new_lines = []
    for line in lines:
        if line.split('#')[0].strip().startswith(f"{key}="):
            new_lines.append(f"{key}={value}")
            found = True
        else:
            new_lines.append(line)
    if not found:
        new_lines.append(f"{key}={value}")
    env_path.write_text('\n'.join(new_lines) + '\n', encoding='utf-8')

    return {"ok": True, "message": f"{key} saved to database + .env. Restart workers to apply."}


class UserConfigUpdate(BaseModel):
    capital_india:  Optional[float] = None
    capital_us:     Optional[float] = None
    capital_crypto: Optional[float] = None
    risk_pct:       Optional[float] = None
    max_positions:  Optional[int]   = None
    sl_pct:         Optional[float] = None
    target_mult:    Optional[float] = None
    enable_india:   Optional[bool]  = None
    enable_us:      Optional[bool]  = None
    enable_crypto:  Optional[bool]  = None
    paused:         Optional[bool]  = None

@app.get("/api/config/user")
def get_user_config(user: str = Depends(require_auth)):
    """Return the current user's per-user config (capital, risk, market toggles, paused)."""
    uid  = _get_user_id(user)
    conn = get_conn()
    c    = conn.cursor()
    c.execute(
        "SELECT capital_india,capital_us,capital_crypto,risk_pct,max_positions,"
        "sl_pct,target_mult,enable_india,enable_us,enable_crypto,paused "
        "FROM user_config WHERE user_id=?", (uid,)
    )
    row = c.fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "User config not found. Contact admin.")
    keys = ['capital_india','capital_us','capital_crypto','risk_pct','max_positions',
            'sl_pct','target_mult','enable_india','enable_us','enable_crypto','paused']
    return dict(zip(keys, row))

@app.put("/api/config/user")
def update_user_config(body: UserConfigUpdate, user: str = Depends(require_auth)):
    """Update the current user's per-user config."""
    uid  = _get_user_id(user)
    now  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Build dynamic SET clause from provided fields only
    fields = {
        'capital_india':  body.capital_india,
        'capital_us':     body.capital_us,
        'capital_crypto': body.capital_crypto,
        'risk_pct':       body.risk_pct,
        'max_positions':  body.max_positions,
        'sl_pct':         body.sl_pct,
        'target_mult':    body.target_mult,
        'enable_india':   (1 if body.enable_india else 0) if body.enable_india is not None else None,
        'enable_us':      (1 if body.enable_us else 0)    if body.enable_us    is not None else None,
        'enable_crypto':  (1 if body.enable_crypto else 0)if body.enable_crypto is not None else None,
        'paused':         (1 if body.paused else 0)        if body.paused        is not None else None,
    }
    updates = {k: v for k, v in fields.items() if v is not None}
    if not updates:
        raise HTTPException(400, "No fields to update.")
    set_clause = ', '.join(f"{k}=?" for k in updates)
    values     = list(updates.values()) + [now, uid]
    conn = get_conn()
    c    = conn.cursor()
    c.execute(f"UPDATE user_config SET {set_clause}, updated_at=? WHERE user_id=?", values)
    if c.rowcount == 0:
        conn.close()
        raise HTTPException(404, "User config not found.")
    conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/api/telegram/test")
def test_telegram(user: str = Depends(require_auth)):
    try:
        from reporting.telegram_alerts import send
        ok = send("🔔 <b>AngelBot Test Alert</b>\nTelegram connection working correctly.")
        return {"ok": ok}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/whatsapp/test")
def test_whatsapp(user: str = Depends(require_auth)):
    try:
        from reporting.whatsapp_alerts import send
        ok = send("🤖 *AngelBot WhatsApp Test*\nConnection working correctly.")
        return {"ok": ok}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/ntfy/test")
def test_ntfy(user: str = Depends(require_auth)):
    try:
        from reporting.ntfy_alerts import send
        ok = send(
            "AngelBot ntfy connected!\nPaper trading mode ON. Trade alerts will appear here.",
            title="AngelBot Test",
            priority="default",
            tags=["white_check_mark"]
        )
        return {"ok": ok}
    except Exception as e:
        return {"ok": False, "error": str(e)}


class ReportAnalysisRequest(BaseModel):
    period: str
    market: str
    overall: dict
    per_day: list
    exit_reasons: list
    markets: list

@app.post("/api/report/analyze")
def analyze_report(req: ReportAnalysisRequest, user: str = Depends(require_auth)):
    """Call AI API (Anthropic Claude or OpenAI) to analyse trade data and return plain-English insights."""
    # Read API keys from .env only (secrets — not stored in bot_settings)
    anthropic_key = openai_key = ''
    env_path = _BOT_DIR / '.env'
    try:
        for line in env_path.read_text(encoding='utf-8').splitlines():
            k, _, v = line.partition('=')
            k = k.strip()
            if k == 'ANTHROPIC_API_KEY': anthropic_key = v.strip()
            elif k == 'OPENAI_API_KEY':  openai_key    = v.strip()
    except Exception:
        pass

    d = req
    per_day_lines = '\n'.join(
        f"  {r['date']}: {r['trades']} trades, {r['win_rate']:.0f}% WR, "
        f"P&L: {'+' if r['pnl']>=0 else ''}{r['pnl']:.2f}"
        for r in d.per_day[:60]
    )
    exit_lines = '\n'.join(
        f"  {r['reason']}: {r['count']} trades, {r['win_rate']:.0f}% WR, "
        f"total P&L {r['pnl']:.2f}, avg {r['avg_pnl']:.2f}"
        for r in d.exit_reasons[:12]
    )
    mkt_lines = '\n'.join(
        f"  {m['market']}: {m['trades']} trades, {m['win_rate']:.0f}% WR, P&L {m['pnl']:.2f}"
        for m in d.markets
    )

    prompt = (
        "You are a friendly trading performance coach reviewing paper-trading results for AngelBot.\n\n"
        "Analyse the data below and respond with EXACTLY these five sections — no other headings:\n\n"
        "**Overall Assessment**\n"
        "2-3 sentences. Be direct — is this profitable, improving, or struggling?\n\n"
        "**What's Working**\n"
        "2-4 bullet points on genuine strengths (specific exit reasons, strong days, markets doing well).\n\n"
        "**What Needs Improvement**\n"
        "2-4 bullet points on clear weaknesses (losing patterns, bad exit reasons, losing days).\n\n"
        "**Suggestions**\n"
        "3-5 actionable bullet points based only on what the data shows.\n\n"
        "**Day-by-Day Highlights**\n"
        "Brief note on the best day, worst day, and any streak patterns. Skip this section if only 1 day.\n\n"
        "Rules: plain English, no jargon, use actual numbers, be concise.\n\n"
        f"Period: {d.period}\nMarket: {d.market}\n\n"
        f"Overall:\n"
        f"  Trades: {d.overall['total_trades']} ({d.overall['wins']}W / {d.overall['losses']}L)\n"
        f"  Win Rate: {d.overall['win_rate']:.1f}%\n"
        f"  Total P&L: {d.overall['total_pnl']:.2f}\n"
        f"  Profit Factor: {d.overall['profit_factor']:.2f}\n"
        f"  Best Trade: +{d.overall['best_trade']:.2f}   Worst: {d.overall['worst_trade']:.2f}\n"
        f"  Avg P&L/trade: {d.overall['avg_pnl']:.2f}\n\n"
        f"Per-Day:\n{per_day_lines}\n\n"
        f"Exit Reasons:\n{exit_lines}\n\n"
        f"Markets:\n{mkt_lines}"
    )

    if anthropic_key:
        try:
            r = requests.post(
                'https://api.anthropic.com/v1/messages',
                headers={
                    'x-api-key': anthropic_key,
                    'anthropic-version': '2023-06-01',
                    'content-type': 'application/json',
                },
                json={
                    'model': 'claude-haiku-4-5-20251001',
                    'max_tokens': 1400,
                    'messages': [{'role': 'user', 'content': prompt}],
                },
                timeout=40,
            )
            if r.status_code == 200:
                return {'ok': True, 'analysis': r.json()['content'][0]['text'], 'provider': 'Claude'}
        except Exception as e:
            pass

    if openai_key:
        try:
            r = requests.post(
                'https://api.openai.com/v1/chat/completions',
                headers={'Authorization': f'Bearer {openai_key}', 'Content-Type': 'application/json'},
                json={
                    'model': 'gpt-4o-mini',
                    'max_tokens': 1400,
                    'messages': [{'role': 'user', 'content': prompt}],
                },
                timeout=40,
            )
            if r.status_code == 200:
                return {'ok': True, 'analysis': r.json()['choices'][0]['message']['content'], 'provider': 'GPT-4o Mini'}
        except Exception as e:
            pass

    return {'ok': False, 'error': 'No AI API key found. Add ANTHROPIC_API_KEY or OPENAI_API_KEY in Settings → API Keys.'}


@app.get("/api/tunnel/url")
def get_tunnel_url(user: str = Depends(require_auth)):
    """Return the current Cloudflare Tunnel URL parsed from the tunnel log."""
    log = _BOT_DIR / "logs" / "AngelBot-Tunnel.log"
    svc_status = _sc_status("AngelBot-Tunnel")
    if not log.exists():
        return {"url": None, "status": svc_status}
    text = log.read_text(encoding='utf-8', errors='ignore')
    matches = _re.findall(r'https://[a-z0-9-]+\.trycloudflare\.com', text)
    return {"url": matches[-1] if matches else None, "status": svc_status}


# ── User preferences (filter defaults, UI state) — stored in DB ──────────────
class PrefUpdate(BaseModel):
    key: str
    value: str

@app.get("/api/prefs")
def get_prefs(user: str = Depends(require_auth)):
    """Return all user preferences stored in bot_settings with PREF_ prefix."""
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT setting_key, setting_value FROM bot_settings WHERE setting_key LIKE 'PREF_%'")
        rows = c.fetchall()
        conn.close()
        return {row[0][5:]: row[1] for row in rows}  # strip PREF_ prefix
    except Exception:
        return {}

@app.post("/api/prefs")
def save_pref(update: PrefUpdate, user: str = Depends(require_auth)):
    """Save a user preference to bot_settings with PREF_ prefix."""
    key = 'PREF_' + update.key.strip()
    val = update.value
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute(
            "IF EXISTS (SELECT 1 FROM bot_settings WHERE setting_key=?) "
            "  UPDATE bot_settings SET setting_value=?, updated_at=GETDATE() WHERE setting_key=? "
            "ELSE "
            "  INSERT INTO bot_settings (setting_key, setting_value, updated_by) VALUES (?,?,?)",
            (key, val, key, key, val, user)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        raise HTTPException(500, f"Pref save failed: {e}")
    return {"ok": True}


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


# ── Portal instance management ────────────────────────────────────────────────
@app.get("/api/instances/next-port")
def get_next_port(user: str = Depends(require_auth)):
    _require_admin(user)
    return {"port": _next_available_port()}


@app.get("/api/instances")
def list_instances(user: str = Depends(require_auth)):
    _require_admin(user)
    conn = get_conn()
    c    = conn.cursor()
    c.execute("SELECT id,name,db_name,port,instance_dir,status,created_at FROM portal_instances ORDER BY id")
    cols = ['id','name','db_name','port','instance_dir','status','created_at']
    rows = [dict(zip(cols, r)) for r in c.fetchall()]
    conn.close()
    for row in rows:
        n = row['name']
        svcs = [f'AngelBot-India-{n}', f'AngelBot-US-{n}',
                f'AngelBot-Crypto-{n}', f'AngelBot-Portal-{n}']
        statuses = [_sc_status(s) for s in svcs]
        row['services'] = dict(zip(['india','us','crypto','portal'], statuses))
        row['running']  = statuses.count('running')
    return {"instances": rows}


class NewInstance(BaseModel):
    name: str
    port: int
    admin_password: str
    capital_india: float = 100000
    capital_us: float = 5000
    capital_crypto: float = 1000


@app.post("/api/instances")
def create_instance(body: NewInstance, user: str = Depends(require_auth)):
    _require_admin(user)
    safe = _re.sub(r'[^A-Za-z0-9_-]', '', body.name.strip().lower())[:30]
    if not safe:
        raise HTTPException(400, "Invalid instance name — use letters/numbers only.")
    if len(body.admin_password) < 8:
        raise HTTPException(400, "Admin password must be at least 8 characters.")
    if not (1024 <= body.port <= 65535):
        raise HTTPException(400, "Port must be 1024–65535.")
    conn = get_conn()
    c    = conn.cursor()
    c.execute("SELECT 1 FROM portal_instances WHERE name=? OR port=?", (safe, body.port))
    if c.fetchone():
        conn.close()
        raise HTTPException(400, "Instance name or port already in use.")
    conn.close()
    try:
        inst_dir = _provision_instance(safe, body.port, body.admin_password,
                                       body.capital_india, body.capital_us, body.capital_crypto)
    except Exception as e:
        raise HTTPException(500, f"Provisioning failed: {e}")
    conn = get_conn()
    c    = conn.cursor()
    c.execute(
        "INSERT INTO portal_instances (name,db_name,port,instance_dir,status,created_at) "
        "VALUES (?,?,?,?,?,?)",
        (safe, f"angelbot_{safe}", body.port, str(inst_dir), 'stopped',
         datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    conn.commit()
    conn.close()
    return {"ok": True, "name": safe, "port": body.port,
            "message": f"Portal '{safe}' created on port {body.port}. Click Start to launch it."}


@app.post("/api/instances/{name}/start")
def start_instance(name: str, user: str = Depends(require_auth)):
    _require_admin(user)
    safe = _re.sub(r'[^A-Za-z0-9_-]', '', name)[:30]
    results = {}
    for label, svc in [('india',  f'AngelBot-India-{safe}'),
                       ('us',     f'AngelBot-US-{safe}'),
                       ('crypto', f'AngelBot-Crypto-{safe}'),
                       ('portal', f'AngelBot-Portal-{safe}')]:
        try:
            r = subprocess.run([_NSSM, 'start', svc], capture_output=True, timeout=30)
            results[label] = 'started' if r.returncode in (0, 1) else 'error'
        except Exception:
            results[label] = 'error'
    conn = get_conn()
    c    = conn.cursor()
    c.execute("UPDATE portal_instances SET status='running' WHERE name=?", (safe,))
    conn.commit()
    conn.close()
    return {"ok": True, "services": results}


@app.post("/api/instances/{name}/stop")
def stop_instance(name: str, user: str = Depends(require_auth)):
    _require_admin(user)
    safe = _re.sub(r'[^A-Za-z0-9_-]', '', name)[:30]
    for svc in [f'AngelBot-India-{safe}', f'AngelBot-US-{safe}',
                f'AngelBot-Crypto-{safe}', f'AngelBot-Portal-{safe}']:
        try:
            subprocess.run([_NSSM, 'stop', svc], capture_output=True, timeout=20)
        except Exception:
            pass
    conn = get_conn()
    c    = conn.cursor()
    c.execute("UPDATE portal_instances SET status='stopped' WHERE name=?", (safe,))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.delete("/api/instances/{name}")
def delete_instance(name: str, user: str = Depends(require_auth)):
    _require_admin(user)
    safe = _re.sub(r'[^A-Za-z0-9_-]', '', name)[:30]
    try:
        _remove_instance(safe)
    except Exception:
        pass
    conn = get_conn()
    c    = conn.cursor()
    c.execute("DELETE FROM portal_instances WHERE name=?", (safe,))
    conn.commit()
    conn.close()
    return {"ok": True, "message": f"Instance '{safe}' removed."}


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
    """Return all portal users with bot status. Admin only."""
    _require_admin(user)
    conn = get_conn()
    c    = conn.cursor()
    c.execute(
        "SELECT p.id, p.username, p.role, p.created_at, p.last_login, "
        "COALESCE(uc.paused, 1) AS paused "
        "FROM portal_users p "
        "LEFT JOIN user_config uc ON uc.user_id = p.id "
        "ORDER BY p.id"
    )
    cols = ['id', 'username', 'role', 'created_at', 'last_login', 'paused']
    rows = [dict(zip(cols, r)) for r in c.fetchall()]
    conn.close()
    return {"users": rows}


@app.post("/api/users/{username}/activate")
def activate_user(username: str, user: str = Depends(require_auth)):
    """Start a user's paper trading services. Admin only."""
    _require_admin(user)
    uid = _get_user_id(username)
    if uid == 1 and username != 'admin':
        raise HTTPException(404, f"User '{username}' not found.")
    _set_user_paused(uid, False)
    # Update service statuses in DB
    conn = get_conn()
    c    = conn.cursor()
    c.execute("UPDATE user_services SET status='running' WHERE user_id=?", (uid,))
    conn.commit()
    conn.close()
    return {"ok": True, "message": f"'{username}' bot services started."}


@app.post("/api/users/{username}/deactivate")
def deactivate_user(username: str, user: str = Depends(require_auth)):
    """Stop a user's paper trading services. Admin only."""
    _require_admin(user)
    uid = _get_user_id(username)
    _set_user_paused(uid, True)
    conn = get_conn()
    c    = conn.cursor()
    c.execute("UPDATE user_services SET status='stopped' WHERE user_id=?", (uid,))
    conn.commit()
    conn.close()
    return {"ok": True, "message": f"'{username}' bot services stopped."}

@app.post("/api/users")
def create_user(body: NewUser, user: str = Depends(require_auth)):
    """Create a new portal user. Admin only. Provisions user_config + NSSM services."""
    _require_admin(user)
    if body.role not in ('admin', 'viewer'):
        raise HTTPException(400, "Role must be 'admin' or 'viewer'.")
    if len(body.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters.")
    pw_hash  = _hash_password(body.password)
    username = body.username.strip()
    now      = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_conn()
    c    = conn.cursor()
    try:
        c.execute(
            "INSERT INTO portal_users (username, password_hash, role, created_at) VALUES (?,?,?,?)",
            (username, pw_hash, body.role, now)
        )
        c.execute("SELECT @@IDENTITY")
        new_uid = int(c.fetchone()[0])
        # Per-user config row — starts paused so no trades fire until admin activates
        c.execute(
            "INSERT INTO user_config "
            "(user_id,capital_india,capital_us,capital_crypto,risk_pct,max_positions,"
            " sl_pct,target_mult,enable_india,enable_us,enable_crypto,paused,created_at) "
            "VALUES (?,100000,5000,1000,2.0,5,2.0,2.0,1,1,1,1,?)",
            (new_uid, now)
        )
        conn.commit()
    except Exception as e:
        conn.close()
        raise HTTPException(400, f"Could not create user: {e}")
    conn.close()
    # Register NSSM services (best-effort — portal keeps working even if NSSM fails)
    try:
        _register_user_services(new_uid, username)
    except Exception:
        pass
    return {"ok": True, "message": f"User '{username}' created (paused). Admin must activate."}

@app.delete("/api/users/{username}")
def delete_user(username: str, user: str = Depends(require_auth)):
    """Delete a portal user and all their services. Admin only."""
    _require_admin(user)
    if username == user:
        raise HTTPException(400, "Cannot delete your own account.")
    # Get uid before deleting
    uid = _get_user_id(username)
    conn = get_conn()
    c    = conn.cursor()
    c.execute("DELETE FROM portal_users WHERE username=?", (username,))
    if c.rowcount == 0:
        conn.close()
        raise HTTPException(404, f"User '{username}' not found.")
    conn.commit()
    conn.close()
    # Stop + remove NSSM services and cleanup user_config/user_services rows
    try:
        _deregister_user_services(uid)
    except Exception:
        pass
    return {"ok": True, "message": f"User '{username}' and their services deleted."}

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
    uid = _get_user_id(user)
    _set_user_paused(uid, True)
    return {"ok": True, "paused": True}

@app.post("/api/bot/resume")
def bot_resume(user: str = Depends(require_auth)):
    uid = _get_user_id(user)
    _set_user_paused(uid, False)
    return {"ok": True, "paused": False}

@app.get("/api/bot/status")
def bot_status(user: str = Depends(require_auth)):
    uid  = _get_user_id(user)
    conn = get_conn()
    c    = conn.cursor()
    c.execute("SELECT paused FROM user_config WHERE user_id=?", (uid,))
    row  = c.fetchone()
    conn.close()
    paused = bool(row[0]) if row else False
    return {"paused": paused}


def _resolve_log_path(market: str, date: str = '') -> Path:
    """Return path to a market's log file.
    If date (YYYYMMDD) given, returns that specific file.
    Otherwise returns the most-recently-modified file — avoids EST/IST date confusion."""
    log_dir = Path(__file__).parent.parent / 'logs'
    if market == 'watchdog':
        return log_dir / 'watchdog.log'
    if market == 'portal':
        return log_dir / 'AngelBot-Portal.log'
    if date:
        return log_dir / f'{market}_{date}.log'
    files = sorted(log_dir.glob(f'{market}_*.log'), key=lambda f: f.stat().st_mtime, reverse=True)
    if files:
        return files[0]
    return log_dir / f'{market}_{datetime.now().strftime("%Y%m%d")}.log'


# ── Logs (historical) ─────────────────────────────────────────────────────────
@app.get("/api/logs/dates/{market}")
def get_log_dates(market: str, user: str = Depends(require_auth)):
    """Return all available log dates for a market, newest first."""
    if market not in ('india', 'us', 'crypto', 'watchdog', 'portal'):
        raise HTTPException(400, "Invalid market")
    log_dir = Path(__file__).parent.parent / 'logs'
    if market in ('watchdog', 'portal'):
        fname = 'watchdog.log' if market == 'watchdog' else 'AngelBot-Portal.log'
        p = log_dir / fname
        size = p.stat().st_size if p.exists() else 0
        return {"dates": [{"date": "", "label": "Current log", "size": size}]}
    files = sorted(log_dir.glob(f'{market}_*.log'), key=lambda f: f.stat().st_mtime, reverse=True)
    dates = []
    for f in files:
        parts = f.stem.split('_', 1)
        if len(parts) == 2:
            try:
                dt = datetime.strptime(parts[1], '%Y%m%d')
                dates.append({"date": parts[1], "label": dt.strftime('%d %b %Y'), "size": f.stat().st_size})
            except ValueError:
                pass
    return {"dates": dates}


@app.get("/api/logs/{market}")
def get_log(market: str, lines: int = 200, date: str = '', user: str = Depends(require_auth)):
    if market not in ('india', 'us', 'crypto', 'watchdog', 'portal'):
        raise HTTPException(400, "Invalid market. Use: india, us, crypto, watchdog, portal")

    # India IST session starts at ~10:45 PM EST — always spans midnight EST and splits
    # across two log files.  Merge the two most recent files when showing "latest" so
    # the full IST day is visible without the user needing to know about EST dates.
    if market == 'india' and not date:
        log_dir = Path(__file__).parent.parent / 'logs'
        files = sorted(log_dir.glob('india_*.log'), key=lambda f: f.stat().st_mtime, reverse=True)
        if len(files) >= 2:
            all_lines = []
            for f in reversed(files[:2]):   # older first, so log reads chronologically
                try:
                    with open(f, encoding='utf-8', errors='replace') as fp:
                        all_lines.extend(fp.readlines())
                except Exception:
                    pass
            tail = all_lines if lines <= 0 else all_lines[-lines:]
            label = f"{files[1].name} + {files[0].name} (IST merged)"
            return {"lines": [l.rstrip() for l in tail], "path": label, "total": len(all_lines)}

    log_path = _resolve_log_path(market, date)
    if not log_path.exists():
        return {"lines": [], "path": str(log_path), "total": 0}
    with open(log_path, encoding='utf-8', errors='replace') as f:
        all_lines = f.readlines()
    tail = all_lines if lines <= 0 else all_lines[-lines:]
    return {"lines": [l.rstrip() for l in tail], "path": str(log_path), "total": len(all_lines)}


@app.get("/api/logs/download/{market}")
def download_log(market: str, date: str = '', user: str = Depends(require_auth)):
    if market not in ('india', 'us', 'crypto', 'watchdog', 'portal'):
        raise HTTPException(400, "Invalid market")
    log_path = _resolve_log_path(market, date)
    if not log_path.exists():
        raise HTTPException(404, f"No log file found for {market}")
    return FileResponse(str(log_path), media_type='text/plain; charset=utf-8', filename=log_path.name)


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
_SERVICE_NAMES = ['AngelBot-India', 'AngelBot-US', 'AngelBot-Crypto', 'AngelBot-Portal', 'cloudflared']

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


_NSSM = r'C:\Windows\nssm.exe'
_BOT_PYTHON = sys.executable
_BOT_WORKER_SCRIPTS = {
    'india':  'india_worker.py',
    'us':     'us_worker.py',
    'crypto': 'crypto_worker.py',
}


def _register_user_services(uid: int, username: str) -> list:
    """Create 3 NSSM services for a new user (started paused/stopped). Returns service names."""
    safe = _re.sub(r'[^A-Za-z0-9_-]', '', username)[:20]
    now  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_conn()
    c    = conn.cursor()
    svc_names = []
    for market, script in _BOT_WORKER_SCRIPTS.items():
        svc_name    = f"AngelBot-{market.capitalize()}-{safe}"
        script_path = str(_BOT_DIR / script)
        svc_names.append(svc_name)
        try:
            subprocess.run([_NSSM, 'install', svc_name, _BOT_PYTHON, script_path],
                           capture_output=True, timeout=30)
            subprocess.run([_NSSM, 'set', svc_name, 'AppDirectory', str(_BOT_DIR)],
                           capture_output=True, timeout=15)
            subprocess.run([_NSSM, 'set', svc_name, 'AppEnvironmentExtra',
                            f'ANGELBOT_USER_ID={uid}'],
                           capture_output=True, timeout=15)
            # Manual start — service won't auto-run on boot until user unpauses
            subprocess.run(['sc', 'config', svc_name, 'start=', 'demand'],
                           capture_output=True, timeout=15)
        except Exception:
            pass
        c.execute(
            "INSERT INTO user_services (user_id,service_name,market,worker_script,status) "
            "VALUES (?,?,?,?,'stopped')", (uid, svc_name, market, script)
        )
    conn.commit()
    conn.close()
    return svc_names


def _deregister_user_services(uid: int):
    """Stop + remove all NSSM services for a user, then delete DB rows."""
    conn = get_conn()
    c    = conn.cursor()
    c.execute("SELECT service_name FROM user_services WHERE user_id=?", (uid,))
    svcs = [row[0] for row in c.fetchall()]
    conn.close()
    for svc in svcs:
        try:
            subprocess.run([_NSSM, 'stop', svc], capture_output=True, timeout=20)
            subprocess.run([_NSSM, 'remove', svc, 'confirm'], capture_output=True, timeout=20)
        except Exception:
            pass
    conn = get_conn()
    c    = conn.cursor()
    c.execute("DELETE FROM user_services WHERE user_id=?", (uid,))
    c.execute("DELETE FROM user_config  WHERE user_id=?", (uid,))
    conn.commit()
    conn.close()


def _set_user_paused(uid: int, paused: bool):
    """Pause/resume a user's bot: update user_config + start/stop their NSSM services."""
    conn = get_conn()
    c    = conn.cursor()
    c.execute("UPDATE user_config SET paused=? WHERE user_id=?", (1 if paused else 0, uid))
    # Fetch this user's services (skip admin's shared services — they use paused.flag)
    if uid != 1:
        c.execute("SELECT service_name FROM user_services WHERE user_id=?", (uid,))
        svcs = [row[0] for row in c.fetchall()]
        conn.commit()
        conn.close()
        for svc in svcs:
            try:
                if paused:
                    subprocess.run([_NSSM, 'stop', svc], capture_output=True, timeout=20)
                else:
                    subprocess.run([_NSSM, 'start', svc], capture_output=True, timeout=20)
            except Exception:
                pass
    else:
        conn.commit()
        conn.close()
        # Admin: also maintain legacy paused.flag so existing workers respect it
        flag = _BOT_DIR / 'paused.flag'
        if paused:
            flag.touch()
        elif flag.exists():
            flag.unlink()


_INSTANCES_DIR = _BOT_DIR / 'instances'


def _next_available_port() -> int:
    conn = get_conn()
    c    = conn.cursor()
    c.execute("SELECT port FROM portal_instances")
    used = {row[0] for row in c.fetchall()}
    conn.close()
    port = 8081
    while port in used or port == 8080:
        port += 1
    return port


def _provision_instance(name: str, port: int, admin_password: str,
                        capital_india: float, capital_us: float, capital_crypto: float) -> Path:
    import shutil
    inst_dir = _INSTANCES_DIR / name
    inst_dir.mkdir(parents=True, exist_ok=True)
    (inst_dir / 'logs').mkdir(exist_ok=True)
    (inst_dir / 'data').mkdir(exist_ok=True)

    db_name  = f"angelbot_{name}"
    sql_pass = os.getenv("SQL_SA_PASS", "")

    env_text = (
        f"ANGELBOT_DB={db_name}\n"
        f"SQL_SA_PASS={sql_pass}\n"
        f"PORTAL_PORT={port}\n"
        f"PORTAL_PASS={admin_password}\n"
        f"PAPER_MODE=true\n"
        f"ALPACA_PAPER=true\n"
        f"BINANCE_PAPER=true\n"
        f"INDIA_CAPITAL={capital_india}\n"
        f"US_CAPITAL={capital_us}\n"
        f"CRYPTO_CAPITAL={capital_crypto}\n"
    )
    (inst_dir / '.env').write_text(env_text, encoding='utf-8')

    env_for_init = {**os.environ,
                    'ANGELBOT_INSTANCE_DIR': str(inst_dir),
                    'ANGELBOT_DB': db_name}
    subprocess.run([sys.executable, '-m', 'data.database'],
                   cwd=str(_BOT_DIR), env=env_for_init,
                   capture_output=True, timeout=60)

    nssm_env = f'ANGELBOT_INSTANCE_DIR={inst_dir}\nANGELBOT_DB={db_name}'
    for svc_name, script in [
        (f'AngelBot-India-{name}',  'india_worker.py'),
        (f'AngelBot-US-{name}',     'us_worker.py'),
        (f'AngelBot-Crypto-{name}', 'crypto_worker.py'),
    ]:
        try:
            subprocess.run([_NSSM, 'install', svc_name, _BOT_PYTHON, str(_BOT_DIR / script)],
                           capture_output=True, timeout=30)
            subprocess.run([_NSSM, 'set', svc_name, 'AppDirectory', str(_BOT_DIR)],
                           capture_output=True, timeout=15)
            subprocess.run([_NSSM, 'set', svc_name, 'AppEnvironmentExtra', nssm_env],
                           capture_output=True, timeout=15)
            subprocess.run(['sc', 'config', svc_name, 'start=', 'demand'],
                           capture_output=True, timeout=15)
        except Exception:
            pass

    portal_svc = f'AngelBot-Portal-{name}'
    portal_args = ['-m', 'uvicorn', 'portal.app:app', '--host', '0.0.0.0', '--port', str(port)]
    try:
        subprocess.run([_NSSM, 'install', portal_svc, _BOT_PYTHON] + portal_args,
                       capture_output=True, timeout=30)
        subprocess.run([_NSSM, 'set', portal_svc, 'AppDirectory', str(_BOT_DIR)],
                       capture_output=True, timeout=15)
        subprocess.run([_NSSM, 'set', portal_svc, 'AppEnvironmentExtra', nssm_env],
                       capture_output=True, timeout=15)
        subprocess.run(['sc', 'config', portal_svc, 'start=', 'demand'],
                       capture_output=True, timeout=15)
    except Exception:
        pass

    return inst_dir


def _remove_instance(name: str):
    import shutil
    for svc in [f'AngelBot-India-{name}', f'AngelBot-US-{name}',
                f'AngelBot-Crypto-{name}', f'AngelBot-Portal-{name}']:
        try:
            subprocess.run([_NSSM, 'stop',   svc],            capture_output=True, timeout=20)
            subprocess.run([_NSSM, 'remove', svc, 'confirm'], capture_output=True, timeout=20)
        except Exception:
            pass
    try:
        shutil.rmtree(_INSTANCES_DIR / name, ignore_errors=True)
    except Exception:
        pass


@app.post("/api/services/{name}/start")
def start_service(name: str, user: str = Depends(require_auth)):
    """Start a stopped Windows service via NSSM."""
    if name not in _SERVICE_NAMES:
        raise HTTPException(400, f"Unknown service '{name}'.")
    if name == "AngelBot-Portal":
        raise HTTPException(400, "Portal cannot be started this way — use the Restart button.")
    try:
        r = subprocess.run([_NSSM, 'start', name], capture_output=True, text=True,
                           encoding='utf-8', errors='replace', timeout=30)
        if r.returncode not in (0, 1):   # NSSM returns 1 if already running — treat as OK
            raise HTTPException(500, f"Failed to start {name}: {(r.stderr or r.stdout).strip()}")
        return {"ok": True, "name": name, "message": f"{name} started."}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/services/{name}/stop")
def stop_service(name: str, user: str = Depends(require_auth)):
    """Stop a running Windows service via NSSM."""
    if name not in _SERVICE_NAMES:
        raise HTTPException(400, f"Unknown service '{name}'.")
    if name == "AngelBot-Portal":
        raise HTTPException(400, "Portal cannot be stopped this way — use the Restart button.")
    try:
        r = subprocess.run([_NSSM, 'stop', name], capture_output=True, text=True,
                           encoding='utf-8', errors='replace', timeout=30)
        if r.returncode not in (0, 3):   # NSSM returns 3 if already stopped — treat as OK
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
            # Use Task Scheduler — completely independent of NSSM Job Objects.
            # Detached-process tricks fail when NSSM's Job Object lacks BREAKAWAY_OK.
            ps = (
                "Unregister-ScheduledTask -TaskName 'ABPortalRestart' -Confirm:$false -ErrorAction SilentlyContinue;"
                "$a=New-ScheduledTaskAction -Execute 'C:\\Windows\\nssm.exe' -Argument 'restart AngelBot-Portal';"
                "$t=New-ScheduledTaskTrigger -Once -At ((Get-Date).AddSeconds(10));"
                "Register-ScheduledTask -TaskName 'ABPortalRestart' -Action $a -Trigger $t -User 'SYSTEM' -RunLevel Highest -Force | Out-Null"
            )
            r = subprocess.run(['powershell', '-NonInteractive', '-Command', ps],
                               capture_output=True, text=True, timeout=20)
            if r.returncode != 0:
                raise HTTPException(500, f"Failed to schedule restart: {r.stderr.strip()}")
            return {"ok": True, "name": name, "message": f"{name} restarting in ~10s — page will reload."}
        subprocess.run([_NSSM, 'stop', name], capture_output=True, text=True,
                       encoding='utf-8', errors='replace', timeout=30)
        start = subprocess.run([_NSSM, 'start', name], capture_output=True, text=True,
                               encoding='utf-8', errors='replace', timeout=30)
        if start.returncode not in (0, 1):
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

        # 3. Portal restart via Task Scheduler — survives NSSM Job Object teardown.
        # Detached Popen (even with CREATE_BREAKAWAY_FROM_JOB) is unreliable when
        # NSSM's Job Object lacks JOB_OBJECT_LIMIT_BREAKAWAY_OK. Task Scheduler is
        # a separate Windows service and is invisible to Job Objects entirely.
        try:
            ps = (
                "Unregister-ScheduledTask -TaskName 'ABPortalRestart' -Confirm:$false -ErrorAction SilentlyContinue;"
                "$a=New-ScheduledTaskAction -Execute 'C:\\Windows\\nssm.exe' -Argument 'restart AngelBot-Portal';"
                "$t=New-ScheduledTaskTrigger -Once -At ((Get-Date).AddSeconds(30));"
                "Register-ScheduledTask -TaskName 'ABPortalRestart' -Action $a -Trigger $t -User 'SYSTEM' -RunLevel Highest -Force | Out-Null"
            )
            r = subprocess.run(['powershell', '-NonInteractive', '-Command', ps],
                               capture_output=True, text=True, timeout=20)
            if r.returncode != 0:
                raise Exception(r.stderr.strip() or "Task Scheduler registration failed")
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
    'WHATSAPP_PHONE', 'ULTRAMSG_INSTANCE', 'ULTRAMSG_TOKEN',
    'ANTHROPIC_API_KEY', 'OPENAI_API_KEY',
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
