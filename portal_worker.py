"""
AngelBot Portal Worker
======================
Starts the web management portal on port 8080.
Run directly: python portal_worker.py
Or via main.py (auto-spawned in separate window).
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data.database import init_db

init_db()

import uvicorn
uvicorn.run("portal.app:app", host="0.0.0.0", port=8080, reload=False,
            log_level="warning")
