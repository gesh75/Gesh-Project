# GESH Financial — Defect Register

**Date:** 2026-07-18 | **Reconciled:** 2026-08-04 | **GFD register merged:** 2026-10-15  
**Two ID schemes:** `D-nn` (this file's original register) and `GFD-nnn` (second audit pass, cited by code, tests and PRs, e.g. GFD-008 / GFD-026). They are independent numberings of the same baseline, not aliases.  
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

---

## GFD-numbered register (second audit pass, merged 2026-10-15)

> Moved verbatim from the former root `GESH_FINANCE_DEFECT_REGISTER.md` (shadow copy removed
> 2026-10-15). These IDs are the ones referenced in code comments, tests and PR titles
> (`paper_trading.py`, `trade_log.py`, `crypto_correlation.py`, `tests/test_paper_long_exit.py`,
> `docs/GFD_FIXES_2026-08-22.md`). Rows without a status update are the 2026-07-18 audit view;
> check `GAPS_AND_IMPROVEMENTS_*.md` and git history before treating one as open.

**Baseline:** `main` at `dc969264`, 2026-07-18. Open PRs are not fixes until merged and deployed.
**Severity:** Critical blocks launch or risks material legal/financial/security harm; High can materially mislead, expose data, deny service or break a core workflow; Medium impairs reliability/maintainability; Low is contained polish/debt.

### Status updates (2026-08-22)

| ID | Update |
|---|---|
| GFD-008 | **Code-fixed** on `grok/gap-fixes-2026-08-22`. Legacy `paper_trading.py` TRIM/SELL FIFO-close open BUY/ADD lots. `close_trade` always uses long P&L. Historical inverted rows are not migrated. |
| GFD-026 | **Code-fixed.** `crypto_risk_summary` equity-weights BTC correlation; unweighted mean still returned for contrast. |

Original rows below are retained as audit evidence.

### Launch-blocking and high-priority defects

| ID | Severity | Area | Defect and evidence | Impact | Required remediation / acceptance |
|---|---|---|---|---|---|
| GFD-001 | Critical | Compliance | Terms are explicitly unreviewed and say outputs are not personalized (`TERMS_OF_SERVICE.md:3-5,32-33`), while customer books feed Kelly/action/deploy-cash outputs. | Paid customers can reasonably perceive personalized investment advice; disclaimers alone may not control the legal characterization. | Securities/fintech counsel reviews actual UI/data flows, approves positioning and disclosures, and required product changes are implemented before public paid launch. |
| GFD-002 | High | Privacy | No privacy policy, AI-vendor disclosure, retention schedule, consent record, export or deletion workflow exists. | Unclear handling of financial holdings and personal data; customer trust and regulatory exposure. | Publish counsel-reviewed privacy notice; record versioned consent; implement authenticated export/deletion and documented retention/deletion jobs. |
| GFD-003 | High | Data security | `gesh_financial.db`, portfolio snapshots, reports and multiple runtime logs are mode 0644. | Other local users/processes can read customer addresses and owner financial/trading data. | Inventory sensitive files, migrate to a private mode-0700 directory, enforce 0600 atomically on creation, redact logs, and add permission tests. |
| GFD-004 | High | Market data | Raw `yf.download` and `Ticker.history` calls bypass the central lock/provenance guard, including installed `gesh_core.kelly`; production logs show repeated failures/timeouts/rate-limit indicators. | Bad or absent prices can contaminate sizing, benchmarks, crypto risk, paper results and research. | One provider gateway with independent sessions, cache, freshness/provenance/sanity contract and coverage metrics; ban direct provider calls in product and shared core. |
| GFD-005 | High | Risk alerts | ECG consumes invalid/non-observation position history on current `main`; #96 is unmerged. | False “portfolio crash” alarms undermine the highest-trust safety feature. | Merge a passing fix only after adversarial history tests; filter valid observations, require coverage, expose degraded/insufficient-data instead of a score. |
| GFD-006 | High | Portfolio accounting | No customer transaction/cash-flow/lot ledger exists, yet the product presents a portfolio experience. | Cannot accurately calculate realized return, TWR/MWR, dividends, fees, taxes, cash allocation or corporate actions. | Either relabel as a manual research book everywhere or implement a double-entry/immutable portfolio ledger with reconciliation and precision tests. |
| GFD-007 | High | Financial outputs | Risk, diversification and much of fair value are AI-generated without deterministic methodology, source citations or calibration. | Precise-looking numbers can be mistaken for audited facts. | Separate fact, model estimate and AI interpretation; require cited inputs, calculation version, confidence/data quality and “not calculated” states. |
| GFD-008 | High | Paper evidence | Legacy `paper_trading.py` models SELL/TRIM as short-like openings and reverses closing P&L semantics. | Track/paper results from this path can be economically wrong or incomparable to the typed engine. | Freeze legacy writes; migrate or quarantine records by engine/version; use one typed order/position model and regression-test long exits/short entries separately. |
| GFD-009 | High | Risk controls | `max_sector_pct`, earnings proximity, consecutive-loss days, risk-off confidence and `require_approval` are configured but not comprehensively enforced in `trading/risk_gate.py`. | UI/configuration gives a false impression of protection. | Enforce each control with explicit decision codes and tests, or remove it from configuration/UI until supported. |
| GFD-010 | High | Recovery | No versioned migration framework, automated encrypted backup, restore test, staging or rollback procedure exists. | A schema/deploy/host failure can cause extended outage or irreversible financial-history loss. | Add migration versions/checksums, daily encrypted off-host backups, automated restore validation and a rehearsed rollback runbook. |
| GFD-011 | High | Android | Android permits cleartext traffic, defaults to `http://10.0.0.113:9000`, has no production authentication and calls protected/owner endpoints. | Mobile product is nonfunctional in production and transmits data insecurely if used on a hostile LAN. | Mark prototype and exclude from launch, or implement HTTPS-only environment config, login/cookie/token lifecycle, customer-compatible APIs and real device/E2E tests. |
| GFD-012 | High | Dependency integrity | `pip check` reports yfinance, FastAPI and uvicorn versions incompatible with installed OpenBB packages. | Undefined runtime behavior and difficult reproducibility/security patching. | Resolve constraints in a locked environment, recreate from scratch in CI, run provider/API regression tests, and fail CI on `pip check`. |
| GFD-013 | High | Billing operations | Nightly billing reconciliation latest run exited 1 on DNS resolution failure; no alert/retry was demonstrated. | Missed webhooks may leave paid users locked out or expired users active until someone reads a log. | Add bounded retry, durable run ledger, stale/failure alert and owner dashboard status; demonstrate recovery from a synthetic outage. |
| GFD-014 | High | Performance evidence | Current stored simulated proof has weak outcomes (aggregate 18.52% win rate; engine 12.5% and negative P&L; autonomy gate 16/100). | “Better signals” or return claims would be unsupported and potentially deceptive. | Freeze marketing claims to decision process and simulation; pre-register a walk-forward protocol and require meaningful sample/benchmark thresholds before performance claims. |

### Reliability, lifecycle and quality defects

| ID | Severity | Area | Defect and evidence | Impact | Required remediation / acceptance |
|---|---|---|---|---|---|
| GFD-015 | Medium | Scheduling | launchd calendar jobs run in the host’s local timezone, but docs call them ET. Host timezone at audit was Europe/Athens. | Jobs fire at the wrong US-market phase and change with host timezone. | Use a timezone-aware scheduler or UTC calendars with DST tests; show intended and actual next-run times. |
| GFD-016 | Medium | Alerts | On current `main`, signal dedup keys and bounded streak logic can drop events after repeated send failures. | Material changes may never reach the customer. | Persist event identity and delivery state independently; retry with backoff/dead-letter and surface failures. #95 remains unshipped. |
| GFD-017 | Medium | Digest | Missing quotes can become `$0`/partial book value without a prominent degraded marker. | Customers may interpret provider failure as portfolio loss. | Never coerce missing price to zero; show coverage, stale timestamp and omitted positions. #95 remains unshipped. |
| GFD-018 | Medium | HTTP semantics | `/alerts/unsubscribe` and `/digest/unsubscribe` mutate state on GET. | Link scanners/prefetchers can unsubscribe users without intent. | GET renders confirmation; CSRF-protected POST performs change; token remains scoped/expiring. #95 remains unshipped. |
| GFD-019 | Medium | Billing UX | No self-service portal, cancellation, invoices/receipts or explicit refund lifecycle. | Support burden and customer-control/compliance problems. | Add hosted customer portal and tested cancel/resume/refund/access-state journeys. |
| GFD-020 | Medium | Sessions | Logout deletes the local cookie but cannot revoke a copied signed session before its seven-day expiry. | A stolen session remains usable while the account is active. | Store hashed session IDs with device/created/last-used/revoked timestamps; revoke on logout and expose “sign out all.” |
| GFD-021 | Medium | Rate limiting | Login/API throttles are in process memory. | Restart or multiple workers reset/bypass limits; state is not observable. | Back limits with Redis/database, trusted-proxy IP parsing and security events; test multi-worker behavior. |
| GFD-022 | Medium | XSS defense | CSP permits `unsafe-inline` script/style and the monolithic dashboard uses many `innerHTML` renderers. | One missed escape becomes same-origin code execution, especially dangerous for owner sessions. | Move to nonce/hash CSP, use DOM text/typed render helpers, and add browser XSS payload tests. |
| GFD-023 | Medium | Local token storage | Pickled broker/session artifacts are deserialized after ownership checks, but at least one inspected session file was mode 0644. | Local tampering/read access can expose credentials or lead to code execution via pickle. | Eliminate pickle for untrusted/persistent state; otherwise enforce 0600 before read/write and cryptographically authenticate contents. |
| GFD-024 | Medium | XML parsing | SEC/news modules use standard ElementTree on external XML. | Crafted/oversized feeds can cause resource exhaustion. | Use `defusedxml`, response-size/time limits and malformed-feed tests. |
| GFD-025 | Medium | Benchmarking | Provider failure can become zero return in batch comparisons. | Incorrect relative-grade and performance conclusions. | Represent missing as missing; require aligned observations/coverage and expose source timestamp. |
| GFD-026 | Medium | Crypto risk | Portfolio correlation is an unweighted average across holdings. | Small and large positions contribute equally, misrepresenting portfolio risk. | Weight pairwise contribution by exposure or calculate covariance contribution; test concentration cases. |
| GFD-027 | Medium | Kelly policy | Analytical Kelly can recommend up to 25% while execution position cap defaults to 2%. | Customer suggestion and safety policy contradict each other. | Establish one policy hierarchy; show raw research fraction separately from approved allocation cap. |
| GFD-028 | Medium | Tax harvesting | Tracker only records a sale-side 30-day clock and average basis; it does not evaluate replacement purchases, lots or substantially identical securities. | “Wash safe” can be tax-inaccurate. | Rename to screening aid and add prominent limits, or build lot/purchase-aware rules reviewed by a tax professional. |
| GFD-029 | Medium | Resource handling | Coverage run emitted hundreds of resource warnings, mainly unclosed SQLite connections; server raises file-descriptor limit as a mitigation. | Long-running process degradation and eventual “too many open files.” | Close connections at ownership boundary, add leak-count tests and remove the raised-limit workaround after measured verification. |
| GFD-030 | Medium | Logging | `/tmp` logs are plaintext, high-volume and not centrally rotated/structured; main log contains many tracebacks/provider failures. | Disk/privacy/incident-detection risk and poor signal-to-noise. | Private log directory, rotation/retention/redaction, JSON event fields and alertable error budgets. |
| GFD-031 | Medium | CI | Ruff excludes app code; mypy has 46 focused errors; no SCA, E2E, Android or accessibility gate. | Green CI overstates deploy confidence. | Expand gates gradually with baselines and no-new-debt enforcement; protect required checks and reviews. |
| GFD-032 | Medium | Branch protection | Required tests exist, but strict/up-to-date reviews/admin enforcement are absent. | Risky changes can merge without current base or independent approval. | Require up-to-date checks, one review, conversation resolution and admin enforcement for production branches. |
| GFD-033 | Medium | Provider degradation | Data-quality badges are not universal and job health does not expose symbol coverage/freshness distributions. | Partial upstream outage can look like valid analysis. | Standard `DataQuality` envelope on every financial response and job; fail closed for sizing/actions below coverage threshold. |
| GFD-034 | Medium | Accessibility | Inline click handlers, icon/color-heavy controls and horizontally scrolling tables lack automated accessibility validation. | Keyboard/screen-reader/mobile users may be blocked. | Semantic controls, focus management, labels, contrast checks and axe/keyboard CI. |
| GFD-035 | Medium | Protected E2E | No authenticated customer/owner browser flow was exercised in this audit or required in CI. | Role gating, empty states and mutations can regress despite unit tests. | Seed isolated test users and run customer/owner Playwright journeys against staging. |
| GFD-036 | Medium | Architecture | `server.py` and `templates/dashboard.html` combine routing, policy, rendering and orchestration. | High coupling, review difficulty and regression risk. | Extract by vertical slice only after characterization tests: identity, portfolio, research, notifications, billing/admin. |
| GFD-037 | Low | Documentation | Architecture docs/context claim React/Next/PostgreSQL/Redis and ET schedules inconsistent with audited FastAPI/SQLite/local-time runtime. | Operators/reviewers make incorrect assumptions. | Make one generated architecture/runtime manifest authoritative and validate docs in release checklist. |
| GFD-038 | Low | Track-record metric | Sharpe is annualized from per-trade observations and sample size is small. | Users may compare it with conventional daily-return Sharpe. | Label frequency/method/sample beside the number or switch to a time-series equity-return calculation. |

### Explicitly not confirmed as defects

- No authentication bypass or cross-customer owner-dollar leak was reproduced.
- The Bandit SHA-1 High finding is used for a non-secret client-order idempotency identifier; collision resistance is not relied on for authentication.
- Live broker execution being absent is intentional and safe, not an implementation defect, provided marketing stays accurate.
- Market-data and AI dependencies are not defects by themselves; unbounded or silently degraded use is the defect.
