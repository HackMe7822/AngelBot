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
import json
import math
import re as _re
import logging as _logging
from datetime import datetime, date as ddate, time as dtime, timezone, timedelta
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from reporting.telegram_listener import is_paused
from analysis.technical import _price_decimals

_A   = lambda n: f"\033[{n}m"
_R   = _A("0");    _G  = _A("1;92");  _RD = _A("1;91")
_CY  = _A("1;96"); _YL = _A("1;93"); _DIM = _A("2")
_ansi_re = _re.compile(r'\033\[[0-9;]*m')

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
                 sl_pct=None, target_pct=None, market='india'):
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
        market          — 'india'|'us'|'crypto' — used for DB state persistence
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
        self._market          = market
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
        self._profit_since    = {}     # pos_id → float (time.time() when position first went profitable)
        self._loss_since      = {}     # pos_id → float (time.time() when position first went into loss)
        # Use the worker's rotating file logger so exit events appear in the portal log viewer
        self._logger = _logging.getLogger(market) if market else None

    def _log(self, msg):
        """Strip ANSI codes and write to the market's rotating log file (portal log viewer)."""
        if self._logger:
            self._logger.info(_ansi_re.sub('', msg).strip())

    # ── State persistence — survives restarts ────────────────────────────────
    def save_state(self):
        """Persist trail_sl, cooldowns, and daily SL hits to the DB monitor_state table."""
        try:
            from data.database import get_conn
            now = datetime.now(_IST).strftime("%Y-%m-%d %H:%M:%S")
            market = self._market
            conn = get_conn()
            c    = conn.cursor()

            def _upsert(key, value):
                c.execute(
                    "MERGE INTO monitor_state AS target "
                    "USING (SELECT ? AS market, ? AS state_key, ? AS value, ? AS updated_at) AS src "
                    "ON target.market = src.market AND target.state_key = src.state_key "
                    "WHEN MATCHED THEN UPDATE SET value = src.value, updated_at = src.updated_at "
                    "WHEN NOT MATCHED THEN INSERT (market, state_key, value, updated_at) "
                    "VALUES (src.market, src.state_key, src.value, src.updated_at);",
                    (market, key, json.dumps(value), now)
                )

            # trail_sl: {str(pos_id): float}
            _upsert('trail_sl', {str(k): v for k, v in self._trail_sl.items()})
            # trail_active: {str(pos_id): bool}
            _upsert('trail_active', {str(k): v for k, v in self._trail_active.items()})
            # sl_cooldown: {symbol: [iso_str, price]}
            cd = {}
            for sym, val in self._sl_cooldown.items():
                t, price = val if isinstance(val, tuple) else (val, None)
                cd[sym] = [t.isoformat(), price]
            _upsert('sl_cooldown', cd)
            # daily_sl_hits: {symbol: [date_str, count]}
            hits = {sym: [str(d), cnt] for sym, (d, cnt) in self._daily_sl_hits.items()}
            _upsert('daily_sl_hits', hits)

            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[Monitor] State save error: {e}")

    def load_state(self):
        """Restore trail_sl, cooldowns, and daily SL hits from DB on startup."""
        try:
            from data.database import get_conn
            market = self._market
            conn = get_conn()
            c    = conn.cursor()
            c.execute("SELECT state_key, value FROM monitor_state WHERE market=?", (market,))
            rows = {row[0]: json.loads(row[1]) for row in c.fetchall()}
            conn.close()

            if 'trail_sl' in rows:
                self._trail_sl = {int(k): float(v) for k, v in rows['trail_sl'].items()}
            if 'trail_active' in rows:
                self._trail_active = {int(k): bool(v) for k, v in rows['trail_active'].items()}
            if 'sl_cooldown' in rows:
                today = datetime.now(_IST).date()
                for sym, (iso, price) in rows['sl_cooldown'].items():
                    try:
                        t = datetime.fromisoformat(iso).replace(tzinfo=_IST)
                        # Only restore if cooldown hasn't expired yet
                        if (datetime.now(_IST) - t).total_seconds() < 1800:
                            self._sl_cooldown[sym] = (t, float(price) if price else None)
                    except Exception:
                        pass
            if 'daily_sl_hits' in rows:
                today = datetime.now(_IST).date()
                for sym, (date_str, cnt) in rows['daily_sl_hits'].items():
                    try:
                        hit_date = ddate.fromisoformat(date_str)
                        if hit_date == today:   # only restore today's bans
                            self._daily_sl_hits[sym] = (hit_date, int(cnt))
                    except Exception:
                        pass

            if self._trail_sl or self._sl_cooldown or self._daily_sl_hits:
                print(f"[Monitor] State restored: {len(self._trail_sl)} trailing stops, "
                      f"{len(self._sl_cooldown)} cooldowns, {len(self._daily_sl_hits)} day bans")
        except Exception as e:
            print(f"[Monitor] State load error (starting fresh): {e}")

    def start(self):
        self.load_state()
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

    def had_sl_today(self, symbol):
        """Returns True if this symbol hit its stop-loss at least once today."""
        today = datetime.now(_IST).date()
        entry = self._daily_sl_hits.get(symbol)
        return entry is not None and entry[0] == today

    # ── Main loop ─────────────────────────────────────────────────────────────
    def _loop(self):
        while self._running:
            try:
                now_ist    = datetime.now(_IST)
                is_weekday = now_ist.weekday() < 5

                if self._always_active:
                    # 24/7 mode (crypto) — always monitor, never force-close
                    if self.trader.open_positions and not is_paused():
                        # Subscribe new positions to live feed so WebSocket ticks flow in
                        if self._live_feed:
                            syms = [p['symbol'] for p in self.trader.open_positions]
                            self._live_feed.subscribe(syms)

                        # Always poll directly every cycle — do NOT rely solely on feed callbacks.
                        # In yfinance REST-polling fallback mode the feed marks itself as
                        # is_connected=True but silently returns None prices when Yahoo throttles,
                        # meaning _on_live_tick never fires and positions never close.
                        # Direct polling every POLL_SECONDS is the reliable safety net.
                        self._poll_positions()

                        self._poll_count += 1
                        if self._poll_count % (HEARTBEAT_MIN * 60 // POLL_SECONDS) == 0:
                            n      = len(self.trader.open_positions)
                            ws_up  = self._live_feed and self._live_feed.is_connected
                            source = "WebSocket" if (ws_up and getattr(self._live_feed, '_mode', '') == 'websocket') else "REST polling"
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
                _m = f"  [TRAIL EXIT] {symbol} {cur}{exit_price:.4f}  trail-SL hit  profit {gain_pct:+.2f}%"
                print(f"{_c}{_m}{_R}"); self._log(_m)
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
                    _m = f"  [SL WATCH]  {symbol} {cur}{price:.4f}  below SL — confirming ({count}/{SL_CONFIRM_POLLS} polls)"
                    print(f"{_YL}{_m}{_R}"); self._log(_m)
                elif count >= SL_CONFIRM_POLLS:
                    # N consecutive polls below SL — genuine breakdown, not a wick
                    elapsed = time.time() - self._sl_breach_time.pop(pos_id)
                    self._sl_breach_count.pop(pos_id, None)
                    cur = self._currency
                    _m = f"  [SL EXIT]   {symbol} {cur}{price:.4f}  confirmed {count} polls ({elapsed:.0f}s) below SL"
                    print(f"{_RD}{_m}{_R}"); self._log(_m)
                    self._exiting.add(pos_id)
                    threading.Thread(
                        target=self._scalp_exit, args=(pos, price, 'SL'), daemon=True
                    ).start()
                else:
                    cur = self._currency
                    _m = f"  [SL WATCH]  {symbol} {cur}{price:.4f}  confirming ({count}/{SL_CONFIRM_POLLS} polls)"
                    print(f"{_YL}{_m}{_R}"); self._log(_m)

            elif at_target:
                # Target hit — clear any pending SL watch, activate trailing stop
                self._sl_breach_time.pop(pos_id, None)
                self._trail_active[pos_id] = True
                # Trail floor = entry + 0.3% — guarantees minimum profit if trail activates
                floor = round(pos['entry_price'] * 1.003, _price_decimals(pos['entry_price']))
                self._trail_sl[pos_id] = floor
                cur = self._currency
                _m = f"  [TRAIL ON]  {symbol} {cur}{price:.4f}  target hit — trail floor {cur}{floor:.4f}"
                print(f"{_G}{_m}{_R}"); self._log(_m)

            else:
                # Price is above SL — cancel any pending breach watch
                if pos_id in self._sl_breach_time:
                    count = self._sl_breach_count.pop(pos_id, 0)
                    elapsed = time.time() - self._sl_breach_time.pop(pos_id)
                    if elapsed > 2:
                        cur = self._currency
                        _m = f"  [SL RECOVER] {symbol} {cur}{price:.4f}  bounced back above SL after {count} poll(s) ({elapsed:.0f}s) — holding"
                        print(f"{_G}{_m}{_R}"); self._log(_m)

                # Time-based exit: free capital if position is stagnating (toggle via USE_TIME_EXIT in .env)
                if not self._trail_active.get(pos_id) and pos_id not in self._exiting:
                    from config import MAX_HOLD_MINUTES, USE_TIME_EXIT
                    if USE_TIME_EXIT:
                        entry_time_str = pos.get('entry_time', '')
                        if entry_time_str:
                            try:
                                entry_dt  = datetime.strptime(entry_time_str[:19], '%Y-%m-%d %H:%M:%S').replace(tzinfo=_IST)
                                mins_open = (datetime.now(_IST) - entry_dt).total_seconds() / 60
                                pct_move  = (price - pos['entry_price']) / pos['entry_price']
                                if mins_open >= MAX_HOLD_MINUTES and pct_move < _tgt_pct * 0.5:
                                    cur = self._currency
                                    _m = f"  [TIME EXIT]  {symbol} {cur}{price:.4f}  {mins_open:.0f}min open ({pct_move*100:+.1f}%) — freeing capital"
                                    print(f"{_YL}{_m}{_R}"); self._log(_m)
                                    self._exiting.add(pos_id)
                                    threading.Thread(
                                        target=self._scalp_exit, args=(pos, price, 'TIME'), daemon=True
                                    ).start()
                            except Exception:
                                pass

                # Profit timer: if in profit for N minutes without hitting target, take it
                if not self._trail_active.get(pos_id) and pos_id not in self._exiting:
                    from config import USE_PROFIT_TIMER, PROFIT_TIMER_MINUTES
                    if USE_PROFIT_TIMER:
                        in_profit = price > pos['entry_price']
                        if in_profit:
                            if pos_id not in self._profit_since:
                                self._profit_since[pos_id] = time.time()
                            else:
                                profit_mins = (time.time() - self._profit_since[pos_id]) / 60
                                if profit_mins >= PROFIT_TIMER_MINUTES:
                                    gain_pct = (price - pos['entry_price']) / pos['entry_price'] * 100
                                    cur = self._currency
                                    _m = f"  [PROFIT TIMER] {symbol} {cur}{price:.4f}  +{gain_pct:.2f}% for {profit_mins:.0f}min — taking profit"
                                    print(f"{_G}{_m}{_R}"); self._log(_m)
                                    self._exiting.add(pos_id)
                                    threading.Thread(
                                        target=self._scalp_exit, args=(pos, price, 'PROFIT_TIMER'), daemon=True
                                    ).start()
                        else:
                            # Dipped below entry — reset the clock
                            self._profit_since.pop(pos_id, None)

                # Loss timer: if in loss for N minutes without recovering, cut it early
                if not self._trail_active.get(pos_id) and pos_id not in self._exiting:
                    from config import USE_LOSS_TIMER, LOSS_TIMER_MINUTES
                    if USE_LOSS_TIMER:
                        in_loss = price < pos['entry_price']
                        if in_loss:
                            if pos_id not in self._loss_since:
                                self._loss_since[pos_id] = time.time()
                            else:
                                loss_mins = (time.time() - self._loss_since[pos_id]) / 60
                                if loss_mins >= LOSS_TIMER_MINUTES:
                                    loss_pct = (price - pos['entry_price']) / pos['entry_price'] * 100
                                    cur = self._currency
                                    _m = f"  [LOSS TIMER] {symbol} {cur}{price:.4f}  {loss_pct:.2f}% for {loss_mins:.0f}min — cutting loss"
                                    print(f"{_RD}{_m}{_R}"); self._log(_m)
                                    self._exiting.add(pos_id)
                                    threading.Thread(
                                        target=self._scalp_exit, args=(pos, price, 'LOSS_TIMER'), daemon=True
                                    ).start()
                        else:
                            # Recovered above entry — reset the clock
                            self._loss_since.pop(pos_id, None)

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
                reason_map = {'TARGET': 'profit target', 'TRAIL': 'trailing stop', 'SL': 'stop-loss', 'TIME': 'time exit — stagnating', 'PROFIT_TIMER': 'profit timer — partial profit taken', 'LOSS_TIMER': 'loss timer — cut early'}
                reason = f"Scalp {reason_map.get(trigger, 'exit')} hit"
                pnl, pct = self.trader.sell(still_open, price, reason)
                if pnl == 0.0 and pct == 0.0:
                    return
                sign = "+" if pnl >= 0 else ""
                cur  = self._currency
                _c   = _G if pnl >= 0 else _RD
                _m = f"  [SOLD] {sym} @ {cur}{price:.4f}  P&L: {sign}{cur}{pnl:.4f} ({sign}{pct:.2f}%)"
                print(f"{_c}{_m}{_R}"); self._log(_m)
        except Exception as e:
            print(f"[Monitor] Scalp exit error for {sym}: {e}")
        finally:
            self._exiting.discard(pos['id'])
            self._trail_active.pop(pos['id'], None)
            self._trail_sl.pop(pos['id'], None)
            self._sl_breach_time.pop(pos['id'], None)
            self._sl_breach_count.pop(pos['id'], None)
            self._profit_since.pop(pos['id'], None)
            self._loss_since.pop(pos['id'], None)
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
                # Trail cooldown: time gate only
                self._sl_cooldown[sym] = (datetime.now(_IST), price)
            elif trigger == 'TIME':
                # Light cooldown: 15 min — stock was stagnant, not a directional failure
                self._sl_cooldown[sym] = (datetime.now(_IST) - timedelta(minutes=15), price)
            elif trigger == 'PROFIT_TIMER':
                # Light cooldown: 10 min — we took profit early, allow re-entry soon
                self._sl_cooldown[sym] = (datetime.now(_IST) - timedelta(minutes=20), price)
            elif trigger == 'LOSS_TIMER':
                # Moderate cooldown: 20 min — position was going wrong direction, wait before re-entry
                self._sl_cooldown[sym] = (datetime.now(_IST) - timedelta(minutes=10), price)
            # Persist updated state so a restart picks up current cooldowns and day bans
            threading.Thread(target=self.save_state, daemon=True).start()

    # ── LTP poll fallback — used when WebSocket is not connected ─────────────
    def _poll_positions(self):
        from data.fetcher import get_live_price
        _get_price = self._price_fn or get_live_price

        for pos in list(self.trader.open_positions):
            try:
                sym   = pos['symbol']
                price = _get_price(sym)
                if price is None or price <= 0 or math.isnan(price):
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
                  sl_pct=None, target_pct=None, market='india'):
    monitor = PositionMonitor(trader, on_exit_fn, live_feed, price_fn=price_fn,
                              always_active=always_active, currency=currency, name=name,
                              market_open_fn=market_open_fn, eod_close=eod_close,
                              sl_pct=sl_pct, target_pct=target_pct, market=market)
    monitor.start()
    return monitor
