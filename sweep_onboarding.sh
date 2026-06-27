#!/bin/bash
# sweep_onboarding.sh — async onboarding safety net (M1).
#
# Re-sends the sign-in magic link to any ACTIVE subscriber still owed one
# (welcome_emailed_at IS NULL) so onboarding delivery no longer depends solely on
# Lemon Squeezy's 4-attempt webhook retry budget. Idempotent — stamps delivery only
# on success, never double-emails. Scheduled every 15 min via the
# com.gesh.onboarding-sweep LaunchAgent.
#
# Root is derived from this script's own location (no hardcoded /Users path).
set -euo pipefail
cd "$(dirname "$0")"
exec ./venv/bin/python billing.py sweep-onboarding
