# Active Context

_Last updated: 2026-07-02_

## Current focus (2026-07-02) — money-path audit remediation, complete

Full-system quant audit of the money path (price → consensus → Kelly → gate →
order → stop → close) followed by remediation of every accepted finding.
All code changes landed UNCOMMITTED on `main` (commit not requested yet).
Suites green: product 361, gesh-core 168, trading-repo floor tests 11.

Key behavior changes to know when operating:
- **Paper SELLs can now close**: `trading/execution._estimate_qty` sizes from
  the PAPER book (`state.paper_open_positions()`, reconstructed from SIMULATED
  fills) in paper mode — no longer from real RH holdings.
- **Stops are real now**: paper BUY fills persist stop/TP (`paper_stops` table,
  `risk_bands.exit_bands` MEDIUM); `close_risk_check` sweeps the paper book and
  emits VALID full-close SELLs (`target_position_pct=0.0`, source=`stop_sweep`).
- **Entry-conviction floor ported from the engine**: BUY candidates need
  `raw_score ≥ cfg.min_entry_score` (0.35, env `MIN_ENTRY_SCORE`) at 08:30 AND
  09:35. `from_consensus` confidence stays participation-only (engine parity —
  a conviction-blended confidence broke the engine's floor tests; reverted).
- **Screener runs on Haiku** by default (`CLAUDE_SCREENER_MODEL` to override);
  optional `LLM_DAILY_BUDGET_USD` hard cap; usage → `llm_usage.jsonl`.
- Autopilot candidates survive restarts (job marker `premarket_candidates`).

## Previous focus (2026-05-28) — security remediation, complete
Security remediation of the GESH platform — **complete**. Full audit run against
`gesh-project` (port 9000, product dashboard) plus a follow-on fix in
`gesh-project-trading` (port 9003, trading engine). Both services restarted onto
the new code; Telegram alerts restored.

**2026-05-29 follow-up — complete:** purged the dead Telegram token from git
history of both repos (`filter-repo`, force-pushed); recovered a `crypto_data.py`
edit that `filter-repo --force` wiped, reconstructing it byte-for-byte from the
9003 service's cached `.pyc`. Both committed/pushed; 9003 restarted (PID 38854).

## Key decisions this session
- **API-key auth** on all mutating endpoints (`/api/config`, `/api/trading/*`,
  `/api/trades/*`, `/api/gemini/*`). Header `X-API-Key`, constant-time compare,
  **fail-closed**: if `GESH_API_KEY` is unset the endpoint returns 503.
- Dashboard sends the key from **browser localStorage** (prompted once) — never
  embedded in page source (the `GET /` page is itself unauthenticated).
- **Generic 500s**: every `detail=str(e)` replaced with `_server_error()` which
  logs server-side and returns `{"detail":"Internal server error"}`.
- **Risk gate hardened to fail-closed**: DATA_FRESH rejects when no `as_of`
  timestamp; `build_data_payload` now stamps `as_of`. Market-hours parse errors
  also fail closed. ORDER_RATE no longer counts REJECTED orders.
- **Dependency-free rate limiting** (per-IP, 300/min general, 30/min heavy AI).
- Idempotency key dropped the random uuid + buckets to the minute (retry-safe).
- Trading `dry-run` wrapped so `build_data_payload`/`evaluate` failures return
  JSON, not plain-text "Internal Server Error".

## Live state / how to operate
- Both services are launchd-managed. They run **stale in-memory code/sessions**
  if left running for days — restart after any code or `.env` change:
  - 9000: `launchctl bootout/bootstrap gui/$(id -u)/com.gesh.main`
  - 9003: `com.gesh.trading`; alerts: `com.gesh.trading.autopilot`
- `GESH_API_KEY` lives in `gesh-project/.env` AND is pasted once into the
  dashboard prompt at http://localhost:9000.
- Telegram token lives in `gesh-project-trading/.env` (the autopilot sends
  alerts, not the 9000 dashboard).

## Manual follow-ups still owned by Georgi
- (done) Telegram token rotated via @BotFather; the malformed paste
  (`oldBotID:newBotID:secret`, two colons) was auto-repaired in
  `gesh-project-trading/.env`.
- (done 2026-05-29) Old revoked token scrubbed from **git history** of both
  repos via `filter-repo` + force-push; stale remote branch deleted.
- None open.
