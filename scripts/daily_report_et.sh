#!/usr/bin/env bash
# daily_report_et.sh — 10:45 ET weekday publisher/retry for gesh_report.html.
#
# Claude's Cowork "Gesh daily portfolio analysis" card used to pick this file
# up around 10:45 ET. launchd replaces that card.
#
# Layer 1 (com.gesh.daily-analysis) still STARTS the chain at 10:30 ET. Measured
# 2026-08-21: CHAIN DONE 10:46:23 ET, 164KB report. This job:
#   1. waits if the 10:30 chain still holds the lock (Grok failover ~15–20 min)
#   2. if today's report is already >=60KB — Telegram "ready" and exit
#   3. otherwise run the chain once. Window is 10:45±90 so a lock-wait that
#      overruns the old ±12 still recovers (2026-08-26: waited 720s then 10:58
#      was outside the window and yesterday's HTML stayed).
#
# Do NOT call et_gate_require until we publish or exec the chain. A lock-held
# skip inside the window must leave the stamp unset so a later tick can recover.
set -uo pipefail

export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
LOG=/tmp/gesh_daily_report.log
LOCK=/tmp/gesh_daily_chain.lock
REPORT="$REPO/gesh_report.html"
TRADING="${GESH_TRADING_HOME:-$HOME/Projects/gesh-project-trading}"
MIN_BYTES="${DAILY_REPORT_MIN_BYTES:-60000}"
WAIT_S="${DAILY_REPORT_WAIT_S:-2400}"
TARGET_ET="${DAILY_REPORT_ET:-10:45}"
TOL="${DAILY_REPORT_TOL:-90}"

ET_GATE_LABEL=daily-report
# shellcheck source=et_gate.sh
source "$REPO/scripts/et_gate.sh"

_tg() {
    local msg="$1" tok="" chat="" envf
    for envf in "$TRADING/.env" "$REPO/.env"; do
        [ -f "$envf" ] || continue
        [ -z "$tok" ]  && tok=$(grep -m1 '^TELEGRAM_BOT_TOKEN=' "$envf" 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'")
        [ -z "$chat" ] && chat=$(grep -m1 '^TELEGRAM_CHAT_ID='  "$envf" 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'")
    done
    [ -n "$tok" ] && [ -n "$chat" ] || { echo "[tg] no credentials"; return 0; }
    printf 'url = "%s"\ndata-urlencode = "chat_id=%s"\ndata-urlencode = "text=%s"\n' \
        "https://api.telegram.org/bot${tok}/sendMessage" "$chat" "$msg" |
        curl -sS -m 10 --config - >/dev/null 2>&1 || true
}

_lock_held() {
    local pid
    pid=$(cat "$LOCK/pid" 2>/dev/null || echo "")
    [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

et_gate_in_window "$TARGET_ET" "$TOL" mon-fri || exit 0

umask 077
{
    echo "═══ daily report — $(TZ=America/New_York date '+%Y-%m-%d %H:%M:%S %Z') ═══"
    cd "$REPO" || exit 0

    deadline=$((SECONDS + WAIT_S))
    while _lock_held && [ "$SECONDS" -lt "$deadline" ]; do
        echo "10:30 chain still running (lock $LOCK) — waiting"
        sleep 15
    done
    if _lock_held; then
        echo "chain still running after ${WAIT_S}s — skip without stamping (later tick may recover)"
        exit 0
    fi

    if et_gate_file_is_today_et "$REPORT" "$MIN_BYTES"; then
        et_gate_require "$TARGET_ET" "$TOL" mon-fri
        size=$(stat -f%z "$REPORT" 2>/dev/null || echo 0)
        echo "today's gesh_report.html is ready (${size}B)"
        _tg "📋 GESH daily report ready (${size}B)
http://127.0.0.1:9000/report"
        exit 0
    fi

    echo "no fresh gesh_report.html — running the chain (10:30 miss or step 3/5 fail)"
    # Do not et_gate_require here: a failed retry would burn the day. The chain
    # writes GESH_ET_GATE_STAMP only after a usable report.
    et_gate_export_chain_llm
    export GESH_ET_GATE_STAMP="$(et_gate_stamp_path)"
    exec /bin/bash "$REPO/run_daily_analysis.sh"
} >>"$LOG" 2>&1
