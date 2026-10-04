#!/bin/bash
# run_weekly_note.sh — send the next weekly learning note to confirmed subscribers.
# Ticked hourly by com.gesh.weekly-note; the SCRIPT decides (Tue-Thu 10:00-15:00
# America/New_York, one note per subscriber per 6 days, ready notes only), so the
# host timezone never matters. Dry-run unless --send is passed by the plist.
set -euo pipefail
cd "$(dirname "$0")"
exec ./venv/bin/python3 scripts/send_weekly_note.py "$@"
