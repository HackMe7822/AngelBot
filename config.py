import os
from dotenv import load_dotenv

load_dotenv()

ANGEL_API_KEY     = os.getenv("ANGEL_API_KEY")
ANGEL_SECRET      = os.getenv("ANGEL_SECRET")
ANGEL_CLIENT_ID   = os.getenv("ANGEL_CLIENT_ID")
ANGEL_PIN         = os.getenv("ANGEL_PIN")
ANGEL_TOTP_SECRET = os.getenv("ANGEL_TOTP_SECRET")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

PAPER_MODE = os.getenv("PAPER_MODE", "true").lower() == "true"

CAPITAL          = float(os.getenv("INDIA_CAPITAL",  "10000"))   # set in .env when going live
MAX_POSITIONS    = 500     # effectively unlimited — capital is the natural cap
MAX_POSITION_PCT = 0.10   # 10% per stock → 10 concurrent positions on ₹10k, scales as capital grows

RELOAD_THRESHOLD = CAPITAL * 0.30   # auto-reload when balance drops below 30% of starting capital
RELOAD_AMOUNT    = CAPITAL          # reload by same amount as starting capital

# Scalping parameters — 2.5:1 R:R gives profit at 29%+ win rate (accounts for ~0.15% NSE round-trip cost)
SCALP_TARGET_PCT = 0.015   # exit on 1.5% profit
SCALP_SL_PCT     = 0.006   # exit on 0.6% loss
SL_CONFIRM_POLLS = 2       # consecutive 15s polls below SL before exit (2 × 15s = 30s)

# Risk controls
MAX_DAILY_LOSS_PCT  = 0.02   # stop all buys if day loss exceeds 2% of capital
MAX_DAILY_TRADES    = 30     # circuit breaker — stop new buys after N closed trades per day
MAX_DEPLOYED_PCT    = 0.70   # never deploy more than 70% of balance at once
PEAK_DRAWDOWN_PCT   = 0.03   # pause new buys if account drops 3% from today's session high
SLIPPAGE_PCT        = 0.0005 # 0.05% simulated slippage on paper entries (realistic fill cost)
ENTRY_START_MIN     = (9, 30)  # no buys before 9:30 AM IST
MAX_HOLD_MINUTES    = 90       # exit stagnating positions after 90 min to free capital
ENTRY_END_MIN       = (15, 0)  # no buys after 3:00 PM (approaching force-close)
MIN_STOCK_PRICE     = 150.0    # skip stocks under ₹150 — SL gap too small vs bid-ask spread

# ── Alpaca / US market config ─────────────────────────────────────────────────
ALPACA_KEY    = os.getenv("ALPACA_KEY",    "")
ALPACA_SECRET = os.getenv("ALPACA_SECRET", "")
ALPACA_PAPER  = os.getenv("ALPACA_PAPER",  "true").lower() == "true"

US_CAPITAL          = float(os.getenv("US_CAPITAL", "10000"))   # set in .env when going live
US_MAX_POSITIONS    = 500       # effectively unlimited — capital is the natural cap
US_MAX_POSITION_PCT = 0.10      # 10% per stock — same as India
US_MAX_DAILY_LOSS_PCT = 0.02    # 2% daily loss limit in USD
US_MAX_DAILY_TRADES   = 30     # circuit breaker for US session
US_MIN_STOCK_PRICE  = 10.0      # skip stocks under $10 — penny stocks are erratic
US_SCALP_TARGET_PCT = 0.015     # exit on 1.5% profit (same as India default)
US_SCALP_SL_PCT     = 0.006     # exit on 0.6% loss (same as India default)
US_MAX_DEPLOYED_PCT = 0.70      # never deploy more than 70% of US balance at once
US_PEAK_DRAWDOWN_PCT = 0.03     # pause new buys if US account drops 3% from session high

# US market entry window (ET → IST: 9:30 AM ET = 7:00 PM IST, 3:30 PM ET = 1:00 AM IST)
US_ENTRY_START_IST = (19, 30)   # 7:30 PM IST = 30 min after US open (skip opening volatility)
US_ENTRY_END_IST   = (1,  0)    # 1:00 AM IST = 30 min before US close
US_FORCE_CLOSE_IST = (1,  25)   # force-exit all at 1:25 AM IST = 3:55 PM ET

# ── Binance / Crypto config (24/7, paper-simulated — no real orders ever) ─────
BINANCE_KEY    = os.getenv("BINANCE_KEY",    "")
BINANCE_SECRET = os.getenv("BINANCE_SECRET", "")
BINANCE_PAPER  = os.getenv("BINANCE_PAPER",  "true").lower() == "true"

CRYPTO_CAPITAL          = float(os.getenv("CRYPTO_CAPITAL", "1000"))   # set in .env when going live
CRYPTO_MAX_POSITIONS    = 500      # capital is the natural cap
CRYPTO_MAX_POSITION_PCT = 0.05     # 5% per trade (adaptive — see crypto_trader.py)
CRYPTO_MAX_DAILY_LOSS_PCT = 0.03   # 3% daily loss limit (crypto is more volatile)
CRYPTO_MAX_DAILY_TRADES   = 20     # circuit breaker for crypto session
CRYPTO_MIN_TRADE_USD    = float(os.getenv("CRYPTO_MIN_TRADE", "5.0"))  # Binance min notional

# Crypto needs wider SL/target — crypto wicks are 3× larger than stock wicks
CRYPTO_TARGET_PCT = 0.030   # 3% profit target (stocks use 1.5%)
CRYPTO_SL_PCT     = 0.015   # 1.5% stop-loss   (stocks use 0.6%)

# Only place NEW buys during US market hours — crypto volume is 3× higher then
CRYPTO_HIGH_LIQ_START = (19, 0)   # 7:00 PM IST = NYSE open
CRYPTO_HIGH_LIQ_END   = (1,  0)   # 1:00 AM IST = NYSE close

# BTC trend filter — skip alt buys if BTC is down more than this % from 1h ago
CRYPTO_BTC_MIN_CHANGE = -0.4      # if BTC dropped >0.4% in last 1h, skip all buys

# Crypto scans 24/7 but skips the lowest-liquidity window to save resources
CRYPTO_SCAN_SKIP_START  = (2, 0)   # skip 2:00 AM IST
CRYPTO_SCAN_SKIP_END    = (5, 0)   # resume at 5:00 AM IST
