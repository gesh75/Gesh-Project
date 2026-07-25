# GESH 00 — Executive State

**Canonical evidence cut:** 2026-07-25, Europe/Sofia  
**Product:** `https://pay.geshfinance.com`  
**Repository:** `gesh75/Gesh-Project`  
**Decision:** **NO-GO for paid/customer release or promotion**

## Current verdict

GESH is a substantial working fintech application, not a prototype shell. It has authentication, a customer-owned portfolio ledger (“My Book”), a shared research dashboard, market-data charts, multi-engine and AI analysis, position sizing and simulation, watchlists and preferences, a public track-record page, an owner/operator surface, background analysis jobs, and Robinhood-derived operator data.

The customer-owned part of the product is now materially healthier than it was on 2026-07-22. The live test account’s TSLA quantity is consistent between `/api/mybook` and `/api/account/export`; customer-book valuation and day-change arithmetic reconcile; chart ranges return distinct datasets; customer access to `/api/config` is denied; unsafe `text/plain` mutation is rejected; owner/admin routes resist harmless query and mass-assignment tampering; and controlled same-symbol writes did not create duplicate or torn rows.

That progress does **not** make the current product commercially releasable. Two customer-reachable routes still cross the intended data-product boundary:

1. `/api/portfolio` returns a model-book response derived from the operator’s real `robinhood_mcp` snapshot rather than from an independently published model dataset.
2. `/api/trades` returns a global non-paper trade row and an internal free-text note.

Those are **VERIFIED CURRENT P0** findings. They are not ordinary anonymous access or a query-parameter IDOR. Authentication and many route guards work. The failure is deeper: current code intentionally authorizes a customer to a dataset whose provenance, consent, publication state, and sanitization are not suitable for that customer-facing use.

The newest remediation candidate, PR #105, is not the deployed truth and does not close either root architecture. It is open, conflicting, and one commit behind current `main`. Its model publisher is a stronger allowlist, but remains a request-time transformation of the current owner snapshot without durable publication, consent, approval, versioning, assignment, or rollback. Its tests still encode customer-readable `/api/trades`.

## Score

**44 / 100**

| Dimension | Score | Reason |
|---|---:|---|
| Identity, privacy, tenant/data boundary | 8/20 | Authentication and UUID scoping are real; two owner/global datasets remain customer-readable. |
| Financial and quantitative integrity | 10/20 | My Book arithmetic is repaired; model portfolio does not reconcile and track-record bases are mixed. |
| Product coherence and truthfulness | 8/20 | Combined stance improved; trade setup can still contradict it, and model/real/simulated provenance is unclear. |
| Reliability, testability, and release control | 10/20 | Large test base and focused checks exist; dirty branch fails three genuine tests and live build identity is unknowable. |
| UX, accessibility, and mobile | 5/15 | Core journeys work; customer page polls absent owner widgets and throws errors; semantics and labels are missing; true mobile is unverified. |
| Operational evidence | 3/5 | Logs and process are inspectable, but no immutable deployed SHA/version or clean reproducible deployment record exists. |

This score is a fresh 2026-07-25 control score, not a continuation of the earlier 41, 43, or 58-point rubrics. The verified P0 boundary failures impose a release cap regardless of the number of smaller fixes.

## What exactly was built

### Customer product

- Signed-session authentication and an account role distinction.
- A UUID-scoped customer portfolio ledger at `/api/mybook`.
- Account export, watchlist, preferences, analyses, usage, and async-job records scoped to the authenticated UUID.
- Customer analysis for a portfolio or ticker, including mechanical votes, regime/momentum/FFT signals, AI research, Kelly sizing, and Monte Carlo output.
- Multiple chart ranges and a public track-record page.
- A customer response convention labelled `view=model_book`.

### Operator/internal product

- Owner-only operational routes and widgets.
- Robinhood/MCP snapshot ingestion and a cached portfolio snapshot.
- A global real-trade and paper-trade ledger.
- Daily deterministic analysis chains and AI/provider integrations.
- Public proof/track-record aggregation from the sibling trading data.

### Actual technical shape

- A FastAPI/Python monolith with server-rendered templates and shared browser JavaScript.
- SQLite-backed auth/customer/job data and separate global trading data.
- In-process background analysis execution plus polling.
- File/cache/provider-backed market, portfolio, and research inputs.
- A customer response middleware that adds customer-view metadata and historically performed broad monetary transformation.

## What exactly changed

### Changes verified in the live application

As of 2026-07-25:

- **FIXED AND VERIFIED — quantity parity:** TSLA is `2.7971` in both `/api/mybook` and `/api/account/export`.
- **FIXED AND VERIFIED — customer-book valuation/day change:** current equity is `$894.20`; prior-close equity is `$1,046.14`; day change is `-$151.94` or `-14.5239%`; the API reports `reconciles:true`.
- **FIXED AND VERIFIED — chart routing:** 1D, 5D, 1M, 3M, 6M, 1Y, and backend `max` return materially distinct periods and point counts.
- **FIXED AND VERIFIED — configuration guard:** customer GET and POST `/api/config` return 403.
- **FIXED AND VERIFIED — mutation media type and origin:** `text/plain` returns 415, malformed JSON returns 400, and an untrusted Origin returns 403.
- **FIXED AND VERIFIED — owner/admin route guards:** tested owner surfaces return 403 and account-deletion GET returns 405.
- **FIXED AND VERIFIED — basic input and tenant resistance:** invalid quantities/symbols are rejected; posted `role`, `admin`, or `view` fields are ignored; user/account/view query overrides do not switch identity.
- **FIXED AND VERIFIED — controlled concurrency:** two concurrent writes left one complete TSLA row and no duplicate; the baseline was restored.
- **FIXED AND VERIFIED — analyze job contract:** one analysis job completed through the 202/poll flow, and an immediate repeat used the cache.
- **FIXED AND VERIFIED — visible owner controls:** the customer does not normally see the owner operations widgets.

### Changes present only in an unmerged candidate

PR #105 (`fe65a816cf33a76b710e8f9746f2ef32f5c0b2b2`) contains additional remediation, including a stricter model-portfolio allowlist, broader financial invariants, unknown-ticker work, response/HTML separation, frontend extraction, and reproducible-dependency work. These are **SUPPORTED BY LATEST EVIDENCE** as candidate code, not production fixes. The PR is open, conflicting, and not release-ready.

### Current `main`

`origin/main` is `cef1fe604d84b2e9d4cc72384e3998387dc9a0bd` after PR #106 updated `gesh-core` to v0.6.1. It does not contain PR #105’s remediation series. It retains the original owner-derived portfolio and customer-readable global trade architecture.

## What is only cosmetic or partial

- **PARTIALLY FIXED — owner console separation:** owner widgets are no longer visibly rendered for the customer, but shared customer JavaScript still fetches `/api/portfolio`, `/api/ecg`, and regime data, calls owner-only render functions whose DOM does not exist, and repeats at least one error every 30 seconds.
- **PARTIALLY FIXED — model publication:** the checked branch and PR #105 strip more fields, but still derive customer content dynamically from the current owner snapshot. A serializer allowlist is not a consented publication system.
- **PARTIALLY FIXED — unified answer:** the top combined stance now aligns with the mechanical and valuation evidence for the tested TSLA case, but the same page still shows a long-biased trade setup under a `REDUCE`/`SELL` conclusion.
- **PARTIALLY FIXED — track record:** engine, manual, and blended cohorts are more visibly separated, but the engine headline sums per-trade percentages while profit factor is dollar-weighted; the benchmark renders `+nan%`; the comparison is not capital- and period-matched.
- **PARTIALLY FIXED — chart product:** backend `max` works, but the UI exposes only 1D through 1Y; intraday responses omit a source label.
- **PARTIALLY FIXED — unknown assets:** the tested `/api/analyze-ticker` rejects an unknown symbol before returning analysis, but `/api/mybook/analyze` and position validation do not have a general synchronous listed-asset contract at every entry point.
- **PARTIALLY FIXED — web hardening:** major security headers are present; CSP still permits `unsafe-inline`, and authenticated financial GET responses lack explicit private/no-store cache policy.

## Current P0 risks

### TEN-01 / A-01 — Owner-derived brokerage portfolio published to customers

**Status:** RECLASSIFIED — previously described as IDOR/cross-role exposure; now verified as an intentionally customer-readable global model view with unsafe real-account provenance. The practical P0 risk remains open.  
**Evidence:** VERIFIED CURRENT

`/api/portfolio` returned `source=robinhood_mcp`, ten real-source holdings, cash and account-level figures to an authenticated customer. Harmless `view`, `user`, and `account` query overrides made no difference. This proves the risk is the authorized dataset itself, not a successful identity switch.

The returned numbers also do not reconcile:

- listed holdings: `$73,597.62`;
- reported portfolio equity: `$102,214.50`;
- unexplained holdings gap: `$28,616.88`;
- cash/deployable amount: `$26,402.39`;
- account total: `$128,616.89`;
- listed holdings plus cash: `$100,000.01`;
- API flag: `reconciles:false`.

The missing `$28,616.88` cannot be bridged from the published holdings. The response appears to combine a scaled model notional with separate owner account components.

### TEN-02 / A-03 — Global real-trade row and internal note published to customers

**Status:** OPEN  
**Evidence:** VERIFIED CURRENT

`/api/trades` returned a row with `is_paper:0`, execution/provenance fields, and a non-empty internal note. The note content is intentionally omitted from this pack. The underlying global tables have no customer tenant key; handlers query and serialize global rows.

The sibling `/api/trades/stats` path is part of the same boundary and must be contained even where its current output looks aggregated.

## Most likely root cause

GESH has two different isolation models that were mistakenly treated as one:

1. **Real tenant isolation:** customer-owned records such as `customer_positions`, watchlist, preferences, analysis jobs, reservations, and exports are keyed by immutable authenticated user UUID.
2. **Presentation isolation:** global owner portfolio and trade data are transformed, scaled, labelled `model_book`, or hidden with CSS/DOM rules.

The first model is a valid data boundary. The second is not. “Model book” is currently a response label, not a durable customer assignment to an approved publication. There is no immutable model version, publication event, consent artifact, approver, entitlement, or fail-closed active-version pointer. Similarly, the global trade ledger has no tenant/publication boundary at all.

The broad response middleware helped create the historical export-scaling defect and obscured provenance. Route guards cannot repair a dataset that is wrong by design.

## Recommended target architecture

Choose **Option C: a separate curated, immutable model-portfolio dataset**, with:

- **Option D, synthetic/paper provenance, as the default source**; and
- **Option E, explicitly consented and stripped real snapshot, only as an exceptional reviewed ingestion source**.

Do not dynamically proxy the owner’s Robinhood account. Do not auto-migrate existing snapshots or free-text trade notes. Quarantine them as internal records under a retention policy. Create the first customer publication from a reviewed draft with an explicit schema, source kind, data-as-of time, model notional, reconciled holdings/cash, immutable version, approver, audit event, and customer entitlement.

## Next five decisions

1. **Contain now:** make customer `/api/portfolio`, `/api/trades`, and `/api/trades/stats` fail closed until a safe dataset exists; stop the customer browser from polling them.
2. **Approve the data-product contract:** My Book is customer-owned; Model Book is a separate curated publication; the owner brokerage account is owner-only.
3. **Approve provenance policy:** synthetic/paper is the default; a real-account snapshot requires explicit consent, review, stripping, versioning, and audit evidence.
4. **Choose the remediation branch strategy:** recreate or rebase the useful parts of PR #105 onto current `main`, resolve conflicts, remove tests that encode unsafe customer trade access, and do not merge the branch wholesale.
5. **Adopt release identity and gates:** every deployment must come from a clean, reviewed commit and expose its SHA/build time/schema version; P0 leak, reconciliation, test, browser, accessibility, and rollback gates must pass on staging before promotion.

## Next three engineering moves

1. **24-hour containment patch:** owner-gate the three global routes, remove customer polling of owner endpoints, add explicit `Cache-Control: private, no-store`, and deploy from a clean hotfix commit with an exposed build SHA.
2. **Model-publication foundation:** implement immutable model portfolio/version/holding/trade-event/assignment/publication-event tables and a fail-closed serving API; seed only a reviewed synthetic/paper publication.
3. **Trust and release correction:** standardize track-record units/benchmark, make trade setup obey or explicitly override the unified stance, reconcile PR #105 against current `main`, and make the complete regression/release suite blocking.

## Release gate

No customer release, marketing claim, or paid expansion until all of the following are true:

- customer portfolio/trade responses contain no owner connector, raw snapshot, real trade, private note, or unapproved field;
- customer handlers continue to work when every owner connector is patched to raise;
- the active model version is immutable, approved, entitled, fully reconciled, and byte-stable across reads;
- My Book write/read/export and financial invariants pass;
- track-record metrics use a documented, consistent calculation basis and the benchmark is finite or explicitly unavailable;
- unified stance and trade setup cannot silently contradict;
- the repository is clean, the candidate merges without conflicts, all blocking tests pass, and the deployed SHA is observable;
- desktop and true mobile browser journeys pass with zero uncaught console errors and the required accessibility semantics.

Until then, the safe operating state is **authorized internal testing only**, with the current P0 routes disabled for customers.
