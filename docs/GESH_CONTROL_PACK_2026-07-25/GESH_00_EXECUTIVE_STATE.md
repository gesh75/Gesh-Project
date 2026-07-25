# GESH 00 — Executive State

**Canonical evidence cut:** 2026-07-25, Europe/Sofia  
**Product:** `https://pay.geshfinance.com`  
**Repository:** `gesh75/Gesh-Project`  
**Decision:** **NO-GO for paid/customer release or promotion**
**Phase 1 update:** P0 customer exposure is contained in the clean candidate; no production deployment was performed.

## Current verdict

GESH is a substantial working fintech application, not a prototype shell. It has authentication, a customer-owned portfolio ledger (“My Book”), a shared research dashboard, market-data charts, multi-engine and AI analysis, position sizing and simulation, watchlists and preferences, a public track-record page, an owner/operator surface, background analysis jobs, and Robinhood-derived operator data.

The customer-owned part of the product is now materially healthier than it was on 2026-07-22. The live test account’s TSLA quantity is consistent between `/api/mybook` and `/api/account/export`; customer-book valuation and day-change arithmetic reconcile; chart ranges return distinct datasets; customer access to `/api/config` is denied; unsafe `text/plain` mutation is rejected; owner/admin routes resist harmless query and mass-assignment tampering; and controlled same-symbol writes did not create duplicate or torn rows.

That progress does **not** make the current product commercially releasable. The canonical audit reproduced two customer-reachable data-product boundary failures before containment:

1. `/api/portfolio` returns a model-book response derived from the operator’s real `robinhood_mcp` snapshot rather than from an independently published model dataset.
2. `/api/trades` returns a global non-paper trade row and an internal free-text note.

Those were **VERIFIED PRE-CONTAINMENT P0** findings. They were not ordinary anonymous access or a query-parameter IDOR. Authentication and many route guards worked; the failure was that pre-containment code intentionally authorized a customer to a dataset whose provenance, consent, publication state, and sanitization were unsuitable for customer use.

Phase 1 now fails closed before handler, repository, connector, or cache access. Authenticated non-owner `GET`/`HEAD` requests to `/api/portfolio`, `/api/trades`, and `/api/trades/stats` return exact 403 unavailable contracts; query tampering cannot change principal scope; customer web and Android clients make zero requests to those owner-domain routes; owner behavior remains available; and principal-dependent responses use private/no-store cache controls. This closes the immediate disclosure path, not the missing durable Model Book publication architecture.

PR #105 was squash-merged before this phase; the exact Phase 1 base was then-current `origin/main` `68287dfd80eab51b40d4270eab07b0bd73267992`. The clean implementation commit is `8ff34b531b31248d71178c6ed23cb88182cf1724`. No production deployment was performed.

## Score

**44 / 100**

| Dimension | Score | Reason |
|---|---:|---|
| Identity, privacy, tenant/data boundary | 8/20 | Immediate customer access to the two owner/global domains is contained; a durable approved Model Book publication boundary still does not exist. |
| Financial and quantitative integrity | 10/20 | My Book arithmetic is repaired; model portfolio does not reconcile and track-record bases are mixed. |
| Product coherence and truthfulness | 8/20 | Combined stance improved; trade setup can still contradict it, and model/real/simulated provenance is unclear. |
| Reliability, testability, and release control | 10/20 | The clean base and Phase 1 candidate are green and reproducible; staging, deployment, rollback, and the broader release gates remain incomplete. |
| UX, accessibility, and mobile | 5/15 | Owner polling is removed from the customer bundle; semantics, labels, accessibility, and true mobile verification remain incomplete. |
| Operational evidence | 3/5 | A deterministic artifact and safe version contract now exist, but no immutable artifact has been deployed or verified in production. |

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

### Merged baseline and Phase 1 candidate

PR #105 was squash-merged as `68287dfd80eab51b40d4270eab07b0bd73267992`. Phase 1 was implemented from that exact clean base on `codex/p0-data-boundary-containment-20260725`; the clean implementation commit is `8ff34b531b31248d71178c6ed23cb88182cf1724`. The candidate full suite passed 2,206 tests with zero failures/skips; the deterministic runtime archive SHA-256 is `a0d8a80a0255e393675352422992eb343df30bb60c016b17ec659d3e8324e345`. These remain candidate facts, not production-deployment proof.

### Current `main`

At Phase 1 worktree creation, the exact then-current remote main was `68287dfd80eab51b40d4270eab07b0bd73267992`, including the squash merge of PR #105. Phase 1 is a separate reviewed candidate based on that SHA; it has not been merged or deployed by this task.

## What is only cosmetic or partial

- **FIXED IN PHASE 1 CANDIDATE — owner console separation:** the customer document excludes owner loaders, components, retries, and polling; the real-browser two-refresh trace makes zero removed owner-domain requests and emits no console errors. The Android customer client no longer defines or schedules the owner portfolio request.
- **PARTIALLY FIXED — model publication:** the checked branch and PR #105 strip more fields, but still derive customer content dynamically from the current owner snapshot. A serializer allowlist is not a consented publication system.
- **PARTIALLY FIXED — unified answer:** the top combined stance now aligns with the mechanical and valuation evidence for the tested TSLA case, but the same page still shows a long-biased trade setup under a `REDUCE`/`SELL` conclusion.
- **PARTIALLY FIXED — track record:** engine, manual, and blended cohorts are more visibly separated, but the engine headline sums per-trade percentages while profit factor is dollar-weighted; the benchmark renders `+nan%`; the comparison is not capital- and period-matched.
- **PARTIALLY FIXED — chart product:** backend `max` works, but the UI exposes only 1D through 1Y; intraday responses omit a source label.
- **PARTIALLY FIXED — unknown assets:** the tested `/api/analyze-ticker` rejects an unknown symbol before returning analysis, but `/api/mybook/analyze` and position validation do not have a general synchronous listed-asset contract at every entry point.
- **PARTIALLY FIXED — web hardening:** major security headers are present; Phase 1 adds `private, no-store`, `Pragma: no-cache`, and `Vary: Cookie` to the contained owner-domain and role-composed responses. CSP and the remaining authenticated-response inventory stay open.

## P0 containment result and remaining root risks

### TEN-01 / A-01 — Owner-derived brokerage portfolio published to customers

**Status:** CONTAINED IN PHASE 1 CANDIDATE; durable publication root cause remains open.  
**Evidence:** VERIFIED PRE-CONTAINMENT plus clean candidate regression proof.

Before containment, `/api/portfolio` returned `source=robinhood_mcp`, ten real-source holdings, cash and account-level figures to an authenticated customer. Harmless `view`, `user`, and `account` query overrides made no difference. Phase 1 now returns 403 before any owner seam and proves that the denial contains no source, holdings, cash, buying power, or account values.

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

**Status:** CONTAINED IN PHASE 1 CANDIDATE; curated customer trade publication remains unimplemented.  
**Evidence:** VERIFIED PRE-CONTAINMENT plus clean candidate regression proof.

Before containment, `/api/trades` returned a row with `is_paper:0`, execution/provenance fields, and a non-empty internal note. The note content is intentionally omitted from this pack. Phase 1 now denies both trade routes before global repository access and proves that rows, statistics, and private notes are absent.

The sibling `/api/trades/stats` path receives the same exact customer fail-close policy.

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

1. **Contain now — COMPLETE IN CANDIDATE:** customer `/api/portfolio`, `/api/trades`, and `/api/trades/stats` fail closed; customer web and Android clients do not call them.
2. **Approve the data-product contract:** My Book is customer-owned; Model Book is a separate curated publication; the owner brokerage account is owner-only.
3. **Approve provenance policy:** synthetic/paper is the default; a real-account snapshot requires explicit consent, review, stripping, versioning, and audit evidence.
4. **Remediation branch strategy — RESOLVED FOR PHASE 1:** PR #105 is already merged in the recorded base; Phase 1 is a fresh reviewed branch from that exact main SHA.
5. **Adopt release identity and gates — PARTIAL:** the candidate exposes a safe commit/build-time identity and is packaged deterministically; staging, production, accessibility, rollback, and the remaining release gates must still pass before promotion.

## Next three engineering moves

1. **24-hour containment patch — COMPLETE AS AN UNDEPLOYED CANDIDATE:** the three routes fail closed, customer owner polling is removed, targeted private/no-store controls are explicit, and safe build identity is packaged. Production deployment was intentionally not performed.
2. **Model-publication foundation:** implement immutable model portfolio/version/holding/trade-event/assignment/publication-event tables and a fail-closed serving API; seed only a reviewed synthetic/paper publication.
3. **Trust and release correction:** standardize track-record units/benchmark, make trade setup obey or explicitly override the unified stance, and make the complete regression/release suite blocking.

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

Until then, the safe operating state is **authorized internal testing only**. Keep the Phase 1 routes in deny mode; do not deploy or reopen them without the remaining gates.
