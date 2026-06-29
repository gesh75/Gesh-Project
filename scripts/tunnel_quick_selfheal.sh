#!/bin/bash
# tunnel_quick_selfheal.sh — run the Cloudflare QUICK tunnel and auto-sync its
# rotating public URL into gesh-project/.env (APP_BASE_URL), restarting :9000 so
# the new base URL takes effect. A quick tunnel mints a NEW trycloudflare URL on
# every (re)start; without this, APP_BASE_URL silently goes stale after a reboot
# and the app's magic-link emails point at a dead host.
#
# Wired in via com.gesh.cloudflared-quick.plist (ProgramArguments → this script).
# Keeps cloudflared in the foreground of the pipeline so launchd KeepAlive still
# manages process lifetime; only rewrites .env + restarts :9000 when the URL
# ACTUALLY changes (no-op on a stable run, so no restart loop).
set -uo pipefail

CLOUDFLARED=/opt/homebrew/bin/cloudflared
ENV_FILE="$HOME/Projects/gesh-project/.env"
MAIN_PLIST="$HOME/Library/LaunchAgents/com.gesh.main.plist"
UID_NUM="$(id -u)"

sync_url() {
  local url="$1"
  [ -z "$url" ] && return 0
  [ -f "$ENV_FILE" ] || return 0
  local cur
  cur="$(grep -m1 '^APP_BASE_URL=' "$ENV_FILE" | cut -d= -f2-)"
  [ "$cur" = "$url" ] && return 0          # unchanged → do nothing (avoids restart loop)
  /usr/bin/sed -i '' "s|^APP_BASE_URL=.*|APP_BASE_URL=${url}|" "$ENV_FILE"
  echo "[selfheal $(date -u +%H:%M:%SZ)] APP_BASE_URL: ${cur:-<unset>} -> ${url}"
  # restart :9000 so the new base URL is loaded (magic-link emails use it)
  launchctl bootout "gui/${UID_NUM}/com.gesh.main" 2>/dev/null
  sleep 2
  launchctl bootstrap "gui/${UID_NUM}" "$MAIN_PLIST" 2>/dev/null
  echo "[selfheal $(date -u +%H:%M:%SZ)] :9000 restarted for new APP_BASE_URL"
}

# Run cloudflared; stream its output to stdout (→ the plist logfile) AND watch for
# the assigned URL. When cloudflared exits, the pipe closes, this script exits, and
# launchd KeepAlive restarts it.
"$CLOUDFLARED" tunnel --no-autoupdate --url http://localhost:9000 2>&1 \
  | while IFS= read -r line; do
      printf '%s\n' "$line"
      if [[ "$line" =~ (https://[a-z0-9-]+\.trycloudflare\.com) ]]; then
        sync_url "${BASH_REMATCH[1]}"
      fi
    done
