#!/usr/bin/env bash
# refresh_dashboard_snapshot.sh — intraday MCP refresh of the :9000 dashboard snapshot.
#
# The dashboard (/api/portfolio) serves live_portfolio_snapshot.json whenever the
# robin_stocks session is offline (the reauth-dead pickle / MFA case). The daily chain
# writes that file ONCE at 10:30 ET, so it ages through the trading day — the dashboard
# showed a ~3h-old snapshot on 2026-07-15. This wrapper re-runs fetch_snapshot_mcp.sh
# (Robinhood MCP via `claude -p` — NO robin_stocks pickle, NO MFA) on a schedule so the
# snapshot stays live without the recurring reauth dance.
#
# Two guards keep it cheap + quiet:
#   • market-hours only (Mon–Fri 09:30–16:00 ET) — positions don't move off-hours and
#     every run costs one `claude -p` call. GESH_SNAPSHOT_FORCE=1 bypasses (ad-hoc run).
#   • graded exit — a transient `claude -p` rate-limit must NOT flag a launchd failure
#     while the fallback snapshot is still serviceable (the daily chain covers the gap).
set -uo pipefail

# launchd runs with a minimal PATH that omits Homebrew, so `claude` (invoked inside
# fetch_snapshot_mcp.sh) is not found. Put Homebrew on PATH (mirrors rule #55).
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

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
        echo "off-hours (ET ${hm}, dow ${dow}) — skipping (no LLM spend)" >> "$LOG"
        exit 0
    fi
fi

# ── refresh via the existing MCP fetch (reused verbatim) ──────────────────────
GESH_PY="${GESH_PY:-/usr/bin/python3}" /bin/bash "$DIR/fetch_snapshot_mcp.sh" >> "$LOG" 2>&1
FETCH_EXIT=$?
echo "(fetch_snapshot_mcp exit ${FETCH_EXIT})" >> "$LOG"

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
    # fetch failed (usually `claude -p` usage/rate limit) but the fallback is still
    # serviceable — WARN in the log, but do NOT fail the run (a false launchd failure
    # would read as the product being offline over a transient CLI blip).
    echo "REFRESH DEFERRED — fetch_exit=${FETCH_EXIT}, snapshot ${age_s}s old but serviceable" >> "$LOG"
    exit 0
fi
echo "REFRESH FAILED — fetch_exit=${FETCH_EXIT}, snapshot stale/missing (age=${age_s}s)" >> "$LOG"
exit 1
