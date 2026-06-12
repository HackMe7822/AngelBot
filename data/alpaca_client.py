"""
Alpaca REST Client — US market prices and account info.
Paper trading only until you set ALPACA_PAPER=false in .env.

Free API: https://alpaca.markets (create account → Paper Trading → API Keys)
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from config import ALPACA_KEY, ALPACA_SECRET, ALPACA_PAPER

_trading_client = None
_data_client    = None


def get_trading_client():
    global _trading_client
    if _trading_client is None:
        from alpaca.trading.client import TradingClient
        _trading_client = TradingClient(ALPACA_KEY, ALPACA_SECRET, paper=ALPACA_PAPER)
    return _trading_client


def get_data_client():
    global _data_client
    if _data_client is None:
        from alpaca.data.historical import StockHistoricalDataClient
        _data_client = StockHistoricalDataClient(ALPACA_KEY, ALPACA_SECRET)
    return _data_client


def get_us_account():
    """Returns {'balance': float, 'buying_power': float, 'equity': float}."""
    try:
        acct = get_trading_client().get_account()
        return {
            'balance':       float(acct.cash),
            'buying_power':  float(acct.buying_power),
            'equity':        float(acct.equity),
            'portfolio_value': float(acct.portfolio_value),
        }
    except Exception as e:
        print(f"[Alpaca] Account fetch failed: {e}")
        return None


def get_us_live_price(symbol):
    """Fetch last actual trade price via Alpaca REST. Falls back to yfinance.

    Uses last trade (not ask/bid quote) so paper simulation matches real execution —
    ask prices can spike far above last trade during thin moments, causing artificial exits.
    """
    try:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockLatestTradeRequest
        client = get_data_client()
        req    = StockLatestTradeRequest(symbol_or_symbols=symbol)
        trades = client.get_stock_latest_trade(req)
        t      = trades.get(symbol)
        if t:
            price = float(t.price or 0)
            if price > 0:
                return price
    except Exception:
        pass
    return _yf_us_fallback(symbol)


def _yf_us_fallback(symbol):
    """yfinance 1-min fallback for US stocks — no .NS suffix."""
    try:
        import yfinance as yf, math
        df = yf.Ticker(symbol).history(period="1d", interval="1m")
        if not df.empty:
            val = float(df['Close'].iloc[-1])
            if val > 0 and not math.isnan(val):
                return val
    except Exception:
        pass
    return None


if __name__ == "__main__":
    print("Testing Alpaca connection...")
    acct = get_us_account()
    if acct:
        print(f"  Paper balance:  ${acct['balance']:,.2f}")
        print(f"  Buying power:   ${acct['buying_power']:,.2f}")
        print(f"  Portfolio value:${acct['portfolio_value']:,.2f}")
    else:
        print("  Connection failed — check ALPACA_KEY and ALPACA_SECRET in .env")

    print("\nTesting live price (AAPL)...")
    price = get_us_live_price("AAPL")
    print(f"  AAPL: ${price:.2f}" if price else "  Failed")
