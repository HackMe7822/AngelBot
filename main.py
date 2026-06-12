"""
AngelBot — Main Monitor
=======================
Initialises the database, runs the first-time ML backtest, spawns the three
market worker windows (India / US / Crypto), starts the Telegram command
listener, and prints a combined status snapshot every minute.

This window never scans or trades — it is the coordinator.
Workers run independently in their own console windows.
"""
import sys, os, time, socket, subprocess, warnings
import logging as _lg, re as _re

# ── Suppress noisy warnings ───────────────────────────────────────────────────
warnings.filterwarnings('ignore', category=DeprecationWarning)
warnings.filterwarnings('ignore', category=PendingDeprecationWarning)
try:
    from sklearn.exceptions import InconsistentVersionWarning
    warnings.filterwarnings('ignore', category=InconsistentVersionWarning)
except ImportError:
    pass

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
BL  = _a("1;94"); MG  = _a("1;95"); WH = _a("97")

# ── Project root ──────────────────────────────────────────────────────────────
_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)

from datetime import datetime, timezone, timedelta, time as dtime

# ── File logger for this window ───────────────────────────────────────────────
os.makedirs(os.path.join(_ROOT, 'logs'), exist_ok=True)
_ansi_re = _re.compile(r'\033\[[0-9;]*m')
_flog = _lg.getLogger('main')
_flog.setLevel(_lg.INFO)
_flog.propagate = False
_fh = _lg.FileHandler(
    os.path.join(_ROOT, 'logs', f"main_{datetime.now().strftime('%Y%m%d')}.log"),
    encoding='utf-8'
)
_fh.setFormatter(_lg.Formatter('%(asctime)s  %(message)s', '%Y-%m-%d %H:%M:%S'))
_flog.addHandler(_fh)


def cprint(msg, color=WH):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    sys.__stdout__.write(f"{color}{ts}  {msg}{R}\n")
    sys.__stdout__.flush()
    _flog.info(_ansi_re.sub('', msg))

def sp():
    sys.__stdout__.write('\n')
    sys.__stdout__.flush()

def sep(color=GY):
    cprint('─' * 60, color)


# ── Single-instance lock (main process only) ──────────────────────────────────
_LOCK_PORT   = 47832
_lock_socket = None

def _acquire_lock():
    global _lock_socket
    _lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    _lock_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    try:
        _lock_socket.bind(('127.0.0.1', _LOCK_PORT))
    except OSError:
        print("ERROR: AngelBot is already running. Only one instance allowed. Exiting.")
        sys.exit(1)

def _release_lock():
    try:
        _lock_socket.close()
    except Exception:
        pass


# ── Time helpers ──────────────────────────────────────────────────────────────
IST = timezone(timedelta(hours=5, minutes=30))
def now_ist(): return datetime.now(IST)

def _is_dst():
    utc = datetime.now(timezone.utc)
    y   = utc.year
    mar = datetime(y, 3, 1, tzinfo=timezone.utc)
    nov = datetime(y, 11, 1, tzinfo=timezone.utc)
    dst_start = mar + timedelta(days=(6 - mar.weekday()) % 7) + timedelta(weeks=1, hours=7)
    dst_end   = nov + timedelta(days=(6 - nov.weekday()) % 7) + timedelta(hours=6)
    return dst_start <= utc < dst_end

def now_et():
    return datetime.now(timezone(timedelta(hours=-4 if _is_dst() else -5)))

def _india_state():
    now = now_ist()
    if now.weekday() >= 5:
        return "WEEKEND"
    return "SCANNING" if dtime(9, 15) <= now.time() <= dtime(15, 30) else "CLOSED"

def _us_state():
    et = now_et()
    if not ALPACA_KEY:
        return "DISABLED"
    if et.weekday() >= 5:
        return "WEEKEND"
    t = et.time()
    if dtime(9, 30) <= t < dtime(15, 55):
        return "SCANNING"
    if dtime(15, 55) <= t < dtime(16, 0):
        return "EOD/CLOSING"
    tz = "EDT" if _is_dst() else "EST"
    return f"OFF ({t.strftime('%I:%M %p')} {tz})"

def _crypto_state(h, m):
    if not BINANCE_KEY:
        return "DISABLED"
    if CRYPTO_SCAN_SKIP_START <= (h, m) < CRYPTO_SCAN_SKIP_END:
        return "PAUSED 2–5 AM"
    return "SCANNING"


# ── Pause flag ────────────────────────────────────────────────────────────────
_PAUSE_FLAG = os.path.join(_ROOT, 'paused.flag')
def is_paused(): return os.path.exists(_PAUSE_FLAG)


# ── Worker spawning ───────────────────────────────────────────────────────────
PY = sys.executable   # use same Python interpreter as the one running main.py

def _spawn(script, title):
    full = os.path.join(_ROOT, script)
    if not os.path.exists(full):
        cprint(f"  [WARN] {script} not found — skipping", YL)
        return
    try:
        if sys.platform == 'win32':
            subprocess.Popen(
                [PY, script],
                creationflags=subprocess.CREATE_NEW_CONSOLE,
                cwd=_ROOT
            )
        elif sys.platform == 'darwin':
            # macOS: open a new Terminal window per worker
            # Use single quotes for shell paths — double quotes break AppleScript string literals
            cmd = f"cd '{_ROOT}' && '{PY}' '{full}'"
            subprocess.Popen([
                'osascript', '-e',
                f'tell application "Terminal" to do script "{cmd}"'
            ])
        else:
            subprocess.Popen(
                ['xterm', '-title', title, '-e', PY, full],
                cwd=_ROOT
            )
        cprint(f"  ✓ Spawned: {title}", GY)
    except Exception as e:
        cprint(f"  ✗ Failed to spawn {title}: {e}", RD)


# ── App imports ───────────────────────────────────────────────────────────────
from config import (PAPER_MODE,
                    ALPACA_KEY, ALPACA_PAPER,
                    BINANCE_KEY, BINANCE_PAPER,
                    CRYPTO_SCAN_SKIP_START, CRYPTO_SCAN_SKIP_END)
from data.database import init_db
from reporting.telegram_listener import (start_listener, set_trader,
                                          set_us_trader, set_crypto_trader)
from reporting.telegram_alerts import send
from backtest.backtest import run_backtest, backtest_already_run


# ── Combined status snapshot (printed every minute in this window) ────────────
_india_t  = None
_us_t     = None
_crypto_t = None

def _print_status():
    ist   = now_ist()
    h, m  = ist.hour, ist.minute
    paused_tag = "  ⏸ PAUSED" if is_paused() else ""

    sp()
    sep()
    cprint(
        f"  [{ist.strftime('%I:%M %p IST')}]  "
        f"India: {_india_state()}  |  "
        f"US: {_us_state()}  |  "
        f"Crypto: {_crypto_state(h, m)}"
        f"{paused_tag}",
        GY
    )
    sep()

    rows = [
        (_india_t,  "[IN] India  ", "₹"),
        (_us_t,     "[US] US     ", "$"),
        (_crypto_t, "[CR] Crypto ", "$"),
    ]
    for trader, label, cur in rows:
        if not trader:
            continue
        try:
            trader.reload()
            stats    = trader.get_daily_stats()
            deployed = sum(p['capital_used'] for p in trader.open_positions)
            pnl      = stats['day_pnl']
            n        = len(trader.open_positions)
            sign     = '+' if pnl >= 0 else '-'
            c        = G  if pnl >= 0 else RD
            cprint(
                f"  {label}  Cash:{cur}{trader.balance:.0f}"
                f"  Deployed:{cur}{deployed:.0f}"
                f"  Open:{n}"
                f"  P&L:{sign}{cur}{abs(pnl):.2f}",
                c
            )
        except Exception:
            pass

    sep()


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    global _india_t, _us_t, _crypto_t

    _acquire_lock()
    import atexit
    atexit.register(_release_lock)

    # ── Main monitor window header ────────────────────────────────────────────
    sp()
    cprint("╔══════════════════════════════════════════════════════════╗", CY)
    cprint("║         [BOT]  ANGELBOT  --  MAIN MONITOR               ║", CY)
    cprint(f"║  Mode: {'PAPER TRADING' if PAPER_MODE else 'LIVE TRADING '}"
           f"   {now_ist().strftime('%d %b %Y  %I:%M %p IST')}         ║", CY)
    cprint("╚══════════════════════════════════════════════════════════╝", CY)
    sp()

    # ── Database init (must complete before workers start) ───────────────────
    cprint("Initialising database...", GY)
    init_db()
    cprint("Database ready.", GY)
    sp()

    # ── First-run ML backtest ─────────────────────────────────────────────────
    if not backtest_already_run():
        cprint("[Backtest] First launch — running 2-year backtest to pre-train ML model...", CY)
        send("⏳ <b>First Launch</b> — running 2-year backtest to pre-train ML.\nThis takes ~2 minutes...")
        _, _, acc = run_backtest()
        if acc > 0:
            send(f"🧠 <b>ML Model Ready</b>\nPre-trained on 2 years of Nifty 50 history.\nAccuracy: {acc*100:.1f}%")
        cprint("[Backtest] Done — ML model trained and ready.", CY)
    else:
        cprint("[Backtest] Already run — ML model ready.", GY)
    sp()

    # ── Create read-only trader instances for Telegram commands ──────────────
    # Workers own the live in-memory state; these reload from DB on every command.
    from trading.paper_trader import PaperTrader
    _india_t = PaperTrader()
    cprint(f"India  — Balance: ₹{_india_t.balance:.2f}  |  Open: {len(_india_t.open_positions)}", CY)

    if ALPACA_KEY:
        from trading.alpaca_trader import AlpacaTrader
        _us_t = AlpacaTrader()
        cprint(f"US     — Balance: ${_us_t.balance:.2f}  |  Open: {len(_us_t.open_positions)}", BL)

    if BINANCE_KEY:
        from trading.crypto_trader import CryptoTrader
        _crypto_t = CryptoTrader()
        cprint(f"Crypto — Balance: ${_crypto_t.balance:.2f}  |  Open: {len(_crypto_t.open_positions)}", MG)

    sp()

    # ── Telegram command listener ─────────────────────────────────────────────
    set_trader(_india_t)
    if _us_t:     set_us_trader(_us_t)
    if _crypto_t: set_crypto_trader(_crypto_t)
    start_listener()
    sp()

    # ── Spawn market workers in separate console windows ──────────────────────
    cprint("Spawning market workers...", CY)
    _spawn('india_worker.py',  'AngelBot - India NSE')
    time.sleep(0.5)
    if ALPACA_KEY:
        _spawn('us_worker.py', 'AngelBot - US Market')
        time.sleep(0.5)
    if BINANCE_KEY:
        _spawn('crypto_worker.py', 'AngelBot - Crypto 24/7')

    sp()
    cprint("All workers launched.  This window = combined status + Telegram.", GY)
    cprint("Press Ctrl+C to stop.", GY)
    sp()

    # ── Telegram startup message ──────────────────────────────────────────────
    us_info     = f"${_us_t.balance:.2f} USD"     if _us_t     else "disabled"
    crypto_info = f"${_crypto_t.balance:.2f} USDT" if _crypto_t else "disabled"
    send(
        f"🤖 <b>AngelBot Started</b>\n"
        f"Mode: {'Paper Trading' if PAPER_MODE else 'LIVE'}\n"
        f"🇮🇳 India:  ₹{_india_t.balance:.2f}\n"
        f"🇺🇸 US:     {us_info}\n"
        f"🪙 Crypto: {crypto_info}\n"
        f"MF safety block: ON"
    )

    # ── Status loop — prints combined snapshot every minute ───────────────────
    _print_status()
    while True:
        try:
            time.sleep(60)
            _print_status()
        except KeyboardInterrupt:
            raise
        except Exception as e:
            cprint(f"[WARN] Monitor loop error: {e}", YL)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        sp()
        cprint("[AngelBot] Stopped by user (Ctrl+C). Goodbye.", GY)
        try:
            send("⏹ <b>AngelBot Stopped</b>\nManually stopped. Restart run.bat to resume.")
        except Exception:
            pass
    except Exception as e:
        import traceback
        err = traceback.format_exc()
        cprint(f"\n[AngelBot] FATAL ERROR — bot will restart:\n{err}", RD)
        try:
            send(f"🚨 <b>AngelBot Crashed</b>\n<code>{str(e)[:200]}</code>\nAuto-restarting via run.bat...")
        except Exception:
            pass
        sys.exit(1)
