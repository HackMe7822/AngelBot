"""
AngelBot — US Market Worker (Alpaca Paper Trading)
==================================================
Runs in its own console window.  NYSE hours: 9:30 AM – 4:00 PM ET, Mon–Fri.
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
BL  = _a("1;94"); YL  = _a("1;93"); GY = _a("90")
WH  = _a("97");   DIM = _a("2")

# ── Project root on path ──────────────────────────────────────────────────────
_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)

from datetime import datetime, timezone, timedelta, time as dtime

# ── File logger ───────────────────────────────────────────────────────────────
os.makedirs(os.path.join(_ROOT, 'logs'), exist_ok=True)
_ansi_re = _re.compile(r'\033\[[0-9;]*m')
_flog = _lg.getLogger('us')
_flog.setLevel(_lg.INFO)
_flog.propagate = False
_fh = _lg.FileHandler(
    os.path.join(_ROOT, 'logs', f"us_{datetime.now().strftime('%Y%m%d')}.log"),
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

# ── Pause flag ────────────────────────────────────────────────────────────────
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
        _lock_sock.bind(('127.0.0.1', 47834))
    except OSError:
        cprint("US worker already running — exiting.", GY)
        sys.exit(0)

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

US_OPEN        = dtime(9,  30)
US_CLOSE       = dtime(16,  0)
US_ENTRY_START = dtime(9,  45)   # skip first 15 min — same opening volatility as NSE
US_ENTRY_END   = dtime(15, 30)
US_FORCE_CLOSE = dtime(15, 55)

def is_us_market():
    et = now_et()
    return et.weekday() < 5 and US_OPEN <= et.time() < US_CLOSE

def _next_us_open():
    et = now_et()
    candidate = et.replace(hour=9, minute=30, second=0, microsecond=0)
    if et.time() >= US_OPEN:
        candidate = candidate + timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate = candidate + timedelta(days=1)
    tz_label = "EDT" if _is_dst() else "EST"
    diff = candidate - et
    total_mins = int(diff.total_seconds() / 60)
    h, m = divmod(total_mins, 60)
    if h >= 24:
        days, rh = divmod(h, 24)
        return candidate.strftime(f'%a %I:%M %p {tz_label}'), f"in {days}d {rh}h"
    return candidate.strftime(f'%I:%M %p {tz_label}'), f"in {h}h {m}m"

# ── Imports ───────────────────────────────────────────────────────────────────
from config import (ALPACA_KEY, ALPACA_PAPER, US_MAX_DAILY_LOSS_PCT, US_MAX_DAILY_TRADES,
                    MAX_DEPLOYED_PCT, PEAK_DRAWDOWN_PCT, BINANCE_KEY, US_MIN_STOCK_PRICE)
from trading.alpaca_trader import AlpacaTrader
from data.alpaca_client import get_us_live_price
from trading.position_monitor import start_monitor
from reporting.telegram_alerts import send_us_daily_summary, send_combined_summary
from reporting.telegram_listener import is_symbol_paused
from analysis.market_filters import us_market_mood_ok, symbol_event_clear, sector_cap_ok

# ── Global state ──────────────────────────────────────────────────────────────
us_trader      = None
us_monitor     = None
_eod_done_date = None

# ── Exit callback ─────────────────────────────────────────────────────────────
def _on_exit(pos, price, pnl, pnl_pct, reason, *_):
    sign = '+' if pnl >= 0 else ''
    cprint(f"[US EXIT] {pos['symbol']} @ ${price:.4f}  P&L: {sign}${pnl:.4f} ({sign}{pnl_pct:.2f}%)  {reason}",
           G if pnl >= 0 else RD)

# ── US scan ───────────────────────────────────────────────────────────────────
def run_us_scan():
    global _eod_done_date
    if is_paused():
        return

    ist      = now_ist()
    et       = now_et()
    today_et = et.date()

    # ── EOD force-close at 3:55 PM ET ────────────────────────────────────────
    if et.time() >= US_FORCE_CLOSE and _eod_done_date != today_et:
        _eod_done_date = today_et
        if us_trader.open_positions:
            sp()
            cprint(f"[US EOD] Force-closing all positions @ {et.strftime('%I:%M %p')} {'EDT' if _is_dst() else 'EST'}", YL)
            for pos in list(us_trader.open_positions):
                price = get_us_live_price(pos['symbol']) or pos['entry_price']
                with us_trader._lock:
                    still = next((p for p in us_trader.open_positions if p['id'] == pos['id']), None)
                    if still:
                        pnl, _ = us_trader.sell(still, price, 'US EOD force-close')
                        if pnl != 0.0:
                            c = G if pnl >= 0 else RD
                            cprint(f"  [US EOD CLOSE] {pos['symbol']} @ ${price:.2f}  P&L: ${pnl:+.2f}", c)

        # ── Send US daily report ──────────────────────────────────────────────
        stats = us_trader.get_daily_stats()
        send_us_daily_summary(
            stats['date'], stats['trades'], stats['wins'], stats['losses'],
            stats['day_pnl'], stats['balance'],
            stats['best'], stats['worst'],
            stats['win_rate'], stats['total_trades'],
            trade_list=stats.get('trade_list')
        )

        # ── Combined summary (reads India + Crypto from shared DB) ────────────
        try:
            from trading.paper_trader import PaperTrader
            _india = PaperTrader()
            india_stats = _india.get_daily_stats(stats['date'])
            cr_pnl = cr_bal = cr_trades = 0.0
            if BINANCE_KEY:
                from trading.crypto_trader import CryptoTrader
                _cr = CryptoTrader()
                _crs = _cr.get_daily_stats(stats['date'])
                cr_pnl, cr_bal, cr_trades = _crs['day_pnl'], _crs['balance'], _crs['trades']
            send_combined_summary(
                stats['date'],
                india_pnl=india_stats['day_pnl'], india_bal=india_stats['balance'],
                india_trades=india_stats['trades'],
                us_pnl=stats['day_pnl'],          us_bal=stats['balance'],
                us_trades=stats['trades'],         us_enabled=True,
                crypto_pnl=cr_pnl,                crypto_bal=cr_bal,
                crypto_trades=int(cr_trades),      crypto_enabled=bool(BINANCE_KEY)
            )
        except Exception as e:
            cprint(f"[US EOD] Combined summary error: {e}", YL)
        return

    if not is_us_market():
        tz = "EDT" if _is_dst() else "EST"
        open_at, opens_in = _next_us_open()
        sp()
        sep()
        cprint(f"  [{et.strftime('%I:%M %p')} {tz}]  US: OFF-HOURS — opens {open_at} ({opens_in})", GY)
        sep()
        return
    if not (US_ENTRY_START <= et.time() < US_ENTRY_END):
        sp()
        sep()
        tz2 = "EDT" if _is_dst() else "EST"
        cprint(f"  [{et.strftime('%I:%M %p')} {tz2}]  US: MARKET OPEN — outside entry window", YL)
        sep()
        return

    stats      = us_trader.get_daily_stats()
    loss_limit = us_trader.balance * US_MAX_DAILY_LOSS_PCT
    if stats['day_pnl'] < -loss_limit:
        cprint(f"[US] Daily loss limit hit (${stats['day_pnl']:.2f}) — no new US buys today", RD)
        return

    if stats['trades'] >= US_MAX_DAILY_TRADES:
        cprint(f"[US] Daily trade cap reached ({stats['trades']}/{US_MAX_DAILY_TRADES}) — no new buys", YL)
        return

    dd = us_trader.get_drawdown_pct()
    if dd >= PEAK_DRAWDOWN_PCT:
        cprint(f"[US] Peak drawdown {dd*100:.1f}% — pausing new buys until recovery", RD)
        return

    dep = us_trader.get_deployed_pct()
    if dep >= MAX_DEPLOYED_PCT:
        cprint(f"[US] {dep*100:.0f}% capital deployed — waiting for positions to close", YL)
        return

    if not us_market_mood_ok():
        cprint("[US] S&P 500 mood bearish — skipping US buy scan", YL)
        return

    if not us_trader.can_buy():
        return

    # ── Scan ──────────────────────────────────────────────────────────────────
    from data.us_stocks import get_us_stocks
    from data.fetcher import get_us_intraday
    from analysis.technical import (compute_indicators_intraday, generate_signals_intraday,
                                     calc_stop_loss, calc_target)
    from analysis.sentiment import get_news_sentiment
    from learning.self_learner import get_weighted_score
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from trading.scanner import MIN_SCORE, MIN_VOL_RATIO

    us_syms    = get_us_stocks()
    open_syms  = [p['symbol'] for p in us_trader.open_positions]
    watch_list = list(dict.fromkeys(us_syms + open_syms))

    sp()
    cprint(f"{'─'*21} US MARKET {'─'*29}", BL)
    sp()
    cprint(f"  [US {et.strftime('%I:%M %p')} {'EDT' if _is_dst() else 'EST'}]  Scanning {len(watch_list)} US stocks...", BL)
    sp()

    def _scan_one(sym):
        try:
            df   = get_us_intraday(sym)
            df   = compute_indicators_intraday(df)
            tech = generate_signals_intraday(df)
            if tech is None or tech['score'] < MIN_SCORE - 1:
                return None
            vol_ratio = tech.get('vol_ratio', 0)
            if vol_ratio > 0 and vol_ratio < MIN_VOL_RATIO:
                return None
            sent       = get_news_sentiment(sym, sym)
            boost      = 1 if sent['label'] == 'positive' else (-1 if sent['label'] == 'negative' else 0)
            total      = tech['score'] + boost
            if total < MIN_SCORE:
                return None
            price      = tech['price']
            confidence = min(95, max(30, total * 12 + 20))
            reason     = " + ".join(tech['reasons'])
            wscore, ml = get_weighted_score(total, tech['signals'], confidence)
            cprint(f"  [CANDIDATE] {sym}  score={total}  ml={ml:.0f}%  ${price:.2f}", BL)
            return {
                'symbol': sym, 'score': total, 'weighted_score': wscore, 'ml_prob': ml,
                'confidence': confidence, 'price': price,
                'stop_loss': calc_stop_loss(price), 'target': calc_target(price),
                'signals': tech['signals'], 'reason': reason,
            }
        except Exception as e:
            cprint(f"  US SKIP {sym}: {e}", DIM)
            return None

    candidates = []
    with ThreadPoolExecutor(max_workers=20) as ex:
        futures = {ex.submit(_scan_one, s): s for s in watch_list}
        for fut in as_completed(futures):
            try:
                r = fut.result()
                if r:
                    candidates.append(r)
            except Exception:
                pass

    candidates.sort(key=lambda x: (x['ml_prob'], x['weighted_score']), reverse=True)

    for c in candidates:
        if not us_trader.can_buy():
            break
        if c['symbol'] in open_syms:
            continue  # already holding this stock — no duplicate positions
        if c['price'] < US_MIN_STOCK_PRICE:
            cprint(f"  [SKIP]         {c['symbol']} ${c['price']:.2f} — below ${US_MIN_STOCK_PRICE:.0f} min price", GY)
            continue
        if is_symbol_paused(c['symbol']):
            cprint(f"  [US PAUSED]    {c['symbol']} — manually paused via Telegram for today", YL)
            continue
        if not symbol_event_clear(c['symbol']):
            cprint(f"  [US EVENT]     {c['symbol']} — earnings/macro event today, skip", YL)
            continue
        if not sector_cap_ok(c['symbol'], us_trader.open_positions, market='us'):
            continue
        if us_monitor and us_monitor.is_in_cooldown(c['symbol'], c['price']):
            cprint(f"  [US COOLDOWN]  {c['symbol']} — SL hit recently, price not recovered enough", YL)
            continue
        pos, _ = us_trader.buy(
            c['symbol'], c['price'], c['stop_loss'], c['target'],
            c['signals'], c['reason'], c['confidence']
        )
        if pos:
            cprint(f"  ▲ US BUY  {c['symbol']} @ ${c['price']:.2f}"
                   f"  SL ${c['stop_loss']:.2f}  TGT ${c['target']:.2f}", BL)


# ── Startup ───────────────────────────────────────────────────────────────────
def main():
    global us_trader, us_monitor

    _acquire_lock()

    sp()
    cprint("╔══════════════════════════════════════════════════════════╗", BL)
    cprint("║         [US MARKET]  ANGELBOT -- US WORKER              ║", BL)
    cprint(f"║         Mode: {'PAPER' if ALPACA_PAPER else 'LIVE '}   "
           f"  {now_et().strftime('%d %b %Y  %I:%M %p')} {'EDT' if _is_dst() else 'EST'}    ║", BL)
    cprint("╚══════════════════════════════════════════════════════════╝", BL)
    sp()

    if not ALPACA_KEY:
        cprint("ERROR: ALPACA_KEY not set in .env — US session disabled.", RD)
        sys.exit(1)

    us_trader  = AlpacaTrader()
    us_monitor = start_monitor(
        us_trader, _on_exit,
        live_feed=None, price_fn=get_us_live_price,
        always_active=False,
        market_open_fn=is_us_market,
        eod_close=False,
        currency='$', name='USMonitor', market='us'
    )

    cprint(f"  Balance : ${us_trader.balance:.2f}", BL)
    cprint(f"  Open pos: {len(us_trader.open_positions)}", BL)
    sp()

    schedule.every(1).minutes.do(run_us_scan)
    cprint("US worker ready — scanning every minute.  Ctrl+C to stop.", GY)
    run_us_scan()   # fire immediately

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
        cprint("[US] Stopped by user.", GY)
    except Exception as e:
        import traceback
        cprint(f"[US] FATAL: {e}\n{traceback.format_exc()}", RD)
        sys.exit(1)
