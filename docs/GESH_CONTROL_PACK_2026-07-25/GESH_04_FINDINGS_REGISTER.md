# GESH 04 — Current Findings Register

**Cut:** 2026-07-25  
**Release decision:** NO-GO  
**Rule:** priority describes current impact; status uses the master prompt’s exact taxonomy
**Phase 1 update:** the clean candidate contains the immediate P0 routes; durable publication and production deployment remain open.

## Severity definition

| Priority | Meaning |
|---|---|
| P0 | Active customer data/privacy boundary failure or condition requiring immediate containment |
| P1 | Release-blocking financial correctness, product-trust, reliability, accessibility, or operational-control failure |
| P2 | Material but deferrable correctness, hardening, UX, test-depth, or completeness gap |
| P3 | Low-risk quality, provenance-detail, or maintenance debt |

## Current register

`GESH_03_REGRESSION_MATRIX.md` is the canonical ID registry. This file uses those same IDs; `QA-*`, `EXP-01`, `WARN-01`, and `API-01` are non-colliding current findings added by this audit. `FIN-07` is consolidated with `TRK-01` below because both describe the same mixed-unit track-record contract; the paired ID preserves both historical labels without counting the defect twice.

| ID | Pri. | Status | Evidence | Canonical finding |
|---|---:|---|---|---|
| TEN-01 / A-01 | P0 | **PARTIALLY FIXED — Phase 1 customer denial is complete in the candidate; durable Model Book publication remains open.** | VERIFIED CANDIDATE / VERIFIED PRE-CONTAINMENT | Customer `/api/portfolio` now returns 403 before owner cache/connector access; no approved model dataset exists yet. |
| TEN-02 / A-03 | P0 | **PARTIALLY FIXED — Phase 1 customer denial is complete in the candidate; curated customer trade events remain open.** | VERIFIED CANDIDATE / VERIFIED PRE-CONTAINMENT | Customer `/api/trades` and `/api/trades/stats` now return 403 before global repository access; owner tables remain internal. |
| FIN-03 | P1 | **OPEN** | VERIFIED CURRENT | Customer-visible model portfolio does not reconcile; `$28,616.88` of reported portfolio equity is not represented by listed holdings. |
| FIN-04 | P1 | **OPEN** | VERIFIED CURRENT / INFERRED | Model-book day change uses a base that cannot be bridged to visible current/prior components; this is separate from repaired My Book day change. |
| FIN-06 | P1 | **PARTIALLY FIXED** | SUPPORTED BY LATEST EVIDENCE | My Book downstream inputs use a reconciled base, but model weights, Kelly, VaR, and Monte Carlo remain untrustworthy wherever they inherit the unreconciled owner-derived model. |
| DEPLOY-01 | P1 | **PARTIALLY FIXED** | VERIFIED CANDIDATE | Safe `/api/version`, exact clean commit/build time, dependency-lock hash, and deterministic artifact exist; production remains unidentified because no deployment occurred. |
| DEPLOY-02 | P1 | **PARTIALLY FIXED** | VERIFIED CANDIDATE | Builder produces only from an exact clean HEAD and refuses in-repository output; staging/promotion/rollback remain unperformed. |
| BUILD-01 | P1 | **PARTIALLY FIXED** | VERIFIED CANDIDATE | PR #105 is in recorded base `68287dfd`; Phase 1 is a clean reviewed descendant, not yet merged/deployed. |
| BUILD-02 | P1 | **NO LONGER REPRODUCIBLE** | VERIFIED CLEAN BASE/CANDIDATE | Base: 2,078 passed; Phase 1 implementation: 2,206 passed; zero failed/skipped. Historical dirty failures were classified and not suppressed. |
| QA-01 | P1 | **PARTIALLY FIXED** | VERIFIED CANDIDATE | P0 tests now deny all three routes and cover arbitrary notes/source, repository/cache, query tampering, and real browser requests; model publication, MAX, and accessibility gaps remain. |
| TRK-01 / FIN-07 | P1 | **RECLASSIFIED — formerly “PF below 1 makes positive return impossible”; now “dollar PF and unweighted trade-return sum can differ, but the bases are misleadingly combined.”** | VERIFIED CURRENT | The arithmetic is possible; the underlying “total P&L”/return-unit contract remains open and is not capital-matched. |
| TRK-02 | P1 | **PARTIALLY FIXED** | VERIFIED CURRENT | Cohorts are separated, but poor quality remains visually subordinated to a positive headline. |
| TRK-03 | P1 | **OPEN** | VERIFIED CURRENT | SPY renders `+nan%`. |
| TRK-04 | P1 | **OPEN** | VERIFIED CURRENT / INFERRED | Benchmark and engine result are not demonstrably period- and capital-matched. |
| AI-01 | P1 | **PARTIALLY FIXED** | VERIFIED CURRENT | Top combined stance aligns for tested TSLA, but the same page gives a long-biased setup under `REDUCE`/mechanical `SELL`. |
| TEN-03 | P1 | **PARTIALLY FIXED** | VERIFIED CANDIDATE | Customer role composition and Android contain no removed owner loaders/polls; owner behavior remains in the owner-only partial. Not deployed. |
| REL-02 | P1 | **PARTIALLY FIXED** | VERIFIED CURRENT / UNKNOWN | Normal async analysis completed and cached, but timeout, crash, retry, reservation release, navigation-away, and durable retrieval are not fully verified. |
| REL-03 | P1 | **PARTIALLY FIXED** | VERIFIED CANDIDATE | Real Chromium two-refresh trace records zero removed owner-domain requests and zero console errors. Not deployed. |
| A11Y-01 | P1 | **OPEN** | VERIFIED CURRENT | Customer shell has no heading hierarchy, main/nav landmarks, async live region, or complete accessible input names. |
| FIN-05 | P1 | **OPEN** | VERIFIED CURRENT | My Book lacks cost basis for the baseline position, so unrealized P&L and return-on-cost cannot be calculated. |
| DATA-04 | P1 | **PARTIALLY FIXED** | VERIFIED CURRENT | Stale flags improved, but live/model/cached/owner/synthetic provenance is still not one durable customer contract. |
| WEB-01 | P1 | **PARTIALLY FIXED** | VERIFIED CURRENT / VERIFIED CODE | `text/plain` and hostile Origin are rejected, but a JSON mutation with no `Content-Type` is accepted and can persist. |
| DATA-03 | P1 | **NO LONGER REPRODUCIBLE** | VERIFIED CURRENT for `/api/analyze-ticker` only | The tested unknown returns 404 before spend; other entry points remain PROMPT-01. |
| PROMPT-01 | P1 | **RECLASSIFIED — formerly “prompt injection”; now “synchronous symbol-validation and quota/job hygiene defect.” Practical defect remains open.** | VERIFIED CODE | My Book can accept a syntactically valid unknown and create work before existence validation. |
| AI-02 | P1 | **OPEN** | VERIFIED CURRENT | The tested 1 bull/2 bear/3 neutral/3 no-data vote showed 60% confidence without a documented denominator/calibration. |
| DATA-02 | P2 | **OPEN** | VERIFIED CURRENT | Backend `max` works; customer UI has no ALL/MAX control. |
| DATA-05 | P2 | **OPEN** | VERIFIED CURRENT | Intraday chart responses omit source/provenance. |
| PRIV-03 | P2 | **PARTIALLY FIXED** | VERIFIED CANDIDATE | Phase 1 owner-domain and role-composed responses use private/no-store, Pragma, and Vary Cookie; the remaining sensitive route inventory is open. |
| WEB-03 | P2 | **PARTIALLY FIXED** | VERIFIED CURRENT | Major security headers are present; CSP still allows inline script and style execution. |
| MOB-01 | P2 | **UNABLE TO VERIFY** | UNKNOWN / REQUIRES ACCESS | True device emulation was unavailable; static layout indicates possible 320px clipping. |
| ERR-01 | P2 | **PARTIALLY FIXED** | SUPPORTED BY LATEST EVIDENCE | Candidate adds Accept-aware HTML errors; live coverage was incomplete. |
| UX-01 | P2 | **PARTIALLY FIXED** | SUPPORTED BY LATEST EVIDENCE | Candidate resets concatenated form state/rejects exponent syntax; no complete live retest. |
| UX-02 | P2 | **OPEN** | VERIFIED CURRENT / HISTORICAL ONLY | My Book navigation remains sections of one dense report rather than distinct journeys. |
| REL-01 | P2 | **RECLASSIFIED — formerly “duplicate analysis job storm”; now “one durable job with POST polling, historically duplicate browser poll loops and weak long-job UX.”** | VERIFIED CURRENT | One job completed and cached; timeout, cancellation, and duplicate-poller coverage remain incomplete. |
| EXP-01 | P2 | **OPEN** | VERIFIED CODE | Export parity exists, but no account-import/restore proves a true portability round trip. |
| QA-02 | P2 | **OPEN** | VERIFIED CURRENT | Same-record concurrency passed spot checks but is not a committed regression. |
| QA-03 | P2 | **OPEN** | VERIFIED CURRENT | Mass-assignment/query tampering passed spot checks but lacks a committed two-tenant matrix. |
| QA-04 | P2 | **OPEN** | VERIFIED CODE | No generated all-route/all-method customer policy inventory prevents a new owner route from defaulting open. |
| BUILD-03 | P2 | **OPEN** | VERIFIED CURRENT | Coverage policy is contradictory across workflow comments, commands, and project configuration, so regression protection can drift silently. |
| OPS-01 | P2 | **OPEN** | VERIFIED CURRENT | Repetitive polling and client exceptions inflate a roughly 22 MB live log and obscure actionable production health. |
| AI-03 | P2 | **RECLASSIFIED — formerly “contradictory RSI math”; now “likely different lookbacks/timeframes with ambiguous labels.”** | SUPPORTED BY LATEST EVIDENCE | No same-window error is proven; period, interval, and version must be explicit. |
| PRIV-01 | P2 | **UNABLE TO VERIFY** | SUPPORTED BY LATEST EVIDENCE | Candidate removes internal provider environment-variable copy; no complete live failure sweep covered every engine. |
| AI-04 | P3 | **NO LONGER REPRODUCIBLE** | VERIFIED CURRENT | Direct prompt-injection strings did not create an information leak or persistent cross-user artifact. |
| CONC-01 | P3 | **NO LONGER REPRODUCIBLE** | VERIFIED CURRENT | Controlled same-symbol writes left one untorn row; coverage gap is QA-02. |
| UX-03 / F-04 | P3 | **OPEN** | HISTORICAL ONLY / SUPPORTED BY CURRENT GAP | Tiny fractions can still render in scientific notation, and no verified minimum-lot/formatting contract closes the historical defect. |
| WARN-01 | P3 | **PARTIALLY FIXED** | VERIFIED CLEAN CANDIDATE | The clean candidate emits three dependency deprecation warnings, not 975; eliminating the remaining warnings remains open. |
| API-01 | P3 | **OPEN** | VERIFIED CURRENT | Analysis responses expose internal fingerprint/job identifiers not required by the customer contract. |

## P0 details

### TEN-01 / A-01 — Owner-derived portfolio presented as customer Model Book

**Status:** PARTIALLY FIXED — Phase 1 candidate is fail-closed; durable publication/live deployment remain open.  
**Evidence:** VERIFIED CANDIDATE / VERIFIED PRE-CONTAINMENT  
**Affected:** `/api/portfolio`, customer response middleware, `model_portfolio.py`, `data_fetcher.py`, `build_snapshot_from_mcp.py`, shared customer JavaScript

#### Failure mode

Before containment, an authenticated customer received a portfolio response whose source and composition derived from the operator’s real Robinhood/MCP snapshot. Query tampering did not select it; the normal customer path selected it. Phase 1 now denies the request before handler/cache/connector access, and customer clients do not fetch the route.

#### Root cause

- `view=model_book` is a response label, not a durable model assignment.
- `/api/portfolio` reads the owner connector/global snapshot rather than an immutable publication.
- Customer isolation was implemented partly as scaling/masking.
- No data-product layer records provenance policy, consent, approval, public schema, version, assignment, or rollback.
- Candidate allowlisting reduces exposed fields but preserves dynamic real-account provenance.

#### Impact

- **Security/privacy:** authenticated disclosure of owner financial composition and account-derived values.
- **Financial:** customer-visible model cannot be bridged or reconciled.
- **Commercial:** “Model Book” can imply a reviewed product while it is a live/stale transformed account.
- **Compliance/policy:** no durable consent/publication evidence was found.
- **Reliability:** owner cache/connector behavior can affect every customer.

#### Immediate control

**Implemented in the Phase 1 candidate:** return an exact 403 to customers, remove customer browser/Android polling, and preserve owner access. Keep deny mode until a separate active approved model version exists.

#### Closure standard

Closure requires source separation, not removal of a source string. See GESH-02 and backlog tickets CP-001 through CP-004.

### TEN-02 / A-03 — Global real-trade ledger and notes customer-readable

**Status:** PARTIALLY FIXED — Phase 1 candidate is fail-closed; curated customer events/live deployment remain open.  
**Evidence:** VERIFIED CANDIDATE / VERIFIED PRE-CONTAINMENT  
**Affected:** `/api/trades`, `/api/trades/stats`, `trade_log.py`, customer leak tests

#### Failure mode

Before containment, an authenticated customer received a global row marked non-paper plus execution/provenance fields and a private note. The note is intentionally redacted from this pack. Phase 1 denies both trade routes before repository access.

#### Root cause

- pre-containment routes had no owner-only or model-publication policy;
- the underlying tables remain global and contain no tenant/publication key;
- owner handlers still query those internal tables broadly;
- pre-containment customer serialization was subtractive masking, not an explicit public DTO;
- legacy tests required a customer 200 and used fixtures that did not include arbitrary text sentinels; Phase 1 inverted those expectations and added sentinels;
- the internal stats implementation still does not enforce its apparent paper filter, but Phase 1 prevents customer access before it executes.

#### Impact

- **Privacy/security:** operator trade and strategy detail disclosed.
- **Commercial:** paper/model/real execution is not trustworthy.
- **Future blast radius:** any new column or note can be published automatically.

#### Immediate control

**Implemented in the Phase 1 candidate:** owner-gate both routes before handler/repository access. Do not “fix” the remaining publication gap by filtering only `is_paper=1` in the existing global table; a separately published model-event schema is required for future customer model trades.

## P1 details

### FIN-03 — Model portfolio does not reconcile

**Status:** OPEN  
**Evidence:** VERIFIED CURRENT

Numerical bridge:

```text
reported portfolio equity                  $102,214.50
minus sum of ten listed holdings            73,597.62
unrepresented portfolio component          $28,616.88

listed holdings                             $73,597.62
plus published cash/deployable               26,402.39
scaled model notional                      $100,000.01

reported portfolio equity                  $102,214.50
plus cash/deployable                         26,402.39
reported account total                     $128,616.89
```

The response mixes a scaled `$100k` composition with an account-level equity/cash identity. Missing broker components, timing, and semantics prevent a complete dollar bridge. The correct fix is one canonical publication balance sheet, not forcing `reconciles:true`.

### DEPLOY-01 — Candidate identity exists; production identity remains unknowable

**Status:** PARTIALLY FIXED  
**Evidence:** VERIFIED CANDIDATE / VERIFIED PRE-CONTAINMENT

- launchd service `com.gesh.main` runs port 9000;
- process start: 2026-07-23 06:29 EEST;
- Uvicorn reload: disabled;
- server/template/model files changed after process start;
- the historical local tree was dirty and divergent;
- PR #105 is now in the exact recorded base `68287dfd`;
- the clean Phase 1 candidate exposes only service, exact commit, UTC build time, and `dirty:false`;
- the deterministic runtime archive SHA-256 is `a0d8a80a0255e393675352422992eb343df30bb60c016b17ec659d3e8324e345`;
- no production response identifies the candidate because deployment was explicitly out of scope.

This prevents reliable claims that a code fix is deployed and makes rollback selection unsafe.

### BUILD-01 and BUILD-02 — Clean Phase 1 line established; promotion remains open

**Statuses:** BUILD-01 PARTIALLY FIXED; BUILD-02 NO LONGER REPRODUCIBLE  
**Evidence:** VERIFIED CLEAN BASE/CANDIDATE

- Historical focused dirty-tree suite: 436/436.
- Historical full dirty-tree suite: 1,639 passed, 4 failed; one environment artifact and three genuine uncommitted regressions.
- Fresh exact base `68287dfd`: 2,078 passed, zero failed/skipped.
- Clean Phase 1 implementation `8ff34b5`: 2,206 passed, zero failed/skipped, 62.52% coverage, three warnings.
- PR #105 is squash-merged into the recorded base.
- Three independent current-tree reviews returned no findings.

Green candidate evidence still does not certify an unidentified live process; staging/promotion/rollback remain required.

### QA-01 — Passing tests encode false safety

**Status:** OPEN  
**Evidence:** VERIFIED CODE/CURRENT

Known false positives:

- **resolved in Phase 1:** customer-isolation tests require exact 403 for portfolio/trade/stats;
- **resolved in Phase 1:** fixtures cover arbitrary notes, source/provenance, owner values, repository seams, warmed caches, and query tampering;
- chart tests pass `MAX` as data but do not inspect rendered controls;
- My Book tests assert that unknown `ZZZZ9` persistence is correct;
- local model tests used source-field semantics later proven wrong by PR #105 fixes;
- **partially resolved in Phase 1:** the real page executes for two refresh intervals with network/console assertions; accessibility announcements and viewport overflow remain open;
- track-record tests verify cohort order but not a positive headline under poor risk-quality metrics.

### TRK-01 through TRK-04 — Track-record contract and framing

**Statuses:** see the current registry above  
**Evidence:** VERIFIED CURRENT

Current engine view:

- headline: `+6.7%`;
- 17 engine rows; independent sum rounds to `+6.8%`;
- win rate: `18%`;
- dollar profit factor: `0.91`;
- maximum drawdown: `27%`;
- probabilistic Sharpe: `0.60`;
- deflated Sharpe: `0.44`;
- expectancy: `0.0039`;
- SPY comparison: `+nan%`.

Independent row checks found positive percent returns of about `37.9%` and negative percent magnitude of about `31.1%`, yielding a percent-based ratio around `1.22`. The displayed `0.91` comes from dollar P&L. Both may be computable, but the page must not present an unweighted sum of overlapping, differently sized trade percentages as strategy “total P&L” beside dollar-weighted quality metrics without an explicit contract.

### AI-01 — Unified stance does not govern trade setup

**Status:** PARTIALLY FIXED  
**Evidence:** VERIFIED CURRENT

For TSLA:

- mechanical consensus: SELL;
- combined stance: REDUCE, 100%, “Reduce on strength”;
- valuation: significantly overvalued;
- thesis: hold/trim;
- trade setup: long entry, stop, target, and 2.94x ratio.

A trade setup can be hypothetical, but the current page presents it as actionable without an override reason. The top recommendation must govern the execution block or explicitly suppress/relabel a counter-trend scenario.

### TEN-03 — Cosmetic owner-surface separation

**Status:** PARTIALLY FIXED  
**Evidence:** VERIFIED CURRENT

The customer does not see the owner cards, but its browser still initializes the corresponding owner/model data flows.

### REL-03 — Repeating customer-browser errors

**Status:** OPEN  
**Evidence:** VERIFIED CURRENT

The browser:

- initializes portfolio/ECG/regime flows;
- fetches the owner-derived portfolio;
- calls renderers for absent DOM;
- throws `className`, `textContent`, and `innerHTML` null errors;
- repeats at least one failure every refresh cycle.

This is not only cosmetic debt. It perpetuates P0 data delivery and degrades performance/log quality.

### A11Y-01 — Customer journey lacks minimum semantics

**Status:** OPEN  
**Evidence:** VERIFIED CURRENT

Fresh rendered inspection found:

- zero `h1`–`h6`;
- zero `<main>`;
- zero `<nav>`;
- zero `aria-live`;
- analysis inputs exposed as unlabeled controls, including at least three number inputs;
- no announcement contract for long-running analysis completion/failure.

Static review finds additional unnamed text/number/textarea controls. This is a release blocker for a paid retail journey, not decorative polish.

### FIN-05 — Cost-basis contract absent

**Status:** OPEN  
**Evidence:** VERIFIED CURRENT

The product correctly returns unavailable/null unrealized P&L rather than inventing it, but a portfolio decision product needs:

- per-lot or documented weighted-average cost basis;
- currency and fee treatment;
- corporate-action/migration policy;
- “unknown” versus zero distinction;
- edit/import history and auditability.

Until implemented, the UI must not imply complete performance analytics for My Book.

## P2/P3 implementation notes

### Media-type matrix

The current guard resembles:

```text
if has_body and content_type_is_present and content_type_is_not_json:
    reject
```

It must instead require an accepted JSON media type for every JSON mutation with a body, subject to intentionally documented exceptions. Origin/SameSite checks remain separate controls.

### Unknown symbols

Synchronous validation must happen before:

- position persistence;
- quota reservation;
- job creation;
- cache keys;
- external AI/provider calls.

Syntax-only validation should remain first, followed by a bounded canonical instrument lookup with explicit unavailable/unknown/error semantics.

### Mobile

True device emulation remains unavailable, not passed. Static geometry suggests that a 320px viewport may leave about 212px of content width after rail/padding while an analysis wrapper requires 220px and the main container hides overflow. This is a hypothesis to test, not a reproduced screenshot.

### Security headers and caching

HSTS, frame denial, MIME sniffing protection, referrer policy, permissions policy, and CSP are present. Phase 1 adds private/no-store, Pragma, and Vary Cookie for its role-composed and owner-domain scope. Remaining work:

- nonce/hash-based CSP without `unsafe-inline`;
- complete the same explicit cache contract across the remaining authenticated financial/account inventory;
- automated header contracts at the edge and application.

## Release blockers derived from this register

The product remains NO-GO while any of the following is true:

1. TEN-01 or TEN-02 is reopened before durable source separation, staging, and live proof.
2. FIN-03 can return an active unreconciled model version.
3. DEPLOY-01 prevents identifying and rolling back the deployed build.
4. Full tests fail on the exact merge candidate.
5. QA-01’s leak/reconciliation false positives remain in required CI.
6. Track-record output is non-finite or uses undisclosed mixed bases.
7. Recommendation and execution setup silently contradict.
8. Customer critical paths have uncaught console errors or lack required accessibility/mobile evidence.
