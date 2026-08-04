# Active Context

_Last updated: 2026-08-04_

## Current focus — gaps roadmap + CI hardening (ACT)

Branch: `cursor/gaps-roadmap-and-ci-hardening-b04d`  
Synced `main` to latest (`a27d52e`+) then started closing doc-drift and CI gaps.

### In this sprint
- Living roadmap: `GAPS_AND_IMPROVEMENTS_2026-08-04.md`
- Reconciled launch checklist / next actions / defect register
- CI: coverage fail-under **55**; new **security** job (pip-audit + bandit)
- `/healthz` uptime; login cookie disclosure; portable `ops_backup` paths
- Minimal `Dockerfile` for :9000

### Still open (host / counsel)
- Attorney ToS + Privacy (remove DRAFT)
- Autonomy `gesh_tracker.db` write path on host
- Sentry / uptime
- `server.py` split; coverage → 70%

## Operate
- Agent model: **grok-build** / **grok-4.5**
- Daily chain on host: `bash run_daily_analysis.sh`
