"""
US Stock Watchlist — Top S&P 500 + NASDAQ 100 stocks by liquidity.
Filtered for: high volume, price > $10, optionable (large cap).
These are the most liquid US stocks — tight spreads, fast fills.
"""

# Mega-cap (most liquid — scan first)
_MEGA_CAP = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO",
    "JPM", "LLY", "V", "UNH", "XOM", "MA", "JNJ", "COST", "HD",
    "BAC", "NFLX", "AMD", "WMT", "ORCL", "CRM", "PG", "CVX",
    "KO", "ABBV", "MRK", "ADBE", "TMO", "CSCO", "ACN", "GE",
    "LIN", "NKE", "PEP", "TXN", "QCOM", "HON", "MDT",
]

# High-momentum tech / growth (fast movers — ideal for scalping)
_GROWTH = [
    "AMD", "MRVL", "PANW", "CRWD", "SNOW", "PLTR", "COIN",
    "PYPL", "SHOP", "UBER", "LYFT", "ABNB", "RBLX", "RIVN",
    "MU", "INTC", "ARM", "SMCI", "DELL", "HPE",
    "ZM", "DDOG", "NET", "OKTA", "TWLO", "DOCU",
    "AMGN", "GILD", "BIIB", "REGN", "MRNA",
]

# Large-cap ETFs (very liquid — great for scalping)
_ETFS = [
    "SPY", "QQQ", "IWM", "DIA", "XLF", "XLK", "XLE",
    "SOXS", "SOXL", "TQQQ", "SQQQ",  # leveraged — higher volatility
]

# Combined unique list
_US_WATCHLIST = list(dict.fromkeys(_MEGA_CAP + _GROWTH + _ETFS))


def get_us_stocks():
    """Return full US watchlist."""
    return list(_US_WATCHLIST)


def get_us_mega_cap():
    """Return only mega-cap stocks — use this for faster scans."""
    return list(_MEGA_CAP)


def is_us_etf(symbol):
    return symbol.upper() in {e.upper() for e in _ETFS}


def is_us_leveraged_etf(symbol):
    leveraged = {"SOXS", "SOXL", "TQQQ", "SQQQ", "SPXU", "SPXL", "LABU", "LABD"}
    return symbol.upper() in leveraged


if __name__ == "__main__":
    stocks = get_us_stocks()
    print(f"US watchlist: {len(stocks)} symbols")
    print(f"  Mega-cap: {len(_MEGA_CAP)}")
    print(f"  Growth  : {len(_GROWTH)}")
    print(f"  ETFs    : {len(_ETFS)}")
    print(f"  First 10: {stocks[:10]}")
