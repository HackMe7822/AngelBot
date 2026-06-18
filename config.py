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
SCALP_TARGET_PCT = float(os.getenv("SCALP_TARGET_PCT", "0.015"))  # exit on 1.5% profit
SCALP_SL_PCT     = float(os.getenv("SCALP_SL_PCT",     "0.006"))  # exit on 0.6% loss
SL_CONFIRM_POLLS = int(os.getenv("SL_CONFIRM_POLLS",   "2"))       # consecutive 15s polls below SL

# Signal quality — higher = fewer but stronger trades (4 = original profitable setting, 3 = more trades/lower quality)
MIN_SIGNAL_SCORE = int(os.getenv("MIN_SIGNAL_SCORE", "4"))

# Risk controls
MAX_DAILY_LOSS_PCT  = float(os.getenv("MAX_DAILY_LOSS_PCT",  "0.02"))   # stop all buys if day loss exceeds 2%
MAX_DAILY_TRADES    = int(os.getenv("MAX_DAILY_TRADES",      "30"))     # circuit breaker: max closed trades/day
MAX_DEPLOYED_PCT    = float(os.getenv("MAX_DEPLOYED_PCT",    "0.70"))   # never deploy more than 70% of balance
PEAK_DRAWDOWN_PCT   = float(os.getenv("PEAK_DRAWDOWN_PCT",   "0.03"))   # pause buys if account drops 3% from high
SLIPPAGE_PCT        = float(os.getenv("SLIPPAGE_PCT",        "0.0005")) # simulated slippage on paper entries
ENTRY_START_MIN     = (9, 45)  # no buys before 9:45 AM IST
ENTRY_END_MIN       = (15, 0)  # no buys after 3:00 PM (approaching force-close)
MIN_STOCK_PRICE     = float(os.getenv("MIN_STOCK_PRICE",     "150.0"))  # skip stocks under ₹150

# Parallel position cap — hard limit on how many stocks open at once across all buys
MAX_CONCURRENT_POSITIONS = int(os.getenv("MAX_CONCURRENT_POSITIONS", "5"))

# Time-based stagnation exit — if enabled, close positions open >MAX_HOLD_MINUTES with <50% of target move
USE_TIME_EXIT    = os.getenv("USE_TIME_EXIT",    "false").lower() == "true"
MAX_HOLD_MINUTES = int(os.getenv("MAX_HOLD_MINUTES", "90"))

# Market mood filter — if OFF, trades regardless of NIFTY/S&P direction (matches original profitable session)
USE_MOOD_FILTER       = os.getenv("USE_MOOD_FILTER",       "true").lower() == "true"
MOOD_FILTER_THRESHOLD = float(os.getenv("MOOD_FILTER_THRESHOLD", "-1.5"))  # block if index down this %

# Sector concentration cap — if OFF, buys best setups regardless of sector (matches original profitable session)
USE_SECTOR_CAP       = os.getenv("USE_SECTOR_CAP",       "true").lower() == "true"
MAX_SECTOR_POSITIONS = int(os.getenv("MAX_SECTOR_POSITIONS", "2"))  # max open positions per sector

# ── Alpaca / US market config ─────────────────────────────────────────────────
ALPACA_KEY    = os.getenv("ALPACA_KEY",    "")
ALPACA_SECRET = os.getenv("ALPACA_SECRET", "")
ALPACA_PAPER  = os.getenv("ALPACA_PAPER",  "true").lower() == "true"

US_CAPITAL          = float(os.getenv("US_CAPITAL", "10000"))   # set in .env when going live
US_MAX_POSITIONS    = 500       # effectively unlimited — capital is the natural cap
US_MAX_POSITION_PCT = 0.10      # 10% per stock — same as India
US_MAX_DAILY_LOSS_PCT = float(os.getenv("US_MAX_DAILY_LOSS_PCT", "0.02"))   # 2% daily loss limit in USD
US_MAX_DAILY_TRADES   = int(os.getenv("US_MAX_DAILY_TRADES",     "50"))     # circuit breaker for US session
US_MIN_STOCK_PRICE  = float(os.getenv("US_MIN_STOCK_PRICE",      "10.0"))   # skip stocks under $10
US_SCALP_TARGET_PCT = float(os.getenv("US_SCALP_TARGET_PCT",     "0.015"))  # exit on 1.5% profit
US_SCALP_SL_PCT     = float(os.getenv("US_SCALP_SL_PCT",         "0.006"))  # exit on 0.6% loss
US_MAX_DEPLOYED_PCT = float(os.getenv("US_MAX_DEPLOYED_PCT",     "0.70"))   # never deploy more than 70%
US_PEAK_DRAWDOWN_PCT = float(os.getenv("US_PEAK_DRAWDOWN_PCT",   "0.03"))   # pause if drops 3% from high
US_MAX_CONCURRENT_POSITIONS = int(os.getenv("US_MAX_CONCURRENT_POSITIONS", "8"))  # max simultaneous US positions

# US loss cascade circuit breaker — stops buying when many SL hits happen in a short window
US_MAX_BUYS_PER_SCAN      = int(os.getenv("US_MAX_BUYS_PER_SCAN",     "50"))   # max new positions per scan cycle (set low in Settings to throttle)
US_LOSS_BURST_COUNT       = int(os.getenv("US_LOSS_BURST_COUNT",      "5"))    # SL hits in window to trigger pause
US_LOSS_BURST_WINDOW      = int(os.getenv("US_LOSS_BURST_WINDOW",     "300"))  # seconds to measure burst in
US_LOSS_BURST_COOLDOWN    = int(os.getenv("US_LOSS_BURST_COOLDOWN",   "1800")) # seconds to pause buying after burst
US_CANDLE_CONFIRM_REENTRY = os.getenv("US_CANDLE_CONFIRM_REENTRY", "true").lower() == "true"  # require bullish candle before re-entry after SL

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

# Crypto-specific risk limits (separate from India/US so Settings changes don't bleed over)
CRYPTO_MAX_DEPLOYED_PCT  = float(os.getenv("CRYPTO_MAX_DEPLOYED_PCT",  "0.80"))  # 80% deployed cap
CRYPTO_PEAK_DRAWDOWN_PCT = float(os.getenv("CRYPTO_PEAK_DRAWDOWN_PCT", "0.05"))  # 5% drawdown limit (wider than stocks)
CRYPTO_MAX_CONCURRENT    = int(os.getenv("CRYPTO_MAX_CONCURRENT",      "8"))     # max simultaneous positions (one per symbol)

# ── Telegram alert toggles ────────────────────────────────────────────────────
# Each can be overridden via bot_settings DB (Settings page) without code change.
TELEGRAM_ALERTS_ENABLED  = os.getenv("TELEGRAM_ALERTS_ENABLED",  "true").lower() == "true"
TELEGRAM_ALERT_BUY       = os.getenv("TELEGRAM_ALERT_BUY",       "true").lower() == "true"
TELEGRAM_ALERT_SELL      = os.getenv("TELEGRAM_ALERT_SELL",      "true").lower() == "true"
TELEGRAM_ALERT_DAILY     = os.getenv("TELEGRAM_ALERT_DAILY",     "true").lower() == "true"
TELEGRAM_ALERT_ERRORS    = os.getenv("TELEGRAM_ALERT_ERRORS",    "true").lower() == "true"
TELEGRAM_ALERT_BOT_START = os.getenv("TELEGRAM_ALERT_BOT_START", "true").lower() == "true"
TELEGRAM_ALERT_BURST     = os.getenv("TELEGRAM_ALERT_BURST",     "true").lower() == "true"
TELEGRAM_ALERT_INDIA     = os.getenv("TELEGRAM_ALERT_INDIA",     "true").lower() == "true"
TELEGRAM_ALERT_US        = os.getenv("TELEGRAM_ALERT_US",        "true").lower() == "true"
TELEGRAM_ALERT_CRYPTO    = os.getenv("TELEGRAM_ALERT_CRYPTO",    "true").lower() == "true"
TELEGRAM_MIN_BUY_CAPITAL  = float(os.getenv("TELEGRAM_MIN_BUY_CAPITAL",  "0"))
TELEGRAM_MIN_PNL_ALERT    = float(os.getenv("TELEGRAM_MIN_PNL_ALERT",    "0"))
TELEGRAM_ALERT_TUNNEL_URL = os.getenv("TELEGRAM_ALERT_TUNNEL_URL", "true").lower() == "true"

# ── WhatsApp alerts (CallMeBot — free, save +34 644 82 13 90 and send "I allow callmebot to send me messages") ──
WHATSAPP_PHONE     = os.getenv("WHATSAPP_PHONE",     "")
WHATSAPP_API_KEY   = os.getenv("WHATSAPP_API_KEY",   "")
WHATSAPP_ALERTS_ENABLED   = os.getenv("WHATSAPP_ALERTS_ENABLED",   "false").lower() == "true"
WHATSAPP_ALERT_BUY        = os.getenv("WHATSAPP_ALERT_BUY",        "true").lower() == "true"
WHATSAPP_ALERT_SELL       = os.getenv("WHATSAPP_ALERT_SELL",       "true").lower() == "true"
WHATSAPP_ALERT_DAILY      = os.getenv("WHATSAPP_ALERT_DAILY",      "true").lower() == "true"
WHATSAPP_ALERT_ERRORS     = os.getenv("WHATSAPP_ALERT_ERRORS",     "true").lower() == "true"
WHATSAPP_ALERT_BOT_START  = os.getenv("WHATSAPP_ALERT_BOT_START",  "true").lower() == "true"
WHATSAPP_ALERT_BURST      = os.getenv("WHATSAPP_ALERT_BURST",      "true").lower() == "true"
WHATSAPP_ALERT_INDIA      = os.getenv("WHATSAPP_ALERT_INDIA",      "true").lower() == "true"
WHATSAPP_ALERT_US         = os.getenv("WHATSAPP_ALERT_US",         "true").lower() == "true"
WHATSAPP_ALERT_CRYPTO     = os.getenv("WHATSAPP_ALERT_CRYPTO",     "true").lower() == "true"
WHATSAPP_ALERT_TUNNEL_URL = os.getenv("WHATSAPP_ALERT_TUNNEL_URL", "true").lower() == "true"
WHATSAPP_MIN_BUY_CAPITAL  = float(os.getenv("WHATSAPP_MIN_BUY_CAPITAL", "0"))
WHATSAPP_MIN_PNL_ALERT    = float(os.getenv("WHATSAPP_MIN_PNL_ALERT",   "0"))

# ── ntfy push notifications (ntfy.sh — free, no signup, no API key needed) ───
# 1. Install the "ntfy" app on your phone (Android/iOS)
# 2. Pick any unique topic name: e.g. "angelbot-yourname"
# 3. Subscribe to it in the app — instant push when bot fires
NTFY_TOPIC   = os.getenv("NTFY_TOPIC",  "")               # required — your unique topic name
NTFY_SERVER  = os.getenv("NTFY_SERVER", "https://ntfy.sh") # change if self-hosting ntfy
NTFY_TOKEN   = os.getenv("NTFY_TOKEN",  "")               # optional — for private topics or ntfy.sh Pro
NTFY_ALERTS_ENABLED   = os.getenv("NTFY_ALERTS_ENABLED",   "false").lower() == "true"
NTFY_ALERT_BUY        = os.getenv("NTFY_ALERT_BUY",        "true").lower() == "true"
NTFY_ALERT_SELL       = os.getenv("NTFY_ALERT_SELL",       "true").lower() == "true"
NTFY_ALERT_DAILY      = os.getenv("NTFY_ALERT_DAILY",      "true").lower() == "true"
NTFY_ALERT_ERRORS     = os.getenv("NTFY_ALERT_ERRORS",     "true").lower() == "true"
NTFY_ALERT_BOT_START  = os.getenv("NTFY_ALERT_BOT_START",  "true").lower() == "true"
NTFY_ALERT_BURST      = os.getenv("NTFY_ALERT_BURST",      "true").lower() == "true"
NTFY_ALERT_INDIA      = os.getenv("NTFY_ALERT_INDIA",      "true").lower() == "true"
NTFY_ALERT_US         = os.getenv("NTFY_ALERT_US",         "true").lower() == "true"
NTFY_ALERT_CRYPTO     = os.getenv("NTFY_ALERT_CRYPTO",     "true").lower() == "true"
NTFY_ALERT_TUNNEL_URL = os.getenv("NTFY_ALERT_TUNNEL_URL", "true").lower() == "true"
NTFY_MIN_BUY_CAPITAL  = float(os.getenv("NTFY_MIN_BUY_CAPITAL", "0"))
NTFY_MIN_PNL_ALERT    = float(os.getenv("NTFY_MIN_PNL_ALERT",   "0"))

# ── Portal UI refresh intervals (seconds) ────────────────────────────────────
PORTAL_DASH_REFRESH    = int(os.getenv("PORTAL_DASH_REFRESH",    "30"))
PORTAL_POS_REFRESH     = int(os.getenv("PORTAL_POS_REFRESH",     "30"))
PORTAL_LB_REFRESH      = int(os.getenv("PORTAL_LB_REFRESH",     "60"))
PORTAL_LOG_REFRESH     = int(os.getenv("PORTAL_LOG_REFRESH",      "5"))
PORTAL_MONITOR_REFRESH = int(os.getenv("PORTAL_MONITOR_REFRESH",  "2"))
