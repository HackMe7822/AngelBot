#!/bin/bash
# AngelBot — macOS launcher (equivalent of run.bat)
# Run with:  chmod +x run.sh && ./run.sh

cd "$(dirname "$0")"

# Suppress third-party warnings across all threads/subprocesses
export PYTHONWARNINGS="ignore::DeprecationWarning,ignore::PendingDeprecationWarning,ignore::Warning:urllib3"

# UTF-8 output
export PYTHONIOENCODING=utf-8

mkdir -p logs

while true; do
    echo ""
    echo "============================================================"
    echo "  AngelBot Starting  [$(date '+%Y-%m-%d %H:%M:%S')]"
    echo "============================================================"
    echo ""

    python3 -W ignore::DeprecationWarning main.py

    EXIT=$?
    echo ""
    echo "============================================================"
    echo "  Bot stopped [$(date '+%Y-%m-%d %H:%M:%S')]  Exit code: $EXIT"
    echo "  Restarting in 15 seconds...  Press Ctrl+C to cancel."
    echo "============================================================"
    sleep 15
done
