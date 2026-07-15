#!/bin/bash
# MCP-sourced snapshot fetch for the GESH daily chain.
# Replaces the robin_stocks pickle login (breaks on every RH password change) with the
# robinhood-trading MCP via `claude -p`. The LLM ONLY fetches raw data -> /tmp/gesh_mcp_raw.json;
# all money math is done deterministically by build_snapshot_from_mcp.py.
# Exit 0 = fresh live_portfolio_snapshot.json written; non-zero = caller should fall back.
set -uo pipefail

# ── launchd environment guard (rule #55 + #56) ────────────────────────────────
# #55: launchd's PATH omits Homebrew, where the claude CLI lives.
# #56: the claude CLI stores its OAuth token in the macOS KEYCHAIN (there is no
#      ~/.claude/.credentials.json). Reading it requires USER to be set — with
#      HOME+PATH alone, `claude mcp list` reports robinhood-trading as
#      "! Needs authentication" and `claude -p` silently writes no dump.
#      Verified 2026-07-14: HOME+PATH → Needs authentication;
#      HOME+PATH+USER → Connected (TMPDIR and LOGNAME make no difference).
#      This was the 2026-07-12..14 step-1/5 outage: the MCP path looked "expired"
#      but the token was fine — the process just couldn't reach the Keychain.
# Never assume the caller's env; derive both here.
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export USER="${USER:-$(id -un)}"

DIR="$HOME/Projects/gesh-project"
CLAUDE="/opt/homebrew/bin/claude"
ACCT="${GESH_RH_ACCOUNT:-557370533}"   # main portfolio account
RAW="/tmp/gesh_mcp_raw.json"
PY="${GESH_PY:-/usr/bin/python3}"

cd "$DIR" || exit 1
rm -f "$RAW"

PROMPT="Use the robinhood-trading MCP to fetch account ${ACCT} and write ONE JSON file to ${RAW} with the Write tool. Steps:
1) get_portfolio(account_number=\"${ACCT}\")
2) get_equity_positions(account_number=\"${ACCT}\")
3) get_equity_quotes(symbols=[every symbol from step 2])
Then Write ${RAW} as a single JSON object EXACTLY:
{\"fetched_account\":\"${ACCT}\",\"portfolio\":<the get_portfolio .data object>,\"positions\":<the get_equity_positions .data.positions array>,\"quotes\":<the get_equity_quotes .data.results array>}
Use the raw values exactly as the tools return them (do not round or recompute). If the robinhood-trading MCP is unavailable, do NOT write the file. Output only WROTE or UNAVAILABLE. Nothing else."

"$CLAUDE" -p "$PROMPT" \
  --allowedTools "mcp__robinhood-trading" "Write" \
  --permission-mode acceptEdits >/tmp/gesh_mcp_fetch.log 2>&1

if [ ! -f "$RAW" ]; then
  echo "[fetch_snapshot_mcp] no raw dump produced (MCP unavailable / token expired)"
  exit 1
fi

"$PY" "$DIR/build_snapshot_from_mcp.py" || { echo "[fetch_snapshot_mcp] build step failed"; exit 1; }
exit 0
