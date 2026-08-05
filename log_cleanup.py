"""
log_cleanup.py — daily housekeeping for AngelBot's logs/ directory.

NSSM and each worker's own file logger produce a mix of "live" files that
stay open forever (AngelBot-India.log, ...) and closed dated files that
appear once a day rolls over or a service rotates/restarts
(india_20260804.log, AngelBot-India-20260710T162437.537.log, ...). This
zips any closed file older than RETENTION_DAYS into a same-named .zip and
removes the raw .log, so old logs keep taking a fraction of the space
instead of accumulating forever. Never deletes a .zip once made.
"""
import os
import re
import zipfile
from datetime import datetime, timedelta

_ROOT = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(_ROOT, 'logs')
RETENTION_DAYS = 3

# Filenames NSSM/the workers keep permanently open for writing -- never
# touch these, only their rotated-out / dated siblings.
_LIVE_NAMES = {
    'AngelBot-India.log', 'AngelBot-US.log', 'AngelBot-Crypto.log',
    'AngelBot-Portal.log', 'AngelBot-Watchdog.log', 'AngelBot-Tunnel.log',
}

_DATED_APP_LOG = re.compile(r'^(?:india|us|crypto)_(\d{8})\.log$')
_NSSM_ROTATED = re.compile(r'^.+-(\d{8})T\d{6}\.\d+\.log$')


def _file_date(fname, path):
    m = _DATED_APP_LOG.match(fname) or _NSSM_ROTATED.match(fname)
    if m:
        try:
            return datetime.strptime(m.group(1), '%Y%m%d')
        except ValueError:
            pass
    return datetime.fromtimestamp(os.path.getmtime(path))


def cleanup(retention_days=RETENTION_DAYS, log=print):
    """Zip+remove closed log files older than retention_days. Returns count zipped."""
    if not os.path.isdir(LOG_DIR):
        return 0
    cutoff = datetime.now() - timedelta(days=retention_days)
    zipped = 0
    for fname in os.listdir(LOG_DIR):
        if fname in _LIVE_NAMES or not fname.endswith('.log'):
            continue
        path = os.path.join(LOG_DIR, fname)
        if not os.path.isfile(path):
            continue
        if _file_date(fname, path) >= cutoff:
            continue
        zip_path = path + '.zip'
        try:
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.write(path, arcname=fname)
            os.remove(path)
            zipped += 1
        except Exception as e:
            log(f"[log_cleanup] could not zip/remove {fname}: {e}")
    if zipped:
        log(f"[log_cleanup] zipped {zipped} log file(s) older than {retention_days} days")
    return zipped


if __name__ == '__main__':
    cleanup()
