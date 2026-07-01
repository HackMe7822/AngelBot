"""
AngelBot — Crypto Worker (Binance prices, simulated trades)
===========================================================
Runs in its own console window.  24/7 except 2–5 AM IST (low liquidity).
Spawned automatically by main.py (do not run directly in production).
No real orders are ever placed — BINANCE_PAPER=true is a hard code guard.
"""
import sys, os, time, schedule, warnings
import logging as _lg, re as _re

# ── Suppress noisy warnings (Binance fires DeprecationWarning every second) ──
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
MG  = _a("1;95"); YL  = _a("1;93"); GY = _a("90")
WH  = _a("97");   DIM = _a("2")

# ── Project root on path ──────────────────────────────────────────────────────
_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)

from datetime import datetime, timezone, timedelta

# ── File logger ───────────────────────────────────────────────────────────────
os.makedirs(os.path.join(_ROOT, 'logs'), exist_ok=True)
_ansi_re = _re.compile(r'\033\[[0-9;]*m')
_flog = _lg.getLogger('crypto')
_flog.setLevel(_lg.INFO)
_flog.propagate = False
_log_date = [None]
_fh = [None]

def _ensure_log_rotated():
    today = datetime.now().strftime('%Y%m%d')
    if today != _log_date[0]:
        if _fh[0]:
            _flog.removeHandler(_fh[0])
            _fh[0].close()
        _log_date[0] = today
        h = _lg.FileHandler(os.path.join(_ROOT, 'logs', f"crypto_{today}.log"), encoding='utf-8')
        h.setFormatter(_lg.Formatter('%(asctime)s  %(message)s', '%Y-%m-%d %H:%M:%S'))
        _flog.addHandler(h)
        _fh[0] = h

_ensure_log_rotated()


def cprint(msg, color=WH):
    _ensure_log_rotated()
    ts = datetime.now().strftime('%Y-%m-%d %I:%M:%S %p')
    sys.__stdout__.write(f"{color}{ts}  {msg}{R}\n")
    sys.__stdout__.flush()
    _flog.info(_ansi_re.sub('', msg))

def sp():
    sys.__stdout__.write('\n')
    sys.__stdout__.flush()

def sep(color=GY):
    cprint('─' * 60, color)

# ── Per-user identity ─────────────────────────────────────────────────────────
_USER_ID   = int(os.getenv('ANGELBOT_USER_ID', '1'))

# ── Pause check ───────────────────────────────────────────────────────────────
_PAUSE_FLAG = os.path.join(_ROOT, 'paused.flag')
def is_paused():
    if _USER_ID == 1:
        return os.path.exists(_PAUSE_FLAG)
    try:
        from data.database import get_conn as _gc
        _c = _gc(); _cur = _c.cursor()
        _cur.execute("SELECT paused FROM user_config WHERE user_id=?", (_USER_ID,))
        _r = _cur.fetchone(); _c.close()
        return bool(_r[0]) if _r else False
    except Exception:
        return os.path.exists(_PAUSE_FLAG)

# ── Single-instance lock ──────────────────────────────────────────────────────
import socket as _sock
_lock_sock = None

def _acquire_lock():
    global _lock_sock
    _lock_sock = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
    _lock_sock.setsockopt(_sock.SOL_SOCKET, _sock.SO_REUSEADDR, 0)
    try:
        _lock_sock.bind(('127.0.0.1', 47835))
    except OSError:
        cprint("Crypto worker already running — exiting.", GY)
        sys.exit(0)

# ── Time helpers ──────────────────────────────────────────────────────────────
IST = timezone(timedelta(hours=5, minutes=30))
def now_ist(): return datetime.now(IST)

# ── Imports ───────────────────────────────────────────────────────────────────
from config import (BINANCE_KEY, BINANCE_PAPER,
                    CRYPTO_MAX_DAILY_LOSS_PCT, CRYPTO_MAX_DAILY_TRADES,
                    CRYPTO_MAX_DEPLOYED_PCT, CRYPTO_PEAK_DRAWDOWN_PCT, CRYPTO_MAX_CONCURRENT,
                    CRYPTO_SCAN_SKIP_START, CRYPTO_SCAN_SKIP_END,
                    CRYPTO_TARGET_PCT, CRYPTO_SL_PCT,
                    CRYPTO_HIGH_LIQ_START, CRYPTO_HIGH_LIQ_END,
                    CRYPTO_BTC_MIN_CHANGE)
from trading.crypto_trader import CryptoTrader
from data.crypto_feed import CryptoFeed
from data.binance_client import test_connection, get_crypto_live_price
from trading.position_monitor import start_monitor
from reporting.telegram_alerts import send_crypto_daily_summary
from reporting.telegram_listener import is_symbol_paused

# ── Global state ──────────────────────────────────────────────────────────────
crypto_trader      = None
crypto_monitor     = None
crypto_feed        = None
_eod_done_date     = None

# ── BTC trend filter ─────────────────────────────────────────────────────────
def _btc_is_trending_up():
    """Return True if BTC is not in a downtrend — safe to buy alts.
    Compares current BTC price against the close of 1 hour ago.
    Returns True on error (fail-open) so a bad API call doesn't freeze the bot.
    """
    try:
        from data.binance_client import get_crypto_ohlcv, get_crypto_live_price
        df = get_crypto_ohlcv('BTCUSDT', interval='1h', limit=3)
        if df is None or len(df) < 2:
            return True
        prev_close  = float(df.iloc[-2]['close'])
        curr_price  = get_crypto_live_price('BTCUSDT') or float(df.iloc[-1]['close'])
        change_pct  = (curr_price - prev_close) / prev_close * 100
        if change_pct <= CRYPTO_BTC_MIN_CHANGE:
            cprint(f"  [BTC FILTER]  BTC {change_pct:+.2f}% in last 1h — skipping all alt buys", YL)
            return False
        return True
    except Exception:
        return True  # fail-open: can't check BTC, allow scan

# ── Exit callback ─────────────────────────────────────────────────────────────
def _on_exit(pos, price, pnl, pnl_pct, reason, *_):
    sign = '+' if pnl >= 0 else ''
    cprint(f"[CRYPTO EXIT] {pos['symbol']} @ ${price:.4f}  P&L: {sign}${pnl:.4f} ({sign}{pnl_pct:.2f}%)  {reason}",
           G if pnl >= 0 else RD)

# ── Crypto scan ───────────────────────────────────────────────────────────────
def run_crypto_scan():
    global _eod_done_date
    if is_paused():
        return

    ist = now_ist()
    h, m   = ist.hour, ist.minute
    today  = ist.date()

    # ── Midnight daily report ─────────────────────────────────────────────────
    if h == 0 and m < 2 and _eod_done_date != today:
        _eod_done_date = today
        stats = crypto_trader.get_daily_stats()
        if stats['trades'] > 0:
            send_crypto_daily_summary(
                stats['date'], stats['trades'], stats['wins'], stats['losses'],
                stats['day_pnl'], stats['balance'],
                stats['best'], stats['worst'],
                stats['win_rate'], stats['total_trades'],
                trade_list=stats.get('trade_list')
            )
            cprint(f"[Crypto EOD] Daily report sent for {stats['date']}", MG)

    # ── Skip dead-zone (2–5 AM IST — lowest global liquidity) ───────────────
    if CRYPTO_SCAN_SKIP_START <= (h, m) < CRYPTO_SCAN_SKIP_END:
        return

    # ── Only place NEW buys during US market hours (7 PM – 1 AM IST) ────────
    # Open positions are always monitored — this only gates new entries
    in_high_liq = (
        (h, m) >= CRYPTO_HIGH_LIQ_START or (h, m) < CRYPTO_HIGH_LIQ_END
    )
    if not in_high_liq:
        return   # outside US hours — monitor existing positions, no new buys

    stats      = crypto_trader.get_daily_stats()
    loss_limit = crypto_trader.balance * CRYPTO_MAX_DAILY_LOSS_PCT
    if stats['day_pnl'] < -loss_limit:
        cprint(f"[Crypto] Daily loss limit hit (${stats['day_pnl']:.4f}) — no new crypto buys", RD)
        return

    if stats['trades'] >= CRYPTO_MAX_DAILY_TRADES:
        cprint(f"[Crypto] Daily trade cap reached ({stats['trades']}/{CRYPTO_MAX_DAILY_TRADES}) — no new buys", YL)
        return

    dd = crypto_trader.get_drawdown_pct()
    if dd >= CRYPTO_PEAK_DRAWDOWN_PCT:
        cprint(f"[Crypto] Peak drawdown {dd*100:.1f}% — pausing new buys until recovery", RD)
        return

    dep = crypto_trader.get_deployed_pct()
    if dep >= CRYPTO_MAX_DEPLOYED_PCT:
        cprint(f"[Crypto] {dep*100:.0f}% capital deployed — waiting for positions to close", YL)
        return

    if len(crypto_trader.open_positions) >= CRYPTO_MAX_CONCURRENT:
        cprint(f"[Crypto] Max concurrent positions ({CRYPTO_MAX_CONCURRENT}) reached — waiting", YL)
        return

    if not crypto_trader.can_buy():
        return

    # ── Scan ──────────────────────────────────────────────────────────────────
    from data.crypto_symbols import get_crypto_symbols
    from data.binance_client import get_crypto_ohlcv
    from analysis.technical import (compute_indicators_intraday, generate_signals_intraday,
                                     calc_stop_loss, calc_target)
    from analysis.sentiment import get_news_sentiment
    from learning.self_learner import get_weighted_score
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from trading.scanner import MIN_SCORE

    symbols    = get_crypto_symbols()
    open_syms  = [p['symbol'] for p in crypto_trader.open_positions]
    watch_list = list(dict.fromkeys(symbols + open_syms))

    sp()
    cprint(f"{'─'*23} CRYPTO {'─'*30}", MG)
    sp()
    cprint(f"  [Crypto {ist.strftime('%I:%M %p')} IST]  Scanning {len(watch_list)} crypto pairs...", MG)
    sp()

    def _scan_one(sym):
        try:
            # 15m candles — less noise than 5m, more reliable signals for crypto
            df = get_crypto_ohlcv(sym, interval='15m', limit=100)
            if df is None or len(df) < 30:
                return None
            df   = compute_indicators_intraday(df)
            tech = generate_signals_intraday(df)
            if tech is None or tech['score'] < MIN_SCORE - 1:
                return None
            coin       = sym.replace('USDT', '').replace('BUSD', '')
            sent       = get_news_sentiment(coin, coin)
            boost      = 1 if sent['label'] == 'positive' else (-1 if sent['label'] == 'negative' else 0)
            total      = tech['score'] + boost
            if total < MIN_SCORE:
                return None
            price      = tech['price']
            confidence = min(95, max(30, total * 12 + 20))
            reason     = " + ".join(tech['reasons'])
            wscore, ml = get_weighted_score(total, tech['signals'], confidence)
            cprint(f"  [CANDIDATE] {sym}  score={total}  ml={ml:.0f}%  ${price:.4f}", MG)
            return {
                'symbol': sym, 'score': total, 'weighted_score': wscore, 'ml_prob': ml,
                'confidence': confidence, 'price': price,
                # Use crypto-specific wider SL/target — not the stock values
                'stop_loss': calc_stop_loss(price, sl_pct=CRYPTO_SL_PCT),
                'target':    calc_target(price, target_pct=CRYPTO_TARGET_PCT),
                'signals': tech['signals'], 'reason': reason,
            }
        except Exception as e:
            cprint(f"  CRYPTO SKIP {sym}: {e}", DIM)
            return None

    candidates = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(_scan_one, s): s for s in watch_list}
        for fut in as_completed(futures):
            try:
                r = fut.result()
                if r:
                    candidates.append(r)
            except Exception:
                pass

    candidates.sort(key=lambda x: (x['ml_prob'], x['weighted_score']), reverse=True)

    if not candidates:
        return

    # BTC trend gate — if BTC is falling, skip all alt buys this cycle
    if not _btc_is_trending_up():
        return

    for c in candidates:
        if not crypto_trader.can_buy():
            break
        if c['symbol'] in open_syms:
            continue  # already holding this coin — no duplicate positions
        if is_symbol_paused(c['symbol']):
            cprint(f"  [CRYPTO PAUSED]  {c['symbol']} — manually paused via Telegram for today", YL)
            continue
        # No sector cap for crypto — BTC trend filter already gates the session
        if crypto_monitor and crypto_monitor.is_in_cooldown(c['symbol'], c['price']):
            cprint(f"  [CRYPTO COOLDOWN]  {c['symbol']} — SL hit recently, price not recovered enough", YL)
            continue
        pos, _ = crypto_trader.buy(
            c['symbol'], c['price'], c['stop_loss'], c['target'],
            c['signals'], c['reason'], c['confidence']
        )
        if pos:
            cprint(f"  ▲ CRYPTO BUY  {c['symbol']} @ ${c['price']:.4f}"
                   f"  SL ${c['stop_loss']:.4f}  TGT ${c['target']:.4f}", MG)


# ── Startup ───────────────────────────────────────────────────────────────────
def main():
    global crypto_trader, crypto_monitor, crypto_feed

    _acquire_lock()

    sp()
    cprint("╔══════════════════════════════════════════════════════════╗", MG)
    cprint("║        [CRYPTO 24/7]  ANGELBOT -- CRYPTO WORKER         ║", MG)
    cprint(f"║        Mode: {'PAPER (simulation)' if BINANCE_PAPER else 'LIVE       '}   "
           f"  {now_ist().strftime('%d %b %Y  %I:%M %p IST')}  ║", MG)
    cprint("╚══════════════════════════════════════════════════════════╝", MG)
    sp()

    if not BINANCE_KEY:
        cprint("ERROR: BINANCE_KEY not set in .env — Crypto session disabled.", RD)
        sys.exit(1)

    crypto_trader = CryptoTrader(user_id=_USER_ID)

    # test_connection() imports binance.client in main thread first —
    # prevents Python 3.14 import deadlock when CryptoFeed thread starts
    ok, msg = test_connection()
    cprint(f"  Binance  : {'✅ ' + msg if ok else '⚠  fallback (yfinance)'}", MG)

    crypto_feed    = CryptoFeed()
    crypto_feed.start()
    crypto_monitor = start_monitor(
        crypto_trader, _on_exit,
        live_feed=crypto_feed,
        price_fn=get_crypto_live_price,
        always_active=True,
        currency='$', name='CryptoMonitor',
        sl_pct=CRYPTO_SL_PCT,
        target_pct=CRYPTO_TARGET_PCT,
        market='crypto'
    )

    cprint(f"  Balance  : ${crypto_trader.balance:.2f}", MG)
    cprint(f"  Open pos : {len(crypto_trader.open_positions)}", MG)
    sp()

    schedule.every(1).minutes.do(run_crypto_scan)
    cprint("Crypto worker ready — scanning every minute.  Ctrl+C to stop.", GY)
    run_crypto_scan()   # fire immediately

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
        cprint("[Crypto] Stopped by user.", GY)
    except Exception as e:
        import traceback
        cprint(f"[Crypto] FATAL: {e}\n{traceback.format_exc()}", RD)
        sys.exit(1)
