"""
Crypto Live Feed — Binance WebSocket
=====================================
Streams real-time prices for every subscribed symbol.
Pushes each tick instantly to registered callbacks — no polling interval.

With python-binance installed:  1-second WebSocket ticks from Binance
Without python-binance:         3-second REST polling fallback (still 5× faster than old 15s)

Usage:
    feed = CryptoFeed()
    feed.on_tick(lambda sym, price: print(sym, price))
    feed.start()
    feed.subscribe(['BTCUSDT', 'ETHUSDT'])
"""

import threading
import time
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from config import BINANCE_KEY, BINANCE_SECRET


class CryptoFeed:
    def __init__(self):
        self._prices          = {}       # symbol → latest price
        self._callbacks       = []       # fn(symbol, price) fired on every tick
        self._subscribed      = set()    # symbols to fire callbacks for
        self._connected       = False
        self._running         = False
        self._last_tick_time  = 0.0      # epoch seconds of last received tick
        self._lock            = threading.Lock()
        self._mode            = 'unknown'

    # ── Public API (mirrors LiveFeed interface) ───────────────────────────────

    def start(self):
        self._running = True
        threading.Thread(target=self._run_loop, daemon=True, name="CryptoFeed").start()
        print("[CryptoFeed] Starting — will use WebSocket if python-binance is installed")

    def stop(self):
        self._running = False

    def on_tick(self, callback):
        """Register fn(symbol, price) — called on every price tick."""
        self._callbacks.append(callback)

    def subscribe(self, symbols):
        """Subscribe symbols for live callbacks. Safe to call at any time."""
        with self._lock:
            for sym in symbols:
                self._subscribed.add(sym.upper())

    def unsubscribe(self, symbols):
        with self._lock:
            for sym in symbols:
                self._subscribed.discard(sym.upper())

    def get_price(self, symbol):
        """Return latest cached price, or None."""
        return self._prices.get(symbol.upper())

    @property
    def is_connected(self):
        return self._connected

    # ── Internal: fire callbacks ──────────────────────────────────────────────

    def _fire(self, symbol, price):
        self._prices[symbol] = price
        self._last_tick_time = time.time()
        if symbol in self._subscribed:
            for cb in self._callbacks:
                try:
                    cb(symbol, price)
                except Exception as e:
                    print(f"[CryptoFeed] Callback error ({symbol}): {e}")

    # ── Main loop — tries WebSocket first, falls back to fast polling ─────────

    def _run_loop(self):
        # Use find_spec (no actual import) to avoid Python 3.14 import-lock deadlock
        # when main thread and this thread both try to import binance simultaneously.
        import importlib.util
        if importlib.util.find_spec('binance') is not None:
            self._mode = 'websocket'
            self._run_websocket()
        else:
            print("[CryptoFeed] python-binance not installed — using 3-second REST polling.")
            print("[CryptoFeed] Run: pip install python-binance  to get real-time WebSocket ticks.")
            self._mode = 'polling'
            self._run_polling()

    # ── Mode 1: Binance WebSocket (python-binance required) ───────────────────

    def _run_websocket(self):
        import warnings
        warnings.filterwarnings('ignore', category=DeprecationWarning)
        warnings.filterwarnings('ignore', category=PendingDeprecationWarning)

        from binance import ThreadedWebsocketManager

        consecutive_failures = 0

        while self._running:
            twm = None
            try:
                twm = ThreadedWebsocketManager(
                    api_key=BINANCE_KEY,
                    api_secret=BINANCE_SECRET
                )
                twm.start()
                twm.start_miniticker_socket(callback=self._on_ws_tick)

                self._connected = True
                self._last_tick_time = 0.0
                consecutive_failures = 0
                print("[CryptoFeed] Binance WebSocket connected — real-time ticks")

                # Reconnect if no tick received for 15s (fast recovery from silent drop)
                while self._running:
                    time.sleep(5)
                    elapsed = time.time() - self._last_tick_time
                    if self._last_tick_time > 0 and elapsed > 15:
                        print(f"[CryptoFeed] No ticks for {elapsed:.0f}s — reconnecting...")
                        break

            except Exception as e:
                print(f"[CryptoFeed] WebSocket error: {e}")
                consecutive_failures += 1
            finally:
                self._connected = False
                if twm:
                    try:
                        twm.stop()
                    except Exception:
                        pass

            # After 3 rapid failures, fall back to REST polling permanently
            if consecutive_failures >= 3:
                print("[CryptoFeed] WebSocket unstable — switching to REST polling fallback.")
                self._run_polling()
                return

            if self._running:
                wait = min(30, 5 * (consecutive_failures + 1))
                print(f"[CryptoFeed] Reconnecting in {wait}s...")
                time.sleep(wait)

    def _on_ws_tick(self, msg):
        """Fires on every Binance mini-ticker update (~1s per symbol)."""
        try:
            if not isinstance(msg, dict):
                return
            # Mini-ticker event type
            if msg.get('e') == '24hrMiniTicker':
                sym   = msg.get('s', '')        # e.g. 'BTCUSDT'
                price = float(msg.get('c', 0))  # current close price
                if sym and price > 0:
                    self._fire(sym, price)
            # Some responses come as a list of tickers
            elif isinstance(msg, list):
                for item in msg:
                    sym   = item.get('s', '')
                    price = float(item.get('c', 0))
                    if sym and price > 0:
                        self._fire(sym, price)
        except Exception:
            pass

    # ── Mode 2: Fast REST polling fallback (3-second interval) ───────────────

    def _run_polling(self):
        """
        Polls live prices every 3 seconds for all subscribed symbols.
        5× faster than the old 15-second monitor polling.
        is_connected=True so PositionMonitor skips its own polling.
        """
        from data.binance_client import get_crypto_live_price

        self._connected = True
        print("[CryptoFeed] Fast REST polling active (3s interval per symbol)")

        while self._running:
            subscribed = list(self._subscribed)  # snapshot
            for sym in subscribed:
                if not self._running:
                    break
                try:
                    price = get_crypto_live_price(sym)
                    if price and price > 0:
                        self._fire(sym, price)
                except Exception:
                    pass
            time.sleep(3)

        self._connected = False
