import sys, os
from concurrent.futures import ThreadPoolExecutor, as_completed
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from data.fetcher import get_intraday, _fallback_price
from data.nifty_stocks import get_all_stocks, is_mutual_fund
from analysis.technical import compute_indicators_intraday, generate_signals_intraday, calc_stop_loss, calc_target
from analysis.sentiment import get_news_sentiment, prefetch_rss
from learning.self_learner import get_weighted_score

from config import MIN_SIGNAL_SCORE as MIN_SCORE
SCAN_WORKERS    = 20     # parallel threads for fetching data

COMPANY_NAMES = {
    "RELIANCE": "Reliance Industries",
    "TCS": "Tata Consultancy",
    "HDFCBANK": "HDFC Bank",
    "INFY": "Infosys",
    "ICICIBANK": "ICICI Bank",
    "SBIN": "State Bank of India",
    "BHARTIARTL": "Airtel",
    "WIPRO": "Wipro",
    "TATAMOTORS": "Tata Motors",
    "ADANIENT": "Adani Enterprises",
    "ZOMATO": "Zomato",
    "KOTAKBANK": "Kotak Bank",
    "LT": "Larsen Toubro",
    "AXISBANK": "Axis Bank",
    "MARUTI": "Maruti Suzuki",
    "SUNPHARMA": "Sun Pharma",
    "NTPC": "NTPC",
    "POWERGRID": "Power Grid",
    "HCLTECH": "HCL Technologies",
    "TECHM": "Tech Mahindra",
}

def scan_stocks(symbols=None, extra_symbols=None, max_price=None):
    """
    Scan for buy candidates.
    symbols: list to scan for new buys (current Nifty list)
    extra_symbols: open-position stocks to monitor regardless of index membership
    """
    if symbols is None:
        symbols = get_all_stocks()

    # Always monitor open positions even if they left the index
    watch_list = list(symbols)
    if extra_symbols:
        for s in extra_symbols:
            if s not in watch_list:
                watch_list.append(s)
                print(f"  [MONITOR] {s} kept in watchlist — open position exists")

    # Safety: filter out mutual funds and index tokens
    watch_list = [s for s in watch_list if not is_mutual_fund(s) and ' ' not in s]

    # Refresh all RSS feeds once before scanning — shared across all stocks
    prefetch_rss()

    candidates = []
    print(f"Scanning {len(watch_list)} stocks (parallel, {SCAN_WORKERS} workers)...")

    def _scan_one(sym):
        try:
            df   = get_intraday(sym)
            df   = compute_indicators_intraday(df)
            tech = generate_signals_intraday(df)
            if tech is None or tech['score'] < MIN_SCORE - 1:
                return None
            if max_price is not None and tech['price'] > max_price:
                return None
            # Volume gate — real breakouts have above-average volume
            company    = COMPANY_NAMES.get(sym, sym)
            sent       = get_news_sentiment(sym, company)
            sent_boost = 1 if sent['label'] == 'positive' else (-1 if sent['label'] == 'negative' else 0)
            total_score = tech['score'] + sent_boost
            if total_score < MIN_SCORE:
                return None
            price      = tech['price']
            sl         = calc_stop_loss(price)
            target     = calc_target(price)
            confidence = min(95, max(30, total_score * 12 + 20))
            reason     = " + ".join(tech['reasons'])
            if sent['label'] == 'positive':
                reason += " + positive news"
            weighted_score, ml_prob = get_weighted_score(total_score, tech['signals'], confidence)
            in_index = sym in symbols
            flag = "" if in_index else " [open pos]"
            print(f"  CANDIDATE: {sym}{flag}  score={total_score}  ml={ml_prob:.0f}%  ₹{price:.2f}")
            return {
                'symbol': sym, 'score': total_score,
                'weighted_score': weighted_score, 'ml_prob': ml_prob,
                'confidence': confidence, 'price': price,
                'stop_loss': sl, 'target': target, 'atr': tech['atr'],
                'signals': tech['signals'], 'reason': reason,
                'sentiment': sent['label'], 'in_index': in_index,
            }
        except Exception as e:
            print(f"  SKIP {sym}: {e}")
            return None

    with ThreadPoolExecutor(max_workers=SCAN_WORKERS) as ex:
        futures = {ex.submit(_scan_one, sym): sym for sym in watch_list}
        for fut in as_completed(futures):
            result = fut.result()
            if result:
                candidates.append(result)

    candidates.sort(key=lambda x: (x['ml_prob'], x['weighted_score']), reverse=True)
    return candidates




def get_prices_for_positions(open_positions):
    """Fetch current prices for all open positions — regardless of index."""
    prices = {}
    for pos in open_positions:
        sym = pos['symbol']
        if is_mutual_fund(sym):
            print(f"SAFETY: Refusing to fetch/trade MF symbol {sym}")
            continue
        price = _fallback_price(sym)
        if price:
            prices[sym] = price
    return prices


if __name__ == "__main__":
    from data.nifty_stocks import get_nifty50
    stocks = get_nifty50()
    results = scan_stocks(stocks[:10])
    print(f"\nTop candidates: {len(results)}")
    for c in results:
        print(f"  {c['symbol']}: score={c['score']} conf={c['confidence']:.0f}% ₹{c['price']:.2f}")
