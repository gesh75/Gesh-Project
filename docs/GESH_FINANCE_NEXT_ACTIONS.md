# GESH Financial — Next Actions

**Date:** 2026-07-18 | **Audit verdict:** Late-Alpha / Early-Beta — resolve P0 items before continuing to charge customers

---

## Top Risks

| Risk | Severity | Impact if Unaddressed |
|---|---|---|
| **Terms of Service is DRAFT** while charging $49/mo | **Critical** | Legal liability; unenforceable terms; FTC risk |
| **No Privacy Policy** exists | **Critical** | GDPR/CCPA violation; customer trust erosion |
| **No database backup process** | **Critical** | Irrecoverable data loss for auth, billing, and analysis data |
| **No account deletion mechanism** | **Critical** | GDPR Art. 17 violation; cannot fulfill deletion requests |
| **No production monitoring** | **High** | Running blind — errors, downtime, or data corruption go undetected |
| **Robinhood API fragility** | **High** | Undocumented API can break without notice; no alternative data source |
| **gesh-core private dependency** | **Medium** | CI requires PAT; installation friction; code drift between repos |
| **SQLite single-writer limit** | **Medium** | Cannot scale horizontally; risk under concurrent customer load |

---

## P0 — Immediate (Must-Do This Week)

| # | Task | Acceptance Criteria |
|---|---|---|
| P0-1 | **Finalize Terms of Service** with a qualified attorney | ToS reviewed by fintech/securities attorney; "DRAFT" label removed; properly dated |
| P0-2 | **Create Privacy Policy** at /privacy | Attorney-reviewed privacy policy served at /privacy; linked from footer, login, and pricing |
| P0-3 | **Implement automated DB backup** | Daily backup of gesh_auth.db + gesh_financial.db + gesh.db to separate location; restore tested |
| P0-4 | **Add account deletion** | DELETE /api/account endpoint; removes user + all associated data from gesh_auth.db; confirmation UI |
| P0-5 | **Add monitoring/alerting** | Error tracking service (e.g., Sentry); uptime monitoring; alert on server restart / 5xx spike |

---

## P1 — Required Before Confident Paid Launch (Next 2-6 Weeks)

| # | Task | Acceptance Criteria |
|---|---|---|
| P1-1 | **Complete gesh-core** or adopt monorepo | All shared logic in one place with parity tests; CI doesn't require PAT for private repo |
| P1-2 | **Raise test coverage to 70%+** | pytest --cov-fail-under=70 passes in CI; server.py core routes covered |
| P1-3 | **Add data provider abstraction** | DataProvider protocol with yfinance adapter; circuit breaker; per-symbol fallback chain |
| P1-4 | **Split server.py** into route modules | server.py ≤ 500 lines (app setup + router includes); separate route files |
| P1-5 | **Add Docker deployment** | Dockerfile + docker-compose.yml; builds and runs :9000 server |
| P1-6 | **Add cookie consent** | Disclosure on login page about session cookie |
| P1-7 | **Add data export** | GET /api/account/export returns JSON of all user data |
| P1-8 | **Add refund policy** | Refund terms in ToS and on /pricing |
| P1-9 | **Add pip-audit + bandit to CI** | CI fails on high-severity CVEs or security issues |
| P1-10 | **Fix empty-state for new customers** | New customer sees "Add your portfolio" prompt, not owner's masked data |

---

## Recommended Implementation Sequence

### Week 1: Legal + Data Safety
```
P0-1 → P0-2 → P0-3 → P0-4
```
- No code dependencies between these items
- Can parallelize: attorney work (P0-1, P0-2) + engineering (P0-3, P0-4)
- P0-3 (backups) is the fastest win — a launchd plist + rsync script

### Week 2: Monitoring + Security
```
P0-5 → P1-9 → P1-6 → P1-8
```
- P0-5 (monitoring) enables visibility into all subsequent work
- P1-9 (security CI) is a ~30-minute PR
- P1-6 (cookie consent) and P1-8 (refund policy) are text additions

### Weeks 3-4: Engineering Foundation
```
P1-4 → P1-1 → P1-10 → P1-5
```
- P1-4 (split server.py) makes everything else easier to work on
- P1-1 (gesh-core) is the largest foundational item
- P1-10 (empty state) is a quick UX fix
- P1-5 (Docker) enables reproducible deployment

### Weeks 5-6: Quality + Resilience
```
P1-2 → P1-3 → P1-7
```
- P1-2 (test coverage) benefits from split server.py
- P1-3 (data providers) benefits from completed gesh-core
- P1-7 (data export) is straightforward after P0-4 (account deletion)

---

## What's Working Well (Don't Touch These)

- ✅ Billing webhook flow — idempotent, HMAC-verified, race-safe
- ✅ Auth system — PBKDF2, HMAC tokens, brute-force protection
- ✅ Customer data isolation — model-book masking with dedicated CI gate
- ✅ Risk gate — 8 gates with full decision logging
- ✅ Consensus engine — 11 nodes, regime-weighted, extensively tested
- ✅ AI spend controls — usage logging, daily budgets, customer quotas
- ✅ Paper trading safety — PAPER_MODE defaults true everywhere
- ✅ Test suite — 1,214 tests passing, 93 test files, 55%+ coverage
