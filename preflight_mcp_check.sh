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
    # Keep credentials and message content out of curl's process arguments.
    printf 'url = "%s"\ndata-urlencode = "chat_id=%s"\ndata-urlencode = "text=%s"\n' \
        "https://api.telegram.org/bot${tok}/sendMessage" \
        "$chat" "⚠️ GESH pre-flight — ${msg} [$(date '+%Y-%m-%d %H:%M %Z')]" |
        curl -sS -m 10 --config - \
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

# ── Check 1: `claude mcp list` (cheap, but NOT authoritative — see Check 2) ───
LINE="$("$CLAUDE_BIN" mcp list 2>&1 | grep -i 'robinhood-trading' || true)"
echo "mcp line: ${LINE:-<absent>}"

if echo "$LINE" | grep -q 'Connected'; then
    echo "mcp list says: connected"
else
    tg_alert "robinhood-trading MCP is NOT connected (${LINE:-server missing}). Re-authorize before the 10:30 chain: claude mcp login robinhood-trading"
fi

# ── Check 2: HEADLESS probe — the authoritative one ───────────────────────────
# `claude mcp list` reports the CONFIGURED/health-checked state and can say
# "✔ Connected" while a headless `claude -p` run — which is what the chain
# actually uses — still has NO robinhood tools, because a rejected OAuth refresh
# token can only be renewed from an INTERACTIVE session. That false green is
# exactly how the 2026-07-12..14 outage stayed invisible for three days: the
# preflight was happy while every fetch silently returned UNAVAILABLE.
# So we ask a real headless session what tools it can actually see.
PROBE="$("$CLAUDE_BIN" -p 'If you have a tool whose name starts with mcp__robinhood, output exactly READY. Otherwise output exactly NONE. Output one word, nothing else.' \
    --allowedTools "mcp__robinhood-trading" \
    --permission-mode acceptEdits 2>&1 | tr -d '[:space:]')"
echo "headless probe: ${PROBE:-<empty>}"

if echo "$PROBE" | grep -q 'READY'; then
    echo "preflight OK — robinhood-trading usable from a headless run"
else
    tg_alert "robinhood-trading MCP is UNUSABLE from headless \`claude -p\` (the chain's snapshot path) even though mcp list may look connected — the OAuth refresh token was rejected. Fix BEFORE 10:30, from an interactive terminal:  claude mcp login robinhood-trading"
fi

# Bonus check: DNS to api.robinhood.com (catches the corporate-VPN block, rule #54).
if ! /usr/bin/nslookup -timeout=5 api.robinhood.com >/dev/null 2>&1; then
    tg_alert "api.robinhood.com does not resolve — VPN block? The legacy fallback will fail too."
fi
exit 0
