# GESH Financial — Gaps & Improvements (living)

**Date:** 2026-08-04  
**Supersedes priority list of:** `GAPS_AND_IMPROVEMENTS_2026-07-09.md` (keep for history)  
**Scope:** `gesh-project` (:9000) after sync to `main` @ `a27d52e` (+80 commits since early Aug cloud snapshot).

## Verdict

Engineering and security hardening moved fast (LLM gateway/contracts, proof-page scope, XSS/report fixes, CI required-check repair, snapshot env fix). **Paid-launch blockers that remain are mostly legal counsel + autonomy proof + observability**, not missing CRUD for GDPR basics.

| Surface | Maturity |
|---|---|
| Personal / operator use | ~80% |
| Confident paid SaaS | ~58% |

---

## Closed since 2026-07-18 audits (doc drift fixed this pass)

| Former claim | Reality (2026-08-04) |
|---|---|
| No Privacy Policy / 404 | `/privacy` serves draft policy (`legal_pages.render_privacy_html`) — **still DRAFT pending attorney** |
| No account deletion | `POST /api/account/delete` + export `GET /api/account/export` |
| No DB backups | `ops_backup.py` + `com.gesh.backup` nightly integrity-checked copies |
| CI never runs on PRs | Fixed (PR #143) — required checks on same-repo PRs + `ruff --isolated` syntax gate |
| Direct MCP needs hardcoded account | `GESH_RH_ACCOUNT` from env/`.env` (PR path + #183) |

---

## P0 — still open

1. **Attorney finalize ToS + Privacy** — remove DRAFT banners only after counsel; refund policy in ToS/pricing.  
2. **Autonomy tracker proof** — host `gesh_tracker.db` was observed empty (0 bytes, Jul 2); confirm write path from autopilot; target ≥30 closed, WR≥55%, Sharpe≥0.5, DD&lt;10%.  
3. **Silent Claude snapshot fallback** — if tier-1 direct MCP fails, chain burns LLM forever; alert on `direct MCP fetch failed` in `/tmp/gesh_fullchain.log`.  
4. **Production error tracking** — no Sentry (or equiv); `/healthz` alone is not enough.

## P1 — engineering (this branch starts)

| ID | Item | Status on this branch |
|---|---|---|
| P1-CI-cov | Align CI `--cov-fail-under` with pyproject **55** | Doing |
| P1-CI-sec | Add **pip-audit** + **bandit** CI job | Doing |
| P1-docs | Reconcile launch checklist / defect register / next actions | Doing |
| P1-health | Richer `/healthz` (uptime, no secrets) | Doing |
| P1-docker | Minimal Dockerfile for :9000 | Doing |
| P1-split | Split `server.py` (~6.5k lines) | Later |
| P1-sentry | Wire optional Sentry via env | Later (needs DSN) |
| P1-coverage70 | Raise floor toward 70% | Later |

## P2

- Cookie consent copy on login (disclosure).  
- Editorial model-portfolio publish store (not live content-hash only).  
- Owner JS out of shared customer bundle.  
- Android `android` / `android 2` / `android 3` cleanup.  
- Public hub presence (gesh75 / github.io).  
- Docker-compose + Prometheus `/metrics`.

## Strengths (do not regress)

Risk gate + paper defaults · customer isolation / leak sweep · billing HMAC + onboarding sweep · LLM gateway contracts · nightly backups · CI syntax + isolation gates · paper-first execution.

## Success metrics

| Metric | Target | Notes |
|---|---|---|
| Coverage floor | 55 → 70 → 80 | CI must match pyproject |
| Daily chain | &lt;5 min, zero silent fails | Last known clean ~26 min — optimize separately |
| Autonomy closed | ≥30 | Host tracker must write |
| Legal | Non-DRAFT ToS + Privacy | Counsel |
| Self-host | Dockerfile boots :9000 | Started this pass |

## Doc map (which file to trust)

| File | Role |
|---|---|
| **This file** | Current prioritized gaps |
| `GAPS_AND_IMPROVEMENTS_2026-07-09.md` | Historical deep review |
| `docs/GESH_FINANCE_LAUNCH_CHECKLIST.md` | Launch readiness — **reconciled 2026-08-04** |
| `docs/GESH_FINANCE_NEXT_ACTIONS.md` | Action queue — **reconciled 2026-08-04** |
| `docs/GESH_FINANCE_DEFECT_REGISTER.md` | Defect IDs — **status column added 2026-08-04** |
| `docs/LLM_OPERATIONS.md` | LLM gateway ops + Grok/Gemini failover (PR #236) |
| `AGENTS.md` / `CLAUDE.md` | Agent operating manual |

*Update this file after every major milestone.*
