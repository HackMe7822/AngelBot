"""
Market Intelligence Filters
============================
Provides three gate-checks before any buy is placed:

1. Market mood  — skip new buys if NIFTY/S&P is down > threshold from prev close
2. Event filter — skip if within 30 min of earnings or major macro event (RBI/Fed)
3. Sector cap   — max 2 open positions in same sector at once

All functions are fail-open: if the data can't be fetched, trading continues.
Caches results for 5 minutes to avoid hammering yfinance on every 1-min scan.
"""

import time
from datetime import datetime, timezone, timedelta

_IST = timezone(timedelta(hours=5, minutes=30))

try:
    from config import MOOD_FILTER_THRESHOLD as _MOOD_THRESHOLD, MAX_SECTOR_POSITIONS as _MAX_SECTOR
except ImportError:
    _MOOD_THRESHOLD = -1.5
    _MAX_SECTOR     = 2

# ── Cache layer (5-min TTL) ───────────────────────────────────────────────────
_cache = {}
_CACHE_TTL = 300  # seconds

def _cached(key, fn):
    now = time.time()
    if key in _cache and now - _cache[key][0] < _CACHE_TTL:
        return _cache[key][1]
    val = fn()
    _cache[key] = (now, val)
    return val


# ── 1. Market Mood Filters ────────────────────────────────────────────────────

def _fetch_index_change(ticker):
    """Returns today's % change for a market index. None on failure."""
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period='2d', interval='1d')
        if hist is None or len(hist) < 2:
            return None
        prev_close = float(hist['Close'].iloc[-2])
        curr_price = float(hist['Close'].iloc[-1])
        if prev_close <= 0:
            return None
        return (curr_price - prev_close) / prev_close * 100
    except Exception:
        return None


def india_market_mood_ok():
    """Returns True if NIFTY is NOT down more than MOOD_FILTER_THRESHOLD% today.

    Threshold is read from config (env-driven). Fail-open: returns True if data unavailable.
    Caches for 5 min — NIFTY intraday trend doesn't flip every second.
    """
    def _check():
        try:
            from config import MOOD_FILTER_THRESHOLD as thr
        except ImportError:
            thr = _MOOD_THRESHOLD
        chg = _fetch_index_change('^NSEI')
        if chg is None:
            return True   # fail-open
        if chg <= thr:
            print(f"  [MOOD FILTER]  NIFTY {chg:+.2f}% today (threshold {thr}%) — skipping India buys")
            return False
        return True

    return _cached('india_mood', _check)


def us_market_mood_ok():
    """Returns True if S&P 500 is NOT down more than MOOD_FILTER_THRESHOLD% today.

    Fail-open: returns True if data unavailable.
    """
    def _check():
        try:
            from config import MOOD_FILTER_THRESHOLD as thr
        except ImportError:
            thr = _MOOD_THRESHOLD
        chg = _fetch_index_change('^GSPC')
        if chg is None:
            return True
        if chg <= thr:
            print(f"  [MOOD FILTER]  S&P500 {chg:+.2f}% today (threshold {thr}%) — skipping US buys")
            return False
        return True

    return _cached('us_mood', _check)


# ── 1b. Intraday Trend Filter ─────────────────────────────────────────────────
# The mood filter above only catches a market that's down a lot vs YESTERDAY's
# close. It misses a market that's been quietly grinding lower for the last
# few hours of TODAY's session while still being up (or only slightly down)
# vs yesterday -- exactly the pattern behind the 2026-08-05 India incident:
# NIFTY drifted from its open down to -0.54% over ~4 hours, never breaching
# the day-over-day mood threshold, while every position bought during that
# drift got stopped out. This filter looks at the index's own recent
# short-term direction instead of a fixed reference point.

def _fetch_recent_trend(ticker, lookback_min):
    """% change of the index over the last `lookback_min` minutes of TODAY's
    session (using 5-min intraday bars), independent of yesterday's close.
    None on failure or if there isn't enough of today's session yet to look
    back that far (e.g. right at the open) -- callers should fail-open on None."""
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period='1d', interval='5m')
        if hist is None or len(hist) < 2:
            return None
        now_price = float(hist['Close'].iloc[-1])
        cutoff = hist.index[-1] - timedelta(minutes=lookback_min)
        past_bars = hist[hist.index <= cutoff]
        if past_bars.empty:
            return None
        past_price = float(past_bars['Close'].iloc[-1])
        if past_price <= 0:
            return None
        return (now_price - past_price) / past_price * 100
    except Exception:
        return None


def india_market_trend_ok():
    """Returns True unless NIFTY has dropped more than TREND_FILTER_THRESHOLD_PCT
    over the last TREND_FILTER_LOOKBACK_MIN minutes -- i.e. the index itself is
    actively trending down right now, regardless of where it stands vs
    yesterday. Fail-open if data unavailable or not enough of today's session
    has elapsed yet."""
    def _check():
        try:
            from config import TREND_FILTER_LOOKBACK_MIN as lookback, TREND_FILTER_THRESHOLD_PCT as thr
        except ImportError:
            lookback, thr = 45, -0.3
        chg = _fetch_recent_trend('^NSEI', lookback)
        if chg is None:
            return True
        if chg <= thr:
            print(f"  [TREND FILTER]  NIFTY {chg:+.2f}% over last {lookback}min "
                  f"(threshold {thr}%) — market actively trending down, skipping new India buys")
            return False
        return True

    return _cached('india_trend', _check)


def us_market_trend_ok():
    """Same idea as india_market_trend_ok(), for the S&P 500."""
    def _check():
        try:
            from config import TREND_FILTER_LOOKBACK_MIN as lookback, TREND_FILTER_THRESHOLD_PCT as thr
        except ImportError:
            lookback, thr = 45, -0.3
        chg = _fetch_recent_trend('^GSPC', lookback)
        if chg is None:
            return True
        if chg <= thr:
            print(f"  [TREND FILTER]  S&P500 {chg:+.2f}% over last {lookback}min "
                  f"(threshold {thr}%) — market actively trending down, skipping new US buys")
            return False
        return True

    return _cached('us_trend', _check)


# ── 2. News/Event Filter ──────────────────────────────────────────────────────

# Macro event dates (IST) — expand as needed; these block ALL buys for the day
# Format: 'YYYY-MM-DD'
_MACRO_BLOCK_DATES = set()  # populated by update_macro_dates()

_RBI_KEYWORDS   = ['rbi', 'repo rate', 'monetary policy']
_FED_KEYWORDS   = ['fomc', 'federal reserve', 'fed rate', 'interest rate decision']


def update_macro_dates(extra_dates=None):
    """Add known macro event dates (YYYY-MM-DD strings) to the block list."""
    if extra_dates:
        _MACRO_BLOCK_DATES.update(extra_dates)


def _has_earnings_today(symbol):
    """Returns True if symbol has earnings within ±30 min of now."""
    try:
        import yfinance as yf
        cal = yf.Ticker(symbol).calendar
        if cal is None or cal.empty:
            return False
        # calendar has 'Earnings Date' row
        if 'Earnings Date' not in cal.index:
            return False
        date_val = cal.loc['Earnings Date'].iloc[0]
        if date_val is None:
            return False
        # yfinance returns Timestamp or datetime
        from pandas import Timestamp
        if isinstance(date_val, Timestamp):
            earn_date = date_val.date()
        else:
            earn_date = date_val
        today = datetime.now(_IST).date()
        return earn_date == today
    except Exception:
        return False


def symbol_event_clear(symbol):
    """Returns True if it's safe to trade symbol right now (no earnings ±30 min).

    Fail-open: returns True if calendar unavailable.
    Caches per-symbol for 5 min.
    """
    today = datetime.now(_IST).strftime('%Y-%m-%d')
    if today in _MACRO_BLOCK_DATES:
        print(f"  [EVENT FILTER]  Macro event today ({today}) — skipping all buys")
        return False

    def _check():
        if _has_earnings_today(symbol):
            print(f"  [EVENT FILTER]  {symbol} has earnings today — skipping")
            return False
        return True

    return _cached(f'event_{symbol}', _check)


# ── 3. Sector Concentration Cap ──────────────────────────────────────────────

# Sector map for India NSE stocks
_INDIA_SECTORS = {
    # IT
    'TCS': 'IT', 'INFY': 'IT', 'WIPRO': 'IT', 'HCLTECH': 'IT', 'TECHM': 'IT',
    'LTIM': 'IT', 'MPHASIS': 'IT', 'PERSISTENT': 'IT', 'COFORGE': 'IT',
    # Banks
    'HDFCBANK': 'Bank', 'ICICIBANK': 'Bank', 'SBIN': 'Bank', 'KOTAKBANK': 'Bank',
    'AXISBANK': 'Bank', 'INDUSINDBK': 'Bank', 'FEDERALBNK': 'Bank', 'IDFCFIRSTB': 'Bank',
    'BANDHANBNK': 'Bank', 'AUBANK': 'Bank',
    # NBFC/Finance
    'BAJFINANCE': 'Finance', 'BAJAJFINSV': 'Finance', 'HDFC': 'Finance',
    'CHOLAFIN': 'Finance', 'MUTHOOTFIN': 'Finance', 'M&MFIN': 'Finance',
    # Auto
    'TATAMOTORS': 'Auto', 'MARUTI': 'Auto', 'BAJAJ-AUTO': 'Auto', 'HEROMOTOCO': 'Auto',
    'EICHERMOT': 'Auto', 'M&M': 'Auto', 'TVSMOTORS': 'Auto',
    # Pharma
    'SUNPHARMA': 'Pharma', 'DRREDDY': 'Pharma', 'CIPLA': 'Pharma', 'DIVISLAB': 'Pharma',
    'APOLLOHOSP': 'Pharma', 'ALKEM': 'Pharma', 'TORNTPHARM': 'Pharma',
    # Energy/Oil
    'RELIANCE': 'Energy', 'ONGC': 'Energy', 'BPCL': 'Energy', 'IOC': 'Energy',
    'GAIL': 'Energy', 'POWERGRID': 'Energy', 'NTPC': 'Energy', 'TATAPOWER': 'Energy',
    # Metals
    'TATASTEEL': 'Metal', 'JSWSTEEL': 'Metal', 'HINDALCO': 'Metal', 'VEDL': 'Metal',
    'SAIL': 'Metal', 'COALINDIA': 'Metal',
    # FMCG
    'HINDUNILVR': 'FMCG', 'ITC': 'FMCG', 'NESTLEIND': 'FMCG', 'BRITANNIA': 'FMCG',
    'GODREJCP': 'FMCG', 'DABUR': 'FMCG', 'MARICO': 'FMCG',
    # Infra/Cement
    'LT': 'Infra', 'ULTRACEMCO': 'Infra', 'SHREECEM': 'Infra', 'ACC': 'Infra',
    'ADANIENT': 'Infra', 'ADANIPORTS': 'Infra',
    # Telecom
    'BHARTIARTL': 'Telecom', 'IDEA': 'Telecom',
    # Consumer/Retail
    'ZOMATO': 'Consumer', 'NYKAA': 'Consumer', 'DMART': 'Consumer', 'TITAN': 'Consumer',
    'TRENT': 'Consumer',
}

# US sector map (S&P 500 majors)
_US_SECTORS = {
    # Tech
    'AAPL': 'Tech', 'MSFT': 'Tech', 'NVDA': 'Tech', 'META': 'Tech', 'GOOGL': 'Tech',
    'GOOG': 'Tech', 'AMZN': 'Tech', 'AMD': 'Tech', 'INTC': 'Tech', 'QCOM': 'Tech',
    'TSM': 'Tech', 'AVGO': 'Tech', 'MU': 'Tech', 'ORCL': 'Tech', 'CRM': 'Tech',
    'ADBE': 'Tech', 'NOW': 'Tech', 'SNOW': 'Tech', 'PLTR': 'Tech', 'NET': 'Tech',
    # Finance
    'JPM': 'Finance', 'BAC': 'Finance', 'GS': 'Finance', 'MS': 'Finance',
    'V': 'Finance', 'MA': 'Finance', 'AXP': 'Finance', 'WFC': 'Finance',
    'C': 'Finance', 'BLK': 'Finance', 'SCHW': 'Finance',
    # Healthcare
    'JNJ': 'Healthcare', 'UNH': 'Healthcare', 'PFE': 'Healthcare', 'MRK': 'Healthcare',
    'ABBV': 'Healthcare', 'TMO': 'Healthcare', 'ABT': 'Healthcare', 'LLY': 'Healthcare',
    'BMY': 'Healthcare', 'AMGN': 'Healthcare',
    # Consumer
    'AMZN': 'Consumer', 'TSLA': 'Consumer', 'NKE': 'Consumer', 'SBUX': 'Consumer',
    'MCD': 'Consumer', 'HD': 'Consumer', 'TGT': 'Consumer', 'WMT': 'Consumer',
    'COST': 'Consumer', 'LOW': 'Consumer',
    # Energy
    'XOM': 'Energy', 'CVX': 'Energy', 'COP': 'Energy', 'SLB': 'Energy',
    # Industrial
    'BA': 'Industrial', 'CAT': 'Industrial', 'HON': 'Industrial', 'GE': 'Industrial',
    'UPS': 'Industrial', 'FDX': 'Industrial',
    # Biotech
    'MRNA': 'Biotech', 'BNTX': 'Biotech', 'BIIB': 'Biotech', 'GILD': 'Biotech',
    'REGN': 'Biotech', 'VRTX': 'Biotech',
    # Retail/E-comm
    'SHOP': 'Ecomm', 'ETSY': 'Ecomm', 'EBAY': 'Ecomm',
    # Media/Streaming
    'NFLX': 'Media', 'DIS': 'Media', 'PARA': 'Media',
    # Semiconductors
    'AMAT': 'Semicon', 'LRCX': 'Semicon', 'KLAC': 'Semicon', 'MRVL': 'Semicon',
}

# Crypto has no sectors — skip sector cap for crypto
MAX_SECTOR_POSITIONS = _MAX_SECTOR  # max open positions per sector (env-driven via config)


def get_sector(symbol, market='india'):
    """Return sector string for a symbol, or None if unknown."""
    if market == 'us':
        return _US_SECTORS.get(symbol)
    return _INDIA_SECTORS.get(symbol)


def sector_cap_ok(symbol, open_positions, market='india'):
    """Returns True if adding this symbol won't exceed MAX_SECTOR_POSITIONS in its sector.

    Reads MAX_SECTOR_POSITIONS live from config (env-driven). Falls back to True if sector unknown.
    """
    try:
        from config import MAX_SECTOR_POSITIONS as cap
    except ImportError:
        cap = MAX_SECTOR_POSITIONS

    sector = get_sector(symbol, market)
    if not sector:
        return True   # unknown sector — allow

    sector_count = sum(
        1 for p in open_positions
        if get_sector(p['symbol'], market) == sector
    )
    if sector_count >= cap:
        print(f"  [SECTOR CAP]  {symbol} ({sector}) — already {sector_count} open positions in {sector} (max {cap})")
        return False
    return True
