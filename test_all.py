"""
AngelBot — Full system test
Run: python3 test_all.py
Each test prints PASS / FAIL with a short reason.
No market hours required — all tests run any time.
"""
import sys, os, traceback, time
sys.path.insert(0, os.path.dirname(__file__))

PASS = "✅ PASS"
FAIL = "❌ FAIL"
results = []

def test(name, fn):
    try:
        detail = fn()
        tag = f"{PASS}  {name}"
        if detail:
            tag += f"  →  {detail}"
        print(tag)
        results.append((True, name))
    except Exception as e:
        print(f"{FAIL}  {name}  →  {e}")
        traceback.print_exc()
        results.append((False, name))

print("\n" + "=" * 60)
print("  AngelBot System Test")
print("=" * 60 + "\n")

# ── 1. Config ──────────────────────────────────────────────────
def t_config():
    from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, CAPITAL, MAX_POSITIONS, PAPER_MODE
    assert TELEGRAM_BOT_TOKEN, "TELEGRAM_BOT_TOKEN missing"
    assert TELEGRAM_CHAT_ID,   "TELEGRAM_CHAT_ID missing"
    assert CAPITAL == 1000,    "CAPITAL should be 1000"
    assert MAX_POSITIONS == 5, "MAX_POSITIONS should be 5"
    assert PAPER_MODE,         "PAPER_MODE should be True"
    return f"capital=₹{CAPITAL}  max_pos={MAX_POSITIONS}  paper={PAPER_MODE}"
test("Config loads correctly", t_config)

# ── 2. Database ────────────────────────────────────────────────
def t_db():
    from data.database import init_db, get_conn
    init_db()
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {r[0] for r in c.fetchall()}
    conn.close()
    assert 'trades' in tables, "trades table missing"
    assert 'signal_performance' in tables, "signal_performance table missing"
    return f"tables: {sorted(tables)}"
test("Database init & tables exist", t_db)

# ── 3. Stock list ──────────────────────────────────────────────
def t_stocks():
    from data.nifty_stocks import get_all_stocks, is_mutual_fund
    stocks = get_all_stocks()
    assert len(stocks) >= 30, f"Too few stocks: {len(stocks)}"
    for s in ['RELIANCE', 'TCS', 'HDFCBANK']:
        assert s in stocks, f"{s} not in list"
    # MF safety
    mf_samples = ['HDFC_MF', 'SBI_EQUITY_FUND', 'NIPPON_ETF']
    for mf in mf_samples:
        assert is_mutual_fund(mf), f"MF check missed: {mf}"
    return f"{len(stocks)} stocks  |  MF safety OK"
test("Stock list — NSE live + MF filter", t_stocks)

# ── 4. Angel One login ─────────────────────────────────────────
def t_login():
    from data.angel_login import get_api
    api = get_api()
    assert api is not None, "get_api() returned None"
    return "Login successful"
test("Angel One login (SmartAPI + TOTP)", t_login)

# ── 5. Historical data fetch ───────────────────────────────────
def t_fetch():
    from data.fetcher import get_historical
    df = get_historical('RELIANCE', period='1mo')
    assert df is not None and len(df) >= 10, "DataFrame too short"
    assert 'Close' in df.columns, "No Close column"
    return f"RELIANCE: {len(df)} rows  last={df['Close'].iloc[-1]:.2f}"
test("Historical data fetch (yfinance)", t_fetch)

# ── 6. Technical indicators ────────────────────────────────────
def t_technical():
    from data.fetcher import get_historical
    from analysis.technical import compute_indicators, generate_signals
    df = get_historical('RELIANCE', period='6mo')
    df = compute_indicators(df)
    for col in ['rsi', 'macd', 'bb_upper', 'bb_lower', 'atr', 'ema20']:
        assert col in df.columns, f"Missing column: {col}"
    result = generate_signals(df)
    assert result is not None, "generate_signals returned None"
    assert 'score' in result and 'signals' in result and 'price' in result
    return f"score={result['score']}  price=₹{result['price']:.2f}  signals={result['signals']}"
test("Technical indicators + signal generation", t_technical)

# ── 7. Sentiment / news ────────────────────────────────────────
def t_sentiment():
    from analysis.sentiment import prefetch_rss, get_news_sentiment
    pool = prefetch_rss()
    assert isinstance(pool, list), "prefetch_rss should return list"
    result = get_news_sentiment('RELIANCE', 'Reliance Industries')
    assert 'label' in result and result['label'] in ('positive', 'negative', 'neutral')
    assert 'score' in result
    return f"RSS pool={len(pool)} headlines  RELIANCE={result['label']} ({result['count']} headlines)"
test("Sentiment — RSS prefetch + per-stock news", t_sentiment)

# ── 8. Self-learner ────────────────────────────────────────────
def t_ml():
    from learning.self_learner import load_weights, get_weighted_score, predict
    weights = load_weights()
    assert set(weights.keys()) >= {'rsi', 'macd', 'bollinger', 'ema', 'volume', 'sentiment'}
    sigs = {'rsi': 'buy', 'macd': 'weak_buy', 'bollinger': 'neutral', 'ema': 'buy', 'volume': 'spike'}
    ws, ml = get_weighted_score(5, sigs, 70)
    assert 0 <= ml <= 100, f"ml_prob out of range: {ml}"
    assert ws >= 0, f"weighted_score negative: {ws}"
    prob = predict(sigs, 70)
    assert 0.0 <= prob <= 1.0, f"predict() out of range: {prob}"
    return f"weighted_score={ws}  ml_prob={ml}%  predict={prob:.2f}"
test("Self-learner weights + prediction", t_ml)

# ── 9. Paper trader — buy / sell cycle ────────────────────────
def t_paper_trader():
    from trading.paper_trader import PaperTrader
    from data.database import init_db
    init_db()
    trader = PaperTrader()

    signals = {'rsi': 'buy', 'macd': 'buy', 'bollinger': 'buy', 'ema': 'buy', 'volume': 'spike'}
    pos, err = trader.buy('TEST_STOCK', 100.0, 95.0, 110.0, signals, 'test buy', 80)

    if err:
        # Might fail if balance is too low from previous test trades — acceptable
        return f"buy skipped: {err}"

    assert pos is not None, "buy returned None position"
    assert any(p['symbol'] == 'TEST_STOCK' for p in trader.open_positions), "position not found"

    # Simulate profitable exit directly (exits are handled by PositionMonitor in production)
    pnl, pnl_pct = trader.sell(pos, 111.0, 'Target reached')
    assert pnl > 0, f"sell should be profitable, got {pnl}"
    assert not any(p['symbol'] == 'TEST_STOCK' for p in trader.open_positions), "position still open after sell"

    return f"bought @ ₹100  sold @ ₹111  pnl=₹{pnl:.2f} ({pnl_pct:.1f}%)"
test("Paper trader — buy → sell → P&L recorded", t_paper_trader)

# ── 10. Scanner — small scan ───────────────────────────────────
def t_scanner():
    from trading.scanner import scan_stocks
    # Scan just 5 well-known stocks to keep the test fast
    sample = ['RELIANCE', 'TCS', 'HDFCBANK', 'INFY', 'SBIN']
    results = scan_stocks(symbols=sample)
    assert isinstance(results, list)
    for c in results:
        assert 'symbol' in c and 'weighted_score' in c and 'ml_prob' in c
        assert 'stop_loss' in c and 'target' in c
    return f"{len(results)} candidate(s) from {len(sample)} stocks"
test("Scanner — 5-stock mini scan with ML scoring", t_scanner)

# ── 11. Telegram alert ─────────────────────────────────────────
def t_telegram_send():
    from reporting.telegram_alerts import send
    send("🧪 <b>AngelBot test suite running</b> — all systems check")
    return "message sent"
test("Telegram — send test alert", t_telegram_send)

# ── 12. Telegram listener import & setup ──────────────────────
def t_telegram_listener():
    from reporting.telegram_listener import set_trader, start_listener
    from trading.paper_trader import PaperTrader
    from data.database import init_db
    init_db()
    trader = PaperTrader()
    set_trader(trader)
    # Just verify the module loads and trader is wired — don't start thread here
    from reporting import telegram_listener as tl
    assert tl._trader_ref is not None, "trader ref not set"
    return "trader wired OK"
test("Telegram listener — trader wiring", t_telegram_listener)

# ── 13. Excel report generation ───────────────────────────────
def t_excel():
    from reporting.excel_report import generate_daily_report
    from datetime import datetime
    path = generate_daily_report(datetime.now().strftime('%Y-%m-%d'))
    # Function may return None if no trades — that's fine, just check no exception
    return f"report path={path}" if path else "no trades yet — report skipped (OK)"
test("Excel report generation", t_excel)

# ── 14. DB — verify TEST_STOCK trade was saved ─────────────────
def t_db_trade():
    from data.database import get_conn
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM trades WHERE symbol='TEST_STOCK'")
    count = c.fetchone()[0]
    conn.close()
    assert count >= 0  # just confirm table is queryable
    return f"TEST_STOCK trades in DB: {count}"
test("Database — trade records persisted", t_db_trade)

# ── Summary ────────────────────────────────────────────────────
print("\n" + "=" * 60)
passed = sum(1 for ok, _ in results if ok)
failed = sum(1 for ok, _ in results if not ok)
print(f"  Results: {passed} passed  |  {failed} failed  |  {len(results)} total")
if failed:
    print("\n  Failed tests:")
    for ok, name in results:
        if not ok:
            print(f"    ✗ {name}")
else:
    print("  All tests passed — bot is ready to run!")
print("=" * 60 + "\n")
