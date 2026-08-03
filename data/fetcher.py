import yfinance as yf
import pandas as pd
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from data.angel_login import get_api, force_relogin

# For live scanning — recent data is enough for indicators
SCAN_PERIOD = "1y"

# For ML training and backtesting — maximum available history
MAX_PERIOD = "max"


def get_historical(symbol, period=SCAN_PERIOD, interval="1d"):
    """Fetch OHLCV data. Use period='max' for full history, '1y' for scanning."""
    try:
        ticker = yf.Ticker(f"{symbol}.NS")
        df = ticker.history(period=period, interval=interval)
        if df.empty:
            return None
        df.index = pd.to_datetime(df.index)
        df = df[['Open','High','Low','Close','Volume']].dropna()
        return df
    except Exception:
        return None


def get_intraday(symbol):
    """Fetch 5-day 5-minute candles for intraday signal calculation."""
    try:
        ticker = yf.Ticker(f"{symbol}.NS")
        df = ticker.history(period="5d", interval="5m")
        if df.empty or len(df) < 30:
            return None
        df.index = pd.to_datetime(df.index)
        df = df[['Open','High','Low','Close','Volume']].dropna()
        return df
    except Exception:
        return None


def get_us_intraday(symbol):
    """Fetch 5-day 5-minute candles for US stocks (no .NS suffix)."""
    try:
        ticker = yf.Ticker(symbol)          # plain symbol: AAPL, MSFT etc.
        df = ticker.history(period="5d", interval="5m")
        if df.empty or len(df) < 30:
            return None
        df.index = pd.to_datetime(df.index)
        df = df[['Open','High','Low','Close','Volume']].dropna()
        return df
    except Exception:
        return None


def get_max_history(symbol):
    """Fetch maximum available history for a stock — used for ML training and backtesting."""
    df = get_historical(symbol, period=MAX_PERIOD)
    if df is not None and len(df) > 0:
        years = round(len(df) / 252, 1)
        print(f"  [{symbol}] {len(df)} days of history (~{years} years)")
    return df


def _is_token_error(response):
    """Returns True if Angel One returned an AG8001 / Invalid Token error.
    Angel One uses 'status' on some endpoints (ltpData) and 'success' on others
    (searchScrip). errorCode capitalisation also varies — check both.
    """
    if isinstance(response, dict):
        status_ok  = response.get('status',  True)   # ltpData style
        success_ok = response.get('success', True)   # searchScrip style
        if not status_ok or not success_ok:
            msg  = str(response.get('message', '')).lower()
            code = str(response.get('errorCode', '') or response.get('errorcode', ''))
            if 'invalid token' in msg or 'AG8001' in code:
                return True
    return False


def get_live_price(symbol):
    for attempt in range(2):
        try:
            api   = get_api()
            token = _get_token(symbol)
            if token:
                quote = api.ltpData("NSE", symbol, token)
                if _is_token_error(quote):
                    raise _TokenError()
                if quote.get('status'):
                    return float(quote['data']['ltp'])
        except _TokenError:
            if attempt == 0:
                print(f"[Auth] Token expired — re-logging in ({symbol})")
                force_relogin()
                continue
        except Exception:
            pass
        break
    return _fallback_price(symbol)


def _fallback_price(symbol):
    try:
        ticker = yf.Ticker(f"{symbol}.NS")
        data = ticker.history(period="1d", interval="1m")
        if not data.empty:
            return float(data['Close'].iloc[-1])
    except Exception:
        pass
    return None


class _TokenError(Exception):
    pass


def _api_ok(data):
    """True when an Angel One response indicates success (handles both key names)."""
    return bool(data.get('status') or data.get('success'))


def _pick_eq_token(results):
    """Return the -EQ (equity) series token; fall back to first result."""
    for r in results:
        if r.get('tradingsymbol', '').endswith('-EQ'):
            return r['symboltoken']
    return results[0]['symboltoken']


_token_cache = {}  # symbol -> symboltoken, cached for the process lifetime.
                   # Only successful lookups are cached -- a transient failure
                   # still retries next call rather than permanently returning
                   # None for that symbol. Tokens don't change intraday, and
                   # position_monitor.py's 15s poll loop was re-fetching this
                   # via a fresh searchScrip call every single cycle.


def _get_token(symbol):
    if symbol in _token_cache:
        return _token_cache[symbol]
    for attempt in range(2):
        try:
            api  = get_api()
            data = api.searchScrip("NSE", symbol)
            if _is_token_error(data):
                raise _TokenError()
            if _api_ok(data) and data.get('data'):
                token = _pick_eq_token(data['data'])
                _token_cache[symbol] = token
                return token
            return None
        except _TokenError:
            if attempt == 0:
                print(f"[Auth] Token expired — re-logging in ({symbol})")
                force_relogin()
                continue
        except Exception:
            pass
        break
    return None


def get_min_lot_size(symbol):
    """
    Return the minimum tradeable quantity for a stock from NSE via Angel One.
    For NSE equity (delivery) the exchange minimum is always 1 share.
    F&O lot sizes don't apply here — we trade equity only.
    Falls back to 1 if API unavailable.
    """
    for attempt in range(2):
        try:
            api  = get_api()
            data = api.searchScrip("NSE", symbol)
            if _is_token_error(data):
                raise _TokenError()
            if data and _api_ok(data) and data.get('data'):
                eq = next((r for r in data['data'] if r.get('tradingsymbol', '').endswith('-EQ')), data['data'][0])
                lot = eq.get('lotsize', 1)
                return max(1, int(lot))
            return 1
        except _TokenError:
            if attempt == 0:
                print(f"[Auth] Token expired — re-logging in ({symbol})")
                force_relogin()
                continue
        except Exception:
            pass
        break
    return 1


def get_bulk_historical(symbols, period=SCAN_PERIOD):
    """Fetch historical data for multiple symbols."""
    results = {}
    for sym in symbols:
        df = get_historical(sym, period=period)
        if df is not None and len(df) > 50:
            results[sym] = df
    return results


def get_bulk_max_history(symbols):
    """Fetch maximum history for multiple symbols — for ML training."""
    results = {}
    print(f"Fetching maximum history for {len(symbols)} stocks...")
    for sym in symbols:
        df = get_max_history(sym)
        if df is not None and len(df) > 100:
            results[sym] = df
    return results


if __name__ == "__main__":
    print("=== Scan history (1y) ===")
    df = get_historical("RELIANCE")
    if df is not None:
        print(f"RELIANCE: {len(df)} rows, last close ₹{df['Close'].iloc[-1]:.2f}")

    print("\n=== Maximum history ===")
    df_max = get_max_history("RELIANCE")
    if df_max is not None:
        print(f"RELIANCE max: {len(df_max)} rows, from {df_max.index[0].date()} to {df_max.index[-1].date()}")

    print("\n=== Live price ===")
    price = _fallback_price("TCS")
    if price:
        print(f"TCS: ₹{price:.2f}")
