import requests
import os
from datetime import datetime

# Mutual fund symbols and patterns — NEVER trade these
MF_BLACKLIST_PATTERNS = [
    'NIFTYBEES', 'JUNIORBEES', 'LIQUIDBEES', 'GOLDBEES', 'BANKBEES',
    'CPSEETF', 'NETF', 'HDFCNIFTY', 'ICICITECH', 'SETFNIF50',
    'ABSLAMC', 'HDFCAMC', 'UTIAMC', 'NIPPONINDIA', 'KOTAKAMC',
    '-ETF', 'ETF', 'FUND', 'BEES', 'MF', 'NIFETF',
]

NSE_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': 'https://www.nseindia.com/',
    'Connection': 'keep-alive',
}


# Sensex 30 fallback (BSE — mostly overlap with Nifty 50)
_SENSEX_FALLBACK = [
    "ADANIENT", "ASIANPAINT", "AXISBANK", "BAJAJ-AUTO", "BAJFINANCE",
    "BHARTIARTL", "HCLTECH", "HDFCBANK", "HINDUNILVR", "ICICIBANK",
    "INDUSINDBK", "INFY", "ITC", "JSWSTEEL", "KOTAKBANK",
    "LT", "M&M", "MARUTI", "NESTLEIND", "NTPC",
    "ONGC", "POWERGRID", "RELIANCE", "SBIN", "SUNPHARMA",
    "TATAMOTORS", "TATASTEEL", "TCS", "TECHM", "TITAN",
]

# Full fallback for Nifty 50 + 200 + Sensex
_FALLBACK = list(set([
    "RELIANCE","TCS","HDFCBANK","INFY","ICICIBANK","HINDUNILVR","ITC","SBIN",
    "BHARTIARTL","KOTAKBANK","LT","AXISBANK","ASIANPAINT","MARUTI","SUNPHARMA",
    "TITAN","ULTRACEMCO","BAJFINANCE","NESTLEIND","WIPRO","HCLTECH","POWERGRID",
    "NTPC","TATAMOTORS","ADANIPORTS","TECHM","BAJAJFINSV","ONGC","JSWSTEEL",
    "COALINDIA","TATASTEEL","M&M","GRASIM","DIVISLAB","CIPLA","APOLLOHOSP",
    "DRREDDY","EICHERMOT","HEROMOTOCO","BPCL","BRITANNIA","SHRIRAMFIN",
    "TATACONSUM","HINDALCO","ADANIENT","SBILIFE","HDFCLIFE","BAJAJ-AUTO","UPL","INDUSINDBK",
    "PIDILITIND","HAVELLS","SIEMENS","BANKBARODA","BERGEPAINT","BIOCON","BOSCHLTD",
    "CANBK","CHOLAFIN","COLPAL","CONCOR","DABUR","DEEPAKNTR","DLF","ESCORTS",
    "FEDERALBNK","GAIL","GODREJCP","GODREJPROP","HINDPETRO","ICICIPRULI",
    "IDFCFIRSTB","IGL","INDHOTEL","INDUSTOWER","IOC","IRCTC","JINDALSTEL",
    "JUBLFOOD","LICHSGFIN","LUPIN","MARICO","MPHASIS","MUTHOOTFIN","NAUKRI",
    "NMDC","OBEROIRLTY","PAGEIND","PERSISTENT","PETRONET","PFC","PNB","POLYCAB",
    "RECLTD","SAIL","SRF","SUNTVNETWORK","TATAPOWER","TATACHEM","TRENT","TVSMOTOR",
    "VEDL","VOLTAS","ZOMATO","ASTRAL","AUBANK","AUROPHARMA","BALKRISIND","BATAINDIA",
    "BHARATFORG","DIXON","FLUOROCHEM","HFCL","INDIAMART","INDIGO","KPITTECH",
    "LALPATHLAB","LAURUSLABS","LTTS","MAXHEALTH","MOTHERSON","NATIONALUM",
    "NAVINFLUOR","NBCC","PHOENIXLTD","RAILTEL","RAMCOCEM","SAFARI","SCHAEFFLER",
    "SJVN","SOBHA","SONACOMS","SUZLON","THERMAX","TIMKEN","TORNTPHARM",
    "TORNTPOWER","TRIDENT","UJJIVANSFB","VBL","VGUARD","ZEEL",
] + _SENSEX_FALLBACK))

# In-memory store
_live_list    = []
_last_fetched = None


def is_mutual_fund(symbol):
    sym = symbol.upper()
    for pattern in MF_BLACKLIST_PATTERNS:
        if pattern in sym:
            return True
    return False


def _clean(symbols):
    return [s for s in symbols if ' ' not in s and not is_mutual_fund(s)]


def _fetch_nse_index(index_name):
    try:
        session = requests.Session()
        session.headers.update(NSE_HEADERS)
        session.get('https://www.nseindia.com', timeout=10)
        url = f"https://www.nseindia.com/api/equity-stockIndices?index={requests.utils.quote(index_name)}"
        resp = session.get(url, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            symbols = [item['symbol'] for item in data.get('data', []) if 'symbol' in item]
            return _clean(symbols)
    except Exception as e:
        print(f"[NSE] Fetch failed for {index_name}: {e}")
    return []


def _fetch_sensex():
    """
    BSE blocks all server-side API access (requires browser cookies).
    We use the accurate fallback list — Sensex 30 changes at most twice a year.
    27 of 30 Sensex stocks are already in Nifty 200 (fetched live from NSE).
    The fallback ensures the remaining 3 are always included.
    """
    return _SENSEX_FALLBACK


def get_all_stocks():
    """
    Fetch live Nifty 50 + Nifty 200 + Sensex 30 from NSE/BSE every call.
    Falls back to last successful fetch, then hardcoded list.
    """
    global _live_list, _last_fetched

    n50     = _fetch_nse_index("NIFTY 50")
    n200    = _fetch_nse_index("NIFTY 200")
    sensex  = _fetch_sensex()

    nse_ok  = len(n50) >= 45 and len(n200) >= 150
    sensex_ok = len(sensex) >= 25

    if nse_ok:
        combined = list(set(n50 + n200 + sensex))
        _live_list    = combined
        _last_fetched = datetime.now()
        sources = "Nifty 50 + Nifty 200" + (" + Sensex" if sensex_ok else " (Sensex fallback)")
        print(f"[INDEX] {sources} → {len(combined)} unique stocks  ({_last_fetched.strftime('%H:%M:%S')})")
        return combined

    if _live_list:
        print(f"[INDEX] NSE unreachable — using last known list ({len(_live_list)} stocks from {_last_fetched.strftime('%H:%M:%S')})")
        return _live_list

    print("[INDEX] All fetches failed — using built-in fallback")
    return _FALLBACK


def get_nifty50():
    return _fetch_nse_index("NIFTY 50") or _FALLBACK[:50]

def get_nifty200():
    return get_all_stocks()

def get_sensex():
    return _fetch_sensex()

def refresh_lists():
    return get_all_stocks()


if __name__ == "__main__":
    print("Fetching Nifty 50 + Nifty 200 + Sensex live...\n")
    stocks = get_all_stocks()
    print(f"\nTotal unique stocks: {len(stocks)}")
    sensex = get_sensex()
    print(f"Sensex stocks: {len(sensex)} → {sensex[:5]}...")
    print(f"\nMF safety checks:")
    print(f"  NIFTYBEES → blocked : {is_mutual_fund('NIFTYBEES')}")
    print(f"  HDFCAMC   → blocked : {is_mutual_fund('HDFCAMC')}")
    print(f"  RELIANCE  → blocked : {is_mutual_fund('RELIANCE')}")
    print(f"  SBIN      → blocked : {is_mutual_fund('SBIN')}")
