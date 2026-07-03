#!/bin/bash
# tunnel_watchdog.sh — revive the gesh-pay named tunnel when its edge connections die.
#
# Failure mode (observed 2026-07-02→03): the corporate VPN's DNS intermittently
# black-holes argotunnel.com lookups; cloudflared keeps running but holds ZERO edge
# connections, so pay.geshfinance.com (LS billing webhooks!) goes dark until a manual
# `launchctl kickstart`. This watchdog automates that kickstart.
#
# Health signal: `cloudflared tunnel info` asks the CLOUDFLARE API how many live
# connectors the tunnel has — authoritative and independent of local DNS state
# (the local log shows benign "Failed to refresh DNS local resolver" errors even
# while healthy, so log-grepping is NOT a reliable signal).
#
# Scheduled by LaunchAgent com.gesh.tunnel-watchdog (every 10 min).
set -u
TUNNEL="gesh-pay"
LOG="/tmp/gesh_tunnel_watchdog.log"
ts() { date "+%Y-%m-%dT%H:%M:%S"; }

CLOUDFLARED="$(command -v cloudflared || echo /opt/homebrew/bin/cloudflared)"
INFO="$("$CLOUDFLARED" tunnel info "$TUNNEL" 2>&1)"
if [ $? -ne 0 ]; then
  # CF API unreachable (offline laptop, etc.) — can't judge health; do nothing.
  echo "$(ts) SKIP: tunnel info failed: $(echo "$INFO" | head -1)" >> "$LOG"
  exit 0
fi

# `tunnel info` lists one row per live connection (CONNECTOR ID / edge location).
CONNS=$(echo "$INFO" | grep -cE "^[0-9a-f]{8}-" )
if [ "$CONNS" -gt 0 ]; then
  echo "$(ts) OK: $CONNS connection row(s)" >> "$LOG"
  exit 0
fi

echo "$(ts) DEAD: 0 edge connections — kickstarting com.gesh.cloudflared" >> "$LOG"
launchctl kickstart -k "gui/$(id -u)/com.gesh.cloudflared" >> "$LOG" 2>&1

# Telegram alert (best-effort; same env the other alert paths use).
ENV_FILE="$(dirname "$0")/../.env"
if [ -f "$ENV_FILE" ]; then
  TOKEN=$(grep -E '^TELEGRAM_BOT_TOKEN=' "$ENV_FILE" | head -1 | cut -d= -f2-)
  CHAT=$(grep -E '^TELEGRAM_CHAT_ID=' "$ENV_FILE" | head -1 | cut -d= -f2-)
  if [ -n "${TOKEN:-}" ] && [ -n "${CHAT:-}" ]; then
    curl -s --max-time 10 "https://api.telegram.org/bot${TOKEN}/sendMessage" \
      -d chat_id="${CHAT}" \
      -d text="⚠️ gesh-pay tunnel had 0 edge connections — watchdog kickstarted cloudflared (billing webhooks were dark)." \
      >> "$LOG" 2>&1 || true
  fi
fi
exit 0
