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

set -u
GP=/Users/georgigaydarov/Projects/gesh-project
GPT=/Users/georgigaydarov/Projects/gesh-project-trading
PY="$GP/venv/bin/python3.14"
LOG=/tmp/gesh_fullchain.log
LOCK=/tmp/gesh_daily_chain.lock

exec >>"$LOG" 2>&1

# Stale-lock recovery: a kill -9 / power loss skips the EXIT trap, and a
# permanently-held lock would silently skip every future run. A real chain run
# finishes in ~4 min, so any lock older than 30 min is garbage — reclaim it.
if [ -d "$LOCK" ]; then
    LOCK_AGE=$(( $(date +%s) - $(stat -f %m "$LOCK" 2>/dev/null || echo 0) ))
    if [ "$LOCK_AGE" -gt 1800 ]; then
        echo "### STALE LOCK reclaimed (age ${LOCK_AGE}s) — $(date '+%Y-%m-%d %H:%M:%S %Z')"
        rmdir "$LOCK" 2>/dev/null
    fi
fi
if ! mkdir "$LOCK" 2>/dev/null; then
    echo "### CHAIN SKIPPED (lock held) — $(date '+%Y-%m-%d %H:%M:%S %Z')"
    exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

cd "$GP" || exit 1
T0=$(date +%s)
FAIL=0
step() { echo ""; echo "=== $1 — $(date '+%H:%M:%S') (t+$(($(date +%s)-T0))s) ==="; }

echo ""
echo "### FULL 10:30 CHAIN — $(date '+%Y-%m-%d %H:%M:%S %Z')"

# ── 1/5 live snapshot (fatal on failure or staleness — never analyze stale data)
step "1/5 fetch_live_snapshot"
"$PY" fetch_live_snapshot.py || { echo "FATAL: snapshot fetch failed"; exit 1; }
"$PY" - <<'PYEOF' || { echo "FATAL: snapshot stale (>30 min) — aborting"; exit 1; }
import json, sys
from datetime import datetime, timezone
d = json.load(open('live_portfolio_snapshot.json'))
age = (datetime.now(timezone.utc) - datetime.fromisoformat(d['fetched_at'])).total_seconds() / 60
print(f"snapshot age: {age:.1f} min  holdings: {len(d['holdings'])}")
sys.exit(0 if age <= 30 else 1)
PYEOF

# ── 2/5 engines (fatal — everything downstream needs engine_results.json)
step "2/5 run_engines2"
"$PY" run_engines2.py || { echo "FATAL: engines failed"; exit 1; }

# ── 3/5 AI analysis + DB save (fatal — the report needs ai_result)
step "3/5 run_ai2"
"$PY" run_ai2.py || FAIL=1

# ── 4/5 paper session + risk-gate audit chain (non-fatal by design)
step "4/5 run_paper_gate2"
"$PY" run_paper_gate2.py || echo "WARN: paper-gate step failed (non-fatal)"

# ── 5/5 HTML report (template fill, <1s)
step "5/5 gen_report2"
"$PY" gen_report2.py || FAIL=1

# ── Assertions — catch silently-degraded runs (e.g. half-size hand-built report)
SIZE=$(stat -f%z gesh_report.html 2>/dev/null || echo 0)
if [ "$SIZE" -lt 60000 ]; then
    echo "ASSERT FAIL: gesh_report.html is ${SIZE}B (<60KB) — report degraded"
    FAIL=1
else
    echo "assert ok: gesh_report.html ${SIZE}B"
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

# ── Publish report copy for the trading repo (skill contract)
cp -f gesh_report.html "$GPT/gesh_report.html" 2>/dev/null || true

echo ""
echo "### CHAIN DONE — $(date '+%Y-%m-%d %H:%M:%S %Z') (total $(($(date +%s)-T0))s, fail=$FAIL)"
exit "$FAIL"
