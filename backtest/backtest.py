"""
AngelBot Backtest Engine
========================
Replays 2 years of real OHLC history through the exact same signal logic
the live scanner uses. No lookahead bias — entry is always at the NEXT
day's open after a signal fires.

Saved trades go into the `trades` table (source='backtest') so the ML
self-learner can train on them before the live bot makes its first real trade.

Run standalone:  python3 backtest/backtest.py
"""

import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import numpy as np
from datetime import datetime

from data.fetcher       import get_historical
from data.nifty_stocks  import get_all_stocks, is_mutual_fund
from data.database      import get_conn, init_db
from analysis.technical import compute_indicators, calc_stop_loss, calc_target
from learning.self_learner import train, should_retrain

# ── Tunables ──────────────────────────────────────────────────────────────────
BACKTEST_PERIOD   = 'max'   # fetch full available history (up to 30 years)
SCAN_EVERY        = 5       # check for signals every N trading days
MAX_HOLD_DAYS     = 20      # force-exit after this many days if neither SL nor target
MIN_SCORE         = 3       # slightly lower than live (no sentiment boost in backtest)
NIFTY50 = [
    'RELIANCE','TCS','HDFCBANK','INFY','ICICIBANK','SBIN','BHARTIARTL',
    'WIPRO','TATAMOTORS','ADANIENT','ZOMATO','KOTAKBANK','LT','AXISBANK',
    'MARUTI','SUNPHARMA','NTPC','POWERGRID','HCLTECH','TECHM','NESTLEIND',
    'ULTRACEMCO','BAJFINANCE','TITAN','ASIANPAINT','INDUSINDBK','HDFCLIFE',
    'BAJAJFINSV','HINDUNILVR','ITC','DIVISLAB','DRREDDY','EICHERMOT','GRASIM',
    'HEROMOTOCO','JSWSTEEL','M&M','ONGC','SBILIFE','TATACONSUM','TATASTEEL',
    'BRITANNIA','CIPLA','COALINDIA','BPCL','UPL','APOLLOHOSP','LTIM','TATACOMM',
]


def _signals_from_rows(latest, prev):
    """
    Replicate generate_signals() logic using precomputed indicator rows.
    Returns (signals_dict, score, reasons_list).
    No sentiment — backtest uses only technical signals.
    """
    signals = {}
    score   = 0
    reasons = []

    # RSI
    rsi = latest.get('rsi')
    if rsi is not None:
        if rsi < 35:
            signals['rsi'] = 'buy';      score += 2; reasons.append(f"RSI oversold ({rsi:.0f})")
        elif rsi < 45:
            signals['rsi'] = 'weak_buy'; score += 1; reasons.append(f"RSI low ({rsi:.0f})")
        elif rsi > 70:
            signals['rsi'] = 'sell';     score -= 2
        else:
            signals['rsi'] = 'neutral'

    # MACD crossover (needs prev row)
    macd_diff      = latest.get('macd_diff')
    prev_macd_diff = prev.get('macd_diff')
    if macd_diff is not None and prev_macd_diff is not None:
        if prev_macd_diff < 0 and macd_diff > 0:
            signals['macd'] = 'buy';      score += 2; reasons.append("MACD bullish crossover")
        elif macd_diff > 0:
            signals['macd'] = 'weak_buy'; score += 1; reasons.append("MACD positive")
        elif macd_diff < 0:
            signals['macd'] = 'sell';     score -= 1
        else:
            signals['macd'] = 'neutral'

    # Bollinger Band
    bb_pct = latest.get('bb_pct')
    if bb_pct is not None:
        if bb_pct < 0.2:
            signals['bollinger'] = 'buy';  score += 2; reasons.append("Price near lower Bollinger Band")
        elif bb_pct > 0.8:
            signals['bollinger'] = 'sell'; score -= 1
        else:
            signals['bollinger'] = 'neutral'

    # EMA trend
    close = latest.get('Close')
    ema20 = latest.get('ema20')
    ema50 = latest.get('ema50')
    if all(v is not None for v in [close, ema20, ema50]):
        if close > ema20 > ema50:
            signals['ema'] = 'buy';  score += 1; reasons.append("Price above EMA20 > EMA50")
        elif close < ema20 < ema50:
            signals['ema'] = 'sell'; score -= 1
        else:
            signals['ema'] = 'neutral'

    # Volume spike
    vol_ratio = latest.get('vol_ratio')
    if vol_ratio is not None:
        if vol_ratio > 1.8:
            signals['volume'] = 'spike';  score += 1; reasons.append(f"Volume spike {vol_ratio:.1f}x")
        else:
            signals['volume'] = 'normal'

    return signals, score, reasons


def _simulate_stock(sym, df):
    """Walk forward through one stock's history and collect simulated trades."""
    trades = []
    n      = len(df)
    i      = 50   # start after enough history for all indicators

    while i < n - MAX_HOLD_DAYS - 2:
        latest = df.iloc[i].to_dict()
        prev   = df.iloc[i - 1].to_dict()

        signals, score, reasons = _signals_from_rows(latest, prev)

        if score >= MIN_SCORE:
            # Entry at next day's open — no lookahead
            entry_idx   = i + 1
            entry_price = df.iloc[entry_idx]['Open']
            entry_date  = str(df.index[entry_idx])[:19]
            atr         = latest.get('atr') or (entry_price * 0.02)
            sl          = calc_stop_loss(entry_price, atr)
            target      = calc_target(entry_price, atr)
            confidence  = min(95, max(30, score * 12 + 20))

            # Simulate exit using future OHLC
            exit_price  = None
            exit_date   = None
            exit_reason = None

            end_idx = min(entry_idx + MAX_HOLD_DAYS + 1, n)
            for j in range(entry_idx + 1, end_idx):
                day = df.iloc[j]
                # Check stop-loss first (conservative — intraday low could hit it)
                if day['Low'] <= sl:
                    exit_price  = sl
                    exit_date   = str(df.index[j])[:19]
                    exit_reason = 'Stop-loss hit'
                    break
                elif day['High'] >= target:
                    exit_price  = target
                    exit_date   = str(df.index[j])[:19]
                    exit_reason = 'Target reached'
                    break

            if exit_price is None:
                j           = min(entry_idx + MAX_HOLD_DAYS, n - 1)
                exit_price  = df.iloc[j]['Close']
                exit_date   = str(df.index[j])[:19]
                exit_reason = 'Time exit'

            pnl     = round(exit_price - entry_price, 4)   # per unit
            pnl_pct = round((pnl / entry_price) * 100, 2)

            trades.append({
                'symbol':       sym,
                'entry_price':  round(float(entry_price), 2),
                'exit_price':   round(float(exit_price),  2),
                'entry_date':   entry_date,
                'exit_date':    exit_date,
                'stop_loss':    round(float(sl),     2),
                'target':       round(float(target), 2),
                'signals':      signals,
                'confidence':   confidence,
                'score':        score,
                'reasons':      reasons,
                'exit_reason':  exit_reason,
                'pnl_pct':      pnl_pct,
                'profitable':   pnl > 0,
            })

            # Jump past this trade's exit to avoid overlapping positions on same stock
            exit_loc = None
            for k in range(entry_idx + 1, end_idx):
                if str(df.index[k])[:19] >= exit_date:
                    exit_loc = k
                    break
            i = (exit_loc + 1) if exit_loc else (i + SCAN_EVERY)
        else:
            i += SCAN_EVERY

    return trades


def _clear_old_backtest_trades():
    """Remove previous backtest trades so re-runs don't duplicate."""
    conn = get_conn()
    c    = conn.cursor()
    c.execute("DELETE FROM trades WHERE source='backtest'")
    deleted = c.rowcount
    conn.commit()
    conn.close()
    if deleted:
        print(f"  Cleared {deleted} previous backtest trades from DB.")


def _save_to_db(trades):
    """Write all backtest trades to the trades table with source='backtest'."""
    conn = get_conn()
    c    = conn.cursor()
    saved = 0
    for t in trades:
        signals_json = json.dumps({
            'signals':    t['signals'],
            'confidence': t['confidence'],
            'reasons':    " + ".join(t['reasons']),
        })
        quantity     = 1.0   # unit quantity — P&L recorded per-unit for ML purposes
        capital_used = t['entry_price']
        pnl          = round((t['exit_price'] - t['entry_price']) * quantity, 2)

        c.execute('''
            INSERT INTO trades
                (symbol, entry_time, exit_time, entry_price, exit_price,
                 quantity, capital_used, pnl, pnl_pct, stop_loss, target,
                 exit_reason, signals, status, source)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'closed','backtest')
        ''', (
            t['symbol'], t['entry_date'], t['exit_date'],
            t['entry_price'], t['exit_price'],
            quantity, capital_used, pnl, t['pnl_pct'],
            t['stop_loss'], t['target'],
            t['exit_reason'], signals_json,
        ))
        saved += 1

    conn.commit()
    conn.close()
    return saved


def _print_summary(trades):
    if not trades:
        print("[Backtest] No trades generated.")
        return

    total     = len(trades)
    wins      = sum(1 for t in trades if t['profitable'])
    losses    = total - wins
    win_rate  = (wins / total) * 100
    avg_pnl   = np.mean([t['pnl_pct'] for t in trades])
    best      = max(trades,  key=lambda t: t['pnl_pct'])
    worst     = min(trades,  key=lambda t: t['pnl_pct'])

    # Signal combo win rates
    combo_stats = {}
    for t in trades:
        key = tuple(sorted((k, v) for k, v in t['signals'].items()))
        if key not in combo_stats:
            combo_stats[key] = {'wins': 0, 'total': 0}
        combo_stats[key]['total'] += 1
        if t['profitable']:
            combo_stats[key]['wins'] += 1

    top_combos = sorted(
        combo_stats.items(),
        key=lambda x: x[1]['wins'] / x[1]['total'] if x[1]['total'] >= 3 else 0,
        reverse=True
    )[:3]

    exit_reasons = {}
    for t in trades:
        exit_reasons[t['exit_reason']] = exit_reasons.get(t['exit_reason'], 0) + 1

    # Date range across all simulated trades
    all_dates  = [t['entry_date'] for t in trades]
    date_from  = min(all_dates)[:10]
    date_to    = max(all_dates)[:10]

    print("\n" + "═" * 56)
    print(f"  BACKTEST RESULTS  ({date_from} → {date_to})")
    print("═" * 56)
    print(f"  Total trades  : {total}")
    print(f"  Wins          : {wins}  ({win_rate:.1f}%)")
    print(f"  Losses        : {losses}")
    print(f"  Avg P&L/trade : {avg_pnl:+.2f}%")
    print(f"  Best trade    : {best['symbol']}  {best['pnl_pct']:+.1f}%  ({best['exit_reason']})")
    print(f"  Worst trade   : {worst['symbol']}  {worst['pnl_pct']:+.1f}%  ({worst['exit_reason']})")
    print(f"\n  Exit breakdown:")
    for reason, count in sorted(exit_reasons.items(), key=lambda x: -x[1]):
        pct = count / total * 100
        print(f"    {reason:20s}: {count:3d}  ({pct:.0f}%)")
    print(f"\n  Top signal combos (win rate, min 3 trades):")
    for combo, stats in top_combos:
        wr  = stats['wins'] / stats['total'] * 100
        sig = {k: v for k, v in combo}
        buys = [k for k, v in sig.items() if 'buy' in v]
        print(f"    {'+'.join(buys):30s}: {wr:.0f}%  ({stats['total']} trades)")
    print("═" * 56 + "\n")


def run_backtest(symbols=None, retrain=True, verbose=True):
    """
    Main entry point.
    Returns (all_trades, weights, model_accuracy).
    Set retrain=False to skip ML training (for testing only).
    """
    init_db()

    if symbols is None:
        symbols = NIFTY50

    symbols = [s for s in symbols if not is_mutual_fund(s)]

    if verbose:
        print(f"\n{'═'*56}")
        print(f"  AngelBot Backtest — {len(symbols)} stocks · {BACKTEST_PERIOD} history")
        print(f"{'═'*56}")

    all_trades = []
    for idx, sym in enumerate(symbols, 1):
        try:
            if verbose:
                print(f"  [{idx:2d}/{len(symbols)}] {sym:<14s}", end='', flush=True)
            df = get_historical(sym, period=BACKTEST_PERIOD)
            if df is None or len(df) < 60:
                if verbose: print("  — skipped (insufficient data)")
                continue
            df = compute_indicators(df)
            if df is None or len(df) < 52:
                if verbose: print("  — skipped (indicators failed)")
                continue

            stock_trades = _simulate_stock(sym, df)
            all_trades.extend(stock_trades)

            if verbose:
                wins = sum(1 for t in stock_trades if t['profitable'])
                wr   = (wins / len(stock_trades) * 100) if stock_trades else 0
                print(f"  {len(stock_trades):3d} trades  win={wr:.0f}%")
        except Exception as e:
            if verbose: print(f"  — error: {e}")
            continue

    if not all_trades:
        print("[Backtest] No trades generated — check data availability.")
        return [], {}, 0.0

    _clear_old_backtest_trades()
    saved = _save_to_db(all_trades)
    if verbose:
        print(f"\n  Saved {saved} backtest trades to database.")

    _print_summary(all_trades)

    weights, accuracy = {}, 0.0
    if retrain:
        print("[ML] Training model on full history (recency-weighted)...")
        # include_backtest=True: use simulated history to pre-warm model before any paper trades exist
        weights, accuracy = train(min_trades=10, include_backtest=True)

    return all_trades, weights, accuracy


def backtest_already_run():
    """Returns True if backtest trades already exist in the DB."""
    try:
        conn = get_conn()
        c    = conn.cursor()
        c.execute("SELECT COUNT(*) FROM trades WHERE source='backtest'")
        count = c.fetchone()[0]
        conn.close()
        return count >= 10
    except Exception:
        return False


if __name__ == "__main__":
    trades, weights, acc = run_backtest()
    if acc > 0:
        print(f"ML model trained — accuracy {acc*100:.1f}%")
        print("Signal weights:")
        for k, v in weights.items():
            bar = '█' * int(v * 10)
            print(f"  {k:12s}: {v:.3f}  {bar}")
