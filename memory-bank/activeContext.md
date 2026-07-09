# Active Context

_Last updated: 2026-07-09_

## Current focus — ACT sprint complete (Grok 4.5 + stabilization)

Full improvement-plan ACT landed on `main` (unpushed → push this session).

### Shipped this session
- **Grok 4.5 routing**: `AGENTS.md`, `.grok/agents/gesh.md`, `.cursor/rules/gesh-grok-model.mdc`, all 11 skills pin `model: grok-build`; global `~/.grok/config.toml` adds `grok-4.5` alias.
- **Spend-gate hardening** (PR #26 content): quota fail-closed, `get_tier()` gates on `is_active`, tests in `test_spend_gate_hardening.py`.
- **DB unification**: `agent_db.py` → `gesh_financial.db`; `scripts/migrate_gesh_db.py` migrated 223 legacy rows from `gesh.db`.
- **Regime staleness fix**: `run_engines2.py` pops `regime_ran_at`/`macro` on regime failure (no MERGE ghost stamps).
- **Dashboard**: snapshot staleness banner (>24h), `/api/sections/status` uses snapshot `fetched_at` + `intelligence` regime badge.
- **CI**: coverage floor 45% (`pyproject.toml` + `tests.yml`); suite **565 passed, ~46.5% cov**.
- **`gesh_config.json`**: `paper_mode: true` aligned with ops default.

### Deferred (not in this sprint)
- `server.py` route split (2778 lines) — large refactor
- PR8 autopilot → `gesh-core` extraction
- Tax harvester purchase-date plumbing in `gesh-core`
- GitHub Pro required checks (~$4/mo) — solo alpha, skip for now

## Live state / how to operate
- **Agent model (GESH)**: Cursor → **grok-build** or **grok-4.5** (not Composer 2.5 Fast).
- Restart :9000 after code changes:
  `launchctl bootout gui/$(id -u)/com.gesh.main && launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.gesh.main.plist`
- Daily chain: `bash run_daily_analysis.sh` (logs `/tmp/gesh_fullchain.log`).
- Snapshot path: MCP-first via `fetch_snapshot_mcp.sh` → `live_portfolio_snapshot.json`.