"""
Binance Client — price data ONLY.
==================================
BINANCE_PAPER=true is enforced: this module NEVER places real orders.
Binance is used solely as a price/OHLCV data source.
All trade simulation happens internally in SQL Server (source='crypto_paper').
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from config import BINANCE_KEY, BINANCE_SECRET, BINANCE_PAPER

if not BINANCE_PAPER:
    raise RuntimeError("BINANCE_PAPER must be true — real crypto orders are not supported by this bot.")

_client = None


_client_init_attempted = False

def _get_client():
    global _client, _client_init_attempted
    if _client is None and BINANCE_KEY and not _client_init_attempted:
        _client_init_attempted = True
        try:
            from binance.client import Client
            _client = Client(BINANCE_KEY, BINANCE_SECRET)
        except ImportError:
            pass   # python-binance not installed — silent, CryptoFeed already logged this
        except Exception as e:
            print(f"[Binance] Client init failed: {e}")
    return _client


def _binance_to_yf(symbol):
    """Convert Binance pair to yfinance ticker: BTCUSDT → BTC-USD."""
    if symbol.endswith('USDT'):
        return symbol[:-4] + '-USD'
    if symbol.endswith('BUSD'):
        return symbol[:-4] + '-USD'
    return symbol


def get_crypto_ohlcv(symbol, interval='5m', limit=100):
    """
    Returns a pandas DataFrame (Open/High/Low/Close/Volume) for the given
    Binance symbol (e.g. 'BTCUSDT'). Falls back to yfinance on failure.
    """
    import pandas as pd

    client = _get_client()
    if client is not None:
        try:
            from binance.client import Client as BClient
            klines = client.get_klines(symbol=symbol, interval=interval, limit=limit)
            if klines and len(klines) >= 30:
                df = pd.DataFrame(klines, columns=[
                    'open_time', 'Open', 'High', 'Low', 'Close', 'Volume',
                    'close_time', 'qav', 'num_trades', 'taker_base', 'taker_quote', 'ignore'
                ])
                df = df[['Open', 'High', 'Low', 'Close', 'Volume']].astype(float)
                df.index = pd.to_datetime([k[0] for k in klines], unit='ms')
                return df
        except Exception as e:
            print(f"[Binance OHLCV] {symbol}: {e}")

    return _yf_ohlcv_fallback(symbol, interval)


def get_crypto_live_price(symbol):
    """Live price for a Binance symbol (e.g. 'BTCUSDT') → float or None."""
    client = _get_client()
    if client is not None:
        try:
            ticker = client.get_symbol_ticker(symbol=symbol)
            price = float(ticker.get('price', 0))
            if price > 0:
                return price
        except Exception:
            pass
    return _yf_price_fallback(symbol)


def _yf_ohlcv_fallback(symbol, interval='5m'):
    """yfinance fallback for OHLCV data — respects interval param."""
    import yfinance as yf
    import pandas as pd
    # Map Binance intervals to yfinance equivalents
    _iv = {'1m':'1m','3m':'2m','5m':'5m','15m':'15m','30m':'30m','1h':'60m','4h':'60m','1d':'1d'}
    yf_iv = _iv.get(interval, '5m')
    try:
        yf_sym = _binance_to_yf(symbol)
        df = yf.Ticker(yf_sym).history(period='5d', interval=yf_iv)
        if df.empty or len(df) < 10:
            return None
        df.index = pd.to_datetime(df.index)
        return df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()
    except Exception:
        return None


def _yf_price_fallback(symbol):
    """yfinance fallback for live price — tries multiple attributes then history."""
    import yfinance as yf
    try:
        yf_sym = _binance_to_yf(symbol)
        ticker = yf.Ticker(yf_sym)
        # Try fast_info attributes (different yfinance versions use different names)
        try:
            info = ticker.fast_info
            for attr in ('last_price', 'regularMarketPrice', 'lastPrice', 'previousClose'):
                v = getattr(info, attr, None)
                if v is not None:
                    price = float(v)
                    if price > 0:
                        return price
        except Exception:
            pass
        # Reliable fallback: most-recent 1-min bar close
        hist = ticker.history(period='1d', interval='1m')
        if not hist.empty:
            price = float(hist['Close'].dropna().iloc[-1])
            if price > 0:
                return price
        return None
    except Exception:
        return None


def test_connection():
    """Returns (connected: bool, message: str)."""
    try:
        client = _get_client()
        if client is None:
            return False, "No API keys set or client init failed"
        status = client.get_system_status()
        if status.get('status') == 0:
            return True, "Connected OK (system operational)"
        return False, f"System status: {status}"
    except Exception as e:
        return False, str(e)
