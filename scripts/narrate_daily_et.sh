#!/usr/bin/env bash
# narrate_daily_et.sh — Grok last-mile for the Cowork "Gesh daily portfolio analysis" task.
#
# com.gesh.daily-analysis already ran run_daily_analysis.sh at 10:30 ET.
# This job (10:50 ET weekdays) reads engine_results.json + gesh_report.html
# and asks Grok for the briefing. Best-effort: Grok failure must not fail
# launchd — layer 1 already produced the report.
set -uo pipefail

export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
LOG=/tmp/gesh_daily_narrate.log
TRADING="${GESH_TRADING_HOME:-$HOME/Projects/gesh-project-trading}"

ET_GATE_LABEL=daily-narrate
# shellcheck source=../../gesh-project-trading/scripts/et_gate.sh
source "$TRADING/scripts/et_gate.sh"
et_gate_require "10:50" 20 "mon-fri"

_tg() {
    local msg="$1" tok="" chat="" envf
    for envf in "$TRADING/.env" "$REPO/.env"; do
        [ -f "$envf" ] || continue
        [ -z "$tok" ]  && tok=$(grep -m1 '^TELEGRAM_BOT_TOKEN=' "$envf" 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'")
        [ -z "$chat" ] && chat=$(grep -m1 '^TELEGRAM_CHAT_ID='  "$envf" 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'")
    done
    [ -n "$tok" ] && [ -n "$chat" ] || { echo "[tg] no credentials"; return 0; }
    printf 'url = "%s"\ndata-urlencode = "chat_id=%s"\ndata-urlencode = "text=%s"\n' \
        "https://api.telegram.org/bot${tok}/sendMessage" "$chat" "$msg" |
        curl -sS -m 10 --config - >/dev/null 2>&1 || true
}

umask 077
{
    echo "═══ daily narrate — $(TZ=America/New_York date '+%Y-%m-%d %H:%M:%S %Z') ═══"
    cd "$REPO" || exit 0
    if ! command -v grok >/dev/null 2>&1; then
        echo "grok CLI not on PATH — skipping narration"
        exit 0
    fi
    today_et=$(TZ=America/New_York date +%F)
    report="$REPO/gesh_report.html"
    eng="$REPO/engine_results.json"
    if [ ! -f "$eng" ]; then
        echo "no engine_results.json — skipping"
        exit 0
    fi
    mtime=$(stat -f %Sm -t %Y-%m-%d "$eng" 2>/dev/null || true)
    # file mtime is host-local; accept today or yesterday (late coalesced run)
    echo "engine_results mtime_local=$mtime today_et=$today_et"
    prompt=$(mktemp -t gesh-daily-prompt)
    {
        echo "Read $eng and, if present, $report."
        echo "Narrate today's GESH briefing. FIRST LINE: reconcile every executable action_plan row (BUY/TRIM shares>0) against gate_results. If paper_session.error is set or any executable row has no matching result, say GATE FAILED — NOT EVALUATED."
        echo "Then: headline (equity, day change, scores, VaR), action-plan table, conflicts, watch items, paper/gate counts."
        echo "Cash = equity_summary.deployable. NOT FINANCIAL ADVICE."
        echo "Do not write files. Do not run commands. Do not call MCP or memory."
    } >"$prompt"
    out=$(mktemp -t gesh-daily-out)
    grok --prompt-file "$prompt" --verbatim --output-format json \
         --max-turns 4 --tools read_file \
         --rules "Read only the files named in the prompt. Do not write, do not run commands, do not call MCP or memory. Reply with the briefing only." \
         --no-auto-update --model grok-4.6 \
         >"$out" 2>"$out.err" || {
        echo "grok failed: $(head -c 400 "$out.err")"
        rm -f "$prompt" "$out" "$out.err"
        exit 0
    }
    text=$(/opt/homebrew/bin/python3 -c 'import json,sys; d=json.load(open(sys.argv[1]));
print(d.get("text") or d.get("result") or d.get("response") or "")' "$out" 2>/dev/null || true)
    if [ -z "$text" ]; then text=$(cat "$out"); fi
    echo "$text"
    _tg "📋 GESH daily
$text"
    rm -f "$prompt" "$out" "$out.err"
    exit 0
} >>"$LOG" 2>&1
