import requests
import html
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

def send(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        r = requests.post(url, data=payload, timeout=10)
        return r.json().get("ok", False)
    except Exception as e:
        print(f"Telegram error: {e}")
        return False

_MAX_TG = 4000  # Telegram hard limit is 4096; leave headroom for safety

def _send_chunked(text):
    """Send text in ≤4000-char chunks split on newline boundaries."""
    if len(text) <= _MAX_TG:
        return send(text)
    lines = text.split('\n')
    chunk, chunk_len = [], 0
    for line in lines:
        line_len = len(line) + 1   # +1 for the \n
        if chunk_len + line_len > _MAX_TG and chunk:
            send('\n'.join(chunk))
            chunk, chunk_len = [], 0
        chunk.append(line)
        chunk_len += line_len
    if chunk:
        send('\n'.join(chunk))
    return True

def send_buy_alert(symbol, entry, stop_loss, target, capital_used, quantity,
                   reason, confidence, ml_prob=None, sentiment='neutral', score=None):
    sl_pct  = ((stop_loss - entry) / entry) * 100
    tgt_pct = ((target    - entry) / entry) * 100
    rr      = round(abs(tgt_pct / sl_pct), 2) if sl_pct != 0 else 0

    sym_s      = html.escape(symbol)
    reason_s   = html.escape(str(reason))
    sent_emoji = {"positive": "📰✅", "negative": "📰🔴", "neutral": "📰➖"}.get(sentiment, "")
    ml_line    = f"ML win prob:  {ml_prob:.0f}%\n" if ml_prob is not None else ""
    score_line = f"Signal score: {score}\n"          if score  is not None else ""

    msg = (
        f"📈 <b>BUY ORDER</b>\n"
        f"{'─'*30}\n"
        f"Stock:        <b>{sym_s}</b>\n"
        f"Entry:        ₹{entry:.2f} × {quantity} share(s)\n"
        f"Capital used: ₹{capital_used:.0f}\n"
        f"{'─'*30}\n"
        f"Stop-Loss:    ₹{stop_loss:.2f}  ({sl_pct:.1f}%)\n"
        f"Target:       ₹{target:.2f}  (+{tgt_pct:.1f}%)\n"
        f"Risk/Reward:  1 : {rr}\n"
        f"{'─'*30}\n"
        f"{ml_line}"
        f"{score_line}"
        f"Sentiment:    {sentiment.upper()}  {sent_emoji}\n"
        f"Confidence:   {confidence:.0f}%\n"
        f"Reason:       {reason_s}"
    )
    return send(msg)


def send_hold_alert(symbol, price, stop_loss, reason, balance):
    sl_pct  = ((price - stop_loss) / stop_loss) * 100
    sym_s   = html.escape(symbol)
    reason_s = html.escape(str(reason))
    msg = (
        f"⏸ <b>HOLD — Watching for Reversal</b>\n"
        f"{'─'*30}\n"
        f"Stock:     <b>{sym_s}</b>\n"
        f"Price:     ₹{price:.2f}  (SL was ₹{stop_loss:.2f})\n"
        f"SL breach: {sl_pct:+.1f}%\n"
        f"{'─'*30}\n"
        f"Reason:    {reason_s}\n"
        f"Balance:   ₹{balance:.2f}\n"
        f"<i>Bot will keep monitoring — will exit if conditions worsen</i>"
    )
    return send(msg)

def send_sell_alert(symbol, entry, exit_price, pnl, pnl_pct, duration, balance, reason,
                    exit_type='full', qty_sold=None, qty_remaining=0, new_target=None,
                    day_pnl=None, day_trades=None):
    emoji    = "✅" if pnl >= 0 else "🔴"
    result   = "PROFIT" if pnl >= 0 else "LOSS"
    sym_s    = html.escape(symbol)
    reason_s = html.escape(str(reason))

    if exit_type == 'partial':
        label    = "PARTIAL EXIT — " + result
        qty_line = f"Sold: {qty_sold} share(s)  |  Remaining: {qty_remaining} share(s)\n"
        tgt_line = f"New target:   ₹{new_target:.2f}\n" if new_target else ""
    else:
        label    = "TRADE CLOSED — " + result
        qty_line = f"Shares sold: {qty_sold}\n" if qty_sold else ""
        tgt_line = ""

    day_line = ""
    if day_pnl is not None and day_trades is not None:
        sign = "+" if day_pnl >= 0 else ""
        day_line = f"{'─'*30}\nToday: {day_trades} trade(s)  Day P&amp;L: {sign}₹{day_pnl:.2f}\n"

    msg = (
        f"{emoji} <b>{label}</b>\n"
        f"Stock:   <b>{sym_s}</b>\n"
        f"Entry:   ₹{entry:.2f}  →  Exit: ₹{exit_price:.2f}\n"
        f"{qty_line}"
        f"P&amp;L:  {'+'if pnl>=0 else ''}₹{pnl:.2f}  ({'+' if pnl_pct>=0 else ''}{pnl_pct:.1f}%)\n"
        f"{tgt_line}"
        f"Duration: {duration}\n"
        f"Reason:   {reason_s}\n"
        f"{day_line}"
        f"Balance:  ₹{balance:.2f}"
    )
    return send(msg)

def send_reload_alert(old_balance, new_balance, reload_count, total_invested, amount=None):
    injected = amount if amount is not None else (new_balance - old_balance)
    msg = (
        f"💰 <b>Auto-Reload #{reload_count}</b>\n"
        f"{'─'*28}\n"
        f"Balance before : ₹{old_balance:.2f}\n"
        f"Injected       : ₹{injected:,.0f}\n"
        f"New balance    : ₹{new_balance:.2f}\n"
        f"Total invested : ₹{total_invested:.0f}\n"
        f"{'─'*28}\n"
        f"Bot continues learning and trading 🤖"
    )
    return send(msg)

def send_daily_summary(date, trades, wins, losses, day_pnl, balance,
                        best, worst, win_rate, total_trades, trade_list=None):
    """India (NSE/Angel One) EOD report — amounts in ₹."""
    emoji = "📈" if day_pnl >= 0 else "📉"
    sign  = "+" if day_pnl >= 0 else ""

    lines = [
        f"{emoji} <b>🇮🇳 INDIA EOD — {date}</b>",
        f"{'─'*30}",
        f"Trades today : {trades}  (✅{wins}  ❌{losses})",
        f"Day P&amp;L  : <b>{sign}₹{day_pnl:.2f}</b>",
        f"Balance      : ₹{balance:.2f}",
        f"{'─'*30}",
        f"Best trade   : {best}",
        f"Worst trade  : {worst}",
        f"Win rate     : {win_rate:.0f}%  ({total_trades} trades lifetime)",
    ]

    if trade_list:
        lines.append(f"{'─'*30}")
        lines.append("<b>All trades today:</b>")
        for t in trade_list:
            s    = "+" if t['pnl'] >= 0 else ""
            icon = "✅" if t['pnl'] >= 0 else "❌"
            lines.append(
                f"{icon} {t['symbol']}  "
                f"₹{t['entry']:.2f}→₹{t['exit']:.2f}  "
                f"<b>{s}₹{t['pnl']:.2f}</b> ({s}{t['pnl_pct']:.2f}%)"
            )

    return _send_chunked("\n".join(lines))


def send_us_daily_summary(date, trades, wins, losses, day_pnl, balance,
                           best, worst, win_rate, total_trades, trade_list=None):
    """US (Alpaca) EOD report — amounts in $."""
    emoji = "📈" if day_pnl >= 0 else "📉"
    sign  = "+" if day_pnl >= 0 else ""

    lines = [
        f"{emoji} <b>🇺🇸 US EOD — {date}</b>",
        f"{'─'*30}",
        f"Trades today : {trades}  (✅{wins}  ❌{losses})",
        f"Day P&amp;L  : <b>{sign}${day_pnl:.2f}</b>",
        f"Balance      : ${balance:.2f}",
        f"{'─'*30}",
        f"Best trade   : {best}",
        f"Worst trade  : {worst}",
        f"Win rate     : {win_rate:.0f}%  ({total_trades} trades lifetime)",
    ]

    if trade_list:
        lines.append(f"{'─'*30}")
        lines.append("<b>All trades today:</b>")
        for t in trade_list:
            s    = "+" if t['pnl'] >= 0 else ""
            icon = "✅" if t['pnl'] >= 0 else "❌"
            lines.append(
                f"{icon} {t['symbol']}  "
                f"${t['entry']:.2f}→${t['exit']:.2f}  "
                f"<b>{s}${t['pnl']:.2f}</b> ({s}{t['pnl_pct']:.2f}%)"
            )

    return _send_chunked("\n".join(lines))


def send_crypto_daily_summary(date, trades, wins, losses, day_pnl, balance,
                               best, worst, win_rate, total_trades, trade_list=None):
    """Crypto (Binance simulated) daily report — amounts in $."""
    emoji = "📈" if day_pnl >= 0 else "📉"
    sign  = "+" if day_pnl >= 0 else ""

    lines = [
        f"{emoji} <b>🪙 CRYPTO EOD — {date}</b>",
        f"{'─'*30}",
        f"Trades today : {trades}  (✅{wins}  ❌{losses})",
        f"Day P&amp;L  : <b>{sign}${day_pnl:.4f}</b>",
        f"Balance      : ${balance:.4f}",
        f"{'─'*30}",
        f"Best trade   : {best}",
        f"Worst trade  : {worst}",
        f"Win rate     : {win_rate:.0f}%  ({total_trades} trades lifetime)",
    ]

    if trade_list:
        lines.append(f"{'─'*30}")
        lines.append("<b>All trades today:</b>")
        for t in trade_list:
            s    = "+" if t['pnl'] >= 0 else ""
            icon = "✅" if t['pnl'] >= 0 else "❌"
            lines.append(
                f"{icon} {t['symbol']}  "
                f"${t['entry']:.4f}→${t['exit']:.4f}  "
                f"<b>{s}${t['pnl']:.4f}</b> ({s}{t['pnl_pct']:.2f}%)"
            )

    return _send_chunked("\n".join(lines))


def send_combined_summary(date, india_pnl, india_bal, india_trades,
                           us_pnl, us_bal, us_trades, us_enabled=True,
                           crypto_pnl=0.0, crypto_bal=0.0, crypto_trades=0,
                           crypto_enabled=False):
    """Combined daily comparison — India + US + Crypto."""
    india_sign  = "+" if india_pnl  >= 0 else ""
    us_sign     = "+" if us_pnl     >= 0 else ""
    crypto_sign = "+" if crypto_pnl >= 0 else ""
    india_icon  = "📈" if india_pnl  >= 0 else "📉"
    us_icon     = "📈" if us_pnl     >= 0 else "📉"
    crypto_icon = "📈" if crypto_pnl >= 0 else "📉"

    # Determine winner (skip disabled markets)
    contenders = [("🇮🇳 India", india_pnl)]
    if us_enabled:
        contenders.append(("🇺🇸 US", us_pnl))
    if crypto_enabled:
        contenders.append(("🪙 Crypto", crypto_pnl))
    winner = max(contenders, key=lambda x: x[1])[0] if contenders else "N/A"

    lines = [
        f"📊 <b>DAILY COMPARISON — {date}</b>",
        f"{'─'*30}",
        f"{india_icon} <b>India (NSE)</b>",
        f"   Trades: {india_trades}  |  P&amp;L: <b>{india_sign}₹{india_pnl:.2f}</b>",
        f"   Balance: ₹{india_bal:.2f}",
        f"{'─'*30}",
    ]
    if us_enabled:
        lines += [
            f"{us_icon} <b>US Market</b>",
            f"   Trades: {us_trades}  |  P&amp;L: <b>{us_sign}${us_pnl:.2f}</b>",
            f"   Balance: ${us_bal:.2f}",
            f"{'─'*30}",
        ]
    if crypto_enabled:
        lines += [
            f"{crypto_icon} <b>Crypto (24/7)</b>",
            f"   Trades: {crypto_trades}  |  P&amp;L: <b>{crypto_sign}${crypto_pnl:.4f}</b>",
            f"   Balance: ${crypto_bal:.4f}",
            f"{'─'*30}",
        ]
    lines.append(f"🏆 Better today: <b>{winner}</b>")

    return send("\n".join(lines))


if __name__ == "__main__":
    ok = send("🤖 <b>AngelBot connected successfully!</b>\nPaper trading mode: ON\nCapital: ₹1,000\nReady to scan markets.")
    print("Telegram test:", "OK" if ok else "FAILED")
