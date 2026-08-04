# GESH Financial — Defect Register

**Date:** 2026-07-18 | **Reconciled:** 2026-08-04  
**Test Results (Jul-18 snapshot):** 1,214 passed · suite has grown since

> Status values: **OPEN** · **PARTIAL** · **FIXED** · **WONTFIX/DOC**.  
> Living priorities: [`GAPS_AND_IMPROVEMENTS_2026-08-04.md`](../GAPS_AND_IMPROVEMENTS_2026-08-04.md)

---

## Severity Legend

| Severity | Definition |
|---|---|
| **Blocker** | Prevents core functionality; must fix before any customer use |
| **Critical** | Data loss, security, or financial accuracy risk |
| **High** | Major feature gap or significant user impact |
| **Medium** | Non-critical but noticeable quality issue |
| **Low** | Minor issue or cosmetic defect |

---

## Defect Register

| ID | Severity | Area | Status | Defect | Notes (2026-08-04) |
|---|---|---|---|---|---|
| D-01 | **Critical** | Compliance | **OPEN** | ToS labeled DRAFT while charging | Attorney must finalize before removing banner |
| D-02 | **Critical** | Compliance | **PARTIAL** | Privacy Policy missing / 404 | `/privacy` route live; **content still DRAFT** |
| D-03 | **Critical** | Compliance | **FIXED** | No account deletion | `POST /api/account/delete` |
| D-04 | **Critical** | Ops | **FIXED** | No automated DB backup | `ops_backup.py` + `com.gesh.backup` |
| D-05 | **High** | Compliance | **PARTIAL** | No cookie disclosure | Login page discloses session cookie (2026-08-04) |
| D-06 | **High** | Compliance | **FIXED** | No data export | `GET /api/account/export` |
| D-07 | **High** | Compliance | **OPEN** | No refund policy | Add to ToS + /pricing |
| D-08 | **High** | UX | **PARTIAL** | New customer sees model book | Published model portfolio intentional; empty-state polish remains |
| D-09 | **High** | Auth | **WONTFIX/DOC** | No password reset | Magic link is the recovery path |
| D-10 | **High** | Ops | **OPEN** | No production monitoring | Sentry/APM still needed |
| D-11 | **Medium** | Architecture | **OPEN** | `server.py` monolith | ~6.5k lines as of 2026-08 |
| D-12 | **Medium** | Architecture | **OPEN** | `dashboard.html` monolith | |
| D-13 | **Medium** | UX | **OPEN** | No marketing landing for anonymous visitors | |
| D-14 | **Medium** | Data | **PARTIAL** | Multi-provider fallback incomplete | price_source + sanity; full abstraction open |
| D-15 | **Medium** | Ops | **PARTIAL** | DB file permissions | Backups use 0600/0700; audit live DB create paths |
| D-16 | **Medium** | CI | **FIXED** | No bandit / pip-audit | CI security job added 2026-08-04 |
| D-17 | **Low** | UX | **PARTIAL** | No “Forgot password?” | Magic-link copy on login |
| D-18 | **Low** | UX | **OPEN** | /pricing basic | |
| D-19 | **Low** | Architecture | **OPEN** | gesh-core private PAT | |
| D-20 | **Low** | UX | **OPEN** | Favicon | |

### Historical detail (Jul-18 reproduction notes)

| ID | Reproduction / root cause (original) | Affected |
|---|---|---|
| D-01 | TERMS_OF_SERVICE.md DRAFT banner | TERMS_OF_SERVICE.md |
| D-02 | Was 404; now route + draft markdown | legal_pages.py, docs/GESH_PRIVACY_POLICY_DRAFT.md |
| D-03–D-06 | Implemented post-audit | server.py, auth_users.py, ops_backup.py |
| D-10 | No APM | Infrastructure |
| D-11 | Organic growth | server.py |
| D-16 | tests.yml only ruff+pytest historically | .github/workflows/tests.yml |

---

## Test Coverage Gaps (Critical Workflows Without Tests)

| Workflow | Criticality | Current Coverage | Recommendation |
|---|---|---|---|
| Full subscriber lifecycle (pay → magic link → login → analyze → cancel) | **Critical** | test_e2e_subscriber_flow.py | Enhance edge cases |
| Data provider failover | **High** | fallback + offline tests | Extend to customer book |
| Customer book analysis pipeline | **High** | test_mybook* | Well-covered |
| Autopilot full cycle | **High** | test_autopilot_* | Extend daily cycle |
| Dashboard HTML rendering | **Medium** | nav + XSS wiring | No browser E2E |
| Mobile responsiveness | **Medium** | No tests | Dedicated pass |
| API response time under load | **Medium** | No performance tests | Basic load test |
| Database corruption recovery | **High** | Recovery procedure in CLAUDE.md | Add health check test |

---

## Notes

- Jul-18 suite baseline: 1,214 tests; subsequent PRs added LLM gateway/contracts, proof-page, XSS, etc.
- Prefer the living gaps doc over this register when prioritizing new work; update Status here when defects close.
