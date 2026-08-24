#!/usr/bin/env bash
# scripts/et_gate.sh — shared America/New_York window gate for scheduled scripts.
#
# WHY THIS EXISTS (rule #56)
# launchd evaluates `StartCalendarInterval` in HOST-LOCAL time. This host is
# Europe/Sofia, where the ET offset is 7 h on 337 days of 2026 and 6 h on the 28
# US/EU DST-mismatch days (Mar 8-28, Oct 25-31). A job pinned to a local wall
# clock is therefore wrong for part of every year, and converting the number once
# does not fix it — the offset itself moves.
#
# The fix is architectural: the plist fires a PERSISTENT cadence and makes no
# wall-clock claim, and the script decides — here, in ET — whether this
# invocation is its scheduled one.
#
# Usage:
#     source "$(dirname "$0")/et_gate.sh"
#     et_gate_require "15:45" 20 "mon-fri"   # 15:45 ET, +/-20 min, weekdays
#     # ... real work runs only if the gate returns
#
# A run inside the window on a day that already completed is skipped via a dated
# stamp file, so a 10-minute cadence executes the work exactly once per day.

set -uo pipefail

ET_GATE_STAMP_DIR="${ET_GATE_STAMP_DIR:-$HOME/.gesh/et_gate}"

# et_gate_now_et -> HH:MM in America/New_York
et_gate_now_et() { TZ=America/New_York date +%H:%M; }

# et_gate_today_et -> YYYY-MM-DD in America/New_York
et_gate_today_et() { TZ=America/New_York date +%F; }

# et_gate_dow_et -> 1..7 (Mon..Sun) in America/New_York
et_gate_dow_et() { TZ=America/New_York date +%u; }

_et_gate_minutes() { # "HH:MM" -> minutes since midnight
    local hh=${1%%:*} mm=${1##*:}
    echo $(( 10#$hh * 60 + 10#$mm ))
}

# et_gate_in_window TARGET_HHMM TOLERANCE_MIN [DAYSPEC]
#   Same window/day checks as et_gate_require, but does NOT write a stamp and
#   does NOT exit. Return 0 = in window; 1 = skip (prints the same reason line).
#   Use this when the caller may still need a later tick today (lock wait,
#   missing report) and must not burn the dated stamp yet.
et_gate_in_window() {
    local target="$1" tol="${2:-15}" dayspec="${3:-mon-fri}"
    local label="${ET_GATE_LABEL:-$(basename "${BASH_SOURCE[1]:-$0}")}"

    local dow; dow="$(et_gate_dow_et)"
    case "$dayspec" in
        mon-fri) [ "$dow" -le 5 ] || { echo "et_gate[$label]: $(et_gate_today_et) is a weekend in ET — skipping"; return 1; } ;;
        any)     : ;;
        [1-7])   [ "$dow" = "$dayspec" ] || { echo "et_gate[$label]: ET weekday $dow != $dayspec — skipping"; return 1; } ;;
        *)       echo "et_gate[$label]: bad dayspec '$dayspec'" >&2; return 1 ;;
    esac

    local now; now="$(et_gate_now_et)"
    local now_m tgt_m diff
    now_m=$(_et_gate_minutes "$now"); tgt_m=$(_et_gate_minutes "$target")
    diff=$(( now_m - tgt_m )); [ "$diff" -lt 0 ] && diff=$(( -diff ))
    if [ "$diff" -gt "$tol" ]; then
        echo "et_gate[$label]: ${now} ET is outside ${target} ET +/-${tol}min — skipping"
        return 1
    fi
    return 0
}

# et_gate_require TARGET_HHMM TOLERANCE_MIN [DAYSPEC]
#   DAYSPEC: "mon-fri" (default), "any", or a single digit 1..7
# Exits 0 (silently, without running the caller's work) when outside the window.
et_gate_require() {
    local target="$1" tol="${2:-15}" dayspec="${3:-mon-fri}"
    local label="${ET_GATE_LABEL:-$(basename "${BASH_SOURCE[1]:-$0}")}"

    et_gate_in_window "$target" "$tol" "$dayspec" || exit 0

    # Once-per-ET-day: the cadence fires repeatedly inside the tolerance window.
    mkdir -p "$ET_GATE_STAMP_DIR" 2>/dev/null || true
    local stamp="$ET_GATE_STAMP_DIR/${label}.$(et_gate_today_et)"
    if [ -e "$stamp" ]; then
        echo "et_gate[$label]: already ran for $(et_gate_today_et) ET — skipping"
        exit 0
    fi
    : > "$stamp"
    # Keep the directory bounded; stamps are worthless after a week.
    find "$ET_GATE_STAMP_DIR" -type f -mtime +7 -delete 2>/dev/null || true
    echo "et_gate[$label]: $(et_gate_now_et) ET is within ${target} ET +/-${tol}min — running"
}

# et_gate_file_is_today_et PATH [MIN_BYTES]
#   0 if PATH exists, is at least MIN_BYTES (default 0), and its mtime calendar
#   date in America/New_York is today. 1 otherwise. Does NOT write a stamp —
#   callers that need "retry until the 10:30 chain finishes" must check this
#   BEFORE et_gate_require, or a 10:30 tick inside a wide window burns the day.
et_gate_file_is_today_et() {
    local path="$1" min="${2:-0}"
    [ -n "$path" ] && [ -f "$path" ] || return 1
    python3 -c '
import os, sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

path, min_b = sys.argv[1], int(sys.argv[2])
try:
    st = os.stat(path)
except OSError:
    raise SystemExit(1)
if st.st_size < min_b:
    raise SystemExit(1)
et = ZoneInfo("America/New_York")
mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).astimezone(et).date()
today = datetime.now(et).date()
raise SystemExit(0 if mtime == today else 1)
' "$path" "$min"
}
