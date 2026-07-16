#!/bin/bash
# run_weekly_digest.sh — weekly customer email digest (retention feature).
#
# Composes ENTIRELY from cached/DB state (signal_history, book valuation via the
# shared close cache, last persisted analysis) and emails one digest per active,
# non-opted-out customer. No LLM, fail-soft per customer. Scheduled Fri 17:15
# local via the com.gesh.weekly-digest LaunchAgent.
#
# Root is derived from this script's own location (no hardcoded /Users path).
set -euo pipefail
cd "$(dirname "$0")"
exec ./venv/bin/python3 run_weekly_digest.py
