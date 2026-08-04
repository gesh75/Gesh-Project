#!/bin/bash
# run_daily_analysis.sh — single idempotent orchestrator for the GESH daily
# portfolio analysis. Replaces the agent hand-running each stage (which took
# 30-60 min); the deterministic chain completes in ~3-6 minutes.
#
#   1/5 fetch_live_snapshot.py  — live Robinhood holdings -> live_portfolio_snapshot.json
#   2/5 run_engines2.py         — all engines -> engine_results.json
#   3/5 run_ai2.py              — radar + Claude deep analysis + DB save
#   4/5 run_paper_gate2.py      — paper session + 8-gate audit chain (non-fatal)
#   5/5 gen_report2.py          — template-fill HTML report (<1s)
#
# Logs to /tmp/gesh_fullchain.log. Exit 0 = clean run, 1 = a fatal step failed.
# Safe to re-run any time (lock prevents overlap; later steps just regenerate).
#
# Sleep/wake note: launchd coalesces a 10:30 StartCalendarInterval missed during
# sleep and fires it once at wake — a late-day wake therefore runs the chain
# with off-hours prices and the MARKET_HOURS gate rejecting paper orders. That
# is the intended recovery path; the report timestamp shows the actual run time.

set -u

# ── Improvement #3 (2026-07-10): prevent host idle-sleep from freezing a run
# mid-chain while it holds the lock (which lock-skips the next launchd fire).
# Re-exec self under caffeinate -i once; guard var prevents an exec loop.
if [ -z "${GESH_CAFFEINATED:-}" ] && command -v caffeinate >/dev/null 2>&1; then
    GESH_CAFFEINATED=1 exec caffeinate -i "$0" "$@"
fi

# launchd spawns children with a 256 soft FD limit; a Yahoo throttle-retry
# morning exhausts it mid-engines ([Errno 24], seen 2026-06-12). Raise to 10240
# (hard limit is unlimited) — same defense server.py applies in-process.
ulimit -n 10240 2>/dev/null || true
# Repo roots derived from this script's location; GESH_TRADING_HOME overrides
# the sibling-directory convention for non-standard layouts.
GP="$(cd "$(dirname "$0")" && pwd)"
GPT="${GESH_TRADING_HOME:-$(dirname "$GP")/gesh-project-trading}"
# gesh_core.tax_harvester resolves its DB via GESH_TRACKER_DB; the chain runs with
# cwd=product root, so point it at the engine repo's gesh_financial.db (the tracker
# DB) to preserve the pre-extraction sibling-path default.
export GESH_TRACKER_DB="${GESH_TRACKER_DB:-$GPT/gesh_financial.db}"
# PR105-RUN-01: the repository declares Python 3.12 (.python-version) but this
# chain hardcoded `python3.14`, which does not exist in a conformant environment —
# the deterministic daily chain would fail at launch. Use the venv's GENERIC
# interpreter so the runtime contract has exactly one source of truth.
PY="$GP/venv/bin/python"
if [ ! -x "$PY" ]; then
    echo "FATAL: no interpreter at $PY — run ./setup.sh first (Python 3.12)." >&2
    exit 1
fi
# stale-venv guard (post-exec check)
DECLARED="$(tr -d ' \t\r\n' < "$GP/.python-version" 2>/dev/null || echo 3.12)"
VENV_VER=$("$PY" --version 2>&1 | awk '{print $2}')
VENV_MM=$(echo "$VENV_VER" | cut -d. -f1,2)
if [ ! -r "$GP/.python-version" ] || [ -z "$VENV_MM" ] || [ "$VENV_MM" != "$DECLARED" ]; then
  echo "expected: ${DECLARED}   actual: ${VENV_MM:-unknown}"
  echo "recreate the venv (do not run setup automatically)"
  exit 1
fi
LOG=/tmp/gesh_fullchain.log
LOCK=/tmp/gesh_daily_chain.lock

# ── Improvement #2 (2026-07-10): Telegram alert on every FATAL path.
# Credentials live in the TRADING repo's .env (the product .env has none).
# Fail-soft by design: a missing token or curl error logs a line, never
# changes the chain's exit path.
tg_alert() {
    local msg="$1" tok="" chat="" envf
    for envf in "$GPT/.env" "$GP/.env"; do
        [ -z "$tok" ]  && tok=$(grep -m1 '^TELEGRAM_BOT_TOKEN=' "$envf" 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'")
        [ -z "$chat" ] && chat=$(grep -m1 '^TELEGRAM_CHAT_ID='  "$envf" 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'")
    done
    if [ -z "$tok" ] || [ -z "$chat" ]; then
        echo "[tg_alert] no TELEGRAM_BOT_TOKEN/CHAT_ID found — alert NOT sent: $msg"
        return 0
    fi
    curl -sS -m 10 "https://api.telegram.org/bot${tok}/sendMessage" \
        --data-urlencode "chat_id=${chat}" \
        --data-urlencode "text=🚨 GESH daily chain — ${msg} [$(date '+%Y-%m-%d %H:%M %Z')]" \
        >/dev/null 2>&1 && echo "[tg_alert] sent: $msg" || echo "[tg_alert] curl send failed: $msg"
    return 0
}

# Cap the append-forever log at ~5MB (keep the most recent ~1MB).
if [ -f "$LOG" ] && [ "$(stat -f%z "$LOG" 2>/dev/null || echo 0)" -gt 5242880 ]; then
    LOG_TMP=$(mktemp "${LOG}.tmp.XXXXXX") || exit 1
    if ! tail -c 1048576 "$LOG" > "$LOG_TMP" || ! mv "$LOG_TMP" "$LOG"; then
        rm -f "$LOG_TMP"
    fi
fi

exec >>"$LOG" 2>&1

# Stale-lock recovery: a kill -9 / power loss skips the EXIT trap, and a
# permanently-held lock would silently skip every future run. Reclaim only when
# the recorded owner process is dead (or no PID was recorded) AND the lock is
# older than 30 min — a legitimately slow run keeps its lock.
if [ -d "$LOCK" ]; then
    LOCK_AGE=$(( $(date +%s) - $(stat -f %m "$LOCK" 2>/dev/null || echo 0) ))
    LOCK_PID=$(cat "$LOCK/pid" 2>/dev/null || echo "")
    if [ -n "$LOCK_PID" ] && kill -0 "$LOCK_PID" 2>/dev/null; then
        : # owner alive — leave the lock alone regardless of age
    elif [ "$LOCK_AGE" -gt 1800 ]; then
        echo "### STALE LOCK reclaimed (age ${LOCK_AGE}s, owner pid='${LOCK_PID}' gone) — $(date '+%Y-%m-%d %H:%M:%S %Z')"
        rm -rf "$LOCK"
    fi
fi
if ! mkdir "$LOCK" 2>/dev/null; then
    echo "### CHAIN SKIPPED (lock held) — $(date '+%Y-%m-%d %H:%M:%S %Z')"
    exit 0
fi
echo $$ > "$LOCK/pid"
trap 'rm -rf "$LOCK" 2>/dev/null' EXIT

cd "$GP" || exit 1
T0=$(date +%s)
FAIL=0
step() { echo ""; echo "=== $1 — $(date '+%H:%M:%S') (t+$(($(date +%s)-T0))s) ==="; }

echo ""
echo "### FULL 10:30 CHAIN — $(date '+%Y-%m-%d %H:%M:%S %Z')"

# ── 1/5 live snapshot — MCP-first (robin_stocks pickle login dies on every RH
# password change), legacy pickle fetch as fallback. Tiered freshness gate
# (snapshot_state.py): FRESH (<=30 min) runs normally; if BOTH fetch paths fail
# but the last snapshot is still SERVICEABLE (<= GESH_SNAPSHOT_SERVICEABLE_MIN,
# default 24h) the chain runs DEGRADED on last-known holdings so engines + GESH
# signals still refresh (only cash/sizing is stale) instead of aborting and
# letting engine_results.json rot (which starves the agentic-rh reader into
# advisory-only). A missing/too-old snapshot is still fatal.
SERVICEABLE_MAX_MIN=${GESH_SNAPSHOT_SERVICEABLE_MIN:-1440}
DEGRADED=0
step "1/5 fetch snapshot (MCP-first)"
# Retry with backoff (restored 2026-07-14 from 2a7eaec, which never reached main).
# The dominant step-1 failure mode is a TRANSIENT expired robinhood-trading MCP
# OAuth token (or a VPN window): a single attempt is far too brittle — it cost two
# full days in July. 4 attempts: now, +15m, +30m, +60m. The lock is held throughout
# (PID-liveness, owner alive) and caffeinate keeps the host awake.
#
# Deliberately NOT fatal when all 4 fail (this is where it differs from 2a7eaec):
# we fall through to the tiered snapshot_state gate below, which decides
# FRESH / SERVICEABLE(degraded) / FATAL. Retry first, degrade second, die last.
# Override the schedule for a manual run: GESH_SNAPSHOT_RETRY_WAITS="0" ./run_daily_analysis.sh
fetch_snapshot_once() {
    if GESH_PY="$PY" /bin/bash "$GP/fetch_snapshot_mcp.sh"; then
        return 0  # source is asserted from the gate below, not assumed from this exit code
    fi
    echo "MCP snapshot path failed — falling back to legacy robin_stocks pickle fetch"
    "$PY" fetch_live_snapshot.py
}

SNAP_OK=0
for RETRY_WAIT in ${GESH_SNAPSHOT_RETRY_WAITS:-0 900 1800 3600}; do
    if [ "$RETRY_WAIT" -gt 0 ]; then
        echo "[retry] snapshot fetch failed — waiting $((RETRY_WAIT/60)) min before retry ($(date '+%H:%M:%S'))"
        sleep "$RETRY_WAIT"
    fi
    if fetch_snapshot_once; then SNAP_OK=1; break; fi
done
if [ "$SNAP_OK" -ne 1 ]; then
    echo "WARN: live fetch failed (MCP + legacy, all attempts) — will try the last serviceable snapshot"
    tg_alert "step 1/5: snapshot fetch failed after all retries (MCP token expired? VPN on? check: claude mcp list) — falling back to the last serviceable snapshot"
fi

# stdout = the single state token; human detail (age/source/WARNING) goes to stderr -> the log.
SNAP_STATE=$("$PY" snapshot_state.py "$SERVICEABLE_MAX_MIN")
echo "snapshot state: $SNAP_STATE (serviceable limit ${SERVICEABLE_MAX_MIN} min)"
case "$SNAP_STATE" in
    FRESH) : ;;                                    # normal, up-to-date run
    SERVICEABLE)
        DEGRADED=1
        echo "WARNING: DEGRADED RUN — live fetch failed; running on the last serviceable snapshot. Engines + GESH signals refresh on last-known holdings; cash/sizing figures are stale and the customer-report publish is skipped." ;;
    *)  echo "FATAL: no serviceable snapshot (state='${SNAP_STATE:-none}', limit ${SERVICEABLE_MAX_MIN} min) — aborting"
        exit 1 ;;
esac

# Degraded provenance threshold (non-fatal): independent of the SERVICEABLE
# limit above, any snapshot older than GESH_DEGRADED_AFTER_S (default 3600s)
# makes the run RESEARCH-ONLY — engines + AI still refresh, but step 4/5 (paper
# gate) is skipped and run_engines2/run_ai2 stamp {degraded,
# snapshot_age_seconds, snapshot_source} into engine_results.json. The exported
# GESH_DEGRADED flag is run_paper_gate2's belt-and-braces entry guard.
DEGRADED_AFTER_S=${GESH_DEGRADED_AFTER_S:-3600}
SNAP_AGE_S=$("$PY" - <<'PYEOF'
import json
from datetime import datetime, timezone
try:
    with open('live_portfolio_snapshot.json') as fh:
        snap = json.load(fh)
    age = datetime.now(timezone.utc) - datetime.fromisoformat(snap['fetched_at'])
    print(int(age.total_seconds()))
except Exception:
    print(-1)   # unreadable age counts as degraded (fail-safe)
PYEOF
)
if [ "$SNAP_AGE_S" -lt 0 ] || [ "$SNAP_AGE_S" -gt "$DEGRADED_AFTER_S" ]; then
    DEGRADED=1
fi
export GESH_DEGRADED="$DEGRADED"
export GESH_RUN_DEGRADED="$DEGRADED"
export GESH_DEGRADED_AFTER_S="$DEGRADED_AFTER_S"
if [ "$DEGRADED" -eq 1 ]; then
    echo "### DEGRADED RUN (RESEARCH-ONLY) — snapshot age ${SNAP_AGE_S}s (limit ${DEGRADED_AFTER_S}s, state $SNAP_STATE). Paper gate will be SKIPPED; report carries a degraded banner."
fi

# ── 2/5 engines (fatal — everything downstream needs engine_results.json)
step "2/5 run_engines2"
"$PY" run_engines2.py || { echo "FATAL: engines failed"; tg_alert "FATAL step 2/5: run_engines2 failed — see /tmp/gesh_fullchain.log"; exit 1; }

# ── 3/5 AI analysis + DB save (fatal — the report needs ai_result; stopping
# here keeps yesterday's good gesh_report.html in place instead of overwriting
# it with empty AI sections)
step "3/5 run_ai2"
"$PY" run_ai2.py || { echo "FATAL: run_ai2 failed — keeping previous report"; tg_alert "FATAL step 3/5: run_ai2 failed — previous report kept, see /tmp/gesh_fullchain.log"; exit 1; }

# ── 4/5 paper session + risk-gate audit chain (non-fatal by design; a degraded
# run is research-only — skipped here, and run_paper_gate2's own entry guard
# double-checks via GESH_DEGRADED + the engine_results provenance stamp)
step "4/5 run_paper_gate2"
if [ "$DEGRADED" -eq 1 ]; then
    echo "### DEGRADED RUN — SKIPPING 4/5 paper gate (research-only: no paper records on a stale snapshot)"
else
    "$PY" run_paper_gate2.py || echo "WARN: paper-gate step failed (non-fatal)"
fi

# ── 5/5 HTML report (template fill, <1s)
step "5/5 gen_report2"
REPORT_FILE="gesh_report.html"
if [ "$DEGRADED" -eq 1 ]; then
    RUN_ID=$("$PY" -c 'import json; print(json.load(open("run_manifest.json"))["run_id"])')
    REPORT_FILE="gesh_report.degraded.${RUN_ID}.html"
    export GESH_REPORT_OUTPUT="$GP/$REPORT_FILE"
    echo "### DEGRADED RUN — writing research artifact $REPORT_FILE; current report remains untouched"
else
    unset GESH_REPORT_OUTPUT
fi
"$PY" gen_report2.py || FAIL=1

# ── Assertions — catch silently-degraded runs (e.g. half-size hand-built report)
SIZE=$(stat -f%z "$REPORT_FILE" 2>/dev/null || echo 0)
if [ "$SIZE" -lt 60000 ]; then
    echo "ASSERT FAIL: $REPORT_FILE is ${SIZE}B (<60KB) — report degraded"
    FAIL=1
else
    echo "assert ok: $REPORT_FILE ${SIZE}B"
fi
"$PY" - <<'PYEOF' || { echo "ASSERT FAIL: no analysis_runs row for today"; FAIL=1; }
import sqlite3, sys
from datetime import datetime, timezone
con = sqlite3.connect('gesh_financial.db')
today = datetime.now(timezone.utc).date().isoformat()
n = con.execute("SELECT COUNT(*) FROM analysis_runs WHERE run_at LIKE ?", (today + '%',)).fetchone()[0]
print(f"assert ok: {n} analysis_runs row(s) for {today}" if n else "no row today")
sys.exit(0 if n > 0 else 1)
PYEOF

# ── Publish report copy for the trading repo (skill contract) — only on clean
# runs, so a failed/degraded run never overwrites the last good published copy.
if [ "$FAIL" -eq 0 ] && [ "$DEGRADED" -eq 0 ]; then
    cp -f gesh_report.html "$GPT/gesh_report.html" 2>/dev/null || true
else
    echo "publish skipped (fail=$FAIL degraded=$DEGRADED) — keeping previous report copy in $GPT"
fi

echo ""
echo "### CHAIN DONE — $(date '+%Y-%m-%d %H:%M:%S %Z') (total $(($(date +%s)-T0))s, fail=$FAIL degraded=$DEGRADED)"
[ "$FAIL" -ne 0 ] && tg_alert "chain finished with fail=1 (report generation or asserts failed) — see /tmp/gesh_fullchain.log"
exit "$FAIL"
