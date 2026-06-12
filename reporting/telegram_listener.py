import requests
import threading
import time
from datetime import datetime
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

_last_update_id    = -1
_processed_ids     = set()   # every processed update_id — prevents any duplicate replies
_trader_ref        = None
_us_trader_ref     = None    # AlpacaTrader — set by main.py after US init
_crypto_trader_ref = None    # CryptoTrader — set by main.py after crypto init

# File-based pause flag — shared across all worker processes
_PAUSE_FLAG = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'paused.flag')

def is_paused():
    return os.path.exists(_PAUSE_FLAG)


def _skip_old_messages():
    """Mark all existing messages as already-processed so they are never acted on."""
    global _last_update_id, _processed_ids
    try:
        url     = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
        resp    = requests.get(url, params={"offset": 0}, timeout=10)
        results = resp.json().get("result", [])
        for u in results:
            _processed_ids.add(u['update_id'])
        if results:
            last_id = results[-1]['update_id']
            # Tell Telegram we've consumed everything up to here
            requests.get(url, params={"offset": last_id + 1}, timeout=10)
            _last_update_id = last_id
        print(f"[Telegram] Cleared {len(results)} old message(s). Ready — send /help anytime.")
    except Exception:
        pass


def set_trader(trader):
    global _trader_ref
    _trader_ref = trader

def set_us_trader(trader):
    global _us_trader_ref
    _us_trader_ref = trader

def set_crypto_trader(trader):
    global _crypto_trader_ref
    _crypto_trader_ref = trader


def _get_updates():
    global _last_update_id
    try:
        url  = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
        resp = requests.get(url, params={"offset": _last_update_id + 1, "timeout": 10}, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("ok"):
                return data.get("result", [])
    except Exception:
        pass
    return []


_MAX_TG = 4000

def _reply(text):
    """Send reply, splitting into multiple messages if over Telegram's 4096-char limit."""
    if len(text) <= _MAX_TG:
        try:
            url     = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
            requests.post(url, data=payload, timeout=10)
        except Exception:
            pass
        return

    lines = text.split('\n')
    chunk, chunk_len = [], 0
    for line in lines:
        line_len = len(line) + 1
        if chunk_len + line_len > _MAX_TG and chunk:
            try:
                url     = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                payload = {"chat_id": TELEGRAM_CHAT_ID, "text": '\n'.join(chunk), "parse_mode": "HTML"}
                requests.post(url, data=payload, timeout=10)
            except Exception:
                pass
            chunk, chunk_len = [], 0
        chunk.append(line)
        chunk_len += line_len
    if chunk:
        try:
            url     = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            payload = {"chat_id": TELEGRAM_CHAT_ID, "text": '\n'.join(chunk), "parse_mode": "HTML"}
            requests.post(url, data=payload, timeout=10)
        except Exception:
            pass


def _handle_update(msg_text):
    # Reload all traders from DB so Telegram commands always show live data
    for _t in (_trader_ref, _us_trader_ref, _crypto_trader_ref):
        if _t and hasattr(_t, 'reload'):
            try:
                _t.reload()
            except Exception:
                pass

    cmd = msg_text.strip().lower().lstrip('/')

    if cmd in ('update', 'status'):
        _cmd_status()
    elif cmd in ('positions', 'pos'):
        _cmd_positions()
    elif cmd in ('pnl', 'profit'):
        _cmd_pnl()
    elif cmd in ('balance', 'bal'):
        _cmd_balance()
    elif cmd in ('india', 'nse', 'in'):
        _cmd_india_report()
    elif cmd in ('us', 'usa', 'alpaca'):
        _cmd_us_report()
    elif cmd in ('crypto', 'btc', 'binance'):
        _cmd_crypto_report()
    elif cmd in ('compare', 'both', 'vs', 'all'):
        _cmd_compare()
    elif cmd in ('help', 'commands'):
        _cmd_help()
    elif cmd in ('stop', 'pause'):
        _cmd_stop()
    elif cmd in ('start', 'resume'):
        _cmd_start()
    else:
        _reply(f"Unknown command: <b>{msg_text}</b>\nSend /help for available commands.")


def _cmd_status():
    from datetime import timezone, timedelta
    _IST = timezone(timedelta(hours=5, minutes=30))
    now  = datetime.now(_IST).strftime("%d %b %Y  %I:%M %p IST")

    def _market_block(label, trader, cur):
        if not trader:
            return f"{label}\n   <i>Not active</i>"
        stats    = trader.get_daily_stats()
        deployed = sum(p['capital_used'] for p in trader.open_positions)
        equity   = trader.balance + deployed
        sign     = "+" if stats['day_pnl'] >= 0 else ""
        icon     = "📈" if stats['day_pnl'] >= 0 else "📉"
        n        = len(trader.open_positions)
        return (
            f"{label}\n"
            f"   {icon} P&amp;L: <b>{sign}{cur}{stats['day_pnl']:.2f}</b>"
            f"  |  Trades: {stats['trades']} (W:{stats['wins']} L:{stats['losses']})\n"
            f"   Cash: {cur}{trader.balance:,.2f}"
            f"  |  Equity: <b>{cur}{equity:,.2f}</b>"
            f"  |  Open: {n}"
        )

    status = "⏸ PAUSED" if is_paused() else "▶️ Active"
    lines  = [
        f"📊 <b>AngelBot — Live Status</b>  [{now}]",
        "─"*32,
        _market_block("🇮🇳 <b>India (NSE)</b>",    _trader_ref,        "₹"),
        "─"*32,
        _market_block("🇺🇸 <b>US Market</b>",      _us_trader_ref,     "$"),
        "─"*32,
        _market_block("🪙 <b>Crypto (24/7)</b>",   _crypto_trader_ref, "$"),
        "─"*32,
        f"Bot: {status}  |  Use /india /us /crypto for full details",
    ]
    _reply("\n".join(lines))


def _cmd_positions():
    from datetime import timezone, timedelta
    _IST = timezone(timedelta(hours=5, minutes=30))

    def _pos_block(trader, cur, dec):
        if not trader or not trader.open_positions:
            return "   📭 No open positions\n"
        block = ""
        for p in trader.open_positions:
            entry_dt = datetime.strptime(p['entry_time'], "%Y-%m-%d %H:%M:%S")
            duration = str(datetime.now(_IST).replace(tzinfo=None) - entry_dt).split('.')[0]
            sl_pct   = ((p['stop_loss'] - p['entry_price']) / p['entry_price']) * 100
            tgt_pct  = ((p['target']    - p['entry_price']) / p['entry_price']) * 100
            block += (
                f"\n  <b>{p['symbol']}</b>\n"
                f"  Entry: {cur}{p['entry_price']:.{dec}f}  "
                f"SL: {cur}{p['stop_loss']:.{dec}f} ({sl_pct:.1f}%)  "
                f"TGT: {cur}{p['target']:.{dec}f} (+{tgt_pct:.1f}%)\n"
                f"  Capital: {cur}{p['capital_used']:.0f}  |  Open: {duration}\n"
            )
        return block

    traders = [
        ("🇮🇳 <b>India</b>",  _trader_ref,        "₹", 2),
        ("🇺🇸 <b>US</b>",     _us_trader_ref,     "$", 2),
        ("🪙 <b>Crypto</b>",  _crypto_trader_ref, "$", 4),
    ]
    total = sum(len(t.open_positions) for _, t, _, _ in traders if t)
    if total == 0:
        _reply("📭 No open positions across any market.")
        return

    msg = f"📋 <b>Open Positions — All Markets ({total})</b>\n{'─'*30}\n"
    for label, trader, cur, dec in traders:
        n = len(trader.open_positions) if trader else 0
        msg += f"{label}  ({n})\n"
        msg += _pos_block(trader, cur, dec)
        msg += "─"*30 + "\n"
    _reply(msg)


def _cmd_pnl():
    from datetime import timezone, timedelta
    _IST  = timezone(timedelta(hours=5, minutes=30))
    today = datetime.now(_IST).strftime("%d %b %Y")

    def _pnl_block(label, trader, cur):
        if not trader:
            return f"{label}: <i>not active</i>"
        s    = trader.get_daily_stats()
        sign = "+" if s['day_pnl'] >= 0 else ""
        icon = "📈" if s['day_pnl'] >= 0 else "📉"
        return (
            f"{icon} {label}\n"
            f"   P&amp;L: <b>{sign}{cur}{s['day_pnl']:.2f}</b>  "
            f"|  Trades: {s['trades']}  (W:{s['wins']} L:{s['losses']})\n"
            f"   Best: {s['best']}  |  Worst: {s['worst']}\n"
            f"   Win rate: {s['win_rate']:.0f}%  ({s['total_trades']} lifetime)"
        )

    lines = [
        f"📊 <b>P&amp;L — {today}</b>",
        "─"*30,
        _pnl_block("🇮🇳 India (NSE)",  _trader_ref,        "₹"),
        "─"*30,
        _pnl_block("🇺🇸 US Market",    _us_trader_ref,     "$"),
        "─"*30,
        _pnl_block("🪙 Crypto (24/7)", _crypto_trader_ref, "$"),
    ]
    _reply("\n".join(lines))


def _cmd_balance():
    def _bal_block(label, trader, cur):
        if not trader:
            return f"{label}: <i>not active</i>"
        deployed = sum(p['capital_used'] for p in trader.open_positions)
        equity   = trader.balance + deployed
        n        = len(trader.open_positions)
        stats    = trader.get_daily_stats()
        sign     = "+" if stats['day_pnl'] >= 0 else ""
        icon     = "📈" if stats['day_pnl'] >= 0 else "📉"
        return (
            f"{icon} {label}\n"
            f"   Cash:     {cur}{trader.balance:,.2f}\n"
            f"   Deployed: {cur}{deployed:,.2f}  ({n} open)\n"
            f"   Equity:   <b>{cur}{equity:,.2f}</b>\n"
            f"   Today P&amp;L: {sign}{cur}{stats['day_pnl']:.2f}  "
            f"({stats['trades']} trades, W:{stats['wins']} L:{stats['losses']})"
        )

    lines = [
        "💰 <b>Portfolio Balance</b>",
        "─"*30,
        _bal_block("🇮🇳 India (NSE)",  _trader_ref,        "₹"),
        "─"*30,
        _bal_block("🇺🇸 US Market",    _us_trader_ref,     "$"),
        "─"*30,
        _bal_block("🪙 Crypto (24/7)", _crypto_trader_ref, "$"),
        "─"*30,
        "<i>Equity = Cash + capital in open positions</i>",
    ]
    _reply("\n".join(lines))


def _cmd_india_report():
    """Full India P&L for today."""
    if not _trader_ref:
        _reply("Bot not ready yet.")
        return
    stats = _trader_ref.get_daily_stats()
    from reporting.telegram_alerts import send_daily_summary
    send_daily_summary(
        stats['date'], stats['trades'], stats['wins'], stats['losses'],
        stats['day_pnl'], stats['balance'],
        stats['best'], stats['worst'],
        stats['win_rate'], stats['total_trades'],
        trade_list=stats.get('trade_list')
    )


def _cmd_us_report():
    """Full US P&L for today."""
    if not _us_trader_ref:
        _reply("🇺🇸 US session is not active.\nAdd ALPACA_KEY to .env to enable.")
        return
    stats = _us_trader_ref.get_daily_stats()
    from reporting.telegram_alerts import send_us_daily_summary
    send_us_daily_summary(
        stats['date'], stats['trades'], stats['wins'], stats['losses'],
        stats['day_pnl'], stats['balance'],
        stats['best'], stats['worst'],
        stats['win_rate'], stats['total_trades'],
        trade_list=stats.get('trade_list')
    )


def _cmd_crypto_report():
    """Full Crypto P&L for today."""
    if not _crypto_trader_ref:
        _reply("🪙 Crypto session is not active.\nAdd BINANCE_KEY to .env to enable.")
        return
    stats = _crypto_trader_ref.get_daily_stats()
    from reporting.telegram_alerts import send_crypto_daily_summary
    send_crypto_daily_summary(
        stats['date'], stats['trades'], stats['wins'], stats['losses'],
        stats['day_pnl'], stats['balance'],
        stats['best'], stats['worst'],
        stats['win_rate'], stats['total_trades'],
        trade_list=stats.get('trade_list')
    )


def _cmd_compare():
    """Side-by-side India vs US vs Crypto for today."""
    if not _trader_ref:
        _reply("Bot not ready yet.")
        return
    from datetime import timezone, timedelta
    _IST  = timezone(timedelta(hours=5, minutes=30))
    today = datetime.now(_IST).strftime("%d %b %Y")

    def _deployed(trader):
        if not trader:
            return 0.0
        return sum(p['capital_used'] for p in trader.open_positions)

    india = _trader_ref.get_daily_stats()
    india_dep = _deployed(_trader_ref)

    us_pnl, us_bal, us_trades, us_dep = 0.0, 0.0, 0, 0.0
    us_enabled = bool(_us_trader_ref)
    if _us_trader_ref:
        us = _us_trader_ref.get_daily_stats()
        us_pnl, us_bal, us_trades = us['day_pnl'], us['balance'], us['trades']
        us_dep = _deployed(_us_trader_ref)

    cr_pnl, cr_bal, cr_trades, cr_dep = 0.0, 0.0, 0, 0.0
    cr_enabled = bool(_crypto_trader_ref)
    if _crypto_trader_ref:
        cr = _crypto_trader_ref.get_daily_stats()
        cr_pnl, cr_bal, cr_trades = cr['day_pnl'], cr['balance'], cr['trades']
        cr_dep = _deployed(_crypto_trader_ref)

    def _row(icon, label, pnl, bal, trades, dep, cur):
        sign   = "+" if pnl >= 0 else ""
        equity = bal + dep
        return (
            f"{icon} <b>{label}</b>\n"
            f"   Trades: {trades}  |  P&amp;L: <b>{sign}{cur}{pnl:.2f}</b>\n"
            f"   Cash: {cur}{bal:.2f}  |  Deployed: {cur}{dep:.2f}\n"
            f"   Equity: <b>{cur}{equity:.2f}</b>"
        )

    contenders = [("🇮🇳 India", india['day_pnl'])]
    if us_enabled:    contenders.append(("🇺🇸 US",     us_pnl))
    if cr_enabled:    contenders.append(("🪙 Crypto", cr_pnl))
    winner = max(contenders, key=lambda x: x[1])[0]

    lines = [f"📊 <b>DAILY COMPARISON — {today}</b>", "─"*30]
    lines.append(_row("🇮🇳", "India (NSE)",  india['day_pnl'], india['balance'], india['trades'], india_dep, "₹"))
    lines.append("─"*30)
    if us_enabled:
        lines.append(_row("🇺🇸", "US Market",   us_pnl, us_bal, us_trades, us_dep, "$"))
        lines.append("─"*30)
    if cr_enabled:
        lines.append(_row("🪙", "Crypto (24/7)", cr_pnl, cr_bal, cr_trades, cr_dep, "$"))
        lines.append("─"*30)
    lines.append(f"🏆 Best today: <b>{winner}</b>")
    lines.append("<i>Equity = Cash + capital in open positions</i>")
    _reply("\n".join(lines))


def _cmd_stop():
    if is_paused():
        _reply("⏸ Bot is already paused. Send /start to resume.")
        return
    try:
        open(_PAUSE_FLAG, 'w').close()
    except Exception as e:
        _reply(f"❌ Could not create pause flag: {e}")
        return
    pos_count = len(_trader_ref.open_positions) if _trader_ref else 0
    _reply(
        f"⏸ <b>Bot Paused</b>\n"
        f"{'─'*28}\n"
        f"All 3 market workers will stop scanning.\n"
        f"Real-time price monitoring also paused.\n"
        f"Open positions: {pos_count}\n\n"
        f"Send /start to resume trading."
    )
    print("[Telegram] Bot PAUSED by user command.")


def _cmd_start():
    if not is_paused():
        _reply("▶️ Bot is already running. Send /stop to pause.")
        return
    try:
        if os.path.exists(_PAUSE_FLAG):
            os.remove(_PAUSE_FLAG)
    except Exception as e:
        _reply(f"❌ Could not remove pause flag: {e}")
        return
    _reply(
        f"▶️ <b>Bot Resumed</b>\n"
        f"{'─'*28}\n"
        f"All 3 market workers are active again.\n"
        f"Next buy scan fires within 60 seconds."
    )
    print("[Telegram] Bot RESUMED by user command.")


def _cmd_help():
    msg = (
        "🤖 <b>AngelBot Commands</b>\n"
        "────────────────────────────\n"
        "/update    — Live snapshot (all 3 markets)\n"
        "/positions — All open trades (all markets)\n"
        "/pnl       — Today's P&amp;L (all markets)\n"
        "/balance   — Cash + deployed + equity\n"
        "/compare   — Side-by-side comparison + equity\n"
        "────────────────────────────\n"
        "/india     — 🇮🇳 India full report &amp; trade list\n"
        "/us        — 🇺🇸 US full report &amp; trade list\n"
        "/crypto    — 🪙 Crypto full report &amp; trade list\n"
        "────────────────────────────\n"
        "/stop      — Pause scanning &amp; monitoring\n"
        "/start     — Resume after pause\n"
        "/help      — This list"
    )
    _reply(msg)


def start_listener():
    """Start the Telegram listener in a background thread."""
    _skip_old_messages()
    t = threading.Thread(target=_poll_loop, daemon=True)
    t.start()
    print("Telegram listener started — send /update anytime for live status.")


def _poll_loop():
    global _last_update_id, _processed_ids
    while True:
        try:
            updates = _get_updates()
            for update in updates:
                uid = update['update_id']
                _last_update_id = uid

                # Hard dedup — never process the same message twice
                if uid in _processed_ids:
                    continue
                _processed_ids.add(uid)

                # Keep set bounded — discard IDs more than 500 behind the latest
                if len(_processed_ids) > 1000:
                    cutoff = uid - 500
                    _processed_ids = {i for i in _processed_ids if i >= cutoff}

                msg = update.get('message') or update.get('edited_message')
                if not msg:
                    continue
                if str(msg.get('chat', {}).get('id')) != str(TELEGRAM_CHAT_ID):
                    continue
                text = msg.get('text', '').strip()
                if text:
                    print(f"[Telegram] Received: {text}")
                    _handle_update(text)
        except Exception:
            pass
        time.sleep(3)


if __name__ == "__main__":
    print("Starting listener — send /help or /update to your bot. Ctrl+C to stop.")
    _skip_old_messages()
    _reply("🧪 <b>Listener active</b> — send /help to test commands.")
    _poll_loop()
