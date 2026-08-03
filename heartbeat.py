"""
heartbeat.py — tiny, dependency-free liveness signal for the trading workers.

Each worker's main loop calls touch(name) once per iteration (~every 60s).
watchdog.py checks how stale these files are to detect a hung process —
this is deliberately just a local file write with no network/DB access,
so it can never itself be the thing that hangs.
"""
import os
from datetime import datetime, timezone

_HB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'heartbeats')


def touch(name):
    """Write the current UTC time to heartbeats/<name>.hb. Never raises —
    a heartbeat failure must never be the reason a worker crashes."""
    try:
        os.makedirs(_HB_DIR, exist_ok=True)
        path = os.path.join(_HB_DIR, f'{name}.hb')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(datetime.now(timezone.utc).isoformat())
    except Exception:
        pass
