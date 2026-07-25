# GESH 01 — Source, Baseline, and Timeline

**Evidence cut:** 2026-07-25, Europe/Sofia  
**Purpose:** one reconciled history and present-state baseline; not a transcript summary

## Evidence method

Conclusions follow the required hierarchy:

1. **Tier 1:** freshly reproduced behavior; code at an exact ref; current schema/tests; dated runtime process and logs.
2. **Tier 2:** latest dated API/browser evidence and audit reports.
3. **Tier 3:** older reports, design documents, remediation claims, and chat conclusions.
4. **Tier 4:** inference.

Important statements are labelled:

- **VERIFIED CURRENT**
- **SUPPORTED BY LATEST EVIDENCE**
- **HISTORICAL ONLY**
- **INFERRED**
- **UNKNOWN / REQUIRES ACCESS**

“Current code” is not a single object in this repository. This pack distinguishes:

| Label | Exact object | Meaning |
|---|---|---|
| Current remote main | `cef1fe604d84b2e9d4cc72384e3998387dc9a0bd` | Latest merged source after PR #106. |
| Checked branch | `09f757c3b61a6d89da6588e3cc17e9853ec39519` | Local `fix/audit-p0-p1-remediation`; 12 unique commits and 4 behind `origin/main`; dirty worktree. |
| Latest audit candidate | `fe65a816cf33a76b710e8f9746f2ef32f5c0b2b2` | PR #105 head; open, conflicting, CI green at the checked time, not merged. |
| Live runtime | unknown immutable ref | Process on port 9000 started 2026-07-23 before several current files changed; no build/version endpoint identifies a SHA. |

## Source inventory

| ID | Date | Source | Type and scope | Reliability |
|---|---|---|---|---|
| SRC-REQ-01 | 2026-07-24 anchor | `GESH_SOL_ULTRA_MASTER_PROMPT.txt` | Requirements, safety boundary, taxonomy, mandatory deliverables | Requirements authority; not factual proof |
| SRC-HIST-01 | Undated; context 2026-07-22 | `Pasted text(16).txt`, original audit prompt | Test scope and authorization | Directive only |
| SRC-HIST-02 | 2026-07-22 | Historical artifact, Round 1 working log and final, lines 426 and 922 | Browser/API/product audit; 41/100 | Tier 2-minus: dated observations, but original trace files absent |
| SRC-HIST-03 | 2026-07-22 | Historical artifact, expanded authorization, line 1095 | Controlled mutation/security scope | Directive only |
| SRC-HIST-04 | 2026-07-22 | Historical artifact, Round 2 working log and final, lines 2255 and 2710 | Security, financial, validation, mutation tests; 43/100 | Tier 2-minus; several causal theories later disproved |
| SRC-HIST-05 | 2026-07-24 by sequence | Historical artifact, post-fix refresh, line 2897 | First deployed-fix retest | Tier 2; precise timestamp omitted |
| SRC-HIST-06 | 2026-07-24 | Historical artifact, Round 3, line 2941 | 30-point regression; 58/100 | Strongest historical live summary |
| SRC-HIST-07 | 2026-07-24 | Historical artifact, deep dive, line 3018 | A-01/A-03 reclassification, concurrency, accessibility/mobile | Tier 2 |
| SRC-DES-01 | 2026-05-29 | `docs/superpowers/specs/2026-05-29-gesh-dashboard-redesign-design.md` | Dashboard/product redesign intent | Historical design, not proof of current behavior |
| SRC-DES-02 | 2026-07-08 | `docs/CUSTOMER_SURFACE.md` | Intended customer route and “zero owner data” contract | Strong for intent; contradicted by current portfolio/trade behavior |
| SRC-REV-01 | 2026-07-09; updated 2026-07-14 | `GAPS_AND_IMPROVEMENTS_2026-07-09.md` | Product/readiness review | Tier 3; overstates isolation maturity |
| SRC-DES-03 | 2026-07-12 | `docs/superpowers/specs/2026-07-12-gesh-full-remediation-design.md` | Remediation design | Design intent only |
| SRC-REV-02 | 2026-07-14 | `docs/CODEX_REVIEW_HANDOFF_2026-07-14.md` | Earlier PR review and masking model | Tier 3 self-report |
| SRC-OPS-01 | 2026-07-15–16 | `docs/reports/2026-07-16-session-report.md`, `docs/SESSION_MEMORY_2026-07-16.md` | Robinhood MCP snapshot introduction and deployment context | Corroborates real-account provenance |
| SRC-GIT-01 | 2026-07-16–20 | Git history and operational docs | Auth hardening, customer features, snapshot work, legal/export/deletion, analysis jobs | Tier 1 at exact commits; not deployment proof |
| SRC-REV-03 | 2026-07-23 | `docs/SESSION_REVIEW_PACKET_2026-07-23.md` | Independent-review packet and build concerns | Useful but untracked/self-authored; claims require recheck |
| SRC-REM-01 | 2026-07-23 | `docs/REMEDIATION_2026-07-23_AUDIT_P0_P1.md` at checked branch | Root-cause and remediation record | Tier 1 for that branch only |
| SRC-GIT-02 | 2026-07-23–25 | Audit branch commits through `fe65a81` | Valuation, publication, verdict, track record, UI, tests, security, build work | Tier 1 at each ref; later commits disprove earlier closure claims |
| SRC-PR-01 | Checked 2026-07-25 | GitHub PR #105 | Audit candidate state and CI | OPEN, non-draft, conflicting, not production |
| SRC-MAIN-01 | 2026-07-25 | PR #106 / `origin/main` `cef1fe6` | `gesh-core` v0.6.1 dependency update | Merged current-main evidence |
| SRC-LIVE-01 | 2026-07-25 | Authenticated browser and API observations | Customer UI, endpoint contracts, state, charts, analysis, track record, headers, console | **VERIFIED CURRENT**; sensitive fields minimized/redacted |
| SRC-RUN-01 | 2026-07-25 | Local process, launchd, file times, `/tmp/gesh_main.log` | Port-9000 runtime identity and repeated polling | **VERIFIED CURRENT** locally; no immutable deployed SHA |
| SRC-CODE-01 | 2026-07-25 | Source/schema review at all three refs | Identity, customer tables, model portfolio, trades, track record, browser bundle | Tier 1 at exact refs |
| SRC-TEST-01 | 2026-07-25 | Focused and full local test runs | Regression, isolation, build health | **VERIFIED CURRENT** for dirty checked worktree only |
| SRC-REVIEW-01 | 2026-07-25 | Coordinated history, architecture, verification, and Grok 4.5 review lanes | Adversarial synthesis | Advisory; conclusions retained only where supported by primary evidence |

The historical attachment is a concatenated transcript. Its “rounds” are evidence units from related sessions, not independent third-party audit reports.

## Fresh account and application baseline

### Account state after testing

**VERIFIED CURRENT, restored 2026-07-25**

| Item | Final state |
|---|---|
| Role/view | `customer` / response `view=model_book` |
| My Book | TSLA `2.7971` shares only |
| Account export | TSLA `2.7971` shares |
| Watchlist | `[T, TSLA, NVDA]` |
| Test symbols | Temporary SPY removed; no test position left |
| Preferences | Original opt-out values restored |
| Account | Active; deletion was not attempted |

No real brokerage trade, money movement, payment, account deletion, credential attack, cross-customer mutation, or disruptive load test was performed. The real-trade note was read only far enough to confirm the boundary failure and is not reproduced.

The logical account baseline was verified after restoration. The auth SQLite file hash changed during the wider review window while the live service was active and controlled mutations occurred; the financial database hash did not change. A byte-identical auth database was neither expected nor claimed, and the exact writer responsible for every file-level change is indeterminate.

### Customer-book financial baseline

**VERIFIED CURRENT**

| Value | Amount |
|---|---:|
| TSLA quantity | `2.7971` |
| Current price | `$319.69` |
| Current position/customer equity | `$894.20` |
| Prior-close position/equity | `$1,046.14` |
| Day change | `-$151.94` |
| Day change % | `-14.5239%` |
| Cash | `$0.00` |
| Cost basis | unavailable |
| API reconciliation | `true` |

Independent arithmetic:

- `2.7971 × 319.69 = 894.204899`, rounded to `$894.20`.
- `$894.20 - $1,046.14 = -$151.94`.
- `-$151.94 / $1,046.14 = -14.524%` within response precision.

Agreement between UI and API is not the only proof: the quantities and amounts were recomputed independently.

### Model/owner-derived portfolio baseline

**VERIFIED CURRENT**

| Value | Amount |
|---|---:|
| Source | `robinhood_mcp` |
| Data as of | `2026-07-24T18:46:33Z` |
| Offline/stale flags | `true` / `true` |
| Listed holdings | 10 |
| Sum of listed holdings | `$73,597.62` |
| Reported portfolio equity | `$102,214.50` |
| Cash/deployable/buying-power component | `$26,402.39` |
| Account equity/total | `$128,616.89` |
| Holdings gap to reported portfolio | `$28,616.88` |
| Holdings plus cash | `$100,000.01` |
| Reported portfolio plus cash | `$128,616.89` |
| Day change | `-$169.57` / `-0.17%` |
| API reconciliation | `false` |

The `$28,616.88` difference cannot be explained from customer-visible holding rows. The response combines at least two accounting bases. The one-cent `$100,000.01` result shows a scaled model-notional layer, while `$128,616.89` preserves an owner-account component.

## Current endpoint evidence

All calls used the authorized test account. Parameters and responses below are sanitized.

| Surface | Method | Sanitized parameters/body | Expected result | Actual status/minimal sanitized response | UI result | Persistence | Evidence date | Current classification |
|---|---|---|---|---|---|---|---|---|
| `/api/me` | GET | None | Authenticated caller’s role/view only | 200; `customer`, `view=model_book` | Customer shell loaded | None | 2026-07-25 | VERIFIED CURRENT |
| `/api/mybook` | GET | None | Caller’s positions and reconciled valuation | 200; one TSLA row, valuation `reconciles:true` | My Book showed the same TSLA holding | None | 2026-07-25 | VERIFIED CURRENT |
| `/api/mybook` | POST | `{"symbol":"TSLA","quantity":<controlled valid value>}` | Exact caller row updated once | 200; one complete row | Value updated, then baseline was restored | Caller row changed; restored | 2026-07-25 | FIXED AND VERIFIED |
| `/api/mybook` | Two concurrent POSTs | Same symbol with paired controlled values | One untorn row; no duplicate | Both 200; one complete final row | Final row remained usable | One row only; baseline restored | 2026-07-25 | NO LONGER REPRODUCIBLE for race hypothesis |
| `/api/mybook` | POST invalid matrix | Empty/space/XSS/negative/zero/huge bodies | 400; zero side effects | 400 | Form/API rejected input | No artifact | 2026-07-25 | FIXED AND VERIFIED |
| `/api/mybook` | POST | Valid JSON bytes, `Content-Type:text/plain` | 415; zero side effects | 415 | N/A (API contract) | No artifact | 2026-07-25 | FIXED AND VERIFIED |
| `/api/mybook` | POST, isolated local proof | Valid JSON bytes, no `Content-Type` | 415; zero side effects | 200; accepted | N/A | Position can persist | 2026-07-25 local/code | OPEN |
| `/api/mybook/{symbol}` | DELETE | Temporary path symbol `SPY` | Delete caller’s row only | 200 | Temporary row disappeared | SPY removed; baseline restored | 2026-07-25 | VERIFIED CURRENT |
| `/api/account/export` | GET | None | Caller-only export; exact quantities | 200; TSLA `2.7971`, no other tenant | Download/API agreed with My Book | None | 2026-07-25 | FIXED AND VERIFIED |
| `/api/portfolio` | GET | Baseline plus harmless `view=owner` and user/account sentinels | 403 or entitled immutable curated model | 200; same owner-derived `robinhood_mcp` model response | Owner cards hidden, but customer page fetched data in background | None | 2026-07-25 | RECLASSIFIED — TEN-01 practical P0 remains open |
| `/api/trades` | GET | None | 403 or curated versioned model events only | 200; one global `is_paper:0` row and non-empty private note | No dedicated visible customer result inspected | None | 2026-07-25 | OPEN (TEN-02 P0) |
| `/api/trades/stats` | GET / code contract | Default and paper-like query semantics reviewed | Same boundary as customer trades; no global aggregate | Customer-readable handler; aggregate ignores intended paper boundary | N/A | None | 2026-07-25 code | OPEN (TEN-02 boundary) |
| `/api/config` | GET and POST | GET none; POST unchanged/sanitized config body | 403 for customer | 403 / 403 | No customer config UI | No change | 2026-07-25 | FIXED AND VERIFIED |
| `/api/preferences` | GET and POST | Existing boolean values only | Caller-only round trip | 200 / 200 | Preferences remained unchanged | Original values restored | 2026-07-25 | VERIFIED CURRENT |
| `/api/watchlist` | GET, POST, DELETE | Temporary `SPY` add/delete | Caller-only exact list | All 200 | Temporary symbol appeared then disappeared | `[T, TSLA, NVDA]` restored | 2026-07-25 | VERIFIED CURRENT |
| `/api/mybook/analyze` | POST/poll | `{"symbol":"TSLA"}` | One durable job, bounded polling, one terminal result | 202 running, then 200 complete in about 35s | Progress completed and results rendered | One job; no duplicate artifact observed | 2026-07-25 | VERIFIED CURRENT |
| `/api/mybook/analyze` | Immediate repeat POST | Same TSLA request | Cached terminal response | 200 cached in under one second | Cached result reused | Cache hit; no new job observed | 2026-07-25 | VERIFIED CURRENT |
| `/api/analyze-ticker` | POST | Unknown `ZZTEST` | Synchronous not-found before spend/persistence | 404 before analysis | No fabricated cards rendered | No persistence | 2026-07-25 | NO LONGER REPRODUCIBLE on this endpoint |
| `/api/chart/TSLA` | GET | `range=1D,5D,1M,3M,6M,1Y,max` | Distinct supported periods | All 200; distinct counts/dates | 1D–1Y controls worked; UI lacks ALL/MAX | None | 2026-07-25 | PARTIALLY FIXED |
| Track Record | GET page | Public, no body | Finite, reproducible, correctly labelled quality/return/benchmark | 200; engine +6.7%, PF 0.91, win 18%, drawdown 27%, SPY `nan` | Cohorts visible; non-finite benchmark shown | None | 2026-07-25 | PARTIALLY FIXED |
| `/watch`, `/invest`, `/usage`, `/api/usage/summary` | GET | Customer session; no body | 403 | 403 | Forbidden/no owner UI | None | 2026-07-25 | FIXED AND VERIFIED |
| `/api/account/delete` | GET only | No destructive confirmation | 405; no mutation | 405 | N/A | No deletion | 2026-07-25 | NO LONGER REPRODUCIBLE for GET trigger |
| Customer dashboard | Browser/network/console | Authenticated customer, idle across refresh intervals | No owner fetches and zero uncaught errors | Owner widgets hidden; owner-data polling and null-DOM errors continue | One error repeated about every 30s | No intended mutation | 2026-07-25 | PARTIALLY FIXED |

### Chart counts

**VERIFIED CURRENT**

| Requested range | Returned range/period | Points | Source field |
|---|---|---:|---|
| 1D | `1d`, 5-minute interval | 192 | absent/null |
| 5D | five intraday sessions | 960 | absent/null |
| 1M | one month | 20 | Yahoo |
| 3M | three months | 61 | Yahoo |
| 6M | six months | 124 | Yahoo |
| 1Y | one year | 250 | Yahoo |
| max | weekly, 2010-06-28 to 2026-07-20 | 839 | Yahoo |

The UI exposes 1D, 5D, 1MO, 3MO, 6MO, and 1Y. It does not expose ALL/MAX, so the backend fix is not a complete customer feature.

## Chronological reconstruction

### Product construction and intended boundary

- **2026-05-29 — HISTORICAL ONLY:** dashboard redesign documented the research/decision product direction.
- **By 2026-07-08 — HISTORICAL ONLY:** the Customer Surface runbook described authenticated per-user positions, watchlist, analyses, and “zero owner data” as the intended contract.
- **2026-07-09–14 — HISTORICAL ONLY:** internal reviews and remediation work increasingly treated response scaling/masking as the isolation boundary. This was the pivotal architecture error: it normalized presentation without creating a separate published dataset.
- **2026-07-15–16 — SUPPORTED BY LATEST EVIDENCE:** Robinhood MCP became a source for the dashboard snapshot. Session records describe a real connected-account snapshot with `source=robinhood_mcp`.
- **2026-07-16–20 — SUPPORTED BY LATEST EVIDENCE:** auth/cookie hardening, customer job reliability, pricing, retention, export/deletion, legal pages, and operational diagnostics expanded the product. This produced a broad paid-beta surface, but did not replace the global owner-data paths.

### Audit rounds

- **2026-07-22 Round 1 — HISTORICAL ONLY:** found My Book/export quantity divergence, chart-range failure, model-book reconciliation failure, verdict conflict, provenance/freshness confusion, and misleading track-record framing. Score: 41/100; internal beta only.
- **2026-07-22 Round 2 — HISTORICAL ONLY:** reproduced the major issues and added customer day-change misuse, delivered-but-hidden owner DOM, customer-readable config, media-type/CSRF hardening, form concatenation, and long-job concerns. Score: 43/100.
- **2026-07-23 — SUPPORTED BY LATEST EVIDENCE:** audit-remediation commits introduced Decimal valuation, a published-model serializer, verdict reconciliation, previous-close pricing, engine-first track record, owner-template separation, route/security changes, and a broad regression suite.
- **2026-07-24 post-fix refresh — HISTORICAL ONLY:** live evidence verified quantity parity, chart routing, config guard, `text/plain` rejection, customer day-change, and removal of visible owner controls. `/api/portfolio` still exposed owner-derived data.
- **2026-07-24 Round 3 — HISTORICAL ONLY:** 30-point regression scored 58/100. It retained A-01 and identified A-03 `/api/trades`.
- **2026-07-24 deep dive — HISTORICAL ONLY:** reclassified A-01 from simple cross-role/IDOR language to an intentionally assigned model-book with unsafe real-account provenance; verified controlled concurrency; documented accessibility gaps and incomplete mobile testing.
- **2026-07-24 22:06 EEST — SUPPORTED BY LATEST EVIDENCE:** PR #105 opened.

### Candidate remediation continued to change

- **2026-07-24 `b58e7b5`:** changed `reconciles` from a literal claim to a computed contract.
- **2026-07-24 `1b6f745`:** unified more unknown/unavailable symbol semantics.
- **2026-07-24 `5c914b2`:** added cleaner dependency-installation work.
- **2026-07-25 `949e13f`:** corrected holding identity and cash-aware day returns.
- **2026-07-25 `6f20808`:** corrected account-level equity semantics that prior remediation had misread.
- **2026-07-25 `fe65a81`:** corrected raw snapshot `price` versus `current_price` normalization before model scaling.

These successive corrections prove that earlier “all P0 exit criteria met” or “final closure” text was premature.

### Fresh canonical audit

- **2026-07-25 — VERIFIED CURRENT:** the test account was authenticated and its baseline reconstructed.
- **2026-07-25 — VERIFIED CURRENT:** live P0 portfolio and trade exposures were reproduced with minimum evidence.
- **2026-07-25 — VERIFIED CURRENT:** financial bridges, all chart ranges, My Book parity/day change, controlled mutations, async analysis, headers, route guards, browser errors, accessibility semantics, and track-record calculations were checked.
- **2026-07-25 — VERIFIED CURRENT:** current `main`, dirty checked branch, PR #105, CI state, process age, file timestamps, tests, and logs were compared.
- **2026-07-25 — VERIFIED CURRENT:** PR #106 merged and moved `origin/main` to `cef1fe6`; PR #105 remained open/conflicting.

## Contradictions resolved

| Apparent contradiction | Canonical resolution |
|---|---|
| Export quantity looked like a second store or migration error. | One UUID-scoped store existed. Broad customer middleware scaled export quantities while `/api/mybook` was exempt. The `2.7971` factor was presentation transformation, not migration. |
| A-01 was described as IDOR/cross-role access, but query tampering failed. | Reclassified: authentication and parameter resistance work. Current code intentionally gives customers a global owner-derived “model book.” The practical privacy risk remains P0. |
| Owner console was “rendered” yet later “removed.” | It was delivered and CSS-hidden, not necessarily normally visible. Visible markup separation improved; customer JS still polls owner endpoints and calls missing owner renderers. |
| “Day” was fixed, but model day change still failed. | Two defects existed: My Book used lifetime cost return as Day; model portfolio mixed account/component/prior-close bases. They require separate tests. |
| Model reconciliation changed from roughly `$2.1k` to roughly `$28.6k`. | Account data, scaling, and field semantics changed. Later commits reveal multiple causal layers. Do not merge the historical figures into one invariant delta. |
| Fifteen analyze POSTs looked like duplicate expensive jobs. | The contract polls with POST after 202. A unique job guard existed; the real browser defect was duplicate polling loops. Fresh testing saw one job complete and then cache. |
| `text/plain` was called an exploitable CSRF hole despite SameSite. | SameSite=Lax reduced the common cross-site-cookie path. Accepting the media type was still fragile defense-in-depth. Current live 415 and Origin checking are valid fixes. |
| PF below 1 with positive return was called mathematically impossible. | It can occur when profit factor is dollar-weighted while the headline sums unweighted per-trade percentages. The inconsistency is the undisclosed mixed basis and misleading “total P&L” label. |
| RSI values disagreed. | Different lookbacks/timeframes can legitimately differ. The current defect is missing basis labels unless a same-window recomputation proves a math error. |
| Unknown ticker was both rejected and accepted. | Different entry points were tested. `/api/analyze-ticker` now rejects the tested unknown; My Book position/analyze flows still need one synchronous listed-asset contract. |
| “Fixed in code” was treated as “fixed live.” | PR #105 is unmerged and conflicting; the running process has no SHA and predates later file changes. Branch, current main, and live claims are separate. |
| Grok review suggested Model Book should simply equal My Book. | Rejected as an architecture shortcut. My Book is customer-owned. Model Book is a distinct product and must be a curated immutable publication if retained. |

## Evidence gaps and limits

- **UNKNOWN / REQUIRES ACCESS:** exact immutable commit/container/build that produced the current live process.
- **UNABLE TO VERIFY:** true iOS/Android phone and tablet layouts; in-app viewport control did not change the rendering viewport and Chrome control was unavailable.
- **UNABLE TO VERIFY:** a clean, conflict-resolved PR #105 successor on top of `cef1fe6`; it does not yet exist.
- **UNABLE TO VERIFY:** a complete account export-to-restore round trip. Export was read; destructive/full restore was not appropriate during this audit.
- **UNABLE TO VERIFY:** reconciliation of every missing model-portfolio dollar because the public response omits the position/account components needed to bridge `$28,616.88`.
- **UNABLE TO VERIFY:** a full independent recomputation of the live Kelly and Monte Carlo outputs. The completed analysis contained those sections, but this pass did not preserve their complete input/assumption vectors; branch property tests are supporting evidence, not a live quantitative proof.
- **UNKNOWN / REQUIRES ACCESS:** explicit legal consent or policy authorizing operator real-account composition for customer publication. No durable artifact/schema was found.
- **UNABLE TO VERIFY:** cross-account isolation with a second controlled customer. Code and tamper tests support UUID isolation for customer-owned tables, but no second account was used.
- **UNABLE TO VERIFY:** production database migration/rollback under a clean candidate, because no deployment was performed.
