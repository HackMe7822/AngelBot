"""
Real-Time Position Monitor
==========================
Runs as a daemon thread during market hours.
Polls Angel One LTP every 15 seconds for every open position.
The moment SL or target is touched, it fires a full exit analysis
(technical + news) and acts immediately — no waiting for the 15-min scan.

The 15-min scanner handles buy candidates only.
This module handles sell decisions in real time.
"""

import threading
import time
from datetime import datetime, time as dtime, timezone, timedelta
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from reporting.telegram_listener import is_paused
from analysis.technical import _price_decimals

_A   = lambda n: f"\033[{n}m"
_R   = _A("0");    _G  = _A("1;92");  _RD = _A("1;91")
_CY  = _A("1;96"); _YL = _A("1;93"); _DIM = _A("2")

POLL_SECONDS  = 15          # how often to check each position price
HEARTBEAT_MIN = 5           # print alive message every N minutes
MARKET_OPEN   = dtime(9, 14)
MARKET_CLOSE  = dtime(15, 35)
FORCE_CLOSE   = dtime(15, 0)   # force-exit all positions at 15:00 IST — never hold overnight
_IST          = timezone(timedelta(hours=5, minutes=30))


def _is_market_open():
    now = datetime.now(_IST)
    if now.weekday() >= 5:
        return False
    return MARKET_OPEN <= now.time() <= MARKET_CLOSE


class PositionMonitor:
    def __init__(self, trader, on_exit_fn, live_feed=None, price_fn=None,
                 always_active=False, currency='₹', name=None,
                 market_open_fn=None, eod_close=True,
                 sl_pct=None, target_pct=None):
        """
        trader          — PaperTrader / AlpacaTrader / CryptoTrader (shared with main thread)
        on_exit_fn      — callable(pos, price, pnl, pnl_pct, reason, exit_type, qty_sold)
        live_feed       — LiveFeed instance for WebSocket prices (optional)
        price_fn        — fn(symbol) → float|None  (default: Angel One get_live_price)
        always_active   — True for 24/7 markets (crypto): skip market-hours gate entirely
        currency        — display prefix for console log lines ('₹' / '$')
        market_open_fn  — callable() → bool  (default: NSE hours check)
                          Pass custom fn for non-India markets (e.g. is_us_market for NYSE)
        eod_close       — if False, monitor never runs EOD force-close
                          (use when the worker handles its own EOD close logic)
        sl_pct          — override stop-loss % for trailing logic (default: SCALP_SL_PCT)
        target_pct      — override target % for trailing logic    (default: SCALP_TARGET_PCT)
        """
        self.trader           = trader
        self.on_exit          = on_exit_fn
        self._live_feed       = live_feed
        self._price_fn        = price_fn
        self._always_active   = always_active   # 24/7 mode (crypto)
        self._currency        = currency
        self._name            = name or ("CryptoMonitor" if always_active else "Monitor")
        self._market_open_fn  = market_open_fn if market_open_fn is not None else _is_market_open
        self._eod_close       = eod_close
        self._sl_pct          = sl_pct      # None = read from config each tick
        self._target_pct      = target_pct  # None = read from config each tick
        self._running         = False
        self._thread          = None
        self._last_price      = {}     # sym → last seen price
        self._exiting         = set()  # position IDs currently mid-exit (avoid duplicates)
        self._poll_count      = 0
        self._eod_closed_date = None   # date of last EOD force-close (prevents double-fire)
        self._trail_active    = {}     # pos_id → bool  (trailing stop engaged)
        self._trail_sl        = {}     # pos_id → float (current trailing stop price)
        self._sl_cooldown     = {}     # symbol → (datetime, exit_price) — 30-min re-entry gate
        self._sl_breach_time  = {}     # pos_id → float (time.time() when SL first breached)
        self._sl_breach_count = {}     # pos_id → int   (consecutive polls below SL)
        self._daily_sl_hits   = {}     # symbol → (date, count) — banned after 2 SL hits same day

    def start(self):
        self._running = True
        self._thread  = threading.Thread(
            target=self._loop, daemon=True, name="PositionMonitor"
        )
        self._thread.start()

        if self._live_feed:
            self._live_feed.on_tick(self._on_live_tick)
            print("[Monitor] Real-time position monitor started — WebSocket live feed active (LTP polling fallback)")
        else:
            print("[Monitor] Real-time position monitor started — Angel One LTP polling every 15s")

    def stop(self):
        self._running = False

    def is_in_cooldown(self, symbol, current_price=None):
        """Returns True if this symbol should be skipped for re-entry.

        Three gates — all must pass to allow re-entry:
        1. Daily cap : if stock hit SL 2+ times today, banned for rest of day
        2. Time gate : 30 min since last SL exit (prevents immediate whipsaws)
        3. Price gate: current price must be ≥0.3% above the SL-exit price
                       (allows HFCL-style momentum re-entries; blocks RAILTEL churn)
        """
        today = datetime.now(_IST).date()

        # Gate 1: daily SL cap — 2 hits on same stock = banned for the day
        hit_entry = self._daily_sl_hits.get(symbol)
        if hit_entry is not None:
            hit_date, hit_count = hit_entry
            if hit_date == today and hit_count >= 2:
                return True
            elif hit_date != today:
                del self._daily_sl_hits[symbol]

        # Gate 2 + 3: time and price cooldown
        entry = self._sl_cooldown.get(symbol)
        if entry is None:
            return False
        t, exit_price = entry if isinstance(entry, tuple) else (entry, None)
        elapsed = (datetime.now(_IST) - t).total_seconds()
        if elapsed > 1800:   # 30 minutes elapsed — clear and allow
            del self._sl_cooldown[symbol]
            return False
        # Within 30-min window: allow early re-entry only if price recovered 0.3%+ above SL exit
        if current_price is not None and exit_price is not None:
            if current_price >= exit_price * 1.003:
                return False   # momentum confirmed — allow re-entry before cooldown expires
        return True

    # ── Main loop ─────────────────────────────────────────────────────────────
    def _loop(self):
        while self._running:
            try:
                now_ist    = datetime.now(_IST)
                is_weekday = now_ist.weekday() < 5

                if self._always_active:
                    # 24/7 mode (crypto) — always monitor, never force-close
                    if self.trader.open_positions and not is_paused():
                        # Subscribe new positions to live feed so ticks flow in
                        if self._live_feed:
                            syms = [p['symbol'] for p in self.trader.open_positions]
                            self._live_feed.subscribe(syms)

                        ws_up = self._live_feed and self._live_feed.is_connected
                        if not ws_up:
                            # No feed at all — fall back to POLL_SECONDS REST polling
                            self._poll_positions()

                        self._poll_count += 1
                        if self._poll_count % (HEARTBEAT_MIN * 60 // POLL_SECONDS) == 0:
                            n      = len(self.trader.open_positions)
                            source = "WebSocket live" if ws_up else "REST polling ⚠"
                            cur    = self._currency
                            print(f"{_CY}[{self._name}] ♥  {n} position(s)  {cur}{self.trader.balance:.2f}  [{source}]{_R}")
                else:
                    # Normal mode — respect market hours + optional EOD force-close

                    # EOD force-close: fire once per day at FORCE_CLOSE (India only by default)
                    if (self._eod_close and
                            is_weekday and
                            now_ist.time() >= FORCE_CLOSE and
                            self._eod_closed_date != now_ist.date() and
                            self.trader.open_positions and
                            not is_paused()):
                        self._force_close_all(now_ist)

                    elif self._market_open_fn() and self.trader.open_positions and not is_paused():
                        # Subscribe any newly opened positions to the live feed
                        if self._live_feed:
                            syms = [p['symbol'] for p in self.trader.open_positions]
                            self._live_feed.subscribe(syms)

                        # Use LTP polling only when WebSocket is not connected
                        ws_up = self._live_feed and self._live_feed.is_connected
                        if not ws_up:
                            self._poll_positions()

                        self._poll_count += 1
                        if self._poll_count % (HEARTBEAT_MIN * 60 // POLL_SECONDS) == 0:
                            n      = len(self.trader.open_positions)
                            source = "WebSocket live" if ws_up else "LTP polling ⚠"
                            print(f"{_CY}[Monitor] ♥  {n} position(s)  {self._currency}{self.trader.balance:.2f}  [{source}]{_R}")

            except Exception as e:
                print(f"[Monitor] Error in loop: {e}")
            time.sleep(POLL_SECONDS)

    # ── Live tick handler — fires on every WebSocket price update ────────────
    def _on_live_tick(self, symbol, price):
        """Called by LiveFeed on every tick. SL requires 20s confirmation to filter wicks."""
        if is_paused():
            return
        if not self._always_active and not self._market_open_fn():
            return
        pos = self.trader.get_position(symbol)
        if not pos or pos['id'] in self._exiting:
            return

        from config import SCALP_SL_PCT, SCALP_TARGET_PCT, SL_CONFIRM_POLLS
        _sl_pct  = self._sl_pct     if self._sl_pct     is not None else SCALP_SL_PCT
        _tgt_pct = self._target_pct if self._target_pct is not None else SCALP_TARGET_PCT
        pos_id = pos['id']

        if self._trail_active.get(pos_id):
            # ── Trailing stop active: ratchet up, exit on pullback ──
            # Tighten trail to 0.4× SL when price is running hot (extended move)
            gain_from_entry = (price - pos['entry_price']) / pos['entry_price']
            tight_trail = gain_from_entry > _tgt_pct * 2
            trail_pct   = _sl_pct * 0.4 if tight_trail else _sl_pct
            new_trail = round(price * (1 - trail_pct), _price_decimals(price))
            if new_trail > self._trail_sl.get(pos_id, 0):
                self._trail_sl[pos_id] = new_trail
            if price <= self._trail_sl[pos_id]:
                # Simulate real stop-order fill: sell at trail_sl, not at the polled price
                # (avoids paper mode being overly pessimistic when poll gap is large)
                exit_price = max(self._trail_sl[pos_id], price)
                gain_pct = (exit_price - pos['entry_price']) / pos['entry_price'] * 100
                cur  = self._currency
                _c   = _G if gain_pct >= 0 else _RD
                print(f"{_c}  [TRAIL EXIT] {symbol} {cur}{exit_price:.4f}  trail-SL hit  profit {gain_pct:+.2f}%{_R}")
                self._exiting.add(pos_id)
                threading.Thread(
                    target=self._scalp_exit, args=(pos, exit_price, 'TRAIL'), daemon=True
                ).start()
        else:
            at_sl     = price <= pos['stop_loss']
            at_target = price >= pos['target']

            if at_sl:
                count = self._sl_breach_count.get(pos_id, 0) + 1
                self._sl_breach_count[pos_id] = count
                if pos_id not in self._sl_breach_time:
                    self._sl_breach_time[pos_id] = time.time()
                    cur = self._currency
                    print(f"{_YL}  [SL WATCH]  {symbol} {cur}{price:.4f}  below SL — confirming ({count}/{SL_CONFIRM_POLLS} polls){_R}")
                elif count >= SL_CONFIRM_POLLS:
                    # N consecutive polls below SL — genuine breakdown, not a wick
                    elapsed = time.time() - self._sl_breach_time.pop(pos_id)
                    self._sl_breach_count.pop(pos_id, None)
                    cur = self._currency
                    print(f"{_RD}  [SL EXIT]   {symbol} {cur}{price:.4f}  confirmed {count} polls ({elapsed:.0f}s) below SL{_R}")
                    self._exiting.add(pos_id)
                    threading.Thread(
                        target=self._scalp_exit, args=(pos, price, 'SL'), daemon=True
                    ).start()
                else:
                    cur = self._currency
                    print(f"{_YL}  [SL WATCH]  {symbol} {cur}{price:.4f}  confirming ({count}/{SL_CONFIRM_POLLS} polls){_R}")

            elif at_target:
                # Target hit — clear any pending SL watch, activate trailing stop
                self._sl_breach_time.pop(pos_id, None)
                self._trail_active[pos_id] = True
                # Trail floor = entry + 0.3% — guarantees minimum profit if trail activates
                floor = round(pos['entry_price'] * 1.003, _price_decimals(pos['entry_price']))
                self._trail_sl[pos_id] = floor
                cur = self._currency
                print(f"{_G}  [TRAIL ON]  {symbol} {cur}{price:.4f}  target hit — trail floor {cur}{floor:.4f}{_R}")

            else:
                # Price is above SL — cancel any pending breach watch
                if pos_id in self._sl_breach_time:
                    count = self._sl_breach_count.pop(pos_id, 0)
                    elapsed = time.time() - self._sl_breach_time.pop(pos_id)
                    if elapsed > 2:
                        cur = self._currency
                        print(f"{_G}  [SL RECOVER] {symbol} {cur}{price:.4f}  bounced back above SL after {count} poll(s) ({elapsed:.0f}s) — holding{_R}")

    # ── Scalp exit — instant sell, no re-analysis ────────────────────────────
    def _scalp_exit(self, pos, price, trigger):
        sym = pos['symbol']
        try:
            with self.trader._lock:
                still_open = next(
                    (p for p in self.trader.open_positions if p['id'] == pos['id']), None
                )
                if still_open is None:
                    return
                reason_map = {'TARGET': 'profit target', 'TRAIL': 'trailing stop', 'SL': 'stop-loss'}
                reason = f"Scalp {reason_map.get(trigger, 'exit')} hit"
                pnl, pct = self.trader.sell(still_open, price, reason)
                if pnl == 0.0 and pct == 0.0:
                    return
                sign = "+" if pnl >= 0 else ""
                cur  = self._currency
                _c   = _G if pnl >= 0 else _RD
                print(f"{_c}  [SOLD] {sym} @ {cur}{price:.4f}  P&L: {sign}{cur}{pnl:.4f} ({sign}{pct:.2f}%){_R}")
        except Exception as e:
            print(f"[Monitor] Scalp exit error for {sym}: {e}")
        finally:
            self._exiting.discard(pos['id'])
            self._trail_active.pop(pos['id'], None)
            self._trail_sl.pop(pos['id'], None)
            self._sl_breach_time.pop(pos['id'], None)
            self._sl_breach_count.pop(pos['id'], None)
            if trigger == 'SL':
                # Full cooldown: time gate + price gate + daily SL counter
                self._sl_cooldown[sym] = (datetime.now(_IST), price)
                today = datetime.now(_IST).date()
                prev  = self._daily_sl_hits.get(sym)
                count = (prev[1] + 1) if (prev and prev[0] == today) else 1
                self._daily_sl_hits[sym] = (today, count)
                if count >= 2:
                    print(f"{_YL}  [DAY BAN]   {sym} — {count} SL hits today, banned until EOD{_R}")
            elif trigger == 'TRAIL':
                # Trail cooldown: time gate only (stock reversed from target, don't chase)
                # No day-ban counter — trail exits are not necessarily bad signals
                self._sl_cooldown[sym] = (datetime.now(_IST), price)

    # ── LTP poll fallback — used when WebSocket is not connected ─────────────
    def _poll_positions(self):
        from data.fetcher import get_live_price
        _get_price = self._price_fn or get_live_price

        for pos in list(self.trader.open_positions):
            try:
                sym   = pos['symbol']
                price = _get_price(sym)
                if price is None:
                    continue

                last   = self._last_price.get(sym)
                self._last_price[sym] = price

                at_sl     = price <= pos['stop_loss']
                at_target = price >= pos['target']

                # Live price line — only print on notable move or at trigger
                if last:
                    chg = ((price - last) / last) * 100
                    sl_gap  = ((price - pos['stop_loss']) / pos['entry_price']) * 100
                    tgt_gap = ((pos['target'] - price)   / pos['entry_price']) * 100
                    marker  = ""
                    if at_sl:
                        marker = "  🔴 STOP-LOSS HIT"
                    elif at_target:
                        marker = "  🎯 TARGET HIT"
                    elif abs(chg) >= 0.4:   # print only on meaningful moves
                        marker = f"  ({chg:+.2f}% move)"
                    if marker or at_sl or at_target:
                        cur = self._currency
                        _c  = _RD if at_sl else (_G if at_target else _DIM)
                        print(f"{_c}  [LIVE] {sym} {cur}{price:.4f}  "
                              f"SL gap {sl_gap:+.1f}%  TGT gap {tgt_gap:+.1f}%{marker}{_R}")

                if pos['id'] not in self._exiting:
                    self._on_live_tick(sym, price)

            except Exception as e:
                print(f"[Monitor] Price fetch error for {pos.get('symbol','?')}: {e}")

    # ── EOD force-close — exits every open position at market price ──────────
    def _force_close_all(self, now_ist):
        self._eod_closed_date = now_ist.date()
        self._sl_breach_time.clear()
        self._sl_breach_count.clear()
        self._daily_sl_hits.clear()
        positions = list(self.trader.open_positions)
        print(f"\n[Monitor] EOD Force-Close @ {now_ist.strftime('%H:%M IST')} — {len(positions)} position(s)")

        from data.fetcher import get_live_price
        _get_price = self._price_fn or get_live_price

        for pos in positions:
            sym = pos['symbol']
            try:
                price = _get_price(sym) or pos['entry_price']
                with self.trader._lock:
                    still_open = next(
                        (p for p in self.trader.open_positions if p['id'] == pos['id']), None
                    )
                    if still_open is None:
                        continue
                    pnl, pct = self.trader.sell(still_open, price, 'EOD force-close — intraday exit')
                    if pnl != 0.0 or pct != 0.0:
                        sign = "+" if pnl >= 0 else ""
                        cur  = self._currency
                        _c   = _G if pnl >= 0 else _RD
                        print(f"{_c}  [EOD CLOSE] {sym} @ {cur}{price:.4f}  P&L: {sign}{cur}{pnl:.4f}{_R}")
            except Exception as e:
                print(f"[Monitor] Force-close error for {sym}: {e}")



# ── Factory used by main.py ───────────────────────────────────────────────────
def start_monitor(trader, on_exit_fn, live_feed=None, price_fn=None,
                  always_active=False, currency='₹', name=None,
                  market_open_fn=None, eod_close=True,
                  sl_pct=None, target_pct=None):
    monitor = PositionMonitor(trader, on_exit_fn, live_feed, price_fn=price_fn,
                              always_active=always_active, currency=currency, name=name,
                              market_open_fn=market_open_fn, eod_close=eod_close,
                              sl_pct=sl_pct, target_pct=target_pct)
    monitor.start()
    return monitor
