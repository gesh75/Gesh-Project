# Progress

_Last updated: 2026-08-04_

## Done — gaps ACT (2026-08-04)
- Synced local `main` with origin (+80 commits).
- Added living `GAPS_AND_IMPROVEMENTS_2026-08-04.md`; pointed CLAUDE/README at it.
- Reconciled stale Jul-18 audits (delete/export/backup/privacy-route were wrongly ❌).
- CI coverage gate 45 → **55**; added pip-audit + bandit `security` job.
- `ops_backup.py`: `GESH_HOME` / `GESH_TRADING_HOME` (no `/Users` hardcode).
- `/healthz` uptime (+ optional `GESH_BUILD_SHA`); login cookie disclosure.
- Minimal `Dockerfile`; regression tests `tests/test_gaps_hardening_2026_08_04.py`.

## Earlier (main, Aug 2026)
- PR #143 CI required-check fix; #144 attorney docs gitignore; LLM gateway; proof-page; XSS/security wave #178–#185+.

## Pending
- [ ] Counsel finalize ToS/Privacy + refund policy
- [ ] Host: verify tracker DB writes; alert silent MCP→Claude fallback
- [ ] Sentry; coverage 70; server.py split; docker-compose
