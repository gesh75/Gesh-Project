#!/usr/bin/env bash
# refresh_dashboard_snapshot.sh — intraday MCP refresh of the :9000 dashboard snapshot.
#
# The dashboard (/api/portfolio) serves live_portfolio_snapshot.json whenever the
# robin_stocks session is offline (the reauth-dead pickle / MFA case). The daily chain
# writes that file ONCE at 10:30 ET, so it ages through the trading day — the dashboard
# showed a ~3h-old snapshot on 2026-07-15. This wrapper now prefers the LLM-free
# direct MCP client (`fetch_snapshot_direct.py`) — the same tier 1 the daily
# chain uses. `fetch_snapshot_mcp.sh` (`claude -p`) is fallback only: that CLI
# cannot authenticate once the Claude subscription lapses.
#
# Two guards keep it cheap + quiet:
#   • market-hours only (Mon–Fri 09:30–16:00 ET) — positions don't move off-hours.
#     GESH_SNAPSHOT_FORCE=1 bypasses (ad-hoc run).
#   • graded exit — a transient fetch failure must NOT flag a launchd failure
#     while the fallback snapshot is still serviceable (the daily chain covers the gap).
set -uo pipefail

# launchd runs with a minimal PATH that omits Homebrew. Put Homebrew + Grok CLI
# on PATH (rule #55). The venv interpreter is required (host python is 3.9).
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

DIR="${GESH_HOME:-$HOME/Projects/gesh-project}"
LOG=/tmp/gesh_dashboard_snapshot.log
SNAP="$DIR/live_portfolio_snapshot.json"
FRESH_S=1800          # < 30 min → this run refreshed it
SERVICEABLE_S=86400   # < 24 h  → fallback still fine (daily chain refreshes at 10:30)

cd "$DIR" || exit 1
echo "═══ dashboard snapshot refresh — $(TZ=America/New_York date '+%Y-%m-%d %H:%M:%S %Z') ═══" >> "$LOG"

# ── market-hours guard (ET) ───────────────────────────────────────────────────
if [ "${GESH_SNAPSHOT_FORCE:-}" != "1" ]; then
    dow=$(TZ=America/New_York date +%u)                 # 1=Mon … 7=Sun
    hm=$(TZ=America/New_York date +%H%M); hm=$((10#$hm)) # base-10 (avoid octal on 0930)
    if [ "$dow" -gt 5 ] || [ "$hm" -lt 930 ] || [ "$hm" -gt 1600 ]; then
        echo "off-hours (ET ${hm}, dow ${dow}) — skipping" >> "$LOG"
        exit 0
    fi
fi

# ── refresh: LLM-free direct MCP first, claude -p courier as fallback ─────────
PY="${GESH_PY:-$DIR/venv/bin/python}"
if [ ! -x "$PY" ]; then
    echo "FATAL: no interpreter at $PY" >> "$LOG"
    exit 1
fi
if "$PY" "$DIR/fetch_snapshot_direct.py" >> "$LOG" 2>&1; then
    FETCH_EXIT=0
    echo "(fetch_snapshot_direct exit 0)" >> "$LOG"
else
    echo "direct MCP failed — falling back to claude -p MCP path" >> "$LOG"
    GESH_PY="$PY" /bin/bash "$DIR/fetch_snapshot_mcp.sh" >> "$LOG" 2>&1
    FETCH_EXIT=$?
    echo "(fetch_snapshot_mcp exit ${FETCH_EXIT})" >> "$LOG"
fi

# ── grade the outcome by the RESULTING snapshot age, not just the fetch exit ──
age_s=-1
if [ -f "$SNAP" ]; then
    now_s=$(date +%s)
    mtime_s=$(stat -f %m "$SNAP" 2>/dev/null || echo 0)
    age_s=$(( now_s - mtime_s ))
    echo "snapshot age: ${age_s}s (fresh<${FRESH_S}s serviceable<${SERVICEABLE_S}s)" >> "$LOG"
fi

if [ "$age_s" -ge 0 ] && [ "$age_s" -lt "$FRESH_S" ]; then
    echo "REFRESH OK (${age_s}s)" >> "$LOG"
    exit 0
fi
if [ "$age_s" -ge 0 ] && [ "$age_s" -lt "$SERVICEABLE_S" ]; then
    # fetch failed but the fallback is still serviceable — WARN in the log, but
    # do NOT fail the run (a false launchd failure would read as the product
    # being offline over a transient MCP blip).
    echo "REFRESH DEFERRED — fetch_exit=${FETCH_EXIT}, snapshot ${age_s}s old but serviceable" >> "$LOG"
    exit 0
fi
echo "REFRESH FAILED — fetch_exit=${FETCH_EXIT}, snapshot stale/missing (age=${age_s}s)" >> "$LOG"
exit 1
