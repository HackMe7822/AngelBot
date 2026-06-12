"""
Binance Client — price data ONLY.
==================================
BINANCE_PAPER=true is enforced: this module NEVER places real orders.
Binance is used solely as a price/OHLCV data source.
All trade simulation happens internally in SQLite (source='crypto_paper').
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

    return _yf_ohlcv_fallback(symbol)


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


def _yf_ohlcv_fallback(symbol):
    """yfinance fallback for OHLCV data."""
    import yfinance as yf
    import pandas as pd
    try:
        yf_sym = _binance_to_yf(symbol)
        df = yf.Ticker(yf_sym).history(period='5d', interval='5m')
        if df.empty or len(df) < 30:
            return None
        df.index = pd.to_datetime(df.index)
        return df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()
    except Exception:
        return None


def _yf_price_fallback(symbol):
    """yfinance fallback for live price."""
    import yfinance as yf
    try:
        yf_sym = _binance_to_yf(symbol)
        info = yf.Ticker(yf_sym).fast_info
        price = float(getattr(info, 'last_price', 0) or 0)
        return price if price > 0 else None
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
