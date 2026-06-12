"""
AngelBot — India NSE Worker
===========================
Runs in its own console window.  Market hours: 9:15 AM – 3:30 PM IST, Mon–Fri.
Spawned automatically by main.py (do not run directly in production).
"""
import sys, os, time, schedule, warnings
import logging as _lg, re as _re

# ── Suppress noisy warnings ───────────────────────────────────────────────────
warnings.filterwarnings('ignore', category=DeprecationWarning)
warnings.filterwarnings('ignore', category=PendingDeprecationWarning)
warnings.filterwarnings('ignore', message='.*NotOpenSSLWarning.*')
warnings.filterwarnings('ignore', message='.*LibreSSL.*')

# ── ANSI — must happen before any print ──────────────────────────────────────
if sys.platform == 'win32':
    try:
        import ctypes as _ct
        _k = _ct.windll.kernel32
        for _h in (-10, -11, -12):
            _m = _ct.c_ulong()
            if _k.GetConsoleMode(_k.GetStdHandle(_h), _ct.byref(_m)):
                _k.SetConsoleMode(_k.GetStdHandle(_h), _m.value | 0x0004)
    except Exception:
        pass

def _a(c): return f"\033[{c}m"
R   = _a("0");     G  = _a("1;92");  RD = _a("1;91")
CY  = _a("1;96"); YL  = _a("1;93"); GY = _a("90")
WH  = _a("97");   DIM = _a("2")

# ── Project root on path ──────────────────────────────────────────────────────
_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)

from datetime import datetime, timezone, timedelta, time as dtime

# ── File logger (console uses direct print — no proxy) ───────────────────────
os.makedirs(os.path.join(_ROOT, 'logs'), exist_ok=True)
_ansi_re = _re.compile(r'\033\[[0-9;]*m')
_flog = _lg.getLogger('india')
_flog.setLevel(_lg.INFO)
_flog.propagate = False
_fh = _lg.FileHandler(
    os.path.join(_ROOT, 'logs', f"india_{datetime.now().strftime('%Y%m%d')}.log"),
    encoding='utf-8'
)
_fh.setFormatter(_lg.Formatter('%(asctime)s  %(message)s', '%Y-%m-%d %H:%M:%S'))
_flog.addHandler(_fh)


def cprint(msg, color=WH):
    ts  = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    sys.__stdout__.write(f"{color}{ts}  {msg}{R}\n")
    sys.__stdout__.flush()
    _flog.info(_ansi_re.sub('', msg))

def sp():
    sys.__stdout__.write('\n')
    sys.__stdout__.flush()

def sep(color=GY):
    cprint('─' * 60, color)

# ── Time helpers ──────────────────────────────────────────────────────────────
IST = timezone(timedelta(hours=5, minutes=30))
def now_ist(): return datetime.now(IST)

def is_market_hours():
    now = now_ist()
    if now.weekday() >= 5:
        return False
    return dtime(9, 15) <= now.time() <= dtime(15, 30)

def _next_india_open():
    now = now_ist()
    candidate = now.replace(hour=9, minute=15, second=0, microsecond=0)
    if now.time() >= dtime(9, 15):
        candidate = candidate + timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate = candidate + timedelta(days=1)
    diff = candidate - now
    total_mins = int(diff.total_seconds() / 60)
    h, m = divmod(total_mins, 60)
    if h >= 24:
        days, rh = divmod(h, 24)
        return candidate.strftime('%a %I:%M %p IST'), f"in {days}d {rh}h"
    return candidate.strftime('%I:%M %p IST'), f"in {h}h {m}m"

# ── Shared pause flag (file-based so all processes see it) ───────────────────
_PAUSE_FLAG = os.path.join(_ROOT, 'paused.flag')
def is_paused(): return os.path.exists(_PAUSE_FLAG)

# ── Single-instance lock ──────────────────────────────────────────────────────
import socket as _sock
_lock_sock = None

def _acquire_lock():
    global _lock_sock
    _lock_sock = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
    _lock_sock.setsockopt(_sock.SOL_SOCKET, _sock.SO_REUSEADDR, 0)
    try:
        _lock_sock.bind(('127.0.0.1', 47833))
    except OSError:
        cprint("India worker already running — exiting.", GY)
        sys.exit(0)

# ── Imports ───────────────────────────────────────────────────────────────────
from config import (PAPER_MODE, MAX_DAILY_LOSS_PCT, MAX_DAILY_TRADES, MAX_DEPLOYED_PCT,
                    PEAK_DRAWDOWN_PCT, ENTRY_START_MIN, ENTRY_END_MIN, MIN_STOCK_PRICE)
from data.nifty_stocks import get_all_stocks
from data.live_feed import LiveFeed
from trading.scanner import scan_stocks
from trading.paper_trader import PaperTrader
from trading.position_monitor import start_monitor
from reporting.excel_report import generate_daily_report
from reporting.telegram_alerts import send, send_daily_summary, send_reload_alert
from reporting.telegram_listener import is_symbol_paused
from analysis.market_filters import india_market_mood_ok, symbol_event_clear, sector_cap_ok
from learning.self_learner import should_retrain, train

# ── Global state ──────────────────────────────────────────────────────────────
trader          = None
live_feed       = None
monitor         = None
_eod_done_date  = None
_last_heartbeat = None

# ── Exit callback ─────────────────────────────────────────────────────────────
def _on_exit(pos, price, pnl, pnl_pct, reason, *_):
    sign = '+' if pnl >= 0 else ''
    cprint(f"[EXIT] {pos['symbol']} @ ₹{price:.2f}  P&L: {sign}₹{pnl:.2f} ({sign}{pnl_pct:.2f}%)  {reason}",
           G if pnl >= 0 else RD)

# ── India buy scan ────────────────────────────────────────────────────────────
def run_scan():
    ist_now = now_ist()
    ist_str = ist_now.strftime('%I:%M %p IST')
    h, m    = ist_now.hour, ist_now.minute

    sp()
    sep()

    if is_paused():
        cprint(f"  [{ist_str}]  ⏸  Bot paused — scan skipped", YL)
        sep()
        return

    if not is_market_hours():
        open_at, opens_in = _next_india_open()
        cprint(f"  [{ist_str}]  India: MARKET CLOSED — opens {open_at} ({opens_in})", GY)
        sep()
        return

    if (h, m) < ENTRY_START_MIN:
        cprint(f"  [{ist_str}]  Opening volatility window — no buys before "
               f"{ENTRY_START_MIN[0]:02d}:{ENTRY_START_MIN[1]:02d}", YL)
        sep()
        return

    if (h, m) >= ENTRY_END_MIN:
        cprint(f"  [{ist_str}]  Too close to market close — no new buys", YL)
        sep()
        return

    stats      = trader.get_daily_stats()
    loss_limit = trader.balance * MAX_DAILY_LOSS_PCT
    if stats['day_pnl'] < -loss_limit:
        cprint(f"  [{ist_str}]  Daily loss limit hit (₹{stats['day_pnl']:.2f}) — no new buys", RD)
        sep()
        return

    if stats['trades'] >= MAX_DAILY_TRADES:
        cprint(f"  [{ist_str}]  Daily trade cap reached ({stats['trades']}/{MAX_DAILY_TRADES}) — no new buys", YL)
        sep()
        return

    dd = trader.get_drawdown_pct()
    if dd >= PEAK_DRAWDOWN_PCT:
        cprint(f"  [{ist_str}]  Peak drawdown {dd*100:.1f}% — pausing new buys until recovery", RD)
        sep()
        return

    dep = trader.get_deployed_pct()
    if dep >= MAX_DEPLOYED_PCT:
        cprint(f"  [{ist_str}]  {dep*100:.0f}% capital deployed — waiting for positions to close", YL)
        sep()
        return

    if not india_market_mood_ok():
        cprint(f"  [{ist_str}]  NIFTY mood bearish — skipping India buy scan", YL)
        sep()
        return

    # ── Print section header ──────────────────────────────────────────────────
    cprint(f"{'─'*20} INDIA NSE {'─'*30}", CY)
    sp()
    cprint(f"  [{ist_now.strftime('%I:%M:%S %p IST')}]  Scanning India stocks...", CY)
    sp()

    current_list = get_all_stocks()
    open_syms    = [p['symbol'] for p in trader.open_positions]

    reloaded, old_bal, amount = trader.auto_reload()
    if reloaded:
        rs = trader.get_reload_stats()
        send_reload_alert(old_bal, trader.balance, rs['count'], rs['total_invested'], amount)

    if not trader.can_buy():
        cprint("  Max positions open — skipping buy scan", GY)
        return

    candidates    = scan_stocks(symbols=current_list, extra_symbols=open_syms, max_price=trader.balance)
    buy_cands     = [c for c in candidates if c['in_index']]

    from concurrent.futures import ThreadPoolExecutor
    from data.fetcher import get_min_lot_size

    def _prefetch(c):
        c['_lot'] = get_min_lot_size(c['symbol'])
        return c

    with ThreadPoolExecutor(max_workers=10) as ex:
        buy_cands = list(ex.map(_prefetch, buy_cands))

    bought = []
    for c in buy_cands:
        if not trader.can_buy():
            break
        if c['symbol'] in open_syms:
            continue  # already holding this stock — no duplicate positions
        if c['price'] < MIN_STOCK_PRICE:
            cprint(f"  [SKIP]      {c['symbol']} ₹{c['price']:.2f} — below ₹{MIN_STOCK_PRICE:.0f} min price (SL gap too small)", GY)
            continue
        if is_symbol_paused(c['symbol']):
            cprint(f"  [PAUSED]    {c['symbol']} — manually paused via Telegram for today", YL)
            continue
        if not symbol_event_clear(c['symbol']):
            cprint(f"  [EVENT]     {c['symbol']} — earnings/macro event today, skip", YL)
            continue
        if not sector_cap_ok(c['symbol'], trader.open_positions, market='india'):
            continue
        if monitor and monitor.is_in_cooldown(c['symbol'], c['price']):
            cprint(f"  [COOLDOWN]  {c['symbol']} — SL hit recently, price not recovered enough", YL)
            continue
        pos, _ = trader.buy(
            c['symbol'], c['price'], c['stop_loss'], c['target'],
            c['signals'], c['reason'], c['confidence'],
            lot_size=c.get('_lot')
        )
        if pos:
            cprint(f"  ▲ BUY  {c['symbol']} @ ₹{c['price']:.2f}"
                   f"  SL ₹{c['stop_loss']:.2f}  TGT ₹{c['target']:.2f}", G)
            bought.append(c['symbol'])

    if bought:
        live_feed.subscribe(bought)


# ── End of day ────────────────────────────────────────────────────────────────
def retrain_if_needed():
    if should_retrain():
        sp()
        cprint("[ML] Retraining signal weights...", CY)
        _, acc = train()
        if acc > 0:
            send(f"🧠 <b>ML Retrained</b>\nAccuracy: {acc*100:.1f}%")
    else:
        cprint("[ML] Retrain check: not needed yet.", GY)


def end_of_day():
    global _eod_done_date
    ist = now_ist()
    if ist.weekday() >= 5 or (ist.hour, ist.minute) < (15, 15):
        return
    if _eod_done_date == ist.date():
        return
    _eod_done_date = ist.date()
    sp()
    cprint("[EOD] Generating India daily report...", CY)
    retrain_if_needed()
    stats = trader.get_daily_stats()
    send_daily_summary(
        stats['date'], stats['trades'], stats['wins'], stats['losses'],
        stats['day_pnl'], stats['balance'],
        stats['best'], stats['worst'],
        stats['win_rate'], stats['total_trades'],
        trade_list=stats.get('trade_list')
    )
    generate_daily_report()
    cprint("[EOD] Done.", CY)


def pre_market_check():
    """Runs at 9:00 AM IST — verify API, DB, balance, and open positions before market opens."""
    global _pre_market_done_date
    ist = now_ist()
    if ist.weekday() >= 5:   # skip weekends
        return
    if not (ist.hour == 9 and ist.minute < 10):
        return
    today = ist.date()
    if _pre_market_done_date == today:
        return
    _pre_market_done_date = today

    checks = []
    all_ok = True

    # 1. DB check
    try:
        from data.database import get_conn
        c = get_conn()
        c.execute("SELECT 1")
        c.close()
        checks.append("✅ DB: Connected")
    except Exception as e:
        checks.append(f"❌ DB: {e}")
        all_ok = False

    # 2. Angel One API
    try:
        from data.angel_login import get_smartapi
        obj = get_smartapi()
        if obj:
            checks.append("✅ Angel One: Connected")
        else:
            checks.append("⚠️ Angel One: Login failed")
            all_ok = False
    except Exception as e:
        checks.append(f"⚠️ Angel One: {e}")

    # 3. Balance and open positions
    try:
        bal  = trader.balance
        open_pos = len(trader.open_positions)
        checks.append(f"✅ Balance: ₹{bal:.2f}  |  Open positions: {open_pos}")
    except Exception as e:
        checks.append(f"❌ Balance check: {e}")
        all_ok = False

    # 4. Disk space (quick sanity)
    try:
        import shutil
        free_gb = shutil.disk_usage('/').free / (1024**3)
        if free_gb < 0.5:
            checks.append(f"⚠️ Disk: Only {free_gb:.1f} GB free")
            all_ok = False
        else:
            checks.append(f"✅ Disk: {free_gb:.1f} GB free")
    except Exception:
        pass

    status_emoji = "✅" if all_ok else "⚠️"
    status_label = "All systems ready" if all_ok else "Issues detected"
    msg = (
        f"{status_emoji} <b>Pre-Market Health Check — India NSE</b>\n"
        f"{ist.strftime('%d %b %Y  %I:%M %p IST')}\n"
        f"{'─'*30}\n"
        + "\n".join(checks) +
        f"\n{'─'*30}\n"
        f"<b>{status_label}</b> — Market opens at 9:15 AM IST"
    )
    send(msg)
    cprint(f"[Pre-Market] Health check sent. Status: {status_label}", CY)


_pre_market_done_date = None


def hourly_heartbeat():
    global _last_heartbeat
    ist = now_ist()
    if ist.weekday() >= 5:
        return
    h = ist.hour
    if h < 10 or h >= 15:
        return
    key = (ist.date(), h)
    if _last_heartbeat == key:
        return
    _last_heartbeat = key
    stats  = trader.get_daily_stats()
    n_open = len(trader.open_positions)
    sign   = '+' if stats['day_pnl'] >= 0 else ''
    emoji  = "📈" if stats['day_pnl'] >= 0 else "📉"
    send(
        f"{emoji} <b>India Hourly</b>  {ist.strftime('%I:%M %p IST')}\n"
        f"P&amp;L: {sign}₹{stats['day_pnl']:.2f}  |  Trades: {stats['trades']}  "
        f"(W:{stats['wins']} L:{stats['losses']})\n"
        f"Balance: ₹{trader.balance:.2f}  |  Open: {n_open}"
    )


# ── Startup ───────────────────────────────────────────────────────────────────
def main():
    global trader, live_feed, monitor

    _acquire_lock()

    sp()
    cprint("╔══════════════════════════════════════════════════════════╗", CY)
    cprint("║        [INDIA NSE]  ANGELBOT -- INDIA WORKER            ║", CY)
    cprint(f"║        Mode: {'PAPER' if PAPER_MODE else 'LIVE '}   "
           f"  {now_ist().strftime('%d %b %Y  %I:%M %p IST')}          ║", CY)
    cprint("╚══════════════════════════════════════════════════════════╝", CY)
    sp()

    trader    = PaperTrader()
    live_feed = LiveFeed()
    live_feed.start()
    monitor   = start_monitor(trader, _on_exit, live_feed, market='india')

    from data.nifty_stocks import get_all_stocks
    stock_list = get_all_stocks()
    cprint(f"  Watchlist : {len(stock_list)} stocks", CY)
    cprint(f"  Balance   : ₹{trader.balance:.2f}", CY)
    cprint(f"  Open pos  : {len(trader.open_positions)}", CY)
    sp()

    if trader.open_positions:
        live_feed.subscribe([p['symbol'] for p in trader.open_positions])

    schedule.every(1).minutes.do(run_scan)
    schedule.every(1).minutes.do(end_of_day)
    schedule.every(1).minutes.do(hourly_heartbeat)
    schedule.every(1).minutes.do(pre_market_check)

    cprint("India worker ready — scanning every minute.  Ctrl+C to stop.", GY)
    run_scan()   # fire immediately on startup

    while True:
        try:
            schedule.run_pending()
        except KeyboardInterrupt:
            raise
        except Exception as e:
            cprint(f"[WARN] Scheduler error: {e}", YL)
        time.sleep(60)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        sp()
        cprint("[India] Stopped by user.", GY)
    except Exception as e:
        import traceback
        cprint(f"[India] FATAL: {e}\n{traceback.format_exc()}", RD)
        sys.exit(1)
