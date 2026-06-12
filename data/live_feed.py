"""
Live Price Feed — Angel One WebSocket
======================================
Streams real-time LTP for every subscribed symbol.
Pushes each tick instantly to registered callbacks — no polling interval.

Usage:
    feed = LiveFeed()
    feed.on_tick(lambda sym, price: print(sym, price))
    feed.start()
    feed.subscribe(['RELIANCE', 'TCS'])
"""

import threading
import time
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from SmartApi.smartWebSocketV2 import SmartWebSocketV2
from config import ANGEL_API_KEY, ANGEL_CLIENT_ID

_EXCHANGE_NSE = 1
_MODE_LTP     = 1   # mode 1 = LTP only (fastest, lowest overhead)


class LiveFeed:
    def __init__(self):
        self._prices     = {}    # symbol  → latest price (float, rupees)
        self._tokens     = {}    # symbol  → token string
        self._tok_to_sym = {}    # token   → symbol
        self._callbacks  = []    # fn(symbol, price) fired on every tick
        self._ws         = None
        self._connected  = False
        self._running    = False
        self._lock       = threading.Lock()

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self):
        self._running = True
        threading.Thread(target=self._run_loop, daemon=True, name="LiveFeed").start()
        print("[LiveFeed] Started — Angel One WebSocket real-time price feed")

    def stop(self):
        self._running = False
        self._close_ws()

    def on_tick(self, callback):
        """Register fn(symbol, price) — called on every price tick."""
        self._callbacks.append(callback)

    def subscribe(self, symbols):
        """Subscribe symbols to the live feed. Safe to call at any time."""
        new_tokens = []
        for sym in symbols:
            if sym not in self._tokens:
                token = self._fetch_token(sym)
                if token:
                    self._tokens[sym]       = token
                    self._tok_to_sym[token] = sym
                    new_tokens.append(token)
                    print(f"[LiveFeed] Subscribed {sym} (token {token})")
        if new_tokens and self._connected and self._ws:
            self._do_subscribe(new_tokens)

    def unsubscribe(self, symbols):
        for sym in symbols:
            token = self._tokens.pop(sym, None)
            if token:
                self._tok_to_sym.pop(token, None)

    def get_price(self, symbol):
        """Return latest live price, or None if not yet received."""
        return self._prices.get(symbol)

    @property
    def is_connected(self):
        return self._connected

    # ── WebSocket lifecycle ───────────────────────────────────────────────────

    def _run_loop(self):
        _fail_count = 0
        while self._running:
            try:
                self._connect()
                _fail_count = 0
            except Exception as e:
                print(f"[LiveFeed] Connection error: {e}")
                _fail_count += 1
            self._connected = False
            if self._running:
                # After 3 consecutive failures force a fresh login (token may be stale)
                if _fail_count >= 3:
                    from data.angel_login import force_relogin
                    print("[LiveFeed] 3 consecutive failures — forcing fresh login")
                    force_relogin()
                    _fail_count = 0
                    time.sleep(30)
                else:
                    print("[LiveFeed] Reconnecting in 10s...")
                    time.sleep(10)

    def _connect(self):
        from data.angel_login import get_auth_token, get_feed_token, force_relogin
        auth = get_auth_token()
        feed = get_feed_token()
        # Only force a new login if tokens are missing (expired or first run)
        if not auth or not feed:
            print("[LiveFeed] Tokens missing — logging in...")
            force_relogin()
            auth = get_auth_token()
            feed = get_feed_token()
        if not auth or not feed:
            print("[LiveFeed] Auth tokens not ready — retrying in 30s")
            time.sleep(30)
            return

        self._ws = SmartWebSocketV2(
            auth, ANGEL_API_KEY, ANGEL_CLIENT_ID, feed,
            max_retry_attempt=10, retry_delay=15
        )
        self._ws.on_open  = self._on_open
        self._ws.on_data  = self._on_data
        self._ws.on_error = self._on_error
        self._ws.on_close = self._on_close
        self._ws.connect()   # blocking — returns when connection closes

    def _close_ws(self):
        if self._ws:
            try:
                self._ws.close_connection()
            except Exception:
                pass

    # ── WebSocket callbacks ───────────────────────────────────────────────────

    def _on_open(self, wsapp):
        self._connected = True
        tokens = list(self._tokens.values())
        print(f"[LiveFeed] Connected ✓  subscribing {len(tokens)} symbol(s)")
        if tokens:
            self._do_subscribe(tokens)

    def _on_data(self, wsapp, message):
        try:
            token = str(message.get('token', '')).strip()
            ltp   = message.get('last_traded_price', 0)
            if not token or not ltp:
                return
            price = ltp / 100.0   # Angel One sends paise → rupees
            sym   = self._tok_to_sym.get(token)
            if not sym:
                return
            with self._lock:
                self._prices[sym] = price
            for cb in self._callbacks:
                try:
                    cb(sym, price)
                except Exception as e:
                    print(f"[LiveFeed] Callback error: {e}")
        except Exception:
            pass

    def _on_error(self, wsapp, error):
        print(f"[LiveFeed] WebSocket error: {error}")
        self._connected = False

    def _on_close(self, wsapp):
        print("[LiveFeed] WebSocket closed")
        self._connected = False

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _do_subscribe(self, tokens):
        try:
            self._ws.subscribe("live1", _MODE_LTP,
                               [{"exchangeType": _EXCHANGE_NSE, "tokens": tokens}])
        except Exception as e:
            print(f"[LiveFeed] Subscribe error: {e}")

    def _fetch_token(self, symbol):
        from data.angel_login import get_api, force_relogin
        for attempt in range(2):
            try:
                data = get_api().searchScrip("NSE", symbol)
                if not data:
                    return None
                # Handle both 'status' (ltpData style) and 'success' (searchScrip style)
                is_ok = data.get('status', True) and data.get('success', True)
                if not is_ok:
                    msg  = str(data.get('message', '')).lower()
                    code = str(data.get('errorCode', '') or data.get('errorcode', ''))
                    if ('invalid token' in msg or 'AG8001' in code) and attempt == 0:
                        print(f"[LiveFeed] Token expired fetching {symbol} — re-logging in")
                        force_relogin()
                        continue
                    return None
                if data.get('data'):
                    eq = next((r for r in data['data'] if r.get('tradingsymbol', '').endswith('-EQ')), data['data'][0])
                    return str(eq['symboltoken'])
            except Exception:
                pass
            break
        return None


if __name__ == "__main__":
    import time as _time
    from data.nifty_stocks import get_nifty50

    feed = LiveFeed()
    count = [0]

    def on_tick(sym, price):
        count[0] += 1
        print(f"  TICK [{count[0]:4d}]  {sym:15s} ₹{price:.2f}")

    feed.on_tick(on_tick)
    feed.start()

    _time.sleep(3)
    feed.subscribe(['RELIANCE', 'TCS', 'HDFCBANK', 'INFY', 'SBIN'])

    print("Streaming live prices — Ctrl+C to stop")
    try:
        while True:
            _time.sleep(1)
    except KeyboardInterrupt:
        feed.stop()
        print(f"\nReceived {count[0]} ticks")
