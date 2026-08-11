#!/bin/bash
# record_equity_snapshots.sh — the customer Equity Curve Gate's data feed.
#
# customer_equity_history.record_snapshots_for_all_customers() values every active
# customer's book (customer_positions) at public closes and appends one observation
# per holding to customer_equity_snapshots in gesh_auth.db. Until this wrapper
# existed the function had no caller, so the customer "Book Health" panel read
# 0 snapshots forever and every holding sat at INSUFFICIENT_HISTORY.
#
# Scope: CUSTOMER data only. The owner is excluded inside record_snapshot() by
# construction (their curve already lives in the owner-only equity_snapshots
# table); nothing here reads that table.
#
# Idempotent by interval: a user whose newest observation is younger than
# --min-interval (6h default) is a cheap no-op, so an extra tick costs nothing and
# cannot inflate the snapshot count that ECG reads as trading sessions.
#
# Root is derived from this script's own location (no hardcoded /Users path), the
# same convention as sweep_onboarding.sh. GESH_PY overrides the interpreter.
# Output goes to stdout/stderr; the LaunchAgent redirects it to
# /tmp/gesh_equity_snapshots.log (see com.gesh.equity-snapshots.plist.template).
set -euo pipefail
cd "$(dirname "$0")"

echo "═══ customer equity snapshots — $(TZ=America/New_York date '+%Y-%m-%d %H:%M:%S %Z') ═══"
exec "${GESH_PY:-./venv/bin/python}" customer_equity_history.py record-snapshots "$@"
