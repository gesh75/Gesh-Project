#!/bin/bash
# preflight_mcp_check.sh — Improvement #5 (2026-07-10)
# Runs ~1h before the daily chain (launchd com.gesh.mcp-preflight, weekdays 09:30
# local — the chain fires 10:30 local). Checks that the robinhood-trading MCP is
# authorized/connected so an expired OAuth token is caught and alerted BEFORE the
# 10:30 chain burns its run on it (the 2026-07-09/10 two-day outage mode).
# Read-only: never fetches data, never triggers the chain.
set -u
# Rule #55: launchd PATH omits Homebrew — claude lives in /opt/homebrew/bin.
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
# Rule #56: claude keeps its OAuth token in the macOS Keychain and needs USER set
# to read it. Without USER this check REPORTS A FALSE "not connected" and pages
# Georgi for an outage that isn't real (and the chain's own fetch fails for the
# same reason). Verified 2026-07-14. Keep in sync with fetch_snapshot_mcp.sh.
export USER="${USER:-$(id -un)}"

GP="$(cd "$(dirname "$0")" && pwd)"
GPT="${GESH_TRADING_HOME:-$(dirname "$GP")/gesh-project-trading}"
LOG=/tmp/gesh_preflight.log
exec >>"$LOG" 2>&1

tg_alert() {
    local msg="$1" tok="" chat="" envf
    for envf in "$GPT/.env" "$GP/.env"; do
        [ -z "$tok" ]  && tok=$(grep -m1 '^TELEGRAM_BOT_TOKEN=' "$envf" 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'")
        [ -z "$chat" ] && chat=$(grep -m1 '^TELEGRAM_CHAT_ID='  "$envf" 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'")
    done
    { [ -z "$tok" ] || [ -z "$chat" ]; } && { echo "[preflight] no telegram creds — alert NOT sent: $msg"; return 0; }
    curl -sS -m 10 "https://api.telegram.org/bot${tok}/sendMessage" \
        --data-urlencode "chat_id=${chat}" \
        --data-urlencode "text=⚠️ GESH pre-flight — ${msg} [$(date '+%Y-%m-%d %H:%M %Z')]" \
        >/dev/null 2>&1 && echo "[preflight] alert sent" || echo "[preflight] alert send failed"
    return 0
}

echo ""
echo "### PREFLIGHT — $(date '+%Y-%m-%d %H:%M:%S %Z')"

CLAUDE_BIN="$(command -v claude || echo /opt/homebrew/bin/claude)"
if [ ! -x "$CLAUDE_BIN" ]; then
    echo "claude CLI not found"
    tg_alert "claude CLI not found on PATH — the 10:30 chain's MCP snapshot will fail"
    exit 0
fi

# `claude mcp list` health-checks every server; grep our line.
LINE="$("$CLAUDE_BIN" mcp list 2>&1 | grep -i 'robinhood-trading' || true)"
echo "mcp line: ${LINE:-<absent>}"

if echo "$LINE" | grep -q 'Connected'; then
    echo "preflight OK — robinhood-trading MCP connected"
else
    tg_alert "robinhood-trading MCP is NOT connected (${LINE:-server missing}). Re-authorize it (open Claude app / claude /mcp) before the 10:30 chain, or the snapshot fetch will fail."
fi

# Bonus check: DNS to api.robinhood.com (catches the corporate-VPN block, rule #54).
if ! /usr/bin/nslookup -timeout=5 api.robinhood.com >/dev/null 2>&1; then
    tg_alert "api.robinhood.com does not resolve — VPN block? The legacy fallback will fail too."
fi
exit 0
