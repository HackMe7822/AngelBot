"""
AngelBot Portal Worker
======================
Starts the web management portal on port 8080 and the Telegram command listener.
The portal is always running, making it the right home for the Telegram listener
across all 4 NSSM services (India/US/Crypto workers are separate processes).
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data.database import init_db

try:
    init_db()
except Exception as e:
    print(f"[Portal] DB init warning: {e} — portal will still start; retry on next restart")

# ── Telegram listener (reads from DB — does not interfere with trading workers) ─
try:
    from trading.paper_trader   import PaperTrader
    from trading.alpaca_trader  import AlpacaTrader
    from trading.crypto_trader  import CryptoTrader
    from reporting.telegram_listener import (
        set_trader, set_us_trader, set_crypto_trader, start_listener
    )
    set_trader(PaperTrader())
    set_us_trader(AlpacaTrader())
    set_crypto_trader(CryptoTrader())
    start_listener()
    print("[Portal] Telegram listener started — /help anytime")
except Exception as e:
    print(f"[Portal] Telegram listener failed to start: {e}")

import uvicorn
uvicorn.run("portal.app:app", host="0.0.0.0", port=8080, reload=False,
            log_level="warning")
