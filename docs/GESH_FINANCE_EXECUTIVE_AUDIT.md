# GESH Financial — Comprehensive Executive Audit

**Date:** 2026-07-18
**Auditor:** Independent AI-assisted audit (Claude Opus 4.6 Thinking)
**Scope:** Full gesh-project (port :9000) + gesh-project-trading (port :9003) + gesh-core
**Methodology:** Evidence-based code inspection, architecture review, test execution, and security analysis

---

## 1. Executive Summary

### Product Maturity: **Late-Alpha / Early-Beta**

GESH Financial is a sophisticated, self-hosted AI-powered financial observability and paper-trading platform built on FastAPI (Python), SQLite, and multi-LLM analysis (Anthropic Claude, Google Gemini, formerly Ollama). It integrates with Robinhood for live portfolio data and employs an 11-node consensus voting engine, 8-gate risk management, Monte Carlo stress testing, Kelly Criterion sizing, and a paper-trading execution pipeline.

The product has undergone significant hardening since early 2026, with multiple security audits (Codex rounds 1-4, Grok, Fable), a live payment integration via Lemon Squeezy, customer data isolation, and a growing test suite (~93 test files). Payment gates are LIVE as of 2026-07-03 with real-card transactions verified.

### Strongest Parts

1. **Security hardening**: Extensive customer-view model-book masking (scales all owner dollar amounts), HMAC-signed session tokens, brute-force throttling, CSP headers, HSTS, rate limiting, XSS prevention, prompt injection awareness
2. **Risk management**: 8-gate risk gate with kill switch, market hours, confidence floor, position cap, order rate, cash floor, daily loss, and data freshness checks — all with decision logging
3. **Paper-first safety**: PAPER_MODE defaults to true everywhere; multiple circuit breakers prevent accidental live trading
4. **Billing maturity**: Lemon Squeezy webhook integration with HMAC signature verification, idempotent claim/reclaim state machine, monotonic ordering, retryable errors, magic-link onboarding
5. **Test coverage**: 93 test files covering billing, authentication, customer isolation, consensus, risk gates, Kelly, Monte Carlo, price provenance, and more
6. **AI spend controls**: LLM usage logging, daily budget caps, per-customer quotas, cost-routed model selection (Haiku for batch, Sonnet for deep analysis)

### Largest Risks

1. **Single-point-of-failure data dependency on Robinhood**: robin_stocks uses undocumented API with pickle-session auth, subject to unannounced breakage
2. **No multi-user portfolio separation at the database level**: The owner's portfolio is the system portfolio; customers get their own `customer_positions` table but share the same research cache
3. **Terms of Service is explicitly labeled DRAFT — not yet legally reviewed**: The product is charging $49/mo with draft legal documents
4. **SQLite as production database**: No concurrent-write safety for multi-worker deployments; WAL mode helps but doesn't solve horizontal scaling
5. **No automated backup/restore process**: Multiple .db files (gesh.db, gesh_financial.db, gesh_auth.db, crypto.db) with no documented backup strategy
6. **gesh-core extraction incomplete**: Shared code between two repos creates drift risk and complex CI (requires PAT for private repo)

### Is It Safe to Onboard Paying Customers?

**Partially.** The product can safely be demonstrated and used by beta testers who understand it is a research tool, not financial advice. The billing flow works (verified with real card), the auth wall prevents unauthorized access, and customer data isolation is tested. However, the draft ToS, the fragile Robinhood dependency, the absence of automated backups, and the single-server SQLite architecture create risk for paying customers who expect service-level reliability.

### Most Important Next Actions (P0)

1. Have the Terms of Service professionally reviewed and finalized
2. Implement automated database backup/restore
3. Add health monitoring and alerting beyond the existing `/healthz` endpoint
4. Complete the gesh-core extraction or adopt a monorepo
5. Document and test the disaster recovery procedure

---

## 2. System Architecture Summary

### Frontend
- **Framework**: Single-page HTML dashboard ([dashboard.html](file:///Users/georgigaydarov/Projects/gesh-project/templates/dashboard.html) — 303KB monolithic file)
- **JS Framework**: Vanilla JavaScript with Chart.js for charts
- **Styling**: Inline CSS, dark-terminal theme (#080d14 background, #00d4ff accent)
- **No frontend build pipeline** — served as a static HTML file by FastAPI

### Backend
- **Framework**: FastAPI 0.135.3 on uvicorn (Python 3.14)
- **Server**: [server.py](file:///Users/georgigaydarov/Projects/gesh-project/server.py) — 4,099 lines, ~192KB, contains ~287 route/function definitions
- **Port 9000**: Main product server (customer-facing)
- **Port 9003**: Trading engine server (owner-only, local tool)
- **Architecture**: Monolithic single-file server with module imports

### Database
- **Technology**: SQLite 3 with WAL mode
- **Databases**:
  - `gesh.db` (~1.1MB) — general state
  - `gesh_financial.db` (~9MB) — analysis results, equity history, paper trades
  - `gesh_auth.db` (~508KB) — users, sessions, watchlists, customer positions, billing events, usage
  - `crypto.db` / `crypto_financial.db` — crypto-specific data (trading repo)
- **Schema management**: DDL in code ([auth_users.py](file:///Users/georgigaydarov/Projects/gesh-project/auth_users.py) line 166-212), idempotent ALTER TABLE migrations
- **No formal migration tool** (Alembic, etc.)

### Authentication & Authorization
- **System**: Custom HMAC-signed session tokens (JWT-style, stdlib only)
- **Password hashing**: PBKDF2-HMAC-SHA256 with 200,000 rounds, 16-byte random salt
- **Session TTL**: 7 days (configurable via GESH_AUTH_TTL)
- **Magic links**: Single-use, purpose-typed (session vs magic), with HMAC spending
- **Roles**: Owner (full access) vs Customer (masked model-book view) — no fine-grained RBAC
- **Auth wall**: OFF by default, enabled via GESH_AUTH_ENABLED env var

### External APIs & Data Providers
| Provider | Purpose | Auth Method |
|---|---|---|
| Robinhood (robin_stocks) | Live portfolio, quotes, fundamentals | Pickle session + TOTP |
| yfinance / Yahoo Finance | Historical prices, fundamentals, charts | No auth (rate-limited) |
| Anthropic Claude (Sonnet 4.6 / Haiku 4.5) | Deep portfolio analysis, ticker research | API key |
| Google Gemini (2.5 Flash/Pro) | Batch sentiment, second opinions | API key |
| FRED (Federal Reserve) | Macro data (FFR, yield curve, CPI) | API key |
| Finnhub | News sentiment | API key (optional) |
| SEC EDGAR | Filing alerts (10-K, 10-Q, 8-K) | No auth |
| Lemon Squeezy | Payment processing (Merchant of Record) | Webhook HMAC |
| Resend | Transactional email (magic links, alerts) | API key |
| Telegram | Alert notifications | Bot token (optional) |

### Background Jobs & Scheduling
- **Autopilot**: APScheduler-based (4 daily jobs: pre-market scan 08:30 ET, open window 09:35, midday rebalance 12:30, close risk check 15:55)
- **Cron jobs**: macOS launchd plists for dashboard snapshots, live data, MCP preflight, portfolio export
- **Daily chain**: run_daily_analysis.sh → engines → AI → consensus → paper gate → report

### Deployment
- **Platform**: Self-hosted (macOS), Cloudflare tunnel for HTTPS termination
- **CI/CD**: GitHub Actions (tests.yml) — 2 jobs: `test` (pytest + ruff) and `customer-isolation` (leak sweep + XSS wiring)
- **No Docker** container or infrastructure-as-code
- **No staging environment** — single production instance

### Testing
- **Framework**: pytest 7.0+, pytest-cov, ruff (lint)
- **Test count**: 93 test files in `/tests/`
- **Coverage target**: 55% (pyproject.toml fail_under), goal of 70-80%+
- **Markers**: `unit` (offline), `integration` (DB/network)
- **CI gate**: Customer-isolation tests run as a separate required check

---

## 3. Feature Status Matrix

See [GESH_FINANCE_FEATURE_MATRIX.md](file:///Users/georgigaydarov/.gemini/antigravity-ide/brain/dcaa2bad-9d44-4648-8269-cf4896a9c290/GESH_FINANCE_FEATURE_MATRIX.md)

## 4. Defect Register

See [GESH_FINANCE_DEFECT_REGISTER.md](file:///Users/georgigaydarov/.gemini/antigravity-ide/brain/dcaa2bad-9d44-4648-8269-cf4896a9c290/GESH_FINANCE_DEFECT_REGISTER.md)

## 5. Security Register

See [GESH_FINANCE_SECURITY_REVIEW.md](file:///Users/georgigaydarov/.gemini/antigravity-ide/brain/dcaa2bad-9d44-4648-8269-cf4896a9c290/GESH_FINANCE_SECURITY_REVIEW.md)

## 6. Financial Accuracy Register

See [GESH_FINANCE_FINANCIAL_ACCURACY_REVIEW.md](file:///Users/georgigaydarov/.gemini/antigravity-ide/brain/dcaa2bad-9d44-4648-8269-cf4896a9c290/GESH_FINANCE_FINANCIAL_ACCURACY_REVIEW.md)

## 7. Compliance and Customer-Trust Review

### Regulatory Risk Assessment

| Area | Status | Risk | Evidence |
|---|---|---|---|
| Terms of Service | DRAFT — not legally reviewed | **Critical** | [TERMS_OF_SERVICE.md](file:///Users/georgigaydarov/Projects/gesh-project/TERMS_OF_SERVICE.md) line 3: "DRAFT — not yet legally reviewed" |
| "Not Financial Advice" disclaimers | Present, prominent | Low | ToS §2, email footer, pricing page, track-record page |
| Investment Adviser disclaimer | Present | Low | ToS §3: "does not create an advisory...relationship" |
| Hypothetical performance disclaimer | Present, well-worded | Low | ToS §4: CFTC-style language, uppercase warning |
| Risk disclosure | Present | Low | ToS §5: "substantial risk of loss" |
| AI-generated content disclosure | Present | Low | ToS §7: "may be incomplete or wrong, can hallucinate" |
| Data-source attribution | Partially present | Medium | Some endpoints stamp `provenance.price_source`; many don't |
| Stale market data labeling | Partially present | Medium | `snapshot_age_seconds` exposed; `stale_snapshot` flag exists |
| Privacy policy | **Missing** | **High** | No `/privacy` route, no privacy policy page |
| Cookie disclosure | **Missing** | **High** | httpOnly session cookie set without cookie banner/disclosure |
| Data retention rules | **Missing** | **High** | No documented data retention or deletion policy |
| Account deletion process | **Missing** | **Critical** | No self-service account deletion (GDPR Art. 17 risk) |
| Data export (portability) | **Missing** | **High** | No GDPR Art. 20 data portability mechanism |
| Subscription/refund disclosure | **Partially present** | Medium | Pricing page says $49/mo; no refund policy stated |
| Consent for financial data processing | **Missing** | **High** | No explicit consent collection beyond implicit ToS acceptance |

### Recommendations for Legal/Compliance Review

> [!CAUTION]
> The Terms of Service explicitly states it is a DRAFT and has not been reviewed by a qualified attorney. This is the single most urgent compliance action required before continuing to charge customers.

1. Engage a fintech/securities attorney to review and finalize the ToS
2. Add a Privacy Policy page (required for any service processing personal/financial data)
3. Add a cookie disclosure (session cookie = personal data under GDPR)
4. Implement self-service account deletion and data export
5. Add explicit consent collection at signup for financial data processing
6. Document and disclose data retention periods
7. Add a refund policy to the subscription terms

---

## 8. UX and Product Audit

### Dashboard (dashboard.html — 303KB monolithic file)

The dashboard is a sophisticated single-page application with a dark terminal theme, rendered as a monolithic HTML file. It includes:
- Portfolio overview with holdings table
- AI analysis panels (deep analysis, consensus, Kelly sizing)
- Risk metrics (regime, Monte Carlo, ECG)
- Chart views (Chart.js with symbol/period switching)
- Navigation sidebar with multiple views
- Customer-specific nav hiding (owner-only views)

**Strengths:**
- Professional dark-theme design
- Rich data visualization
- Responsive AI research cards
- Clear customer vs owner role separation in UI

**Concerns:**
- 303KB single HTML file is difficult to maintain and test
- No component-based architecture — all UI logic in one file
- No frontend build/bundle optimization
- Limited mobile responsiveness testing evidence
- No automated browser/E2E tests
- No accessibility audit evidence (WCAG compliance)

### User Personas Assessment

| Persona | Experience | Key Issues |
|---|---|---|
| New visitor | Can see /pricing, /track-record, /terms | No landing page; /pricing is functional but basic |
| New free user | N/A — no free tier exists | No trial or freemium path |
| Paid subscriber (new) | Magic link → dashboard | Good; clean onboarding email |
| Paid subscriber (returning) | Login → dashboard | Session persists 7 days; magic link recovery works |
| User with no portfolio | Sees owner's masked portfolio (model book) | Confusing — sees $100K model book, not empty state |
| Customer with own book | /api/mybook → analyze | Well-implemented per-user position management |
| Paper trading user | Owner-only (/watch is owner-gated) | Customers cannot paper trade |
| Mobile user | **Not verified** | No responsive design testing evidence |
| API/data failure user | Graceful degradation via snapshot | Well-handled — offline badge, stale warnings |

---

## 9. Engineering Quality Audit

### Architecture Strengths
- Clear separation of concerns across modules (consensus, risk_gate, paper_trading, billing)
- Defense-in-depth security (CSP, rate limiting, auth wall, customer masking, HMAC verification)
- Fail-closed patterns consistently applied (billing signature verification, auth fallback, price validation)
- Comprehensive error handling with generic 500s (no internal details leaked)

### Architecture Concerns

| Issue | Evidence | Impact |
|---|---|---|
| 4,099-line monolithic server.py | [server.py](file:///Users/georgigaydarov/Projects/gesh-project/server.py) | Difficult to navigate, test, and review |
| 303KB monolithic dashboard.html | [dashboard.html](file:///Users/georgigaydarov/Projects/gesh-project/templates/dashboard.html) | No component reuse, hard to test UI logic |
| No database migration tool | DDL in code, ALTER TABLE in _migrate() | Schema changes are risky; no rollback |
| SQLite for multi-user production | All DBs are SQLite files | Single-writer limitation; no horizontal scaling |
| Two overlapping codebases | gesh-project vs gesh-project-trading | Drift risk in shared modules |
| Blocking I/O on event loop | yfinance/robin_stocks are sync | Can stall the FastAPI event loop |
| No structured logging | print() and logging.basicConfig | Hard to aggregate, search, and alert on logs |
| Private dependency (gesh-core) | Requires PAT in CI | Single-point-of-failure for CI; installation friction |

### Code Quality Metrics
- **No TODO/FIXME/HACK markers found** in Python files (clean codebase)
- **Well-documented modules** — most files have extensive docstrings
- **Type hints used throughout** (Python 3.14 features)
- **Ruff lint** applied to test suite (E, F, I rules)
- **No mypy or type-checking in CI** — type annotations are advisory only

---

## 10. Prioritized Roadmap

See [GESH_FINANCE_PRODUCT_ROADMAP.md](file:///Users/georgigaydarov/.gemini/antigravity-ide/brain/dcaa2bad-9d44-4648-8269-cf4896a9c290/GESH_FINANCE_PRODUCT_ROADMAP.md)

---

## 11. Final Verdict

### 1. Can the product be safely demonstrated?
**Yes.** The product is visually impressive, feature-rich, and the security hardening is above average for an alpha. The paper-trading-first approach and clear disclaimers make it safe to show.

### 2. Can selected beta testers use it?
**Yes, with caveats.** Beta testers who understand it is a research tool (not a trading platform) can use it. The auth wall, customer data isolation, and model-book masking are well-tested. The draft ToS should be communicated as draft.

### 3. Can paying customers use it?
**Conditionally.** Customers ARE paying ($49/mo) and the billing flow works. However:
- The ToS is labeled "DRAFT"
- There is no privacy policy
- There is no account deletion mechanism
- There is no refund policy
- These create legal/regulatory risk

### 4. Can customers rely on its financial calculations?
**For informational/research purposes, yes.** The consensus engine, Kelly sizing, Monte Carlo, and regime detection are algorithmically sound and tested. However:
- All data flows through yfinance/Robinhood with known fragility
- AI analysis may hallucinate despite guardrails
- Paper-trading track record is still building toward the 100-close gate
- No independent financial auditor has verified the calculations

### 5. Top Ten Actions Required Next

| Priority | Action | Reason |
|---|---|---|
| **P0-1** | Finalize Terms of Service with a fintech attorney | Charging customers with draft legal documents is legally risky |
| **P0-2** | Add Privacy Policy page | Required for any service processing personal/financial data |
| **P0-3** | Implement automated database backup/restore | Data loss risk — no recovery mechanism for 3 SQLite databases |
| **P0-4** | Add account deletion capability | GDPR Art. 17 / CCPA requirement; currently no way to delete user data |
| **P0-5** | Add production monitoring/alerting | No visibility into errors, latency, or system health beyond /healthz |
| **P1-1** | Complete gesh-core extraction or adopt monorepo | Drift between two repos creates bugs; CI fragility from PAT dependency |
| **P1-2** | Raise test coverage to 70%+ | Current 55% floor leaves critical paths untested |
| **P1-3** | Add data provider abstraction layer | Single-point-of-failure on yfinance/robin_stocks |
| **P1-4** | Split server.py into route modules | 4,099-line file is a maintenance and review burden |
| **P1-5** | Add Docker/containerized deployment | Self-hosted macOS-only deployment limits reliability and portability |
