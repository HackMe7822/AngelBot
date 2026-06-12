"""
2-minute Telegram alert smoke test.
Fires every alert type with realistic dummy data.
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(__file__))

from reporting.telegram_alerts import (
    send, send_buy_alert, send_hold_alert,
    send_sell_alert, send_reload_alert, send_daily_summary
)

def pause(secs, label):
    print(f"  ⏳ waiting {secs}s — {label}...")
    time.sleep(secs)

print("=" * 50)
print("  AngelBot — Telegram Alert Smoke Test")
print("=" * 50)

# ── 0. Banner ─────────────────────────────────────────
send("🧪 <b>Alert Smoke Test Starting</b>\nYou should receive 6 alerts over ~90 seconds.")
print("✅  Banner sent")
pause(6, "next: BUY alert")

# ── 1. Buy alert ──────────────────────────────────────
send_buy_alert(
    symbol      = "RELIANCE",
    entry       = 2845.50,
    stop_loss   = 2802.00,
    target      = 2930.00,
    capital_used= 284550,   # would be lower in real ₹1k mode
    quantity    = 100,
    reason      = "EMA crossover + MACD buy signal + RSI recovery",
    confidence  = 78,
    ml_prob     = 71.4,
    sentiment   = "positive",
    score       = 6,
)
print("✅  BUY alert sent")
pause(12, "next: HOLD alert (SL hit but reversal spotted)")

# ── 2. Hold alert — SL touched but reversal signals ───
send_hold_alert(
    symbol   = "RELIANCE",
    price    = 2799.10,
    stop_loss= 2802.00,
    reason   = "SL hit but RSI deeply oversold (28) + positive news + weak_buy MACD — watching for reversal",
    balance  = 1000.00,
)
print("✅  HOLD alert sent")
pause(15, "next: PARTIAL EXIT alert")

# ── 3. Partial sell — target hit, momentum still strong
send_sell_alert(
    symbol       = "RELIANCE",
    entry        = 2845.50,
    exit_price   = 2931.00,
    pnl          = 4275.00,
    pnl_pct      = 3.00,
    duration     = "0:47:32",
    balance      = 1000.00,
    reason       = "Target reached but momentum strong (MACD=buy, EMA=buy, news=positive) — selling half, new target ₹3010.00",
    exit_type    = "partial",
    qty_sold     = 50,
    qty_remaining= 50,
    new_target   = 3010.00,
    day_pnl      = 4275.00,
    day_trades   = 1,
)
print("✅  PARTIAL EXIT alert sent")
pause(15, "next: FULL EXIT alert (loss)")

# ── 4. Full sell — stop-loss exit (loss)
send_sell_alert(
    symbol    = "ZOMATO",
    entry     = 218.40,
    exit_price= 212.75,
    pnl       = -56.50,
    pnl_pct   = -2.59,
    duration  = "1:12:05",
    balance   = 943.50,
    reason    = "Stop-loss hit  (RSI=52, news=neutral, MACD=sell)",
    exit_type = "full",
    qty_sold  = 10,
    day_pnl   = 4218.50,
    day_trades= 2,
)
print("✅  FULL EXIT (loss) alert sent")
pause(12, "next: AUTO-RELOAD alert")

# ── 5. Auto-reload ────────────────────────────────────
send_reload_alert(
    old_balance    = 87.30,
    new_balance    = 1087.30,
    reload_count   = 1,
    total_invested = 2000.00,
)
print("✅  AUTO-RELOAD alert sent")
pause(12, "next: DAILY SUMMARY")

# ── 6. Daily summary ──────────────────────────────────
send_daily_summary(
    date        = "2026-05-07",
    trades      = 5,
    wins        = 3,
    losses      = 2,
    day_pnl     = 312.80,
    balance     = 1087.30,
    best        = "RELIANCE +₹428.50",
    worst       = "ZOMATO -₹56.50",
    win_rate    = 62.5,
    total_trades= 23,
)
print("✅  DAILY SUMMARY sent")

print()
print("=" * 50)
print("  All 6 alerts sent. Check Telegram!")
print("=" * 50)
