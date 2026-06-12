#!/bin/bash
# AngelBot Watchdog — auto-monitors all 3 workers, restarts if crashed, sends Telegram updates
# Runs via Mac cron every 30 min during India market hours + checks US/Crypto overnight

BOT_DIR="/Users/pete/Desktop/AngelBot"
PY="/usr/bin/python3"
LOG_DATE=$(date +%Y%m%d)
TG_TOKEN="8760657306:AAH4zYb7oo5yDKWg54UZXMy62VSVEiINNCM"
TG_CHAT="5615154720"
IST_HOUR=$(TZ="Asia/Kolkata" date +%H)
IST_MIN=$(TZ="Asia/Kolkata" date +%M)
IST_TIME=$(TZ="Asia/Kolkata" date +"%H:%M")
WEEKDAY=$(TZ="Asia/Kolkata" date +%u)   # 1=Mon ... 7=Sun

tg_send() {
    curl -s -X POST "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
        -d chat_id="${TG_CHAT}" \
        -d parse_mode="HTML" \
        -d text="$1" > /dev/null 2>&1
}

is_running() {
    pgrep -f "$1" > /dev/null 2>&1
}

restart_worker() {
    local script="$1"
    local log="$2"
    osascript -e "tell application \"Terminal\" to do script \"cd '${BOT_DIR}' && ${PY} ${script} 2>&1 | tee -a logs/${log}\"" > /dev/null 2>&1
    sleep 3
}

get_india_pnl() {
    sqlite3 "${BOT_DIR}/angelbot.db" \
        "SELECT COUNT(*), ROUND(COALESCE(SUM(pnl),0),2), SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END)
         FROM trades WHERE status='closed' AND (source IS NULL OR source='paper')
         AND date(exit_time)=date('now','localtime');" 2>/dev/null
}

get_india_open() {
    sqlite3 "${BOT_DIR}/angelbot.db" \
        "SELECT COUNT(*) FROM trades WHERE status='open' AND (source IS NULL OR source='paper');" 2>/dev/null
}

get_us_pnl() {
    sqlite3 "${BOT_DIR}/angelbot.db" \
        "SELECT COUNT(*), ROUND(COALESCE(SUM(pnl),0),2), SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END)
         FROM trades WHERE status='closed' AND source='us_paper'
         AND date(exit_time)=date('now');" 2>/dev/null
}

# ── India market hours: 9:15 AM – 3:30 PM IST, Mon–Fri ──────────────────────
INDIA_OPEN=0
if [ "$WEEKDAY" -le 5 ] && \
   ( [ "$IST_HOUR" -gt 9 ] || ( [ "$IST_HOUR" -eq 9 ] && [ "$IST_MIN" -ge 15 ] ) ) && \
   ( [ "$IST_HOUR" -lt 15 ] || ( [ "$IST_HOUR" -eq 15 ] && [ "$IST_MIN" -le 30 ] ) ); then
    INDIA_OPEN=1
fi

MSG=""
RESTART_MSG=""

# ── Check India worker ────────────────────────────────────────────────────────
if [ "$INDIA_OPEN" -eq 1 ]; then
    if ! is_running "india_worker.py"; then
        restart_worker "india_worker.py" "india_${LOG_DATE}.log"
        RESTART_MSG="${RESTART_MSG}⚠️ India worker was DOWN — restarted\n"
    fi

    IND_STATS=$(get_india_pnl)
    IND_OPEN=$(get_india_open)
    IND_TRADES=$(echo "$IND_STATS" | cut -d'|' -f1)
    IND_PNL=$(echo "$IND_STATS" | cut -d'|' -f2)
    IND_WINS=$(echo "$IND_STATS" | cut -d'|' -f3)
    IND_LOSSES=$(( ${IND_TRADES:-0} - ${IND_WINS:-0} ))

    # Check for errors in log
    IND_ERR=$(grep -i "error\|traceback\|exception" "${BOT_DIR}/logs/india_${LOG_DATE}.log" 2>/dev/null | grep -v "urllib\|ssl\|requests" | tail -2)

    MSG="🇮🇳 <b>India NSE</b> — ${IST_TIME} IST\n"
    MSG="${MSG}• Worker: ✅ Running\n"
    MSG="${MSG}• Open positions: ${IND_OPEN:-0}\n"
    MSG="${MSG}• Today P&amp;L: ₹${IND_PNL:-0} (${IND_WINS:-0}W / ${IND_LOSSES}L of ${IND_TRADES:-0} trades)\n"
    if [ -n "$IND_ERR" ]; then
        MSG="${MSG}• ⚠️ Error: $(echo "$IND_ERR" | tail -1)\n"
    else
        MSG="${MSG}• Errors: None ✅\n"
    fi
fi

# ── Check US worker (7 PM – 1:30 AM IST) ─────────────────────────────────────
US_OPEN=0
if ( [ "$IST_HOUR" -ge 19 ] || [ "$IST_HOUR" -lt 2 ] ); then
    US_OPEN=1
fi

if [ "$US_OPEN" -eq 1 ]; then
    if ! is_running "us_worker.py"; then
        restart_worker "us_worker.py" "us_${LOG_DATE}.log"
        RESTART_MSG="${RESTART_MSG}⚠️ US worker was DOWN — restarted\n"
    fi
fi

# ── Check Crypto worker (always) ──────────────────────────────────────────────
if ! is_running "crypto_worker.py"; then
    restart_worker "crypto_worker.py" "crypto_${LOG_DATE}.log"
    RESTART_MSG="${RESTART_MSG}⚠️ Crypto worker was DOWN — restarted\n"
fi

# ── Send Telegram if India open or there were restarts ───────────────────────
if [ -n "$RESTART_MSG" ]; then
    tg_send "🔄 <b>AngelBot Watchdog</b> — ${IST_TIME} IST\n${RESTART_MSG}"
fi

if [ -n "$MSG" ]; then
    tg_send "$MSG"
fi

# ── Market open alert ─────────────────────────────────────────────────────────
if [ "$INDIA_OPEN" -eq 1 ] && [ "$IST_HOUR" -eq 9 ] && [ "$IST_MIN" -ge 15 ] && [ "$IST_MIN" -le 20 ]; then
    tg_send "🔔 <b>India NSE OPEN</b> — AngelBot scanning. Good morning!"
fi
