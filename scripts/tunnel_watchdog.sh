#!/bin/bash
# tunnel_watchdog.sh — revive the gesh-pay named tunnel when its edge connections die.
#
# Failure mode (observed 2026-07-02→03, repeatedly): the corporate VPN's DNS
# intermittently black-holes argotunnel.com lookups; cloudflared keeps running but
# holds ZERO edge connections, so pay.geshfinance.com (LS billing webhooks!) goes
# dark until a manual `launchctl kickstart`.
#
# HEALTH SIGNAL — cloudflared's LOCAL metrics /ready endpoint (127.0.0.1:41900,
# pinned in com.gesh.cloudflared.plist via --metrics). It reports readyConnections
# with NO external network, so it stays truthful even while the VPN is choking
# DNS/CF-API. This fixes the prior watchdog's blind spot: the old version hit the
# Cloudflare API, which the same VPN choke made time out, so it logged SKIP and did
# nothing exactly when the tunnel was down (2026-07-03 16:14).
#
# Decision: readyConnections>0 => healthy. readyConnections==0 OR /ready
# unreachable => the tunnel is dark => kickstart cloudflared. Scheduled every 5 min.
set -u
READY_URL="http://127.0.0.1:41900/ready"
LOG="/tmp/gesh_tunnel_watchdog.log"
ts() { date "+%Y-%m-%dT%H:%M:%S"; }

BODY="$(curl -s --max-time 4 "$READY_URL" 2>/dev/null)"
CONNS="$(printf '%s' "$BODY" | grep -oE '"readyConnections":[0-9]+' | grep -oE '[0-9]+$')"

if [ -n "${CONNS:-}" ] && [ "$CONNS" -gt 0 ]; then
  echo "$(ts) OK: readyConnections=$CONNS" >> "$LOG"
  exit 0
fi

# Either /ready was unreachable (metrics server not up yet / cloudflared wedged)
# or it reported 0 connections. Both mean the tunnel is not serving — revive it.
reason="readyConnections=${CONNS:-unreachable}"
echo "$(ts) DEAD: $reason — kickstarting com.gesh.cloudflared" >> "$LOG"
launchctl kickstart -k "gui/$(id -u)/com.gesh.cloudflared" >> "$LOG" 2>&1

# Give it a moment, then confirm recovery for the log/alert.
sleep 10
BODY2="$(curl -s --max-time 4 "$READY_URL" 2>/dev/null)"
CONNS2="$(printf '%s' "$BODY2" | grep -oE '"readyConnections":[0-9]+' | grep -oE '[0-9]+$')"
echo "$(ts) after kickstart: readyConnections=${CONNS2:-unreachable}" >> "$LOG"

# Telegram alert (best-effort; same env the other alert paths use).
ENV_FILE="$(dirname "$0")/../.env"
if [ -f "$ENV_FILE" ]; then
  TOKEN=$(grep -E '^TELEGRAM_BOT_TOKEN=' "$ENV_FILE" | head -1 | cut -d= -f2-)
  CHAT=$(grep -E '^TELEGRAM_CHAT_ID=' "$ENV_FILE" | head -1 | cut -d= -f2-)
  if [ -n "${TOKEN:-}" ] && [ -n "${CHAT:-}" ]; then
    curl -s --max-time 10 "https://api.telegram.org/bot${TOKEN}/sendMessage" \
      -d chat_id="${CHAT}" \
      -d text="⚠️ gesh-pay tunnel was dark ($reason) — watchdog kickstarted cloudflared; now readyConnections=${CONNS2:-unreachable} (billing webhooks)." \
      >> "$LOG" 2>&1 || true
  fi
fi
exit 0
