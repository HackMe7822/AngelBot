"""
watchdog.py — general-purpose hang detector for AngelBot's worker services.

Runs as its own NSSM service (AngelBot-Watchdog), checking every worker every
few minutes:
  - India/US/Crypto: liveness via heartbeat file staleness (each worker's main
    loop touches heartbeats/<name>.hb once per iteration, ~every 60s, via
    heartbeat.py — a stale file means the loop is stuck somewhere, even if
    the OS still shows the process as "running").
  - Portal: an active HTTP probe (it's a request-driven web server, not a
    polling loop, so a quiet log doesn't mean anything is wrong).

On detecting a hang: logs it, sends a Telegram alert, and runs `nssm restart`
on the affected service. Repeated failures within a short window stop
triggering further restarts (to avoid silently restart-looping a service
that's persistently broken) and instead escalate the alert.

Design constraints, since THIS is the thing that must never itself hang:
  - No import of anything that talks to a broker API or does unbounded I/O.
  - Every subprocess call and every network call has an explicit timeout.
  - Any single check failing (exception, timeout) is treated as "unhealthy"
    for that service and logged, never allowed to crash the whole loop.
"""
import json
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import log_cleanup

_ROOT = os.path.dirname(os.path.abspath(__file__))
_HB_DIR = os.path.join(_ROOT, 'heartbeats')
_STATE_FILE = os.path.join(_ROOT, 'watchdog_state.json')
_LOG_DIR = os.path.join(_ROOT, 'logs')
_LOG_FILE = os.path.join(_LOG_DIR, 'AngelBot-Watchdog.log')

NSSM = r"C:\Windows\nssm.exe"
CHECK_INTERVAL_SEC = 120          # how often to run all checks
MAX_RESTARTS_PER_WINDOW = 3       # per service, before we stop auto-restarting
RESTART_WINDOW_MIN = 60

CHECKS = [
    {'service': 'AngelBot-India',  'type': 'heartbeat', 'hb': 'india',  'max_age_min': 5},
    {'service': 'AngelBot-US',     'type': 'heartbeat', 'hb': 'us',     'max_age_min': 5},
    {'service': 'AngelBot-Crypto', 'type': 'heartbeat', 'hb': 'crypto', 'max_age_min': 5},
    {'service': 'AngelBot-Portal', 'type': 'http', 'url': 'http://127.0.0.1:8080/login', 'timeout': 8},
]


def _log(msg):
    os.makedirs(_LOG_DIR, exist_ok=True)
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f"{ts}  {msg}"
    print(line)
    try:
        with open(_LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass


def _send_telegram(message):
    """Deliberately standalone — no DB, no shared config import beyond the
    two token/id values, explicit timeout. Failure here must never raise."""
    try:
        import requests
        from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            return
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, data={
            'chat_id': TELEGRAM_CHAT_ID,
            'text': message,
            'parse_mode': 'HTML',
        }, timeout=10)
    except Exception as e:
        _log(f"[WARN] Telegram alert failed: {e}")


def _load_state():
    try:
        with open(_STATE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state):
    try:
        with open(_STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        _log(f"[WARN] Could not save watchdog state: {e}")


def _prune_and_count(timestamps, window_min):
    cutoff = time.time() - window_min * 60
    kept = [t for t in timestamps if t >= cutoff]
    return kept


def _check_heartbeat(name, max_age_min):
    path = os.path.join(_HB_DIR, f'{name}.hb')
    if not os.path.exists(path):
        # No heartbeat yet at all (e.g. worker never started, or pre-upgrade) —
        # don't restart-loop something that was never wired up; just warn once.
        return False, f"no heartbeat file found ({path})"
    age_sec = time.time() - os.path.getmtime(path)
    if age_sec > max_age_min * 60:
        return False, f"heartbeat stale by {age_sec/60:.1f} min (limit {max_age_min} min)"
    return True, f"heartbeat fresh ({age_sec:.0f}s ago)"


def _check_http(url, timeout):
    try:
        import requests
        r = requests.get(url, timeout=timeout)
        # Any response at all (even 401/403) means the server is alive and
        # answering requests -- that's the bar, not a full functional check.
        return True, f"HTTP {r.status_code}"
    except Exception as e:
        return False, f"request failed: {e}"


def _restart_service(service):
    """nssm restart, with its own timeout so a hung nssm/service manager
    call can't hang the watchdog itself."""
    try:
        subprocess.run([NSSM, 'restart', service], capture_output=True,
                        text=True, timeout=45)
        return True
    except Exception as e:
        _log(f"[ERROR] Failed to restart {service}: {e}")
        return False


_LAST_CLEANUP_DAY = [None]


def _maybe_cleanup_logs():
    """Run log_cleanup once per calendar day -- cheap to check every cycle,
    actual zipping work happens once/day."""
    today = datetime.now().strftime('%Y%m%d')
    if _LAST_CLEANUP_DAY[0] == today:
        return
    _LAST_CLEANUP_DAY[0] = today
    try:
        log_cleanup.cleanup(log=_log)
    except Exception:
        _log("[ERROR] log_cleanup.cleanup() raised:\n" + traceback.format_exc())


def run_checks():
    _maybe_cleanup_logs()
    state = _load_state()
    changed = False

    for chk in CHECKS:
        service = chk['service']
        try:
            if chk['type'] == 'heartbeat':
                healthy, detail = _check_heartbeat(chk['hb'], chk['max_age_min'])
            else:
                healthy, detail = _check_http(chk['url'], chk['timeout'])
        except Exception as e:
            healthy, detail = False, f"check raised {e}"

        if healthy:
            continue

        _log(f"[UNHEALTHY] {service}: {detail}")

        history = _prune_and_count(state.get(service, []), RESTART_WINDOW_MIN)
        if len(history) >= MAX_RESTARTS_PER_WINDOW:
            _log(f"[ESCALATE] {service} already restarted {len(history)}x in "
                 f"the last {RESTART_WINDOW_MIN}min -- not restarting again, "
                 f"needs manual investigation.")
            _send_telegram(
                f"\U0001F6A8 <b>{service}</b> is unhealthy ({detail}) and has "
                f"already been auto-restarted {len(history)} times in the last "
                f"{RESTART_WINDOW_MIN} min. Watchdog is standing down -- this "
                f"needs a human look."
            )
            continue

        ok = _restart_service(service)
        history.append(time.time())
        state[service] = history
        changed = True

        if ok:
            _log(f"[RECOVERED] Restarted {service} (attempt {len(history)} "
                 f"in last {RESTART_WINDOW_MIN}min)")
            _send_telegram(
                f"\u26A0\uFE0F <b>{service}</b> was unhealthy ({detail}) -- "
                f"watchdog auto-restarted it."
            )
        else:
            _log(f"[FAILED] Could not restart {service}")
            _send_telegram(f"\U0001F6A8 <b>{service}</b> is unhealthy ({detail}) "
                            f"AND the watchdog's restart attempt itself failed.")

    if changed:
        _save_state(state)


def main():
    _log("AngelBot Watchdog started -- checking every "
         f"{CHECK_INTERVAL_SEC}s: " + ", ".join(c['service'] for c in CHECKS))
    while True:
        try:
            run_checks()
        except Exception:
            _log("[ERROR] run_checks() raised:\n" + traceback.format_exc())
        time.sleep(CHECK_INTERVAL_SEC)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        _log("Watchdog stopped by user.")
