# GESH Financial — Feature Matrix

**As of:** 2026-07-18 at `dc969264` on `main`.
**Allowed statuses:** Implemented, Partially implemented, Broken, Missing, Not applicable, Not verified.
**Policy:** open PR behavior is not counted as implemented.

## Public product and access

| Capability | Status | Evidence and gap |
|---|---|---|
| Public login/magic-link entry | Implemented | `/login`; signed, expiring, single-use magic tokens in `auth.py`. |
| Public pricing page | Implemented | `/pricing`; one honest $49/month plan. |
| Public simulated track record | Implemented | `/track-record`; cohort labels, provisional metrics and failed autonomy gate are exposed. |
| Terms of service | Partially implemented | `/terms` exists, but `TERMS_OF_SERVICE.md:3-5` explicitly says draft/not legally reviewed. |
| Privacy policy | Missing | No public privacy notice or policy document found. |
| Customer self-registration | Missing | Provisioning is payment/admin driven; no registration workflow. |
| Password reset | Not applicable | Product uses magic-link authentication, not passwords. |
| Account/profile management | Missing | No customer profile, email change, active-session or device management UI. |
| Account data export | Missing | No user-facing export for personal data, portfolio or decision records. |
| Account deletion | Missing | No customer deletion request or retention workflow. |
| Consent/disclosure version ledger | Missing | No affirmative consent record tied to terms/privacy versions. |

## Customer research and portfolio experience

| Capability | Status | Evidence and gap |
|---|---|---|
| Customer dashboard | Implemented | Authenticated customer home, Analyze and My Portfolio views in `templates/dashboard.html`. |
| Manual portfolio entry | Implemented | Per-user book/position CRUD in `auth_users.py` and customer API routes. |
| Bulk symbol import | Implemented | Customer book import with equal-weight/privacy behavior and tests. |
| Brokerage import for customers | Missing | Robinhood connection is owner infrastructure only. |
| Cash balance/cash-flow ledger | Missing | Customer book is positions only; weights exclude cash. |
| Transaction/dividend/fee ledger | Missing | No customer transaction accounting model. |
| Multiple portfolios/accounts | Missing | One logical book per user. |
| Multi-currency accounting | Missing | Values assume USD; no FX/currency ledger. |
| Corporate actions | Missing | No split/merger/spinoff adjustment pipeline. |
| Watchlist | Implemented | Per-customer symbols are stored and isolated. |
| Any-ticker search/research | Implemented | Symbol/name search and `/api/analyze-ticker` workflow. |
| Deep portfolio analysis | Implemented | Async start/poll workflow with conflict guard and JSON-safe results. |
| Portfolio report | Partially implemented | Report exists; staleness checks and source/data completeness are limited. |
| Deploy-cash suggestions | Partially implemented | Kelly-based allocation exists, but it is personalized-looking, not suitability-governed and lacks legal validation. |
| Saved investment thesis | Missing | Analysis can be rerun, but no durable thesis/assumption/version object exists. |
| Decision journal/contract | Missing | No user commitment, invalidation condition, time horizon or confidence contract. |
| Thesis monitoring | Missing | Alerts watch consensus flips, not explicit thesis conditions or source evidence. |
| Regret replay | Missing | No counterfactual replay against the original decision contract. |
| Source citations on AI research | Missing | Outputs do not provide traceable source-level citations for financial claims. |
| Empty-state onboarding | Partially implemented | UI guidance exists, but no guided goal/risk/disclosure onboarding was verified. |

## Analytics, financial math and risk

| Capability | Status | Evidence and gap |
|---|---|---|
| Consensus engine | Implemented | Multi-node consensus in `consensus.py`; LT Macro defaults display-only/off. |
| LT Macro governance | Implemented | Environment-gated voting; unavailable data abstains. |
| Canonical fair-value percentage | Implemented | `(estimate-current)/abs(current)` centralized and invariant-tested. |
| Deterministic intrinsic valuation | Missing | Fair-value estimate is generally AI/model output, not an auditable DCF/comps model. |
| Trade setup levels | Implemented | Positive-price and strict stop-entry-target postconditions; invariant tests. |
| Risk bands | Implemented | Deterministic volatility-tiered bands with tests. |
| Kelly sizing | Partially implemented | Capped/blended/correlation-adjusted implementation and tests; data freshness, model calibration and 25%-versus-2% policy consistency remain unresolved. |
| Monte Carlo scenario engine | Partially implemented | Repeatable GBM/covariance implementation and invariants; heuristic regime/GARCH treatment and fallbacks are not calibrated predictive probabilities. |
| User portfolio market value | Implemented | Sum of `shares * current price` for manual customer positions. |
| User asset allocation | Partially implemented | Position-only weights; cash and other assets excluded. |
| User unrealized gain/loss | Partially implemented | Basic average-cost calculation exists; no lot, currency, fee or corporate-action support. |
| User realized gain/loss | Missing | No customer transaction ledger from which to calculate it. |
| TWR/MWR/total return | Missing | No customer cash-flow-aware performance engine. |
| Benchmark comparison | Partially implemented | Price-return comparisons exist; provider failures can collapse to zero/misleading grades. |
| User portfolio volatility/beta/Sharpe | Missing | No deterministic, customer-level performance/risk accounting suite. |
| Risk/diversification score | Partially implemented | Displayed scores are primarily AI-generated and lack an auditable deterministic formula. |
| Crypto risk correlation | Partially implemented | Unweighted average correlation and raw provider calls can misstate portfolio contribution. |
| Tax-loss harvesting | Partially implemented | Decimal loss math and a 30-day tracker exist; it lacks full lots, purchases before/after sale, substantially-identical assets and broker/tax-lot integration. |
| ESG analytics | Missing | No ESG data or scoring functionality found. |
| ECG portfolio alert | Broken | Current `main` can ingest invalid/non-observation history and trigger false crash alarms; #96 is unmerged. |

## Trading and evidence

| Capability | Status | Evidence and gap |
|---|---|---|
| Typed trade intent | Implemented | `trading/contracts.py` and persisted intent/decision records. |
| Risk gate | Partially implemented | Key gates work, but sector, earnings proximity, consecutive-loss, risk-off confidence and approval settings are not all enforced. |
| Paper execution | Implemented | New typed execution path defaults to paper. |
| Live broker execution | Not applicable | Deliberately raises `NotImplementedError`; product must not claim live execution. |
| Kill switch | Implemented | Enforced by risk gate. |
| Legacy paper-trading view | Broken | `paper_trading.py` treats SELL/TRIM like short entries and can produce misleading P&L semantics. |
| Track-record calculation | Partially implemented | Transparent simulated metrics; small sample and per-trade Sharpe methodology require caution. |
| Walk-forward validation | Missing | No validated out-of-sample program for signals, Kelly or risk heuristics. |
| Autonomy readiness gate | Implemented | Gate exists and currently fails honestly. |

## Alerts, retention and support

| Capability | Status | Evidence and gap |
|---|---|---|
| Daily signal snapshot | Implemented | Scheduled job seeds per-symbol history. |
| Confirmed flip alerts | Partially implemented | Works and a launchd email was previously proven; failed sends can age out on current `main`. |
| Weekly digest | Partially implemented | Real launchd send verified; missing-price paths can report `$0` without a degraded badge on current `main`. |
| Alert/digest unsubscribe | Broken | GET routes mutate preferences and can be activated by email link scanners; #95 is unmerged. |
| Schedule timezone correctness | Broken | launchd calendar values use host local time while documentation promises ET. |
| Price/news/risk threshold alerts | Missing | Only consensus changes/digest are customer-facing. |
| Notification center/history | Missing | No customer-visible delivery/event history. |
| Granular notification settings | Partially implemented | Broad email preferences exist; channel, frequency and alert-type controls are limited. |
| Customer support console | Missing | No dedicated support/admin case tooling. |
| Role-based administration | Partially implemented | Owner/customer split exists; no granular staff roles or least-privilege admin model. |
| Audit log for support/admin actions | Missing | Trading decisions are audited, but administrative user actions are not comprehensively logged. |

## Billing and commercial operations

| Capability | Status | Evidence and gap |
|---|---|---|
| Hosted checkout | Implemented | Lemon Squeezy hosted flow and strict variant mapping. |
| Signed webhook verification | Implemented | Raw-body HMAC, constant-time compare, generic errors. |
| Idempotent provisioning | Implemented | Leased claim and ordering logic with strong tests. |
| Subscription revocation/grace | Implemented | Access-status model and terminal revocation rules are tested. |
| Onboarding recovery sweep | Implemented | 15-minute launchd job; latest runtime status was exit 0. |
| Billing reconciliation | Partially implemented | Daily job exists and historically succeeds; latest run exited 1 on DNS failure and no operational alert was demonstrated. |
| Billing portal/self-service cancellation | Missing | No customer portal link or in-app lifecycle controls. |
| Invoice/receipt access | Missing | Not surfaced in the application. |
| Refund handling | Missing | No explicit `order_refunded`/refund lifecycle behavior verified. |
| Trials/coupons/free tier | Missing | One paid plan; tier names exist internally but do not create distinct public entitlements. |
| Tax/VAT merchant-of-record handling | Implemented | Lemon Squeezy is documented as merchant of record; exact production account configuration was not independently verified. |
| Real production checkout smoke in this audit | Not verified | Synthetic lifecycle tests pass; no new charge was made. |

## Security, privacy and platform

| Capability | Status | Evidence and gap |
|---|---|---|
| Auth wall/default deny | Implemented | Non-public routes require an active session; local probes returned 401/redirect. |
| Password hashing | Implemented | PBKDF2-HMAC-SHA256, 200,000 iterations. |
| Secure session cookie | Implemented | HttpOnly, SameSite=Lax and request-aware Secure behavior. |
| Session revocation | Partially implemented | Disabling a user blocks access, but logout does not invalidate a stolen signed cookie for up to seven days. |
| Login rate limiting | Partially implemented | IP+email and global limits are in-memory/single-process. |
| Tenant isolation | Implemented | Per-session identity keys and extensive leak/money-masking tests. |
| API error redaction | Implemented | Generic server errors on reviewed routes; no raw exception leak confirmed. |
| CSRF protection | Partially implemented | SameSite and origin restrictions help; no explicit CSRF token system, and GET unsubscribe mutates state. |
| XSS output handling | Partially implemented | Escaping and deny-by-default tests cover known renderers; CSP permits `unsafe-inline`, leaving little defense if a sink is missed. |
| Security headers | Implemented | CSP, HSTS at public edge, nosniff, DENY and referrer policy observed. |
| Secrets in source control | Implemented | No committed secrets found; `.env` is ignored and mode 0600. |
| Sensitive data file permissions | Broken | Main DB, snapshots/reports and several logs are mode 0644. |
| Encrypted backups | Missing | No backup system found. |
| Data retention/deletion controls | Missing | No lifecycle or customer-rights implementation. |
| Dependency vulnerability management | Not verified | No successful vulnerability audit; installed dependency constraints conflict. |

## AI and data-provider behavior

| Capability | Status | Evidence and gap |
|---|---|---|
| Structured AI responses | Implemented | Tool/schema validation used in core analysis. |
| AI timeout/retry/error handling | Implemented | Bounded calls and honest degrade paths exist. |
| AI usage/cost logging | Implemented | Model/token/cost metadata logged without prompts. |
| Customer quotas | Implemented | Per-user/tier usage controls. |
| Hard global spend ceiling | Partially implemented | Budget support exists; a required production ceiling was not verified. |
| Hallucination controls | Partially implemented | Price grounding and schemas help; financial claims/fair values lack mandatory source citations. |
| Prompt-injection isolation | Partially implemented | External text is treated as prompt data and tools are constrained, but no formal provenance/injection policy or adversarial suite was found. |
| AI privacy disclosure/consent | Missing | Customer positions may be sent to an AI vendor without a published privacy/retention disclosure. |
| Central price-provenance gateway | Partially implemented | Sanity/cache logic exists, but raw yfinance calls remain throughout product and shared core. |
| Provider failure observability | Partially implemented | Degraded markers exist in some cards/jobs; production logs show frequent failures and health does not quantify data coverage. |

## Delivery, quality and operations

| Capability | Status | Evidence and gap |
|---|---|---|
| Python automated tests | Implemented | 1,214 passing tests. |
| Application coverage depth | Partially implemented | 56% overall; many provider, job and legacy modules are 0–30%. |
| Customer isolation CI gate | Implemented | Separate required GitHub status. |
| Application lint gate | Broken | Ruff reports green because configuration includes tests, not application modules. |
| Static typing gate | Broken | Focused mypy run has 46 errors; no CI type gate. |
| Dependency consistency | Broken | `pip check` reports OpenBB/FastAPI/uvicorn/yfinance conflicts. |
| Dependency vulnerability CI | Missing | No SCA job or lockfile audit. |
| Protected-role E2E tests | Missing | No browser login/customer/owner journey in CI. |
| Accessibility testing | Missing | No axe/keyboard/screen-reader gate. |
| Performance/load testing | Missing | No concurrent customer/provider/load budget suite. |
| Android build | Implemented | Debug build succeeds. |
| Android automated tests | Missing | Gradle reports `testDebugUnitTest NO-SOURCE`. |
| Android production connectivity | Broken | Cleartext LAN URL, no login/session and incompatible protected endpoints. |
| Versioned database migrations | Missing | Inline schema creation/ALTER only. |
| Backup/restore drill | Missing | No automated backup or verified restore. |
| Staging environment | Missing | No isolated production-like environment found. |
| Automated deployment/rollback | Missing | launchd/manual pull/restart process; no release pipeline. |
| Job/service health UI | Partially implemented | Owner page reads summaries; thresholds and alerting coverage are incomplete. |
| Log management | Broken | High-volume `/tmp` plaintext logs, no rotation/structured aggregation/redaction policy demonstrated. |
| Production availability monitoring | Partially implemented | launchd/tunnel self-heal exists; no external SLO/error-budget monitoring verified. |
