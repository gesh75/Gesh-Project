#!/bin/bash
# run_signal_alerts.sh — daily signal-change alert job (retention feature).
#
# Snapshots the customer symbol universe into signal_history and emails each
# affected customer ONE digest of their confirmed (persistence-gated) verdict
# flips. LLM-free, idempotent (alert_log). Scheduled 11:05 ET Mon-Fri via the
# com.gesh.signal-alerts LaunchAgent, after the 10:30 daily-analysis chain.
#
# Root is derived from this script's own location (no hardcoded /Users path).
set -euo pipefail
cd "$(dirname "$0")"
exec ./venv/bin/python3 run_signal_alerts.py
