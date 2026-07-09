# Progress

_Last updated: 2026-07-09_

## Done — ACT stabilization + Grok 4.5 (2026-07-09)

- Merged spend-gate hardening locally (`88cbf60`); full ACT diff staged for push.
- **agent_db.py** + migration: 220 news_sentiment, 3 fundamental_scans rows → `gesh_financial.db`.
- **run_engines2** regime MERGE fix + tests (`test_sections_status.py`, `test_agent_db.py`).
- **Dashboard** snapshot staleness banner + intelligence/regime sidebar badges.
- **Grok 4.5** project harness: AGENTS.md, `.grok/agents/gesh.md`, skill `model: grok-build`.
- **565 tests** pass, **46.5%** coverage (floor 45%).

## Done — open-items closeout (2026-07-02 evening)

- RS window alignment hardened; engine suite 502 passed in 10.6s (Telegram ENABLED=False in conftest).
- PR8 phase 1: `gesh_core.trading.autopilot_core` wired on product autopilot.

## Done — MCP-first data revival (2026-07-02)

- Robinhood MCP canonical snapshot path; Yahoo hard-blocked under VPN.
- Single-symbol regime MultiIndex crash fixed (`679c7cd`).

## Done — Money-path audit (2026-07-02)

- Paper book ledger, working stops, price_source NaN guard, Kelly SIMPLE returns, screener on Haiku.

## Done — Security audit (2026-05-28)

- 3 CRITICAL / 11 HIGH remediated; API-key auth, fail-closed risk gate, generic 500s.

## Pending / known
- Push `main` + close PR #26 on GitHub after this commit.
- Run `run_daily_analysis.sh` to refresh stale snapshot (was 2026-07-07).
- Prune stale remote feature branches (security/*, feat/* merged).
- Trading repo doc drift (`GESH_COMMERCIAL_ROADMAP.md`, `PROGRESS_LOG.md`) — commit separately.
- `server.py` monolith split — next architecture sprint.