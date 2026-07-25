# GESH 06 — Test, CI, Staging, and Release-Gate Plan

**Evidence cutoff:** 2026-07-25  
**Current test verdict:** Exact clean base and Phase 1 candidate suites pass, and Phase 1 boundary/artifact gates are implemented. Durable publication, staging, accessibility/mobile, rollback, and live identity remain incomplete. **No paid/customer release is permitted.**

## 1. What the current test evidence actually says

| Baseline | Command/evidence | Current result | Interpretation |
|---|---|---|---|
| Checked dirty H (`09f757c3`) — focused audit matrix | Exact command below | **436 passed in 19.75s** | Strong evidence for the selected fixtures. It does not prove that the test contracts are safe, that other tests pass, or that live runs this code. |
| Checked dirty H — full CI-style suite | Exact command below | **1,639 passed, 4 failed, 59.15% coverage, 975 warnings** | One failure was caused by verification-time `GESH_DB_PATH` import contamination and passed under the normal workflow environment. Three are genuine dirty-tree regressions: `ai_analyzer.py` uses `client.messages.stream()`, while fakes in `test_ai_analyzer_caching.py` and `test_analyze_ticker.py` provide only `.create()`. |
| Checked dirty H — lint | `./venv/bin/ruff check tests/` | **Passed** | Tests are lint-clean; product code was not certified by this command. |
| Historical checked dirty H — customer-isolation subset | Exact command below | **83 passed** | Historical false-green: the suite treated scaled `model_book`, `/api/trades`, and `/api/trades/stats` responses as permitted customer behavior. |
| Historical PR #105 P (`fe65a816`) | GitHub Actions run `30150094753`, Python 3.12.13 | **2,077 passed, 1 skipped, 62.16% coverage; customer-isolation job 83 passed** | Historical evidence; PR #105 was subsequently squash-merged into recorded base `68287dfd`. |
| Recorded clean base M (`68287dfd`) | Fresh locked Python 3.12.13 full run | **2,078 passed, 0 failed, 0 skipped; 61.75% coverage; 3 warnings** | Exact pre-change baseline. Historical dirty four-failure result did not reproduce; no unrelated product fix was added. |
| Phase 1 implementation (`8ff34b5`) | Exact full command with commit-bound JUnit | **2,206 passed, 0 failed, 0 skipped; 62.52% coverage; 3 warnings** | Clean candidate proof. Unsafe P0 expectations are inverted; browser, repository/cache, identity, and artifact tests are included. Not production proof. |
| Live L | Browser/API checks | Product checks reproduced both verified fixes and P0 defects | No build/SHA endpoint means live cannot be mapped to M, H, or P. |

### Exact focused command already run

```bash
./venv/bin/pytest -p no:cacheprovider --basetemp "$VERIFY_TMP_DIR/pytest" -q \
  tests/test_holdings_integrity.py tests/test_day_change_contract.py \
  tests/test_portfolio_valuation.py tests/test_chart_endpoint.py \
  tests/test_chart_range_contract.py tests/test_owner_surface_isolation.py \
  tests/test_csrf_hardening.py tests/test_model_portfolio_boundary.py \
  tests/test_model_book_reconciliation.py tests/test_mybook_job_control.py \
  tests/test_analyze_progress_ux.py tests/test_data_freshness.py \
  tests/test_degraded_provenance.py tests/test_unknown_symbol_honesty.py \
  tests/test_mybook.py tests/test_account_data_rights.py tests/test_preferences.py \
  tests/test_watchlist.py tests/test_customer_leak_sweep.py \
  tests/test_e2e_subscriber_flow.py tests/test_track_record_presentation.py \
  tests/test_proof_page_gate.py tests/test_decision_quality.py tests/test_customer_view.py
```

### Exact full command already run

```bash
./venv/bin/pytest -p no:cacheprovider --basetemp "$VERIFY_TMP_DIR/pytest" -q \
  tests/ --cov=. --cov-report=term --cov-fail-under=45
```

### Exact Phase 1 clean-candidate full command

```bash
venv/bin/pytest -p no:cacheprovider \
  --basetemp /tmp/gesh-phase1-evidence/code/basetemp -q tests/ \
  --cov=. --cov-report=term --cov-fail-under=45 \
  --junitxml=/tmp/gesh-phase1-evidence/code/full.xml
```

JUnit properties bind the evidence to `8ff34b531b31248d71178c6ed23cb88182cf1724` and `git_dirty=false`. The release builder parses and rejects failures, errors, zero/negative/inconsistent counts, dirty/mismatched SHA properties, non-finite/negative duration, and non-finite JSON.

### Exact isolation subset already run

```bash
./venv/bin/pytest -p no:cacheprovider -q \
  tests/test_customer_leak_sweep.py \
  tests/test_dashboard_xss_wiring.py \
  tests/test_customer_view.py
```

## 2. Test policy

1. **The contract comes before the implementation.** A test may not bless transformed owner data merely because legacy/pre-containment code intentionally returned it.
2. **Every financial assertion uses `Decimal` or integer cents.** `pytest.approx` remains acceptable only for market-model statistics where an explicit tolerance is part of the contract, not for accounting identities.
3. **Every customer-boundary fixture contains unmistakable sentinels.** Sentinels cover arbitrary text, identifiers, symbols, quantities, prices, cash, buying power, notes, account numbers, source labels, timestamps, and nested objects.
4. **All tests are offline and database-isolated by default.** No production DB, live Robinhood connector, real email, real billing, real LLM, or mutable live endpoint is used in CI.
5. **A release blocker cannot be skipped.** Missing credentials or a missing staging service must fail the release job, not convert it to a passing skip.
6. **The deployed artifact is tested, not a mutable checkout.** Tests record and compare build SHA, dependency-lock hash, schema version, and artifact digest.
7. **“Fixed” requires four proofs:** a failing regression on the unsafe baseline; passing unit/integration/contract tests on the candidate; passing staged browser/security tests; and a passing post-deploy canary on the exact promoted SHA.

## 3. Required deterministic fixtures

| Fixture | Required contents | Purpose |
|---|---|---|
| `owner_broker_snapshot_v1` | Ten synthetic holdings; unique account ID; `source=robinhood_mcp`; cash, settled/unsettled amounts, buying power, margin, prior close, options/crypto/unpriced sleeves, quote sessions, avg costs, arbitrary note-like strings, timestamps | Detect any owner/broker provenance crossing the customer boundary and exercise real production field semantics. |
| `owner_broker_snapshot_v2` | One changed quantity, one new symbol, changed cash and timestamp | Prove owner state cannot change customer output until an explicit publication is approved. |
| `model_publication_v1` | Synthetic/paper source, immutable UUID/version, approved holdings, calculated cash/total, published-at/data-as-of, allowlisted methodology fields | Canonical safe model output. |
| `model_publication_draft` | Same shape but `status=draft` and no active pointer | Prove drafts never leak. |
| `model_publication_revoked` | Previously active version with revocation event | Prove revoked data stops serving and rollback is auditable. |
| `customer_alpha`, `customer_beta` | Separate immutable UUIDs; different MyBook, watchlist, preferences, analyses, jobs, quotas, and string/number sentinels | Tenant, cache, export, mass-assignment, and IDOR testing. |
| `owner_user` | Owner UUID and role, separate from both customers | Positive owner-path tests without making customer tests rely on owner data. |
| `trade_real` | `is_paper=0`, arbitrary free-text note, execution metadata, brokerage references, unique numeric/string sentinels | Must never appear in customer output. |
| `trade_model_paper` | Explicit publication/version ID, paper/synthetic source, allowlisted fields only, no arbitrary text | Safe alternative if product retains a customer model-trade feed. |
| `market_data_clocked` | Fixed quotes, prior closes, missing quote, stale quote, delayed quote, split-adjusted quote, provider and retrieval timestamps | Financial, freshness, and fallback tests. |
| `symbol_catalog` | Active, invalid, delisted, suspended, ambiguous, crypto-like, Unicode/control, long, and syntactically valid nonexistent symbols | Synchronous validation before persistence/spend. |
| `track_quality_matrix` | Positive raw trade-return sum with PF<1; negative return with PF>1; NaN/Inf SPY; overlapping trades; unequal sizing; cash flows | Track Record arithmetic and responsible framing. |
| `malicious_external_text` | News/filing text containing prompt instructions, HTML, URLs, and fake tool commands | Indirect prompt-injection and output-sanitization tests. |
| `fake_clock` | Market open/closed, stale boundary, quota window, job timeout, publication activation, DST transition | Removes time-based flakiness. |

Fixture files must be reviewed like code. They may use realistic shapes but never copied production values, account IDs, notes, email addresses, tokens, or brokerage records.

## 4. Financial contract and invariant tests

These formulas are release contracts, not display suggestions.

### Canonical definitions

For each priced position \(i\):

```text
position_market_value_i = quantity_i × current_price_i
position_prior_value_i  = quantity_i × previous_close_i
positions_market_value  = Σ position_market_value_i
positions_prior_value   = Σ position_prior_value_i
```

If the product definition includes cash:

```text
calculated_total_equity = positions_market_value + included_cash
prior_close_equity      = positions_prior_value + same_included_cash_basis
day_change_amount       = calculated_total_equity - prior_close_equity
day_change_percent      = day_change_amount / prior_close_equity × 100
holding_weight_i        = position_market_value_i / calculated_total_equity
cash_weight             = included_cash / calculated_total_equity
Σ holding_weight_i + cash_weight = 100%
```

Rules:

- Round only at the published boundary; keep exact decimal intermediates.
- `reported_account_equity` is preserved as a separate broker fact. It must never silently replace `calculated_total_equity`.
- If options, crypto, unsettled cash, pending deposits, or unpriced positions are not itemized, return an explicit `unavailable`/`excluded_sleeves` block and `reconciles:false`.
- Missing prior close makes day change unavailable, not zero.
- Missing cost basis makes unrealized P&L unavailable, not zero.
- A model publication must reconcile to its own complete published components before activation.
- Track-record return must declare whether it is per-trade-return sum, dollar P&L, time-weighted return, or money-weighted return. Only an actual portfolio-return series may be compared directly with SPY.

### Required unit/property tests

In the following test tables, `RB` means **release blocker** and `NB` means a nonblocking follow-up. These are test-gate labels, not finding severities; the two current P0 findings are TEN-01 and TEN-02.

| Test ID | Test | Gate | Current coverage |
|---|---|---:|---|
| **FIN-U001** | Quantity × price equals position market value over fractional, large, and sub-cent cases. | RB | Partial |
| **FIN-U002** | Positions + included cash equals calculated total in integer cents. | RB | Partial; existing model tests can reconcile by construction. |
| **FIN-U003** | Broker reported total remains separate; every delta is exact and signed. | RB | Partial |
| **FIN-U004** | Day change uses matched current/prior components, never cost basis. | RB | Good fixture coverage; needs real-shape normalization path. |
| **FIN-U005** | Null/zero/missing cost basis returns unavailable and blocks gain-based advice. | RB | Missing end-to-end |
| **FIN-U006** | Holding weights plus the explicit cash weight sum to 100% of total model equity; unpriced positions are separately disclosed and block publication where required. | RB | Partial |
| **FIN-U007** | Kelly share delta uses the canonical versioned book total and explicit rounding direction. | RB | Partial |
| **FIN-U008** | VaR/Monte Carlo consume the same book version and fail closed on unreconciled inputs. | RB | Missing |
| **FIN-P009** | Property-based invariant test across random holdings, cash, missing data, and precision. | RB | Partial |
| **TRK-U001** | PF = gross dollar gains / absolute gross dollar losses; zero-loss branch is explicit. | RB | Partial |
| **TRK-U002** | Win rate, expectancy, drawdown, Sharpe, and deflated Sharpe share one exact cohort and date window. | RB | Partial |
| **TRK-U003** | NaN, Inf, missing, and stale benchmark values render unavailable. | RB | Missing |
| **TRK-U004** | Unequal position sizing fixture demonstrates why PF and raw return-sum can diverge; labels remain accurate. | RB | Missing |

## 5. Tenant, publication, trade, and security tests

### Current-P0 customer-boundary sentinels

| Test ID | Required test | Release contract | Current coverage |
|---|---|---|---|
| **TEN-I001** | Customer routes with `build_data_payload`, Robinhood MCP, owner DB, and trade DB patched to raise. | `/`, `/api/me`, `/api/mybook`, analysis, charts, customer export, preferences, and watchlist still succeed without touching an owner connector. | Phase 1 target routes pass before owner seams; broader all-customer-route inventory remains partial |
| **TEN-I002** | Owner snapshot changes between two customer requests. | Customer model bytes, version, and `published_at` remain identical until an explicit activation transaction. | Missing |
| **TEN-I003** | Draft, failed-validation, unapproved, revoked, and unassigned publications. | None can be served. Active immutable version is served atomically. | Missing |
| **TEN-I004** | Customer `/api/portfolio` contract. | Return the customer’s own book or an entitled immutable curated/synthetic model version. Never return owner `source`, account totals, buying power, cost basis, quote sessions, raw timestamps, or an owner-derived request-time snapshot. | Phase 1 exact 403/source/value/tamper/repository/cache contract passes; durable publication missing |
| **TEN-I005** | Customer `/api/trades` and `/api/trades/stats`. | Default target: 403/404. If model trades are retained, only versioned synthetic/paper rows pass a build-up serializer; arbitrary text and real rows are forbidden. | Phase 1 exact 403/real-row/stat/note/repository/cache contract passes |
| **TEN-I006** | Two-customer cache and single-flight collision matrix. | Cache key includes immutable user/publication/version; no cross-user job, result, quote-derived total, or export. | Phase 1 owner/customer warmed endpoint and role-document cache replay passes; broader job/result matrix partial |
| **TEN-I007** | Publication assignment/entitlement. | Customer A can read assigned version; customer B cannot; revocation is immediate and audited. | Missing |
| **TEN-I008** | Publication audit log. | Actor, source class, consent reference, draft hash, active version, validation result, activation, rollback, and revocation are append-only. | Missing |
| **TEN-C009** | Recursive response key/value sentinel scan. | No owner string or number appears anywhere in any customer response, HTML, browser storage, network body, log, or error. | Phase 1 target responses/HTML/network/log metadata pass arbitrary source/value/note sentinels; global all-route/storage inventory remains partial |

### Authentication and authorization contracts

| Test ID | Required test | Gate |
|---|---|---:|
| **ACL-C001** | Generate inventory from every registered route × method × role; require an explicit public/customer/owner/internal policy entry. New uncategorized routes fail CI. | RB |
| **ACL-C002** | Customer matrix for `/api/config`, owner pages, usage, watch, invest, trading actions, scanner mutation, admin and debug surfaces. | RB |
| **SEC-C003** | Two tenants + owner; inject `view`, `role`, `tier`, `email`, `user`, `user_id`, `account`, `account_id`, `is_owner`, and admin-like headers in query/body. | RB |
| **SEC-C004** | Session token purpose/type, expiry, revocation, subject UUID immutability, cookie Secure/HttpOnly/SameSite, and session fixation. | RB |
| **SEC-C005** | Object-level authorization on every user-owned row and job, including numeric/UUID enumeration. | RB |
| **SEC-C006** | Account deletion method, recent re-authentication, exact confirmation, origin/CSRF, audit event, and staging-only purge. | RB |

### Request and web-security contracts

| Test ID | Required test | Gate |
|---|---|---:|
| **WEB-C001** | Media-type matrix: absent, blank, malformed, `text/plain`, form-urlencoded, multipart, JSON with charset, valid JSON, invalid JSON, duplicate headers. | RB |
| **WEB-C002** | Same-origin and hostile Origin/Referer matrix for every mutation. | RB |
| **WEB-C003** | CSP/HSTS/frame/nosniff/referrer/permissions headers; nonce/hash CSP and no newly introduced inline script. | RB |
| **PRIV-C004** | `private, no-store` and `Pragma: no-cache` for session/account/financial responses; correct `Vary`; public market cache tested separately. | RB — Phase 1 owner-domain/root scope passes; remaining inventory open |
| **ERR-C005** | Typed, redacted API errors; accessible HTML for browser pages; no stack trace, filesystem path, provider key name, prompt, or raw exception. | RB |
| **SEC-I006** | Malicious external news/filing fixture cannot change system instructions, tools, tenant IDs, or output schema. | RB |
| **SEC-C007** | Security log records denied cross-account/object reads without sensitive payloads. | RB |

## 6. Customer data, jobs, charts, and concurrency tests

| Test ID | Required test | Gate | Current coverage |
|---|---|---:|---|
| **BOOK-I001** | POST → DB → GET MyBook → summary → report → analysis input → export. | RB | Quantity parity good; complete chain partial. |
| **BOOK-I002** | Account export for two customers, including analyses/jobs/preferences/watchlist. | RB | Good basic coverage |
| **BOOK-I003** | If portability import is added, export → clean account restore → byte/semantic comparison. | NB | No import endpoint exists. |
| **SYM-C001** | Unknown/delisted/suspended symbol preflight before any position write, reservation, queue, LLM, or artifact. | RB | Missing; existing test accepts `ZZZZ9`. |
| **JOB-I002** | One click/one fingerprint → one reservation, one job, one worker, one LLM call, bounded polling, one artifact. | RB | Partial |
| **JOB-I003** | Fake-clock timeout/crash/cancel/retry/navigation-away; reservation released or terminally accounted; result retrievable once ready. | RB | Missing timeout path |
| **JOB-I004** | Changed book while prior job runs; stale generation cannot overwrite or present as current. | RB | Good unit/integration coverage |
| **CONC-I001** | 30 same-user/same-symbol writes with paired quantity/cost sentinels. | RB | Live spot check only |
| **CONC-I002** | Simultaneous quota reservations at the limit. | RB | Good DB-level coverage |
| **CHART-C001** | 1D, 5D, 1M, 3M, 6M, 1Y, max mapping, point order, duplicate times, timezone, source, and as-of. | RB | Backend ranges good; provenance partial |
| **PREF-I001** | Preferences and watchlist two-user isolation, limits, idempotence, and restoration. | RB | Good |
| **BULK-I001** | Mixed valid/invalid import is atomic; duplicates normalized; no partial persistence. | RB | Unverified |

## 7. Browser E2E, accessibility, and mobile gates

Browser tests must run against the staged artifact with network recording, console capture, trace, screenshots, and the exact `/api/version` response attached. Phase 1 already runs a strict real-Chromium customer trace for two refresh intervals; staging/device/accessibility artifacts remain open.

| Test ID | Journey | Required assertions | Gate |
|---|---|---|---:|
| **E2E-001** | Customer login → Home → Analyze → MyBook → report → Track Record | Role/customer book clear; no owner dataset or controls; no failed requests; no console errors. | RB |
| **E2E-002** | Customer page idle for at least two refresh intervals | Zero `/api/portfolio`, `/api/trades`, ECG, owner regime, or owner-console requests unless the product explicitly displays an entitled immutable model version; no repeated timers/errors. | RB |
| **E2E-003** | Chart range switching | Visible title/range, network parameter, data dates, point count, and rendered series all change; ALL sends `max`. | RB |
| **E2E-004** | MyBook analysis | One click, immediate queued state, elapsed status, polite announcement, bounded polling, completion, cached repeat, navigation-away recovery. | RB |
| **E2E-005** | Invalid/rejected form input then valid resubmit | Accessible error; fields do not concatenate; request body equals visible normalized input; no persistence on failure. | RB |
| **E2E-006** | Track Record negative-quality fixture | Failed quality gate precedes/controls positive raw return styling; no `nan`/`inf`; cohort and benchmark bases disclosed. | RB |
| **E2E-007** | Friendly 403/404/provider error | Accessible HTML for pages; typed redacted JSON for API; focus lands on error heading/recovery action. | RB |
| **A11Y-001** | Automated axe scan of every customer route/state | Zero critical/serious violations; one `<h1>`; `<main>` and `<nav>`; logical headings; all inputs named; tables/canvas alternatives. | RB |
| **A11Y-002** | Keyboard-only flow | Skip link, logical focus order, visible focus, no trap, dialogs restore focus, chart range operable. | RB |
| **A11Y-003** | Async screen-reader semantics | `role=status`/`aria-live` announces queue, elapsed state, completion/error, verdict and chart updates without duplicating announcements. | RB |
| **MOB-001** | 320×568, 375×667, 390×844, 768×1024 portrait and landscape | No horizontal overflow, clipped input, hidden action, overlap, or offscreen dialog; tables have a documented small-screen pattern. | RB |
| **MOB-002** | Touch and zoom | 200% zoom, 44px targets, no hover-only meaning, sticky elements do not cover content. | RB |

True device emulation is mandatory. Desktop resizing or a viewport API that leaves `innerWidth` at 1280px does not satisfy MOB-001.

## 8. Performance and reliability budgets

Use five bounded sequential samples in staging, not disruptive load testing.

| Operation | Budget | Failure condition |
|---|---:|---|
| `/api/me`, cached MyBook/preferences/watchlist | p95 ≤ 500 ms | Any unexplained >1 s response or 5xx |
| Cached chart/market card | p95 ≤ 2 s | Timeout, stale-without-label, or wrong provider |
| Analysis enqueue | p95 ≤ 1 s | LLM work blocks enqueue response |
| Cached analysis delivery | p95 ≤ 2 s | Cache miss/spend or wrong fingerprint |
| Uncached MyBook analysis | terminal within declared 330 s contract | No durable terminal state, unbounded poll, reservation leak |
| Poll cadence | no more than one request per 3 s per active browser | Duplicate timer/worker loop |
| Customer idle page | zero owner-only calls and zero repeated console errors | Any owner read or recurring exception |

All budgets are recorded by route, build SHA, status, cache state, provider state, and publication version. A latency improvement never permits stale, cross-tenant, or incomplete data.

## 9. CI pipeline

### Pull-request jobs

1. **`policy-inventory` — blocking**
   - Generate route × method × role inventory.
   - Fail on uncategorized route or a customer route reaching an owner connector.
   - Scan migrations and publication schemas for tenant/foreign-key/index omissions.

2. **`static-quality` — paid-release blocking**
   - Compile, format check, Ruff, type checks for changed critical modules, dependency/license/secret scan.
   - Verify tracked lock/requirements hashes and no local path dependency.

3. **`release-sentinels` — blocking, run first**
   - `TEN-I001` through `TEN-I008` plus `TEN-C009`; `FIN-U001` through `FIN-U004` plus `FIN-P009`; `ACL-C001`, `ACL-C002`, and `SEC-C003` through `SEC-C006`; `WEB-C001`, `WEB-C002`; `BOOK-I001`.
   - Patch owner connectors to raise.
   - No skips and no network.

4. **`unit-integration-py312` — blocking**
   - Full tests in a clean Python 3.12 locked environment.
   - Zero failures; warning budget cannot increase.

5. **`unit-integration-py314` — paid-release blocking**
   - Match the host/runtime interpreter if production remains on Python 3.14.
   - Same release sentinels plus critical financial/jobs tests.

6. **`coverage` — paid-release blocking**
   - Remove the current disagreement between workflow comment, 45% command floor, and 55% `pyproject.toml`.
   - Initial global floor: **62%**, based on the current clean PR’s 62.16%, then ratchet upward.
   - Changed lines ≥90%; tenant/publication/auth/financial serializers and current-P0 sentinel branches 100%.

7. **`frontend-contract` — paid-release blocking**
   - **Phase 1 implemented:** server-compose separate customer/owner documents; customer static/Android inventory contains no removed owner action/endpoint and real Chromium records zero matching requests.
   - Future published-model, accessibility, and device contracts remain blocking.

### Candidate/staging jobs

8. **`build-artifact` — blocking**
   - **Phase 1 implemented for the clean candidate:** exact clean HEAD/current-base ancestry, exact code/resource allowlist, deterministic USTAR, validated JUnit evidence, adjacent build identity and manifest, external output only.
   - The candidate runtime SHA-256 is `a0d8a80a0255e393675352422992eb343df30bb60c016b17ec659d3e8324e345`; normal merge/promotion remains open.
   - Actual Phase 1 manifest shape (abridged):

```json
{
  "schema_version": 1,
  "service": "gesh-financial",
  "base_sha": "<40-char base SHA>",
  "final_sha": "<40-char build SHA>",
  "built_at": "<UTC ISO-8601>",
  "clean_tree": true,
  "artifact": {
    "name": "gesh-financial-runtime.tar",
    "format": "ustar",
    "sha256": "<hash>",
    "size_bytes": "<integer>",
    "member_count": "<integer>"
  },
  "build_info": {"name": "GESH_BUILD_INFO.json", "sha256": "<hash>"},
  "requirements_lock_sha256": "<hash>",
  "runtime": {
    "python": "<exact>",
    "python_implementation": "<exact>"
  },
  "runtime_members": ["<exact allowlisted paths>"],
  "tests": ["<parsed command/count/SHA/clean JUnit evidence>"],
  "production_deployment": false
}
```

9. **`migration-restore` — blocking**
   - Copy production-shaped synthetic DBs.
   - Run forward migrations, integrity checks, row-count/checksum invariants, and application smoke.
   - Restore backup and/or execute supported rollback; prove old artifact can read the rolled-back state.

10. **`deploy-staging` — blocking**
    - Deploy the exact saved artifact, never a workspace.
    - `/api/version` must equal the artifact manifest and include process start time.

11. **`staging-contract` — blocking**
    - Run current-P0 and release-blocking API sentinels against staging.
    - Verify customer API succeeds while owner connectors are disabled.
    - Verify active publication ID/version/hash and trades policy.

12. **`browser-a11y-mobile` — paid-release blocking**
    - Run E2E-001–007, A11Y-001–003, and MOB-001–002.
    - Attach Playwright trace, screenshots, console/network logs, axe report, viewport/UA, and version manifest.

13. **`rollback-drill` — blocking**
    - Promote the previous artifact in staging.
    - Restore compatible DB/publication pointer.
    - Re-run `/api/version`, login, MyBook parity, tenant sentinels, and financial smoke.

### Suggested marker command after tests are added

```bash
./venv/bin/pytest -p no:cacheprovider -q -m release_blocker \
  --maxfail=1 --strict-markers --disable-warnings
```

The full suite remains mandatory after the fast blocker job. `release_blocker` is an ordering optimization, not a substitute.

## 10. Production release gates

| Gate | Must be true | Blocks |
|---|---|---|
| **G0 — source** | Candidate is a clean commit descended from current main; PR conflict resolved; required review approved. | Merge |
| **G1 — sentinel tests** | Every current-P0 and release-blocking sentinel passes with no skip/network/production DB. | Merge |
| **G2 — full CI** | Zero failures; declared coverage and warning budgets pass on locked Python. | Merge |
| **G3 — data architecture** | Customer portfolio is own or immutable curated/synthetic publication; customer trades are denied or curated; consent/approval/version/audit/rollback exist. | Staging promotion |
| **G4 — financial integrity** | Publication and MyBook reconcile; current/prior bases match; missing sleeves/cost basis are explicit; Track Record metrics are finite and correctly labelled. | Staging promotion |
| **G5 — staged browser** | E2E, console/network, axe, keyboard, and mobile matrix pass on exact artifact. | Production |
| **G6 — migration/rollback** | Forward migration, integrity verification, backup restore, previous artifact, and publication-pointer rollback all pass. | Production |
| **G7 — identity** | Staging and candidate report exact artifact SHA/hash/schema/start time; no mutable checkout deployment. | Production |
| **G8 — canary** | Dedicated canary customer passes read-only current-P0 and release sentinels; no owner reads/trade notes; metrics/logs clean. | Wider rollout |
| **G9 — post-deploy** | Production SHA equals candidate; login/MyBook/export parity/config guard/trade denial/publication version/security headers pass; no console/error-rate regression. | Release completion |

Any G0–G9 failure is a **NO-GO**. Disclaimers, masking, CSS hiding, a green nonblocking check, or founder acceptance cannot waive TEN-01, TEN-02, FIN-03, DEPLOY-01, or a failed rollback drill.

## 11. Deployment identity and observability

Phase 1 adds a minimal principal-neutral `/api/version` response that reads one adjacent validated release sidecar and returns only:

```json
{
  "service": "gesh-financial",
  "commit_sha": "40-lowercase-hex",
  "built_at": "YYYY-MM-DDTHH:MM:SSZ",
  "dirty": false
}
```

Missing, malformed, oversized, extra-key, dirty, or non-UTC metadata fails closed with one generic 503 contract. Artifact digest, dependency-lock hash, member inventory, and test evidence stay in the adjacent release manifest rather than expanding the public API. Schema/process-start identity remains a future deployment concern. Do not expose paths, environment values, provider names, tokens, dirty diffs, or host identity.

Required telemetry:

- customer request count by sanitized route/status/build/publication version;
- denied owner/object reads;
- customer calls to any owner connector (target: zero);
- publication validation/activation/rollback events;
- reconciliation failures and excluded-sleeve count, without dollar details;
- job queue/run/timeout/cache/reservation state;
- browser-reported exceptions by build and customer/owner shell;
- stale-provider/fallback state;
- post-deploy and rollback canary result.

## 12. Rollback plan and checkpoints

1. **Before deployment**
   - Preserve the previous immutable application artifact and manifest.
   - Back up auth/customer DB and publication DB with integrity check and encrypted retention.
   - Record active publication/version pointer and schema version.
   - Verify restore in staging before production approval.

2. **During canary**
   - Deploy candidate to a bounded canary.
   - Do not migrate destructively. Prefer additive tables/columns and dual-read only where explicitly tested.
   - If any current-P0 sentinel, reconciliation, console, or error-rate gate fails, stop rollout and promote the previous artifact.

3. **Publication rollback**
   - Change the active pointer atomically to the last known-good immutable version.
   - Append a rollback event; never mutate old version rows in place.
   - Revoke a compromised version and invalidate caches by publication/version key.

4. **Database rollback**
   - If the previous artifact is forward-compatible, leave additive schema in place and roll back code.
   - If not, stop writes, restore the verified backup, run integrity and tenant row-count checks, then start the previous artifact.
   - Never improvise a destructive migration against the only production DB.

5. **Post-rollback proof**
   - `/api/version` matches the previous manifest.
   - Login, MyBook quantity/export parity, route ACL, trade denial, active publication, and financial reconciliation pass.
   - Record incident ID, failed gate, artifact hashes, restore point, and customer-impact window.

## 13. Flake, skip, and evidence policy

- A test that reaches Yahoo, Robinhood, an LLM, email, billing, or wall-clock time is not deterministic and cannot be a blocker until replaced with a fixture/fake clock.
- A release-blocking skip is a failure.
- Quarantine requires an issue, owner, expiration date ≤7 days, and no coverage of release-blocking contracts. Tenant, financial, auth, migration, and rollback tests may never be quarantined.
- CI stores JUnit, coverage, route inventory, artifact manifest, migration report, browser trace, console/network log, screenshots, axe report, and rollback proof for every candidate.
- The test report must name the exact baseline and distinguish M, H, P, staging artifact, and production artifact.

## 14. Existing tests that must be inverted or supplemented

1. **`tests/test_customer_leak_sweep.py`**
   - **Phase 1 resolved:** now requires exact customer 403 on `/api/portfolio`, `/api/trades`, and `/api/trades/stats`; proves owner source/numbers/notes are absent and owner seams are untouched.
   - Future: replace deny mode only with a separately curated versioned publication domain.

2. **`tests/test_model_portfolio_boundary.py`**
   - Current: build-up field allowlist over a request-time owner payload.
   - Required: keep allowlist, then add immutable persisted version, approval/consent, entitlement, stable bytes, activation, revocation, and owner-change independence.

3. **`tests/test_model_book_reconciliation.py`**
   - Current: synthetic display can reconcile while production meanings of `portfolio_equity` differ.
   - Required: run raw MCP-shaped and normalized snapshots through real production normalizers; assert a complete bridge and excluded sleeves.

4. **`tests/test_mybook.py`**
   - Current: explicitly accepts a syntax-valid unknown `ZZZZ9`.
   - Required: synchronous existence/tradability failure before position write, job, reservation, quota, or LLM.

5. **Chart tests**
   - Current: backend `MAX` coverage.
   - Required: browser asserts an ALL/MAX control exists and sends `max`.

6. **Owner-surface tests**
   - **Phase 1 resolved:** inspect the composed customer document and execute the real customer bundle; capture network/console for two refresh intervals.

7. **Track Record tests**
   - Current: cohort ordering and disclosures.
   - Required: positive return + failed quality gate, unequal sizing, matched benchmark, and NaN/Inf presentation fixtures.

8. **Static customer tests**
   - Current: HTML/string assertions.
   - Required: axe, keyboard, live-region, focus, canvas alternative, and true device-emulation gates.

## 15. Current gate state

| Gate area | State on 2026-07-25 |
|---|---|
| P0 tenant/publication sentinels | **PASS ON PHASE 1 CANDIDATE — denial/owner seam/cache/tamper/source/note proofs; durable publication still absent** |
| Real trade/note customer denial | **PASS ON PHASE 1 CANDIDATE — exact 403 before repository access; not deployed** |
| Financial model-book bridge | **FAIL — live `reconciles:false` with unexplained `$28,616.88` holdings-to-portfolio gap** |
| Clean base/final-candidate suite | **PASS — base 2,078; implementation 2,206; zero failed/skipped** |
| PR/base integration | **PARTIAL — PR #105 is in recorded base; Phase 1 branch is clean/reviewed but not merged** |
| Coverage policy | **FAIL — contradictory thresholds** |
| Accessibility/mobile | **FAIL/UNABLE — no blocker suite and current a11y defects** |
| Candidate artifact test | **PASS LOCALLY — deterministic digest/import/version/customer-denial smoke; staging still FAIL/not run** |
| Runtime SHA identity | **PARTIAL — safe candidate contract exists; live source remains unidentifiable** |
| Rollback drill | **FAIL — no evidence supplied** |

Phase 1 completed the P0 boundary red/green cycle. The next valid engineering move is Phase 2 durable publication plus the still-failing financial, staging, accessibility/mobile, rollback, and production-identity gates—not reopening the owner-domain endpoints.
