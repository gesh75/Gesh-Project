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

# et_gate_require TARGET_HHMM TOLERANCE_MIN [DAYSPEC]
#   DAYSPEC: "mon-fri" (default), "any", or a single digit 1..7
# Exits 0 (silently, without running the caller's work) when outside the window.
et_gate_require() {
    local target="$1" tol="${2:-15}" dayspec="${3:-mon-fri}"
    local label="${ET_GATE_LABEL:-$(basename "${BASH_SOURCE[1]:-$0}")}"

    local dow; dow="$(et_gate_dow_et)"
    case "$dayspec" in
        mon-fri) [ "$dow" -le 5 ] || { echo "et_gate[$label]: $(et_gate_today_et) is a weekend in ET — skipping"; exit 0; } ;;
        any)     : ;;
        [1-7])   [ "$dow" = "$dayspec" ] || { echo "et_gate[$label]: ET weekday $dow != $dayspec — skipping"; exit 0; } ;;
        *)       echo "et_gate[$label]: bad dayspec '$dayspec'" >&2; exit 1 ;;
    esac

    local now; now="$(et_gate_now_et)"
    local now_m tgt_m diff
    now_m=$(_et_gate_minutes "$now"); tgt_m=$(_et_gate_minutes "$target")
    diff=$(( now_m - tgt_m )); [ "$diff" -lt 0 ] && diff=$(( -diff ))
    if [ "$diff" -gt "$tol" ]; then
        echo "et_gate[$label]: ${now} ET is outside ${target} ET +/-${tol}min — skipping"
        exit 0
    fi

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
    echo "et_gate[$label]: ${now} ET is within ${target} ET +/-${tol}min — running"
}
