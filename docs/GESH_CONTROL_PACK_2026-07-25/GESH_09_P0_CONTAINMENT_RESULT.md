# GESH 09 — Phase 1 P0 Containment Result

**Evidence date:** 2026-07-25, Europe/Sofia  
**Phase verdict:** **PHASE 1 COMPLETE AS A CLEAN, REVIEWED, UNDEPLOYED CANDIDATE**  
**Product/release verdict:** **NO-GO**  
**Production deployment:** **NOT PERFORMED**  
**Model Book publication domain:** **NOT IMPLEMENTED; PHASE 2**

## 1. Provenance

| Field | Exact value |
|---|---|
| Recorded base SHA before edits | `68287dfd80eab51b40d4270eab07b0bd73267992` |
| Base source | Exact then-current `origin/main` fetched before worktree creation; clean; includes squash-merged PR #105 |
| Fresh worktree | `/Users/georgigaydarov/Projects/gesh-phase1-p0-containment` |
| Branch | `codex/p0-data-boundary-containment-20260725` |
| Clean implementation commit | `8ff34b531b31248d71178c6ed23cb88182cf1724` |
| Final control-pack commit | `$Format:%H$` |
| Runtime ancestry requirement | Base must be an ancestor of the exact build commit |

The final-commit placeholder above is a standard Git `export-subst` field. The committed source cannot contain its own hash; `git archive` renders the exact containing commit. After that clean commit exists, the final artifact is rebuilt and its adjacent manifest independently records the literal final SHA. The candidate sidecar hashes recorded below identify the implementation commit only.

The original dirty project worktree was not modified. No merge, push, pull request, staging deployment, production deployment, account mutation, trade, or data migration was performed.

## 2. Scope and root cause

Phase 1 implemented only P0 containment for:

- `/api/portfolio`;
- `/api/trades`;
- `/api/trades/stats`;
- every customer web/Android fetch, retry, hidden component, or polling loop that consumed those owner-domain flows;
- exact-commit build identity and immutable candidate packaging needed to prove the result.

The backend root cause was principal policy, not successful query-parameter IDOR: authenticated customers were intentionally allowed to reach handlers backed by a singleton owner brokerage snapshot/cache and global trade tables. Generic response scaling/masking could not make that provenance safe and could not reliably remove arbitrary notes.

The frontend root cause was role separation by presentation inside one shared runtime document. Owner markup could be absent while owner loaders, retries, intervals, and renderers still executed.

Phase 1 therefore denies before the handler/cache/repository boundary and composes the customer document without owner code. It does not reinterpret or implement the durable Model Book design.

## 3. Endpoint contracts: before and after

### Before Phase 1

| Principal and endpoint | Pre-containment behavior |
|---|---|
| Customer `GET /api/portfolio` | 200 model-book projection derived from the owner snapshot; observed `source=robinhood_mcp`, holdings, cash/buying-power/account values |
| Customer `GET /api/trades` | 200 global trade rows, including a non-paper row and private free-text note |
| Customer `GET /api/trades/stats` | 200 statistics from the same unscoped global domain |
| Query variants | `view`, `user`, `user_id`, `account`, and `account_id` did not switch identity; they reached the same unsafe authorized dataset |
| Owner | Existing owner handlers and data sources |

### After Phase 1

For an authenticated non-owner whose request is admitted by the API rate limiter:

```http
GET or HEAD /api/portfolio
403
Cache-Control: private, no-store
Pragma: no-cache
Vary: Cookie
```

```json
{
  "detail": "Owner data is unavailable to customer accounts.",
  "code": "model_book_unavailable"
}
```

```http
GET or HEAD /api/trades
GET or HEAD /api/trades/stats
403
Cache-Control: private, no-store
Pragma: no-cache
Vary: Cookie
```

```json
{
  "detail": "Owner data is unavailable to customer accounts.",
  "code": "customer_trade_ledger_unavailable"
}
```

Contract notes:

- unauthenticated requests retain the existing 401 contract;
- rate-limited requests retain the existing 429 contract;
- owner requests bypass the customer deny policy and retain the legitimate handlers;
- exact-path policy ignores query parameters for principal selection;
- denial happens before owner connector, snapshot/cache, trade repository, row serializer, or response masker;
- denial logs contain method/path/suppressed-count metadata only and are throttled;
- warmed owner responses cannot be replayed to customers.

## 4. Customer-client containment

`templates/dashboard.html` is the customer/shared shell. The server composes `templates/owner_console.html` only when the initial signed-session render resolves to owner, and that server-side role starts the owner runtime. `/api/me` subsequently and independently reconciles the UI role; it is not the gate that inserts or starts the partial. A customer initial render receives no owner partial.

The customer document does not contain:

- `/api/portfolio`, `/api/trades`, or `/api/trades/stats`;
- the owner portfolio/ECG loader or retry path;
- the exact global regime loader;
- portfolio-history sparkline requests;
- owner section-status/topbar refresh polling;
- owner alerts/macro loaders;
- owner command-palette destinations;
- owner loader definitions or the 30-second owner refresh interval.

The owner partial retains the legitimate owner functions:

- `loadPortfolio`;
- `loadRegime`;
- `loadPortfolioWithECG`;
- `runScanner`;
- `loadKpiSparklines`;
- `refreshSectionBadges`;
- owner command-palette and status/refresh behavior.

Android removes `ApiService.getPortfolio`, initialization fetch, and the 30-second auto-refresh job/loop. `PortfolioViewModel.loadPortfolio()` returns a deterministic unavailable state without a network request. The unrelated deep-analysis client remains intact.

## 5. Changed files and functions

### Production, client, build, and CI files

| File | Changed functions/surface |
|---|---|
| `.github/workflows/tests.yml` | Pinned test requirements; expanded customer-isolation lane; blocking real Chromium customer-browser job |
| `server.py` | `_CUSTOMER_DENIED_OWNER_READS`, `_PRINCIPAL_SENSITIVE_READS`, `_mark_private_no_store`, `_customer_view_middleware`, `_security_headers_middleware`, `api_version`, fixed adjacent `BUILD_INFO_PATH` |
| `templates/dashboard.html` | Customer/shared role composition; fail-closed `applyRoleNav`/`switchTab`; removed owner loaders/retries/polls/components |
| `templates/owner_console.html` | Owner-only `loadPortfolio`, `loadRegime`, `loadPortfolioWithECG`, `runScanner`, `loadKpiSparklines`, `refreshSectionBadges`, owner status/command behavior |
| `android/GeshFinancial/app/src/main/java/com/gesh/financial/network/ApiService.kt` | Removed `getPortfolio` |
| `android/GeshFinancial/app/src/main/java/com/gesh/financial/viewmodel/PortfolioViewModel.kt` | Fail-closed initialization and `loadPortfolio`; removed auto-refresh job/loop; preserved `runDeepAnalysis` |
| `build_identity.py` | `validate_build_info`, `load_build_info`, exact safe build-info schema |
| `release_artifact.py` | Exact allowlists; clean/SHA/ancestry gates; deterministic USTAR; JUnit provenance parser; manifest/build-info generation |
| `scripts/build_release_artifact.py` | Release-builder CLI bootstrap |
| `requirements-test.txt` | Exact pytest, pytest-cov, Ruff, and Playwright pins |
| `.gitattributes` | Canonical Markdown hard-break policy and final-commit export substitution |

### New regression tests

- `tests/test_p0_api_contract.py`
- `tests/test_p0_repository_cache_boundary.py`
- `tests/test_p0_customer_frontend.py`
- `tests/test_p0_customer_browser.py`
- `tests/test_build_identity.py`
- `tests/test_release_artifact.py`

`tests/conftest.py` records exact Git commit and clean/dirty state into JUnit evidence.

### Reconciled legacy tests

- `tests/test_customer_endpoint_isolation.py`
- `tests/test_customer_leak_sweep.py`
- `tests/test_data_fetcher_fallback.py`
- `tests/test_e2e_subscriber_flow.py`
- `tests/test_equity_summary_semantics.py`
- `tests/test_financial_invariants.py`
- `tests/test_holdings_integrity.py`
- `tests/test_model_book_reconciliation.py`
- `tests/test_model_portfolio_boundary.py`
- `tests/test_portfolio_contract_compat.py`
- `tests/test_raw_snapshot_scaling.py`
- `tests/test_reconciliation_contract.py`

Unsafe customer-200 expectations were inverted to exact 403 contracts. Dormant financial publisher/scaler math remains covered directly. No test was skipped, weakened, deleted, quarantined, or xfailed.

### Canonical documents

`GESH_00` through `GESH_08` now distinguish verified pre-containment live evidence, the exact clean base, the Phase 1 candidate, and the still-unidentified production runtime. This file is the added Phase 1 result.

## 6. Test reproduction and classification

### Exact clean-base reproduction

```bash
venv/bin/pytest -p no:cacheprovider \
  --basetemp /tmp/gesh-phase1-baseline/full -q tests/ \
  --cov=. --cov-report=term --cov-fail-under=45 \
  --junitxml=/tmp/gesh-phase1-baseline/baseline-full.xml
```

Result: **2,078 passed, 0 failed, 0 skipped, 3 warnings; 61.75% coverage; 68.53 seconds**.

The previously reported dirty-tree four-failure result did not reproduce from current clean main. It remains historical evidence: one environment contamination artifact and three genuine regressions in that different uncommitted tree. No unrelated production behavior was changed for them.

### Test-first containment evidence

The initial new backend/client containment set against the unsafe contract produced **54 failed, 16 passed**. The original real-browser trace also failed because owner command-palette/runtime code remained in the customer document. These failures established the red gate before implementation.

The first broader run after changing the endpoint contract produced 32 failures. They were legacy tests that positively required customer 200 responses from the owner-domain routes, not unrelated production regressions. Their unsafe HTTP expectations were inverted while their financial serializer/scaler mathematics remained covered directly. No failure was hidden or suppressed.

### Clean implementation-commit full suite

```bash
venv/bin/pytest -p no:cacheprovider \
  --basetemp /tmp/gesh-phase1-evidence/code/basetemp -q tests/ \
  --cov=. --cov-report=term --cov-fail-under=45 \
  --junitxml=/tmp/gesh-phase1-evidence/code/full.xml
```

Result at `8ff34b531b31248d71178c6ed23cb88182cf1724`: **2,206 passed, 0 failed, 0 skipped, 3 warnings; 62.52% coverage; 133.02 seconds**.

JUnit SHA-256: `2ffdd119c03a923a64348d77a3d3282da3ceaea419892995c1816a9881401eef`; properties: exact implementation commit and `git_dirty=false`.

### Focused/latest gates

```bash
venv/bin/pytest -p no:cacheprovider \
  --basetemp /tmp/gesh-p0-final-targeted-2 -q \
  tests/test_release_artifact.py \
  tests/test_build_identity.py \
  tests/test_p0_api_contract.py \
  tests/test_p0_repository_cache_boundary.py \
  tests/test_raw_snapshot_scaling.py
```

Result: **96 passed, 0 failed, 0 skipped, 1 warning**.

```bash
venv/bin/ruff check .
```

Result: **passed**.

```bash
uv pip check --python venv/bin/python
```

Result: **81 installed packages compatible**.

```bash
android/GeshFinancial/gradlew -p android/GeshFinancial \
  testDebugUnitTest --no-daemon
```

Result: **BUILD SUCCESSFUL; 21 tasks; Kotlin/Java unit-test tasks had no sources**.

The full suite includes the real Chromium two-refresh regression. Its isolated run passed one browser test with one dependency deprecation warning in approximately 66 seconds.

## 7. Immutable artifact and version identity

### Candidate runtime proof

| Field | Value |
|---|---|
| Artifact | `gesh-financial-runtime.tar` |
| Format | normalized sorted USTAR |
| Runtime members | 80 exact allowlisted files |
| Size | 1,556,480 bytes |
| SHA-256 | `a0d8a80a0255e393675352422992eb343df30bb60c016b17ec659d3e8324e345` |
| Candidate build commit | `8ff34b531b31248d71178c6ed23cb88182cf1724` |
| Candidate `built_at` | `2026-07-25T16:33:03Z` |
| Candidate build-info SHA-256 | `7c913b12a68cae61f76d6b8ffc84dae284ea150b50e9a86243541e90402d11fb` |
| Candidate manifest SHA-256 | `de3663904afcbf5cdaf612b051a237f7a1d65c05d97e18b1643f6942c1d32baa` |
| Production deployed | `false` |

The runtime archive is built from committed blobs, not the mutable filesystem. Control-pack documentation and generated sidecars are excluded from the runtime tar, so the documentation-only final commit must reproduce the same runtime digest while its adjacent build-info/manifest records the literal final SHA.

The archive includes the complete required local runtime import closure, including the tracked `trading` package and the allowlisted runtime launcher. It excludes tests, Android sources, control-pack documents, live databases, snapshot files, config, ad-hoc debug/patch/operator scripts, and generated identity.

The extracted candidate was smoke-imported in isolation with its adjacent build-info. It returned the exact safe version payload, denied all three tampered customer routes, and rendered a customer document with zero removed endpoint literals.

`/api/version` returns only:

```json
{
  "service": "gesh-financial",
  "commit_sha": "<exact 40-character lowercase SHA>",
  "built_at": "<UTC timestamp to the second>",
  "dirty": false
}
```

Missing, oversized, malformed, extra-key, dirty, invalid-SHA, or invalid-time sidecars return a generic 503 without path/content/environment disclosure.

## 8. Security proof

| Required proof | Candidate evidence |
|---|---|
| Customer never receives `source=robinhood_mcp` | Exact 403 denial occurs before source selection; recursive sentinel assertions and extracted-artifact smoke find no source marker |
| No owner holdings/cash/buying power/account values | Backend arbitrary nested sentinels, legacy leak sweeps, repository-seam bombs, and response-body assertions |
| No owner trades/statistics/private notes | Both routes deny before repository; arbitrary free-text note and real-row sentinels never serialize |
| Query tampering cannot change principal scope | Identity-like query/header variants retain exact customer 403 and never touch owner seams |
| Warmed owner cache cannot reach customer | Owner-first warm/replay tests for endpoint responses and role-composed `/`; private/no-store/Pragma/Vary asserted |
| Customer frontend makes zero removed requests | Static customer/Android inventories plus real Chromium network trace across two refresh intervals |
| Owner behavior preserved | Owner session bypasses customer deny policy; existing handlers and owner partial/loaders remain; regression tests exercise owner responses |
| Direct denials do not bypass abuse controls | Denial path uses the API rate limiter and returns existing 429 when limited |
| Denial logs do not leak principal/data | Metadata-only method/path/suppressed-count log; throttled; no email, query, holdings, balances, rows, or notes |
| Release evidence cannot be trivially forged by malformed JUnit | Exact SHA/clean properties, logical nonnegative counts, green status, finite nonnegative duration, evidence digest, and strict JSON |

Three independent current-tree reviews—general code, Python, and security—returned no findings. The required Grok 4.5 security reconciliation independently confirmed the boundary, cache, log/rate-limit, evidence, and allowlist fixes.

## 9. Rollback

### Before deployment

No runtime rollback is required because no deployment occurred. The candidate artifact is external to the repository and production. Discarding the branch or artifact does not modify production data.

### If a later authorized deployment fails

1. Keep the backend customer deny policy active. Do not roll back to customer-readable owner endpoints.
2. If only the customer document fails, serve a customer-lite/maintenance shell while retaining the three backend denials.
3. Remove traffic from an unknown/mismatched SHA.
4. Promote the prior known-good immutable artifact only if it also contains the Phase 1 deny policy; otherwise use maintenance mode.
5. Verify `/api/version` against the selected manifest.
6. Re-run exact customer 403/tamper/cache/connector/repository/browser smoke.
7. Do not modify, delete, migrate, or republish owner snapshots, global trade rows, or notes during rollback.

A Git revert of `8ff34b531b31248d71178c6ed23cb88182cf1724` would restore the unsafe customer contract and is therefore **not an acceptable production rollback**.

## 10. Remaining risks and gates

- No production deployment or post-deploy canary was performed; production remains the pre-existing unidentified runtime.
- No staging promotion or rollback drill was performed.
- The complete immutable Model Book schema, consent/quarantine, approval, assignment, publication event, entitlement, version serving, and atomic rollback pointer do not exist.
- The global owner snapshot and trade tables remain internal legacy domains; Phase 1 denies customer access rather than migrating them.
- FIN-03/FIN-04/FIN-06 publication reconciliation and downstream quantitative integrity remain open.
- Track Record methodology/finite benchmark, recommendation/setup coherence, unknown-symbol-before-spend, cost basis, accessibility, and real mobile/device gates remain open.
- CSP still permits inline execution; the broader authenticated private-cache inventory remains open outside the Phase 1 route/root scope.
- Artifact signing/SBOM, staging schema/migration identity, production start-time identity, and operational promotion records remain future release controls.
- The release score remains **44/100**. No paid/customer release, promotion, or marketing claim is authorized.

## 11. Phase 2 only

The next architecture phase may create the separately curated immutable Model Book and structured model trade-event domains, using synthetic/paper provenance by default and explicitly consented/quarantined real-source ingestion only by exception.

Phase 2 must not:

- reopen the owner-domain routes as a dynamic proxy;
- copy existing owner snapshots or private notes into customer records;
- weaken the Phase 1 deny, cache, client, test, or artifact gates;
- treat an allowlist/serializer as consented publication;
- deploy without separate authorization, staging proof, rollback proof, and exact live identity.
