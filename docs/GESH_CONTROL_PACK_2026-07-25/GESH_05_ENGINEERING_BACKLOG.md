# GESH 05 — Engineering Backlog

**Control-pack date:** 2026-07-25  
**Ordering rule:** dependency and risk reduction, not feature appeal  
**Release posture:** no customer release until every P0/P1 ticket and every explicitly mandatory release gate—including the P2 mobile, chart, mutation-contract, and response-hardening gates—passes CP-016 on the exact artifact  
**Evidence labels:** `VERIFIED CURRENT`, `SUPPORTED BY LATEST EVIDENCE`, `INFERRED`
**Phase 1 update:** CP-001 is complete in an undeployed clean candidate; CP-002/008/014 are partially advanced; Phase 2+ tickets remain open.

The only canonical **P0 findings** are **TEN-01**, the owner-derived customer portfolio, and **TEN-02**, the global real trade/note exposure. Phase 1 contains both customer read paths but does not implement their durable publication-domain closure. Several tickets below are P0 work packages because they jointly close those root findings; they do not represent additional P0 findings. Financial reconciliation, deployment, track-record methodology, recommendation coherence, and accessibility are P1 release blockers. Mobile verification is P2 but remains mandatory before a public paid release.

## 1. Planning baseline

The backlog is based on four states that must not be conflated:

1. **Live customer behavior — VERIFIED PRE-CONTAINMENT, 2026-07-25.** An authenticated customer could read an owner-derived `robinhood_mcp` portfolio and a global non-paper trade/private note. The numerical evidence below remains the historical root-cause record.
2. **Recorded Phase 1 base — VERIFIED CLEAN.** Exact then-current `origin/main` `68287dfd80eab51b40d4270eab07b0bd73267992`, including squash-merged PR #105, reproduced 2,078 passed with zero failed/skipped.
3. **Phase 1 candidate — VERIFIED CLEAN.** `8ff34b531b31248d71178c6ed23cb88182cf1724` on `codex/p0-data-boundary-containment-20260725`; 2,206 passed with zero failed/skipped; three independent reviews approved.
4. **Artifact — VERIFIED CANDIDATE.** Deterministic 1,556,480-byte USTAR, 80 exact runtime members, SHA-256 `a0d8a80a0255e393675352422992eb343df30bb60c016b17ec659d3e8324e345`; safe build identity sidecar; no deployment.

The historical dirty-tree failures were classified and not suppressed. They did not reproduce on the exact clean base or Phase 1 candidate. Unrelated open contracts, including missing-`Content-Type` mutation behavior, remain in their existing backlog tickets.

## 2. Remediation option decision for TEN-01 and TEN-02

These are the canonical IDs for the findings historically reported as A-01 and A-03.

| Option | What it solves | What it does not solve | Correct role |
|---|---|---|---|
| **A. Scope current endpoints to each customer's records** | Correct treatment for the customer's self-entered `mybook`; removes cross-customer reads if every query is keyed by immutable `user_id` | Does not create the shared research model customers were meant to follow; cannot safely turn an owner brokerage snapshot into a published product | Retain for `/api/mybook`, export, preferences, watchlist, and customer analyses |
| **B. Return 403/404 to customers** | Stops the live exposure immediately and is safe to ship as a small containment patch | Removes the shared model-book/trade-history feature rather than rebuilding it | **Mandatory interim control** until C is production-ready; permanent fallback on any publication failure |
| **C. Separate curated, durable model dataset** | Separates brokerage, internal execution, model research, and customer books; enables immutable versions, approval, entitlement, rollback, audit, and exact reconciliation | Still needs a safe source and publication process | **Recommended target architecture** |
| **D. Synthetic/paper model source** | Removes real-brokerage provenance and consent risk; gives deterministic seed data and reproducible tests | Must still be honestly labeled and financially reconciled | **Default source inside C** |
| **E. Explicitly consented, stripped snapshot** | Can publish a deliberately selected real strategy composition without exposing account metadata | Consent, withdrawal, sanitization, reconciliation, and editorial approval remain mandatory; no raw note or account field may cross the quarantine boundary | Optional ingestion mode inside C, disabled by default |

**Decision:** build **C with D as the default source**. Permit **E only as an explicitly consented ingestion path**. **B is implemented in the Phase 1 candidate and remains the required mode.** Continue using **A for the customer's own `mybook`**, not as a substitute for the shared model portfolio.

The target flow is:

```text
synthetic/paper generator ───────────────┐
                                        ├─> quarantine/draft
consented source snapshot (optional) ───┘       │
                                                ├─ sanitize + validate symbols
                                                ├─ reconcile every financial identity
                                                ├─ editorial + privacy approval
                                                v
                                      immutable published version
                                                │
                                      atomic active-version pointer
                                                │
                            entitlement-scoped customer read APIs
```

Customer requests must never call an owner brokerage connector, read the global trade ledger, or construct a new publication from the current owner snapshot.

## 3. Ownership discipline

Every ticket has one accountable DRI. “Engineering” as an owner is not accepted.

| Discipline | Accountability |
|---|---|
| **Incident/Security DRI** | Customer containment, privacy decision, legacy-data restrictions, incident evidence |
| **Backend/API DRI** | Handler policy, customer identity, endpoint contracts, fail-closed behavior |
| **Data/Publication DRI** | New schema, migrations, immutable versions, assignments, publication events |
| **Financial Integrity DRI** | Reconciliation equations, metric definitions, benchmark methodology, acceptance fixtures |
| **Frontend/A11y DRI** | Customer bundle, labeling, async state, keyboard/screen-reader/mobile behavior |
| **Release/SRE DRI** | Clean builds, deploy identity, promotion, smoke tests, monitoring, rollback |
| **Product/Editorial DRI** | Model methodology, source disclosure, approval, consent scope, public claims |
| **Independent reviewer** | Cannot be the ticket author for P0 privacy, financial-integrity, or release-gate sign-off |

## 4. Dependency-ordered tickets

### CP-001 — Contain customer access to owner portfolio and global trades

- **Priority / status / effort:** P0 / **COMPLETE IN PHASE 1 CANDIDATE; NOT DEPLOYED** / S
- **Owner discipline:** Incident/Security DRI, implemented by Backend/API DRI; Release/SRE DRI deploys.
- **Affected code/data:** `server.py` customer middleware and `GET /api/portfolio`, `GET /api/trades`, `GET /api/trades/stats`; `templates/dashboard.html` portfolio/ECG/regime/trade polling; global `trade_log` and snapshot files remain untouched.
- **Evidence:** pre-containment responses contained owner brokerage composition and a non-paper global trade. Candidate tests prove exact customer 403s before owner seams, private/no-store cache isolation, zero web/Android removed owner requests, zero browser console errors, and preserved owner behavior.
- **Reproduction:** run the Phase 1 API, repository/cache, static frontend/Android, and real-browser regression suites listed in GESH-09.
- **Root cause:** authentication succeeds, but authorization is expressed as generic “authenticated customer may receive model_book.” The portfolio handler builds an owner brokerage payload, the customer middleware transforms/stamps it, and the trade handlers read unscoped global tables. UI CSS hiding does not change either server delivery or background fetches.
- **Implementation result:** the clean Phase 1 branch adds an explicit deny policy for all three routes before handler access. Non-owner calls return stable 403 contracts; owner behavior is unchanged. Customer initialization, retry, polling, portfolio/ECG/exact-global-regime/trade loaders are absent; `/api/mybook` and separately authorized public-market research remain available. The safe fallback is `deny`, never “serve legacy model.”
- **Migration / old data:** none. Do not delete, rewrite, export, or migrate any snapshot or trade row during containment.
- **Backward-compatibility risk:** customer clients that currently assume 200 responses will receive 403. Ship the customer UI removal in the same artifact, keep the owner contract unchanged, and document the temporary unavailable state; do not preserve the unsafe response for compatibility.
- **Acceptance criteria:** **passed on candidate** — customer receives exact 403 from all three routes; owner contract remains intact; customer dashboard/Android sends zero removed owner requests; no owner connector/global trade query executes; real browser has zero related console errors for two refresh intervals; denied reads are rate-limited and emit throttled metadata-only logs.
- **Tests:** unit role-policy matrix; integration tests with owner/customer sessions; API contract tests for exact 403 bodies; browser E2E asserting no matching network requests and zero console errors; security regression with owner connector/global trade function patched to raise if touched.
- **Rollback:** roll back UI independently if necessary, but keep the backend in deny mode. If the hotfix cannot serve the dashboard, use authenticated maintenance mode; never roll back to the exposed endpoint behavior.
- **Observability:** counters by route and role for allow/deny, without symbols, balances, or notes; alert on any successful non-owner response or invocation of an owner connector on a customer request.
- **Dependencies:** none. This is the first production change.

### CP-002 — Establish a clean, identifiable release line and resolve PR #105

- **Priority / status / effort:** P1 release blocker / **PARTIALLY FIXED; DEPLOYMENT OPEN** / M
- **Owner discipline:** Release/SRE DRI; Backend and Security reviewers approve the reconstructed delta.
- **Affected code/data:** git branch/PR history, dependency pins, CI workflow, launchd deployment path, application health/version response, startup logs.
- **Evidence:** PR #105 is in recorded base `68287dfd`; Phase 1 is a clean reviewed descendant; clean base/candidate full suites pass; candidate archive and safe version identity are reproducible. The live process remains unchanged and unidentified.
- **Reproduction:** compare `git rev-parse` values, PR metadata, process start time, and source mtimes; no live endpoint can prove which SHA supplies in-memory handlers.
- **Root cause:** the application runs directly from a mutable worktree with `reload=False`; source changes and merges are not coupled to an immutable build artifact, promotion record, or runtime SHA.
- **Implementation result/remaining plan:** fresh Phase 1 branch from exact main, clean full-suite reconciliation, dependency/test pins, deterministic exact-commit artifact, JUnit provenance binding, and non-sensitive `/api/version` are implemented. Normal merge, staging, schema/migration identity, startup logging, promotion, and rollback proof remain open. Deploy from the immutable artifact, never the development worktree.
- **Migration / old data:** none. Record the current live process metadata as evidence before replacement. Do not restart the dirty tree.
- **Backward-compatibility risk:** deployment scripts and health probes may assume a mutable worktree or the old health shape. Version the response, update probes before promotion, and retain the previous immutable artifact without exposing filesystem or configuration details.
- **Acceptance criteria:** clean worktree; one reviewable PR based on current main; full test suite has zero genuine failures; customer-isolation lane green; built artifact hash recorded; staging and production report the promoted SHA; source changes after process start cannot alter the running release; release record identifies previous and current artifact.
- **Tests:** version-contract unit/API tests; clean-install CI; dependency-lock verification; deploy smoke that compares expected SHA with live SHA; negative CI test for dirty or unpinned builds.
- **Rollback:** retain the previous artifact and configuration, but because the prior customer behavior is unsafe, rollback must reapply CP-001 deny mode or maintenance mode. Schema changes are not part of this ticket.
- **Observability:** release event with SHA, actor, artifact hash, migration version, start time, and smoke result; alert on unknown/mismatched SHA.
- **Dependencies:** CP-001 containment patch may be the first artifact on this release line; no later customer feature deploys before CP-002.

### CP-003 — Add the durable curated model-publication schema

- **Priority / status / effort:** P0 work package for TEN-01 / **OPEN; current allowlist is only partial** / L
- **Owner discipline:** Data/Publication DRI; Security, Financial Integrity, and Product/Editorial reviewers.
- **Affected code/data:** new migration(s) in the auth/product database; new repository/service modules. Do not reuse `customer_positions`, the owner snapshot file, `trade_log`, or internal paper/autopilot tables as the published store.
- **Evidence:** current candidate `model_portfolio.publish()` creates a request-time content hash from the latest owner payload. It has no durable draft, immutable version, actor approval, assignment, consent, publication event, or rollback pointer.
- **Reproduction:** change the upstream owner snapshot and call the endpoint again; composition/version can change without an editorial publish transaction.
- **Root cause:** “model_book” was a response transformation, not a first-class persisted domain with a publication lifecycle.
- **Implementation plan:** add at least:
  - `model_portfolios(id, slug, name, status, active_version_id, methodology_version, created_at, updated_at)`;
  - `model_portfolio_versions(id, portfolio_id, sequence, schema_version, content_hash, source_kind, source_ref, consent_id, status, data_as_of, effective_at, positions_market_value, published_cash, cash_weight_bps, total_equity, created_by, approved_by, approved_at, published_at, supersedes_version_id, revocation_reason)`;
  - `model_portfolio_holdings(version_id, symbol, quantity, price, price_as_of, market_value, weight_bps, asset_type, sort_order)`;
  - `model_trade_events(id, portfolio_id, version_id, occurred_at, symbol, side, quantity, price, reason_code, public_rationale_code)`;
  - `model_portfolio_assignments(user_id, portfolio_id, status, starts_at, ends_at, assigned_by, created_at)`;
  - `model_consents(id, subject_id, source_scope, policy_version, granted_at, revoked_at, evidence_ref, recorded_by)`;
  - append-only `model_publication_events(id, portfolio_id, version_id, action, actor_id, occurred_at, content_hash, reason_code)`;
  - restricted `model_ingest_quarantine(id, source_kind, encrypted_blob_ref, checksum, consent_id, received_at, expires_at, state)`.
  Enforce unique `(portfolio_id, sequence)`, immutable published rows, foreign keys, finite numeric checks, symbol normalization, allowed status transitions, and an active pointer that can reference only a published non-revoked version.
- **Migration / old data:** additive migration only. Create tables with no active version and no assignments. **Do not populate them from existing snapshots or trade logs.** Record schema version and a reversible pre-migration backup.
- **Backward-compatibility risk:** old application code must safely ignore the additive tables while no publication is active. Do not set an active pointer until every serving process understands the schema; use feature flags and a rehearsed forward-compatible code rollback.
- **Acceptance criteria:** migration succeeds on a production-shaped copy; rerun is idempotent; downgrade rehearsal works while no publication exists; published rows cannot be mutated; active pointer cannot target draft/quarantined/revoked data; a user cannot receive a portfolio without an active assignment; publication events are append-only.
- **Tests:** DDL/constraint units; migration upgrade/downgrade and crash-recovery integration; concurrent publish/assignment transactions; security tests for cross-user assignments; API tests remain 403 because no version is active; property tests for finite quantities/prices.
- **Rollback:** before first publication, disable code and reverse the additive migration. After publication, do not drop audit/history tables; disable reads and restore the prior active pointer or deny mode.
- **Observability:** migration duration/result, version-state counts, orphan checks, failed transition counters; no raw payload or consent evidence in logs.
- **Dependencies:** CP-002.

### CP-004 — Build safe ingestion, quarantine, consent, and legacy-data treatment

- **Priority / status / effort:** P0 work package for TEN-01 / **OPEN** / L
- **Owner discipline:** Data/Publication DRI is accountable; Security and Product/Editorial are required policy reviewers for consent, retention, and source use.
- **Affected code/data:** new ingestion service, synthetic/paper generator, quarantine store, consent ledger, existing snapshot files and global trade notes.
- **Evidence:** live model data is derived from `robinhood_mcp`; the current allowlist removes fields at serialization time but does not establish consent or prevent automatic composition changes.
- **Reproduction:** inspect the pre-containment live source tag and legacy owner-backed handler path; there is no explicit source-consent or draft-review step.
- **Root cause:** collection, transformation, approval, and publication were collapsed into one request path.
- **Implementation plan:** make `source_kind=synthetic|paper` the only enabled default. Generate deterministic seed portfolios and trade events under a disclosed model methodology. For optional E ingestion, require a non-revoked consent record before reading a source; write raw material only to access-restricted, encrypted, TTL-bound quarantine; strip account IDs, source session metadata, cash/buying power unless explicitly part of the model, margin, deposits, cost basis, personal performance, execution IDs, and **all free-text notes** before a draft can be created. Convert rationale to reviewed reason codes. A separate validator and separate human approver promote a draft.
- **Migration / old data:** inventory existing snapshot files and trade-note stores by checksum and access class. Do not copy any existing owner snapshot, trade row, or note into model tables. Start the first publication from a new synthetic/paper seed. Restrict legacy data to owner/internal access; apply the documented legal retention period; expire or securely remove transient quarantine blobs after that period while retaining non-sensitive checksums and audit events. If a prior snapshot is later proposed under E, re-ingest it through current consent and quarantine controls as new material.
- **Backward-compatibility risk:** operator workflows that read snapshots directly will not automatically become publication inputs. Keep necessary internal reads under owner policy, but require every customer publication to use the new pipeline; no legacy fallback or silent conversion is permitted.
- **Acceptance criteria:** no ingestion runs without an allowed source kind; E fails closed without active scoped consent; revocation blocks future ingest and triggers withdrawal review of affected active versions; forbidden-key and secret sentinels never cross quarantine; free text never enters a public table; two-person approval is required; synthetic seed is reproducible from its declared inputs.
- **Tests:** consent state-machine units; sanitization property/fuzz tests; forbidden-key recursive tests; integration from quarantine to draft; API contract proving drafts are never readable; security test with sensitive sentinels in every nested field; E2E editorial approve/reject/revoke flow.
- **Rollback:** disable ingesters, revoke source credentials, discard transient quarantine blobs according to retention policy, and keep customer APIs in deny mode. Audit metadata remains append-only.
- **Observability:** ingest ID, source kind, consent ID, sanitization-rule version, validation outcome, approver IDs, and counts only; alert on missing consent, forbidden fields, expired quarantine, or one-person create-and-approve.
- **Dependencies:** CP-003.

### CP-005 — Publish immutable, reconciled versions and serve entitlement-scoped model APIs

- **Priority / status / effort:** P0 work package for TEN-01 / **OPEN** / L
- **Owner discipline:** Data/Publication DRI is accountable; Backend/API implements serving and Financial Integrity independently signs reconciliation.
- **Affected code/data:** publication transaction, active-version resolver, `/api/portfolio` compatibility contract or a new `/api/model-portfolios/{id}/current`, assignments/entitlements, cache keys.
- **Evidence:** pre-containment customer portfolio changed with the owner request-time source and had no durable assignment. Phase 1 denies customer `/api/portfolio` before handler/connector access; no durable Model Book version/assignment exists.
- **Reproduction:** customer identity has role/tier but no durable model portfolio/version; Phase 1 returns 403 rather than invoking the legacy owner-backed handler.
- **Root cause:** response middleware is standing in for a domain model and data authorization policy.
- **Implementation plan:** publish in one transaction: validate draft, freeze holdings/trade events, compute content hash, write approval/publication event, set published status, then atomically change `active_version_id`. Resolve a customer by immutable `user_id` through `model_portfolio_assignments`; return only the referenced published version. Customer reads query the publication DB only—never owner connectors. Response includes model ID, immutable version, `published_at`, `data_as_of`, source class (`synthetic` or `paper` by default), methodology version, stale state, and reconciliation evidence. Cache keys include portfolio/version/entitlement; drafts and revoked versions are never cacheable.
- **Migration / old data:** create an explicitly reviewed synthetic/paper v1 and assign only authorized test users in staging. Do not create assignments to a legacy owner-derived version.
- **Backward-compatibility risk:** `/api/portfolio` consumers may expect the legacy dynamic shape and timestamps. Publish a versioned DTO or compatibility adapter backed only by the immutable store, update the customer client atomically, and reject fields that cannot be represented safely.
- **Acceptance criteria:** changing the owner snapshot has zero effect on customer bytes; identical version reads are byte-stable except request metadata; drafts/revoked/unassigned portfolios return 403/404; publisher can roll back the active pointer to a prior approved version; customer handler still works when every owner connector is patched to raise; ETags/content hashes change only on publication.
- **Tests:** publication state-machine units; finance fixtures; integration for publish/rollback/concurrent readers; API contract for entitlement and immutable metadata; E2E editorial publish then customer view; security tests for guessed IDs, cache bleed, revoked assignment, and owner-connector independence.
- **Rollback:** atomic active-pointer reversal to the prior published version; if none is valid, switch to deny mode. Never reconstruct from a current owner snapshot as a fallback.
- **Observability:** version served, assignment ID, publication age, cache hit, and authorization result; alert on source kinds outside policy, no active version, or owner connector call in the customer request context.
- **Dependencies:** CP-003, CP-004, CP-007; CP-002 for promotion.

### CP-006 — Split public model trade events from the internal/global trade ledger

- **Priority / status / effort:** P0 work package for TEN-02 / **OPEN** / M
- **Owner discipline:** Backend/API DRI; Security and Product/Editorial review.
- **Affected code/data:** `server.py` `/api/trades` and `/api/trades/stats`; `trade_log.py`; new `model_trade_events`; internal paper/autopilot ledgers.
- **Evidence:** before containment, a customer received a global `is_paper=0` row and sensitive note. Phase 1 now denies both customer routes before repository access; leak-sweep tests require exact 403 and arbitrary text/source absence.
- **Reproduction:** non-owner calls `/api/trades` and `/api/trades/stats`; Phase 1 returns 403 because no immutable customer/publication-scoped trade domain exists.
- **Root cause:** global operational tables were exposed as if they were a customer model feed; response masking cannot remove structural provenance or reliably sanitize prose.
- **Implementation plan:** keep real/internal `trade_log`, paper books, orders, risk decisions, and notes owner-only. Customer trade APIs read only `model_trade_events` associated with the customer's assigned published model. Public events use structured reason codes and reviewed, template-rendered rationale—no free text. Stats take an explicit portfolio/version/cohort and use the same model event set. If no published model event set exists, return an honest empty result, not internal fallback.
- **Migration / old data:** do not migrate any global real or discretionary trade or its note. Retain/archive it only under the internal retention policy and remove customer permissions. Curate new model events from synthetic/paper activity; any E-derived event must pass CP-004 as a new record.
- **Backward-compatibility risk:** customer callers expecting global `/api/trades` rows or mixed stats will receive 403 or a narrower versioned model-event schema. Update the first-party UI in the same release; preserve owner/internal APIs separately rather than emulating unsafe fields.
- **Acceptance criteria:** customers can never receive global `is_paper=false` data, broker execution detail, arbitrary notes, or internal risk fields; customer stats are calculated only from the returned model event set; an owner can still access internal operations through explicitly owner-only routes; no `paper` query value changes the authorization boundary.
- **Tests:** route-policy units; model-event calculation fixtures; owner/customer integration; API contracts for empty and populated assigned models; E2E model history; security sentinels in notes, nested objects, query tampering, alternate content types, cache keys, and stats.
- **Rollback:** switch customer trade endpoints to deny mode; internal ledger remains unchanged.
- **Observability:** model portfolio/version/cohort and result count only; alert if a customer query touches internal trade tables or if serialized output contains a forbidden field.
- **Dependencies:** CP-003, CP-004, CP-005.

### CP-007 — Enforce financial reconciliation and provenance as publication gates

- **Priority / status / effort:** P1 release blocker and TEN-01 dependency / **OPEN for model publication; customer `mybook` arithmetic is verified fixed** / M
- **Owner discipline:** Financial Integrity DRI; independent quantitative reviewer; Data DRI implements.
- **Affected code/data:** publication validator, `portfolio_valuation.py`, model holdings/version totals, day-change bases, stale-price policy.
- **Evidence:** live owner-derived payload has a $28,616.88 gap between itemized holdings and source portfolio equity. The candidate serializer can force its published total to holdings plus cash, but that does not explain or safely migrate the unitemized source sleeve.
- **Reproduction:** independently sum `quantity × current_price` and compare with every headline and cash definition.
- **Root cause:** source account totals cover data not itemized in the customer payload; historical code reused those totals and then normalized them. Renaming or overwriting the total hides the source discrepancy but does not make the source complete.
- **Implementation plan:** a publishable version must satisfy, to the cent:
  - each `market_value = quantity × price`;
  - `positions_market_value = Σ market_value`;
  - `total_equity = positions_market_value + published_cash`;
  - `Σ holding weight_bps + cash_weight_bps = 10,000` after a documented rounding allocator, using total model equity as the denominator;
  - day change compares the same version universe and same cash convention at current and prior close;
  - all prices, quantities, percentages, and timestamps are finite;
  - data older than policy is rejected or explicitly published as stale with no actionable setup.
  Unitemized sleeves cause publication failure; they are not silently folded into “cash” or “other.” Record source-versus-published bridges internally, but only approved reconciled components enter the model.
- **Migration / old data:** mark old owner snapshots `legacy_unreconciled` in the restricted inventory only. Do not repair them into public history unless every missing sleeve can be itemized and approved.
- **Backward-compatibility risk:** stricter validators will reject inputs legacy code accepted and may make the model unavailable. This fail-closed behavior is intentional; retain the last valid immutable version or deny mode instead of weakening equations.
- **Acceptance criteria:** every published fixture reconciles exactly; a one-cent variance, NaN, missing price, duplicate symbol, unknown symbol, stale forbidden price, or unitemized sleeve blocks publication; API `reconciles=true` is computed, never a literal; rollback version also revalidates.
- **Tests:** decimal arithmetic units and property tests; known good/bad golden portfolios; migration and publication integration; API recomputation from response bytes; E2E visible totals/labels; security test that diagnostic source totals never reach customers.
- **Rollback:** reject the draft and retain the prior valid active version; if no valid version remains, deny customer access.
- **Observability:** invariant name, sanitized magnitude band, version, source state, and validator version; alert immediately on a failed active-version recheck.
- **Dependencies:** CP-003 and CP-004; gates CP-005 and CP-006.

### CP-008 — Cut the customer frontend over to explicit customer and model domains

- **Priority / status / effort:** P1 with P0 privacy impact / **PARTIALLY FIXED — Phase 1 customer cutover complete; published-model UI pending** / M
- **Owner discipline:** Frontend/A11y DRI with Backend and Security review.
- **Affected code/data:** `templates/dashboard.html` or extracted customer bundle/templates; role bootstrap from `/api/me`; My Book and Model Book navigation/components.
- **Evidence:** customer role composition now ships no owner blocks/loaders/retries/polls; Android no longer exposes the owner portfolio request; real Chromium observes zero removed owner requests and zero console errors. A future durable published-model surface remains pending.
- **Reproduction:** load the customer dashboard, inspect the network and console for at least ten minutes.
- **Root cause:** customer/owner separation is CSS- and runtime-conditional inside one large page instead of server-composed routes/bundles with explicit data dependencies.
- **Implementation plan:** render a customer-specific shell/bundle that does not ship or invoke owner operations code. Separate “My Book” (`/api/mybook`) from “Published Model” (CP-005) in navigation, labels, state, cache, and error copy. Display source class, version, publication time, data time, and stale state. Guard every renderer by a real component contract, not null checks scattered after fetch. Remove duplicate timers and cancel polling on navigation.
- **Migration / old data:** none; invalidate old static assets/caches on deploy.
- **Backward-compatibility risk:** old bookmarks, DOM selectors, cached bundles, and automation may assume the combined dashboard. Preserve safe customer URLs, version assets, and provide friendly unavailable states; do not retain owner code solely for selector compatibility.
- **Acceptance criteria:** zero owner endpoint requests from customer home unless the user opens the published-model surface; zero customer access to ECG/risk-gate/internal trade functions; no console errors; My Book and Model Book cannot share state objects; model-unavailable state is clear and non-actionable; stale badges match API metadata.
- **Tests:** component/HTML contract units; API-mock integration; browser E2E by role with network allowlists, timer cleanup, navigation, stale/error/empty states; security assertion that owner strings and internal controls are absent from customer HTML/bundle.
- **Rollback:** serve the previous customer shell only with CP-001 backend deny controls still active; otherwise maintenance mode.
- **Observability:** client error rate, forbidden-route request count, polling count per page/session, model version displayed, and static asset version.
- **Dependencies:** CP-001; full model surface depends on CP-005 and CP-006.

### CP-009 — Close mutation media-type and symbol-validation gaps

- **Priority / status / effort:** P2 mandatory release gate / **OPEN** / M
- **Owner discipline:** Backend/API DRI; Security and Cost/LLM reviewer.
- **Affected code/data:** JSON mutation middleware, `/api/mybook`, preferences, watchlist, analyze routes, symbol resolver, `mybook_jobs`, reservations, analyses, usage.
- **Evidence:** explicit `text/plain` is rejected, but a missing `Content-Type` can be accepted and persist. Unknown-symbol persistence/late failure is encoded as expected in current tests for at least one workflow, while the direct ticker-analysis route now rejects unknowns before spend.
- **Reproduction:** send a valid JSON body without `Content-Type` and compare state before/after; submit a syntactically valid nonexistent symbol to every creation/analyze path and inspect jobs/reservations/artifacts.
- **Root cause:** middleware checks an explicitly wrong media type but treats absence as acceptable; symbol validation is path-specific and some workflows validate only after persistence or worker dispatch.
- **Implementation plan:** centralize a strict mutation contract: JSON endpoints require `application/json` (charset allowed), absent/unsupported media type returns 415 before body parsing, malformed JSON returns 400, and Origin/CSRF policy runs before mutation. Add one canonical synchronous symbol resolver with bounded cache and explicit `unknown` versus `temporarily unavailable`; reject unknowns before position persistence, reservation, job, usage event, or LLM call.
- **Migration / old data:** inventory syntactically valid but unresolvable customer symbols and analysis jobs; quarantine/flag them for user correction, without fabricating prices or silently deleting customer-entered data. No new job artifacts for rejected requests.
- **Backward-compatibility risk:** clients omitting `Content-Type` and workflows relying on syntax-only symbols will begin receiving 415/404/422. Inventory and update supported clients, publish the error contract, and never grandfather browser mutations that bypass the guard.
- **Acceptance criteria:** missing/wrong type produces 415 with byte-identical state; malformed JSON produces 400; hostile Origin produces 403; unknown symbol produces 404/422 synchronously and creates zero DB rows/reservations/jobs/usage; provider outage produces a distinct retryable response; supported legacy clients are updated before enforcement.
- **Tests:** middleware units; route matrix for all mutations; persistence integration; API contract for status/error schema; E2E form error; security tests for missing/duplicate/content-type variants and CSRF; cost test proving no provider/LLM call.
- **Rollback:** feature flag may temporarily allow a documented legacy client media type, but never absent type on browser customer mutations; symbol enforcement can fail closed with “temporarily unavailable.”
- **Observability:** rejection reason and route counters, resolver availability, jobs-created-to-results ratio; never log request bodies.
- **Dependencies:** CP-002; required before reopening customer writes after a release.

### CP-010 — Correct and reframe the public track-record methodology

- **Priority / status / effort:** P1 / **OPEN** / M
- **Owner discipline:** Financial Integrity DRI with Product/Editorial and independent quantitative review.
- **Affected code/data:** `proof_page.py`, sibling trading metric DTO, benchmark fetch/cache, public copy, historical paper outcomes.
- **Evidence:** the page calls the sum of per-trade percentage returns “total P&L”; public engine rows sum to approximately +6.8%, while dollar-weighted profit factor is about 0.91 and win rate is about 18%. SPY renders `+nan%`. Overlapping trades and different notionals mean the sum is not a portfolio return.
- **Reproduction:** recompute per-row `pnl_pct`, dollar gains/losses, timing, and benchmark; inspect `_cohort_stats()` and `_fmt_spy()`.
- **Root cause:** independent metrics use different denominators and aggregation semantics, then are placed under one performance headline. The finite-value formatter treats NaN as a normal float.
- **Implementation plan:** write and version a metric specification before changing copy. Prefer a timestamped, capital-aware paper equity curve with time-weighted return and an exactly period-matched benchmark. If historical data cannot support that, label the existing value “sum of closed-trade returns” and remove any portfolio-return/benchmark implication. Use one disclosed basis for profit factor or show both dollar-weighted and trade-normalized values with names. Require `math.isfinite`; missing benchmark renders `n/a`. Lead with sample size, gate status, drawdown, PF, and methodology—not the most flattering number.
- **Migration / old data:** do not rewrite outcome rows. Backfill a versioned derived-metrics table/cache from immutable rows and retain the old metric definition for audit comparison. Flag periods lacking capital/exposure data as not computable.
- **Backward-compatibility risk:** headline labels and values may change, and prior reports may no longer match. Preserve the old methodology version for audit comparison, publish an effective date, and do not retain a misleading default for visual continuity.
- **Acceptance criteria:** no NaN/Inf reaches HTML/JSON; return label matches its equation; benchmark start/end and cash/exposure convention match; engine/manual/blended cohorts remain disjoint; every displayed value is independently reproducible from a fixture; weak metrics are not visually subordinated to a headline return.
- **Tests:** metric units/golden vectors; property tests; sibling-DTO integration; API/page contract; browser copy and finite-render E2E; regression for mixed cohort and missing benchmark.
- **Rollback:** switch the page to a conservative table with `n/a` for unverified aggregate metrics; never restore the misleading headline.
- **Observability:** metric definition version, source row count, last close, benchmark availability/period, recompute failures.
- **Dependencies:** CP-002; does not block containment, but blocks public commercial release.

### CP-011 — Make the final recommendation and trade setup one coherent contract

- **Priority / status / effort:** P1 / **PARTIALLY FIXED** / M
- **Owner discipline:** Financial Integrity DRI; Backend/AI DRI implements; Product reviewer approves language.
- **Affected code/data:** `verdict_reconciliation.py`, ticker-card assembly, trade-setup generator, AI/mechanical/valuation/regime/FFT/gate DTOs, frontend.
- **Evidence:** current top-level combined stance can say SELL/REDUCE with high confidence while the same view still presents a long-biased entry/stop/target and positive reward/risk.
- **Reproduction:** analyze the verified TSLA case and compare combined stance with the actionable setup.
- **Root cause:** reconciliation produces a headline, but trade-setup generation/rendering remains an independent downstream lane that does not consume the final stance or a no-trade gate.
- **Implementation plan:** define a versioned `RecommendationDecision` containing final action, confidence definition, evidence timestamps, conflicts, valuation state, risk-gate state, allowed setup direction, and `actionable` boolean. Generate or suppress setup only after this decision. SELL/REDUCE/AVOID cannot emit a long entry; unresolved/stale conflicts produce “no actionable setup.” Expose sub-signals as evidence, not competing calls to action.
- **Migration / old data:** no destructive migration; stored analyses retain their decision-contract version. Old results display as historical/unreconciled and are never used as current actionable guidance.
- **Backward-compatibility risk:** consumers may expect a trade setup for every analysis. Version the decision DTO, make `actionable=false` and suppression reasons explicit, and update first-party consumers before removing legacy setup fields.
- **Acceptance criteria:** direction matrix is exhaustive; no rendered setup contradicts the final stance; stale/insufficient data is non-actionable; confidence has a documented denominator; one final action/version is shared by API, UI, report, Kelly, and trade setup.
- **Tests:** decision-table units and property tests; integration through setup/Kelly; API schema contract; E2E for BUY, SELL/REDUCE, conflict, stale, and unavailable cases; regression using the current contradictory fixture.
- **Rollback:** disable actionable trade setup and show research evidence only; do not roll back to contradictory guidance.
- **Observability:** decision version, final action, suppressed-setup reason, conflict count, source ages—without customer holdings.
- **Dependencies:** CP-007 for financial inputs and CP-002 for release.

### CP-012 — Capture cost basis without inventing unrealized P&L

- **Priority / status / effort:** P1 / **OPEN** / M
- **Owner discipline:** Backend/API DRI is accountable; Financial Integrity and Privacy are required reviewers.
- **Affected code/data:** `customer_positions.avg_cost`, book forms/import/export/report, valuation DTO.
- **Evidence:** current customer book correctly reports cost basis and unrealized P&L unavailable when `avg_cost` is null. The missing feature limits analysis but must not be replaced with zero.
- **Reproduction:** inspect a position without average cost and its valuation flags.
- **Root cause:** position entry/import does not consistently capture validated per-share basis; historical UI once treated missing/zero ambiguously.
- **Implementation plan:** support optional positive finite per-share cost with explicit currency and “unknown” state; allow audited correction; keep quote price separate. Calculate total basis and unrealized P&L only when every required field is present. Export/import round-trips null and values exactly. Do not infer basis from current price or owner data.
- **Migration / old data:** keep null as unknown; never backfill zero. Offer a user correction workflow. Record changes in a customer-scoped audit table without exposing them elsewhere.
- **Backward-compatibility risk:** old exports and clients may omit cost basis or encode null differently. Keep it optional and nullable, version import/export schemas, and reject ambiguous zero-as-unknown conversions without changing existing quantities.
- **Acceptance criteria:** null shows unavailable, never $0 gain/loss; valid basis reconciles to quantity × average cost; partial-book totals state coverage; write/read/export/report agree; one customer cannot read another's basis.
- **Tests:** decimal/null units; customer-scoped integration; import/export contract; E2E add/edit/remove basis; security and concurrency tests.
- **Rollback:** retain stored basis but hide derived metrics if calculation fails; schema addition remains.
- **Observability:** basis coverage percentage and validation failures in aggregate only.
- **Dependencies:** CP-002 and CP-009.

### CP-013 — Deliver accessible, device-verified customer journeys

- **Priority / status / effort:** P1 accessibility release blocker; P2 mobile verification / **OPEN; mobile is UNABLE TO VERIFY** / M
- **Owner discipline:** Frontend/A11y DRI; independent accessibility tester; Product accepts device matrix.
- **Affected code/data:** customer templates/bundle, navigation, forms, chart/analysis async state, responsive CSS.
- **Evidence:** zero `h1`–`h6`, `main`, `nav`, and `aria-live` regions were observed; at least three inputs lack programmatic labels. In-app resizing did not provide true mobile emulation.
- **Reproduction:** inspect the accessibility tree and semantic DOM; run real browser device emulation and keyboard-only flows.
- **Root cause:** visual cards and placeholders substitute for document structure and accessible names; async updates are designed only for sighted users; mobile was assumed from viewport metadata rather than verified.
- **Implementation plan:** one page `h1`, logical headings, `header/nav/main/aside/footer`, persistent `<label>` or `aria-labelledby`, error association, focus management, status versus alert live regions, chart text/table equivalent, visible focus, reduced motion, keyboard-complete controls. Test 320, 390, 768, and desktop widths under actual device emulation, including zoom/text scaling and long content.
- **Migration / old data:** none.
- **Backward-compatibility risk:** semantic DOM, focus, and responsive changes can break brittle selectors and screenshots. Update first-party tests to accessible roles/names, retain stable URLs, and roll out cache-busted assets; accessibility semantics are not optional compatibility debt.
- **Acceptance criteria:** no critical/serious automated violations on critical pages; meaningful heading/landmark outline; every control has an accessible name; async analysis and chart completion announce once; keyboard completes login, ticker analysis, book edit, model view, preferences, and export; no horizontal overflow or obscured action at required widths; touch targets and contrast meet WCAG 2.2 AA.
- **Tests:** semantic component units; axe browser suite; accessibility-tree assertions; keyboard/focus E2E; Playwright device projects for phone/tablet; manual VoiceOver and 200% zoom checklist with evidence.
- **Rollback:** roll back individual CSS/component assets to the prior accessible release; if a critical flow becomes unusable, disable that control rather than bypass labels/focus.
- **Observability:** client errors by viewport, async completion/abandonment, accessibility test artifacts and screenshots in CI; no assistive-technology fingerprinting.
- **Dependencies:** CP-008 and CP-011; blocks public release.

### CP-014 — Harden private-response caching, CSP, and error contracts

- **Priority / status / effort:** P2 defense-in-depth / **PARTIALLY FIXED** / M
- **Owner discipline:** Backend/API DRI is accountable; Frontend/Security implement CSP extraction and Release/SRE validates origin and edge behavior.
- **Affected code/data:** security-header middleware, authenticated HTML/API responses, Cloudflare/cache behavior, inline scripts/styles and event handlers, browser/API error renderers.
- **Evidence:** current security headers are present and Phase 1 adds private/no-store, Pragma, and Vary Cookie for contained owner-domain and role-composed responses. CSP still permits `unsafe-inline`, and the remaining sensitive route inventory needs explicit classification.
- **Reproduction:** inspect response headers for `/`, `/api/me`, `/api/mybook`, export, model, analyses, and account routes; inspect CSP and customer error navigation.
- **Root cause:** headers were added as broad middleware protection around an inline-script application, without classifying responses by sensitivity or extracting executable code into nonce/hash-compatible assets.
- **Implementation plan:** classify response types. Send `Cache-Control: no-store` (plus compatibility headers where required) on authenticated financial/account responses and `Vary: Cookie` on session-dependent representations. Keep deliberately public, versioned market/proof assets separately cacheable. Extract customer JavaScript/event handlers during CP-008, adopt CSP nonces or hashes, and remove `unsafe-inline` from `script-src`; plan style extraction without weakening other directives. Return stable sanitized JSON errors for APIs and an accessible customer error state for browser navigation.
- **Migration / old data:** none; purge edge/browser caches for any route whose policy changes.
- **Backward-compatibility risk:** `no-store` changes cache performance and nonce/hash CSP can break inline handlers or third-party assets. Stage CSP in report-only mode, inventory scripts/styles, update asset loading, and retain the private-cache policy even if CSP rollout pauses.
- **Acceptance criteria:** no authenticated financial/account response is reusable from a shared cache; owner/customer representations cannot share a cache key; public cache behavior remains explicit; CSP blocks an injected inline-script fixture; API errors expose no stack/config details; browser errors retain navigation and accessible status.
- **Tests:** header-policy units; owner/customer/cache integration; API error contracts; browser E2E for offline/403/404/500; CSP violation test; security regression at the edge/staging layer.
- **Rollback:** retain no-store/private headers even if CSP asset extraction rolls back. Re-enable a specific nonce/hash temporarily; never restore a broad policy to solve a single asset failure without review.
- **Observability:** cache-status sampling by route class, CSP violation reports with sensitive URL/query stripping, error class/status counters, and owner/customer cache-key sentinel alarms.
- **Dependencies:** CP-002 and CP-008; blocks final hardening gate but not CP-001 containment.

### CP-015 — Complete chart UI range and provenance contracts

- **Priority / status / effort:** P2 / **PARTIALLY FIXED** / S
- **Owner discipline:** Frontend DRI with Market-Data/API reviewer.
- **Affected code/data:** chart controls in `templates/dashboard.html` or customer bundle, `/api/chart/{symbol}`, intraday cache/provenance metadata.
- **Evidence:** live backend ranges 1D, 5D, 1M, 3M, 6M, 1Y, and `max` return distinct datasets; the UI exposes only through 1Y and has no ALL/MAX control. Intraday responses currently have a null source field.
- **Reproduction:** compare UI controls with direct range responses and inspect `period`, point count, first/last timestamps, interval, source, and data time.
- **Root cause:** backend range repair and UI control/provenance work landed as separate concerns; the client contract was not updated to enumerate supported ranges from one mapping.
- **Implementation plan:** define one shared range enum/mapping; expose “ALL” only if it maps to backend `max`; preserve selected range through loading/error states; display interval, source class, and data-as-of. Populate an explicit intraday source/provenance value or `unknown` with reason—never a misleading null. Keep stale/cached/live semantics distinct.
- **Migration / old data:** none; invalidate old chart cache entries that lack required provenance if they cannot be labeled honestly.
- **Backward-compatibility risk:** adding `max` and provenance fields or rejecting unsupported ranges can affect older clients. Use an additive versioned response, keep existing supported labels stable, and never silently remap an old label to a different period.
- **Acceptance criteria:** every visible control maps to one supported period; switching changes request, point count/time span, chart, and summary; ALL returns the max history; unsupported values return a stable 4xx; every successful response has explicit finite timestamps, interval, source state, and freshness.
- **Tests:** range mapping units; backend contract table; cache/provenance integration; browser E2E for every control, keyboard use, loading/error state, and distinct point counts; mobile chart check under CP-013.
- **Rollback:** hide a broken range while preserving other correct mappings; do not silently map multiple labels to the same period.
- **Observability:** request/error/latency by range and source, null/unknown provenance count, cache age, and client range-switch failures.
- **Dependencies:** CP-008; its phone/tablet chart checks run inside CP-013 before the final release gate.

### CP-016 — Make regression and production verification mandatory release gates

- **Priority / status / effort:** P1 release control / **OPEN** / M
- **Owner discipline:** Release/SRE DRI; each domain DRI owns its gate; independent release approver has final no-go authority.
- **Affected code/data:** CI, staging, migration rehearsal, browser suites, deploy/promotion script, post-deploy smoke, release manifest.
- **Evidence:** Phase 1 unsafe portfolio/trade expectations are inverted; clean base/candidate full suites pass; exact-commit artifact/JUnit gates are implemented. Unknown-symbol, durable publication, staging, accessibility/mobile, rollback, and live identity still block CP-016.
- **Reproduction:** compare test expectations, PR state, live responses, and runtime identity.
- **Root cause:** CI proves a branch's encoded behavior, not that the behavior is safe, mergeable, deployed, identifiable, or financially correct.
- **Implementation plan:** update unsafe expectations first. Add required lanes for tenant/connector independence, model publication state machine, migration rollback, decimal reconciliation, mutation contracts, track-record math, decision coherence, browser network/console, axe, and device emulation. Promotion requires current-main ancestry, clean reproducible artifact, migrations rehearsed, two-person P0 approval, staging E2E, live SHA match, sanitized smoke, and baseline restoration. Persist a signed release manifest.
- **Migration / old data:** CI uses synthetic fixtures only. Staging migration uses a scrubbed production-shaped copy; never copy free-text trade notes or raw brokerage snapshots into test artifacts.
- **Backward-compatibility risk:** the new gates will block changes that formerly passed and may expose flaky legacy tests. Land deterministic fixtures and ownership before enforcement, but do not grandfather any tenant, financial, auth, accessibility, migration, or rollback blocker.
- **Acceptance criteria:** zero failures and no quarantine/xfail for release blockers; PR mergeable and current with main; all P0/P1 and explicitly mandatory P2 gates pass on the exact artifact; customer smoke proves no owner endpoint/data access; rollback drill succeeds; production SHA and schema match manifest; 24-hour canary has no forbidden-data or invariant alert.
- **Tests:** this ticket operationalizes every ticket's test list. A deliberately seeded owner sentinel must make the release fail if it reaches any customer response, cache, HTML, log, or artifact.
- **Rollback:** automated promotion halts before traffic on any mismatch. After traffic, return customer model/trade surfaces to deny mode, reverse active-version pointer, and restore prior known-good artifact; use forward migration unless the rehearsed downgrade is data-safe.
- **Observability:** gate result per artifact, test counts, migration hash, approvals, live SHA, canary metrics, rollback duration, and forbidden-data alerts.
- **Dependencies:** CP-001 through CP-015 as applicable. CP-016 is the final release decision, not a parallel documentation exercise.

## 5. Critical path and parallel work

```text
CP-001 containment [candidate complete; deploy open]
    └─> CP-002 clean release identity [candidate partial; promotion open]
            └─> CP-003 durable schema
                    └─> CP-004 safe ingestion
                            └─> CP-007 financial gate
                                    └─> CP-005 published read model
                                            └─> CP-006 model trade feed
                                                    └─> CP-008 customer cutover
                                                            ├─> CP-013 a11y/mobile ─────┐
                                                            ├─> CP-014 response hardening ─┤
                                                            └─> CP-015 chart contract ─────┤
                                                                                           └─> CP-016 release

CP-002 ─> CP-009 mutation/symbol contract ─┐
CP-002 ─> CP-010 track-record methodology ├─> CP-016
CP-007 ─> CP-011 recommendation coherence ┤
CP-009 ─> CP-012 cost basis ───────────────┘
```

CP-009 through CP-015 can proceed in parallel where their listed dependencies permit, but they cannot displace CP-003 through CP-007 from the critical path.

## 6. Backlog completion rule

A ticket is not “done” when code exists or a branch is green. It is done only when:

1. its acceptance criteria pass on the exact immutable artifact;
2. required reviewers sign the release manifest;
3. migration and rollback are rehearsed where relevant;
4. staging contract/E2E/security tests pass;
5. production reports the expected SHA and schema;
6. the sanitized live smoke verifies the intended customer behavior;
7. the test-account baseline is restored; and
8. no P0/P1 monitoring alert fires during the defined canary.

If the durable architecture slips, the correct state is **continued deny mode**, not a dynamic allowlist over owner data.
