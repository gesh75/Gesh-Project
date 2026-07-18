# GESH Financial — Defect Register

**Date:** 2026-07-18 | **Test Results:** 1,214 passed, 0 failed

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

| ID | Severity | Area | Defect | Reproduction Steps | Root Cause | Affected Files | Recommended Fix |
|---|---|---|---|---|---|---|---|
| D-01 | **Critical** | Compliance | Terms of Service labeled "DRAFT — not yet legally reviewed" while charging customers $49/mo | Read [TERMS_OF_SERVICE.md](file:///Users/georgigaydarov/Projects/gesh-project/TERMS_OF_SERVICE.md) line 3 | ToS was written as template language, never reviewed by attorney | TERMS_OF_SERVICE.md | Engage fintech attorney; remove DRAFT label only after review |
| D-02 | **Critical** | Compliance | No Privacy Policy page exists | Navigate to /privacy → 404; no privacy link in footer/nav | Never implemented | server.py, legal_pages.py | Create privacy_policy.md + /privacy route in legal_pages.py |
| D-03 | **Critical** | Compliance | No account deletion mechanism | No UI or API endpoint for users to delete their account/data | Never implemented | auth_users.py, server.py | Add DELETE /api/account + confirmation UI |
| D-04 | **Critical** | Ops | No automated database backup process | Check for cron/launchd backup job → none exists | Never implemented | Infrastructure | Implement daily automated backup of gesh_auth.db, gesh_financial.db, gesh.db |
| D-05 | **High** | Compliance | No cookie disclosure/consent mechanism | Session cookie set on login without disclosure | Session cookie = personal data; requires disclosure | server.py L829 | Add cookie consent notice to login flow |
| D-06 | **High** | Compliance | No data export/portability mechanism | User has no way to export their data | GDPR Art. 20 / CCPA requirement | auth_users.py | Add GET /api/account/export endpoint |
| D-07 | **High** | Compliance | No refund policy stated | /pricing page has no refund terms; ToS has none | Never drafted | server.py pricing_page(), TERMS_OF_SERVICE.md | Add refund policy section |
| D-08 | **High** | UX | Customer with no portfolio sees owner's masked $100K model book | Login as customer → dashboard shows model-book portfolio | Customer view defaults to showing owner's masked data | server.py _customer_view_middleware | Show empty state or "Add your portfolio" prompt for new customers |
| D-09 | **High** | Auth | No password reset functionality | User with password forgets it → no /auth/reset route | Never implemented (magic link is the recovery path) | server.py, auth_users.py | Add password reset via email, or document magic link as the intended recovery |
| D-10 | **High** | Ops | No production monitoring or error tracking | No APM (Sentry, Datadog, etc.) configured; errors go to stdout | Never implemented | Infrastructure | Add structured logging + error tracking service |
| D-11 | **Medium** | Architecture | server.py is 4,099 lines / 192KB | Open server.py → 287 route/function definitions in one file | Organic growth without refactoring | [server.py](file:///Users/georgigaydarov/Projects/gesh-project/server.py) | Split into route modules (auth_routes, billing_routes, portfolio_routes, etc.) |
| D-12 | **Medium** | Architecture | dashboard.html is 303KB monolithic file | Open templates/dashboard.html | All UI logic in one file | [dashboard.html](file:///Users/georgigaydarov/Projects/gesh-project/templates/dashboard.html) | Consider component-based architecture (Vite + vanilla JS modules) |
| D-13 | **Medium** | UX | No landing page for unauthenticated visitors | Navigate to / without session → redirect to /login | Only /login, /pricing, /track-record are public | server.py | Add a public landing page explaining the product |
| D-14 | **Medium** | Data | No multi-provider price fallback chain | yfinance fails → limited RH fallback via price_source.py | Partial implementation; some paths hard-depend on yfinance | price_source.py, data_fetcher.py | Implement circuit breaker pattern with provider abstraction |
| D-15 | **Medium** | Ops | Database files have no OS-level permission hardening | ls -la gesh_auth.db → default permissions | DB files created with default umask | auth_users.py, db.py | Set 0600 permissions at file creation |
| D-16 | **Medium** | CI | No security linting (bandit) or dependency vulnerability scanning (pip-audit) in CI | Review tests.yml → only ruff + pytest | Never added | .github/workflows/tests.yml | Add pip-audit and bandit steps |
| D-17 | **Low** | UX | Login page has no "Forgot password?" flow | /login page has "Email me a sign-in link" but no password reset | Magic link is the intended recovery path | server.py L662 | Document clearly that magic link IS the password reset |
| D-18 | **Low** | UX | /pricing page is functional but basic | Navigate to /pricing → minimal HTML with inline styles | Server-rendered minimal HTML | server.py L895-933 | Improve with consistent styling and more detail |
| D-19 | **Low** | Architecture | gesh-core is a private repo requiring PAT for CI | CI needs GESH_CORE_PAT secret to clone gesh-core | Private dependency architecture | requirements.txt L45, tests.yml L62 | Consider monorepo or published package |
| D-20 | **Low** | UX | No favicon configured (serves 404 redirect to Cloudflare default) | Navigate to / → favicon.ico returns redirect | Favicon handling attempts redirect to CDN | server.py L1105-1106 | Add a proper favicon file |

---

## Test Coverage Gaps (Critical Workflows Without Tests)

| Workflow | Criticality | Current Coverage | Recommendation |
|---|---|---|---|
| Full subscriber lifecycle (pay → magic link → login → analyze → cancel) | **Critical** | test_e2e_subscriber_flow.py exists (6KB) | Enhance with more edge cases |
| Data provider failover (yfinance down → RH fallback → snapshot) | **High** | test_data_fetcher_fallback.py (8KB), test_analysis_offline_fallback.py | Good coverage; extend to customer book |
| Customer book analysis pipeline (positions → valuation → AI → results) | **High** | test_mybook.py (52KB) | Well-covered |
| Autopilot full cycle (scan → intent → gate → execute → close) | **High** | test_autopilot_*.py (3 files) | Extend to cover full daily cycle |
| Dashboard HTML rendering correctness | **Medium** | test_dashboard_customer_nav.py, test_dashboard_xss_wiring.py | No browser-level E2E tests |
| Mobile responsiveness | **Medium** | No tests | Add responsive design tests |
| API response time under load | **Medium** | No performance tests | Add basic load testing |
| Database corruption recovery | **High** | gesh_financial.db.corrupt.bak exists (evidence of past corruption) | Add DB health check + recovery test |

---

## Notes

- **No test failures detected.** All 1,214 tests pass cleanly in 74 seconds.
- **16 deprecation warnings** from openbb_intrinio (PydanticDeprecatedSince211/212) — informational only, from a dependency.
- **No TODO/FIXME/HACK markers** found in any Python source files — indicates conscious cleanup.
- **Test suite is comprehensive** for a project at this stage (93 test files, ~500KB total test code).
