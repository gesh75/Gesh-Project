#!/usr/bin/env bash
# scripts/daily_analysis_et.sh — ET-gated wrapper for the daily analysis chain.
#
# WHY A WRAPPER RATHER THAN AN EDIT TO run_daily_analysis.sh
# The chain script is long-lived, carries its own lock/logging/Telegram contract,
# and is under active edit. Gating belongs at the schedule boundary, not inside
# the orchestrator, so the chain stays independently runnable by hand at any hour
# (`bash run_daily_analysis.sh`) while the SCHEDULED path is ET-correct.
#
# WHAT THIS FIXES
# launchd evaluates `StartCalendarInterval` in HOST-LOCAL time. This host is
# Europe/Sofia, so `Hour 10, Minute 30` fired at **03:30 ET** — the chain ran
# against a market that had been closed for 10 hours, with the MARKET_HOURS gate
# rejecting every paper order. The intent is 10:30 ET.
#
# Converting once would not fix it: the ET offset is 7 h on 337 days of 2026 and
# 6 h on the 28 US/EU DST-mismatch days (Mar 8-28, Oct 25-31), so a pinned Sofia
# time drifts by an hour twice a year. The plist now fires a persistent cadence
# with no wall-clock claim and the ET decision is made here.
#
# The gate is once-per-ET-day idempotent, so the 10-minute cadence runs the chain
# exactly once even though it wakes ~6 times inside the tolerance window.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

# Rule #55: launchd runs jobs with a minimal PATH that omits Homebrew.
#
# $HOME/.local/bin is load-bearing, not cosmetic: the Grok CLI installs there, and
# llm_failover.grok_available() resolves it with a bare shutil.which("grok"). Without
# this entry the LLM failover's PRIMARY leg is invisible to every scheduled run —
# grok_available() returns False, the chain skips silently to Gemini, and nothing in
# the log says the preferred provider was never even attempted. Verified 2026-08-21:
#   env -i PATH="<the old value>" sh -c 'command -v grok'  → not found
#   interactive                                            → /Users/…/.local/bin/grok
# Every prior verification of the Grok path was done interactively, where it IS on
# PATH — which is exactly why this survived. Rule #55, one level deeper.
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

ET_GATE_LABEL=daily-analysis
# shellcheck source=scripts/et_gate.sh
source "$REPO/scripts/et_gate.sh"

# 10:30 ET, +/-20 min, weekdays. Overridable for verification without editing.
#
# Stamp ONLY after a successful chain. Stamping at the start of et_gate_require
# (the old path) burned the day on a failed step 3/5: 2026-08-26 10:16 FATAL
# kept yesterday's HTML, and the 10:45 publisher waited 720s on a Grok failover
# lock then fell outside ±12 min with no retry.
et_gate_in_window "${DAILY_ANALYSIS_ET:-10:30}" "${DAILY_ANALYSIS_TOL:-20}" mon-fri || exit 0
if et_gate_already_stamped; then
    echo "et_gate[$ET_GATE_LABEL]: already ran for $(et_gate_today_et) ET — skipping"
    exit 0
fi
echo "et_gate[$ET_GATE_LABEL]: $(et_gate_now_et) ET is within ${DAILY_ANALYSIS_ET:-10:30} ET +/-${DAILY_ANALYSIS_TOL:-20}min — running"

et_gate_export_chain_llm
export GESH_ET_GATE_STAMP="$(et_gate_stamp_path)"

cd "$REPO" || { echo "FATAL: cannot cd $REPO" >&2; exit 1; }
exec /bin/bash "$REPO/run_daily_analysis.sh"
