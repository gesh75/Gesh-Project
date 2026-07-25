# GESH 08 — Next-Session Handoff

**As of:** 2026-07-25, Europe/Sofia  
**Decision:** NO-GO for paid/customer release  
**Canonical pack:** `docs/GESH_CONTROL_PACK_2026-07-25/`
**Phase 1 result:** complete as a clean, reviewed, undeployed containment candidate; see `GESH_09_P0_CONTAINMENT_RESULT.md`.

## What GESH is

GESH is a FastAPI/Python financial decision product with signed-session authentication, a UUID-scoped customer portfolio (“My Book”), account export, watchlist/preferences, async analysis jobs, charts, mechanical/AI research, Kelly and Monte Carlo outputs, an owner/operator surface, Robinhood/MCP snapshot ingestion, a global trade ledger, and a public track-record page.

The important data domains are:

1. **My Book:** private positions owned by one authenticated customer UUID.
2. **Model Book:** intended customer-visible model strategy; no durable publication domain exists yet.
3. **Owner portfolio:** real operator brokerage data from Robinhood/MCP; owner-only by policy.
4. **Trades:** global real and paper trade tables with free-text notes; not tenant/publication scoped and now denied to customers in Phase 1.
5. **Track record:** public aggregation from the sibling trading tracker database.

Do not treat these as aliases.

## Repository and runtime baseline

- Recorded exact then-current `origin/main` base: `68287dfd80eab51b40d4270eab07b0bd73267992`, including squash-merged PR #105.
- Phase 1 branch: `codex/p0-data-boundary-containment-20260725`.
- Clean implementation commit: `8ff34b531b31248d71178c6ed23cb88182cf1724`; after the documentation-only descendant is committed, its exact SHA is resolved by the GESH-09 export placeholder and a new clean-final release manifest. The recorded candidate manifest identifies the implementation commit only.
- Clean base suite: 2,078 passed, zero failed/skipped.
- Clean implementation suite: 2,206 passed, zero failed/skipped, 62.52% coverage, three warnings.
- Candidate runtime: 80-member deterministic USTAR, 1,556,480 bytes, SHA-256 `a0d8a80a0255e393675352422992eb343df30bb60c016b17ec659d3e8324e345`.
- Port-9000 launchd process started 2026-07-23 06:29 EEST with reload disabled. Several relevant files changed afterward.
- Candidate `/api/version` safely identifies service, exact commit, UTC build time, and `dirty:false`; adjacent manifest carries digest/lock/test evidence. Production remains unidentified because no deployment occurred.

Do not restart the live process from a mutable worktree. Promote only an approved saved artifact after staging/rollback gates.

## Restored authorized test-account baseline

- Role/view response: `customer` / `model_book`.
- My Book: TSLA `2.7971` only.
- Export: TSLA `2.7971`.
- Watchlist: `[T, TSLA, NVDA]`.
- Temporary SPY artifacts removed.
- Preferences restored.
- Account remains active.

Never reproduce credentials, cookies, tokens, or private trade-note content in output.

## Fixes verified live on 2026-07-25

- My Book/write/export quantity parity.
- Customer valuation and day-change arithmetic:
  - current `$894.20`;
  - prior close `$1,046.14`;
  - day `-$151.94` / `-14.5239%`;
  - `reconciles:true`.
- Distinct chart data for 1D, 5D, 1M, 3M, 6M, 1Y, and backend `max`.
- Customer GET/POST `/api/config` returns 403.
- JSON body sent as `text/plain` returns 415; malformed JSON 400; untrusted Origin 403.
- Tested owner/admin routes return 403; deletion GET returns 405.
- Invalid/negative/zero/huge My Book inputs rejected.
- Mass-assigned role/admin/view fields and identity query overrides ignored in spot checks.
- Controlled same-symbol writes left one complete row; baseline restored.
- One `/api/mybook/analyze` job completed via 202 polling; immediate repeat was cached.
- Visible owner-operation cards are absent from normal customer UI.

## Phase 1 containment and current release blockers

### P0 — owner-derived portfolio: contained in candidate

Before containment, customer `/api/portfolio` returned `source=robinhood_mcp`, ten owner-derived holdings, cash/account fields, stale/offline state, and `reconciles:false`. Phase 1 now returns the exact `model_book_unavailable` 403 before handler/cache/connector access. Query tampering cannot alter scope, and the denial contains no owner field.

Fresh numerical bridge:

- listed holdings: `$73,597.62`;
- reported portfolio equity: `$102,214.50`;
- missing holding component: `$28,616.88`;
- cash/deployable: `$26,402.39`;
- account total: `$128,616.89`;
- listed holdings + cash: `$100,000.01`.

This is not classic query IDOR. Authentication and query tamper resistance work. The normal customer dataset is wrong: `view=model_book` is merely response metadata, and the model is dynamically derived from a real owner snapshot without consent, approval, immutable version, entitlement, or rollback.

### P0 — real trade and private note: contained in candidate

Before containment, customer `/api/trades` returned a global `is_paper:0` row, execution/provenance fields, and a private free-text note. Phase 1 returns the exact `customer_trade_ledger_unavailable` 403 for both `/api/trades` and `/api/trades/stats` before repository access. The owner tables remain internal and unchanged.

### Other blockers

- **Resolved in Phase 1 candidate:** customer web and Android contain zero removed owner endpoint fetches/retries/polls; real Chromium emits no related console error across two refresh intervals.
- Track Record shows engine `+6.7%`, PF `0.91`, win rate `18%`, max drawdown `27%`, Deflated Sharpe `0.44`, and SPY `+nan%`. Headline sums per-trade percentages while PF uses dollar P&L; it is not a capital-matched portfolio return.
- Tested TSLA combined stance is `REDUCE` and mechanical consensus is `SELL`, but the trade setup still presents a long entry/stop/target.
- My Book has no cost basis for the baseline holding.
- No headings, main/nav landmarks, async live region, or complete accessible input names.
- True mobile device emulation is unverified; static CSS suggests possible 320px clipping.
- A missing `Content-Type` on a JSON mutation is accepted in current code.
- My Book can accept a syntactically valid unknown and create work before existence validation.
- Historical dirty-tree result was 1,639 passed/4 failed; it remains classified evidence. It did not reproduce on exact clean base (2,078 passed) or Phase 1 implementation (2,206 passed). No test was skipped, weakened, deleted, or xfailed.

## Target decision already made

Use:

- **B immediately — implemented in Phase 1 candidate:** customer portfolio/trades/stats fail closed.
- **C durably:** separate curated, immutable Model Book dataset.
- **D by default:** synthetic/paper source.
- **E only by exception:** explicitly consented real snapshot, quarantined, stripped, reviewed, reconciled, approved, and published as an immutable derivative.

Do not auto-migrate current raw snapshots or trade notes. Owner data remains internal. Customer model events use a structured allowlist, never copied free text.

Required model records:

- `model_portfolios`;
- `model_portfolio_versions`;
- `model_portfolio_holdings`;
- `model_trade_events`;
- `model_portfolio_assignments`;
- append-only `model_publication_events`.

No active approved version must fail closed—never fall back to owner data.

## Exact next action after Phase 1

Do not repeat the broad audit and do not reopen the contained routes. Review/merge the clean Phase 1 result through the normal process, then perform staging and rollback proof on the saved artifact. Phase 2 may implement only the separately approved durable publication work:

1. preserve the three exact 403 contracts and zero customer owner-domain requests;
2. design/approve immutable Model Book schema, consent/quarantine, assignment, publication event, and atomic rollback;
3. use synthetic/paper provenance by default; do not migrate owner snapshots or notes;
4. close the existing financial, track-record, recommendation, accessibility, mobile, mutation, staging, and rollback gates;
5. deploy only through a separately authorized production change and verify the live SHA.

PR #105 reconstruction is no longer an action: it is already in the recorded Phase 1 base.

## Required evidence references

- Executive decision: `GESH_00_EXECUTIVE_STATE.md`
- Sources, timeline, endpoint baseline: `GESH_01_SOURCE_AND_TIMELINE.md`
- Architecture and target model: `GESH_02_ARCHITECTURE_AND_DATA_FLOW.md`
- Historical regression state: `GESH_03_REGRESSION_MATRIX.md`
- Current findings: `GESH_04_FINDINGS_REGISTER.md`
- Dependency-ordered tickets: `GESH_05_ENGINEERING_BACKLOG.md`
- Blocking test design: `GESH_06_TEST_AND_CI_PLAN.md`
- Recovery order and rollback points: `GESH_07_30_DAY_RECOVERY_SEQUENCE.md`
- Phase 1 containment result and exact evidence: `GESH_09_P0_CONTAINMENT_RESULT.md`

## Ready-to-paste continuation prompt

```text
Continue GESH from the canonical 2026-07-25 control pack on branch:
codex/p0-data-boundary-containment-20260725

First fetch/check out the exact final control-pack commit recorded by the
GESH-09 `export-subst` value or its adjacent final release manifest. Then read:
docs/GESH_CONTROL_PACK_2026-07-25/

Read all ten control-pack files, CLAUDE.md, and current AGENTS instructions before acting. Do not use a similarly named directory from an unrelated or stale worktree. Use Grok 4.5 for repository analysis/implementation as required. Re-fetch origin/main state; do not assume refs are unchanged. Preserve unrelated dirty-worktree changes and do not restart/deploy from that tree.

Phase 1 containment is complete in the clean candidate recorded by GESH-09. Preserve:
1) exact customer 403 contracts for /api/portfolio, /api/trades, and /api/trades/stats;
2) zero customer web/Android owner-domain fetches;
3) owner behavior;
4) repository/cache/tamper/sentinel/browser/build-identity/artifact gates.

Do not place trades, move money, delete accounts, expose secrets, or inspect more private data than necessary. Treat My Book, curated Model Book, owner Robinhood data, global trades, and public track record as separate domains.

Continue only with separately authorized Phase 2 immutable model-publication work using synthetic/paper provenance by default. Return exact commit/ref, diff, tests, staging/rollback evidence, and updated canonical status. Do not call deny mode or a serializer-only change a complete Model Book publication fix.
```
