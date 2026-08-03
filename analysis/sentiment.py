import feedparser
import requests
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError
from datetime import datetime
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

_FEED_USER_AGENT = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                                  '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'}
# A generic identifying UA (e.g. naming this bot) gets a 403 from some of
# these news sites' bot-detection (moneycontrol confirmed) -- a standard
# browser UA string is what feedparser's own default fetcher effectively
# looked like to these servers, so match that instead of standing out.


def _parse_feed_safe(url, timeout=8):
    """feedparser.parse(url) has NO timeout of its own — if a remote server
    accepts the connection but never responds, it blocks forever (this is
    what froze the India worker for ~12 hours on 2026-08-02/03: one stuck
    RSS feed hung the whole main thread with no exception, no crash).
    Fetch with an explicit requests timeout first, then hand feedparser the
    already-downloaded bytes -- it never touches the network itself this way."""
    try:
        resp = requests.get(url, timeout=timeout, headers=_FEED_USER_AGENT)
        return feedparser.parse(resp.content)
    except Exception:
        return feedparser.parse(b'')  # empty feed -- feedparser never raises on this

_analyzer = SentimentIntensityAnalyzer()

# All RSS sources — fetched once per scan, shared across all stocks
RSS_FEEDS = [
    "https://economictimes.indiatimes.com/markets/stocks/rss.cms",
    "https://economictimes.indiatimes.com/markets/rss.cms",
    "https://economictimes.indiatimes.com/industry/rss.cms",
    "https://www.moneycontrol.com/rss/marketreports.xml",
    "https://www.moneycontrol.com/rss/business.xml",
    "https://www.moneycontrol.com/rss/latestnews.xml",
    "https://www.business-standard.com/rss/markets-106.rss",
    "https://www.business-standard.com/rss/economy-policy-102.rss",
    "https://www.livemint.com/rss/markets",
    "https://www.livemint.com/rss/companies",
    "https://www.financialexpress.com/market/feed/",
    "https://www.ndtvprofit.com/feed",
    "https://www.zeebiz.com/rss/markets.xml",
    "https://feeds.reuters.com/reuters/INbusinessNews",
]

# In-memory pool — refreshed every scan
_rss_pool      = []       # all headlines from RSS feeds
_pool_fetched  = None     # timestamp of last RSS fetch
_google_cache  = {}       # symbol → headlines; cleared each scan to avoid stale data


def prefetch_rss():
    """
    Fetch all RSS feeds in parallel — call once at the start of each scan.
    Takes ~3-5 seconds for all 14 feeds combined.
    All stocks then filter from this shared pool instantly.
    """
    global _rss_pool, _pool_fetched, _google_cache
    _google_cache = {}  # reset per-scan Google cache

    all_headlines = []

    def fetch_one(url):
        try:
            feed = _parse_feed_safe(url, timeout=8)
            return [e.get('title', '').strip() for e in feed.entries[:60] if e.get('title')]
        except Exception:
            return []

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(fetch_one, url): url for url in RSS_FEEDS}
        try:
            # Belt-and-suspenders on top of the per-request timeout above --
            # bounds the whole batch even if something unexpected stalls.
            for future in as_completed(futures, timeout=25):
                all_headlines.extend(future.result())
        except FuturesTimeoutError:
            print("[NEWS] RSS prefetch timed out waiting on some feeds -- continuing with what completed")

    _rss_pool     = _dedupe(all_headlines)
    _pool_fetched = datetime.now()
    print(f"[NEWS] RSS pool refreshed: {len(_rss_pool)} headlines from {len(RSS_FEEDS)} feeds  ({_pool_fetched.strftime('%H:%M:%S')})")
    return _rss_pool


def get_news_sentiment(symbol, company_name=None):
    """
    Get sentiment for a stock using:
    1. Pre-fetched RSS pool (instant — shared across all stocks)
    2. Live Google News query (fresh, per stock, ~1-2 sec)
    """
    search_terms = [symbol.upper()]
    if company_name:
        search_terms.append(company_name.lower())
        first = company_name.split()[0].lower()
        if len(first) > 3:
            search_terms.append(first)

    # Filter from the shared RSS pool
    rss_hits = []
    for title in _rss_pool:
        for term in search_terms:
            if term.lower() in title.lower():
                rss_hits.append(title)
                break

    # Live Google News — cached per symbol per scan to avoid hammering the API
    if symbol in _google_cache:
        google_hits = _google_cache[symbol]
    else:
        google_hits = _fetch_google_news(symbol, company_name)
        _google_cache[symbol] = google_hits

    headlines = _dedupe(rss_hits + google_hits)

    if not headlines:
        return {'score': 0, 'label': 'neutral', 'headlines': [], 'count': 0}

    scores = [_analyzer.polarity_scores(h)['compound'] for h in headlines]
    avg    = sum(scores) / len(scores)

    if avg >= 0.15:
        label = 'positive'
    elif avg <= -0.15:
        label = 'negative'
    else:
        label = 'neutral'

    return {
        'score':     round(avg, 3),
        'label':     label,
        'headlines': headlines[:8],
        'count':     len(headlines),
        'rss_hits':  len(rss_hits),
        'google_hits': len(google_hits),
    }


def _fetch_google_news(symbol, company_name=None):
    """Live Google News queries — called per stock."""
    headlines  = []
    company_short = company_name.split()[0] if company_name else symbol

    queries = [
        f"{symbol} NSE stock India",
        f"{company_short} share price NSE",
        f"{company_short} earnings results India",
    ]
    for query in queries:
        try:
            url  = f"https://news.google.com/rss/search?q={requests.utils.quote(query)}&hl=en-IN&gl=IN&ceid=IN:en"
            feed = _parse_feed_safe(url, timeout=6)
            for entry in feed.entries[:25]:
                title = entry.get('title', '').strip()
                if title:
                    headlines.append(title)
        except Exception:
            continue

    return headlines


def _dedupe(headlines):
    seen, unique = set(), []
    for h in headlines:
        key = h.lower()[:60]
        if key not in seen:
            seen.add(key)
            unique.append(h)
    return unique


if __name__ == "__main__":
    import time

    print("Step 1: Prefetch all RSS feeds (runs once per scan)...")
    t = time.time()
    prefetch_rss()
    print(f"RSS fetch done in {round(time.time()-t, 1)}s\n")

    print("Step 2: Get sentiment per stock (uses shared pool + live Google)...")
    for sym, name in [("RELIANCE", "Reliance Industries"), ("SBIN", "State Bank of India"), ("INFY", "Infosys")]:
        t = time.time()
        r = get_news_sentiment(sym, name)
        elapsed = round(time.time()-t, 1)
        print(f"{sym}: {r['label'].upper()}  score={r['score']}  total={r['count']}  (RSS:{r['rss_hits']} + Google:{r['google_hits']})  {elapsed}s")
        for h in r['headlines'][:2]:
            print(f"  - {h[:88]}")
