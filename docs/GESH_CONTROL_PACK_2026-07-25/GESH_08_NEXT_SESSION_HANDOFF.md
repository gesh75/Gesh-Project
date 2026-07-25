# GESH 08 — Next-Session Handoff

**As of:** 2026-07-25, Europe/Sofia  
**Decision:** NO-GO for paid/customer release  
**Canonical pack:** `docs/GESH_CONTROL_PACK_2026-07-25/`

## What GESH is

GESH is a FastAPI/Python financial decision product with signed-session authentication, a UUID-scoped customer portfolio (“My Book”), account export, watchlist/preferences, async analysis jobs, charts, mechanical/AI research, Kelly and Monte Carlo outputs, an owner/operator surface, Robinhood/MCP snapshot ingestion, a global trade ledger, and a public track-record page.

The important data domains are:

1. **My Book:** private positions owned by one authenticated customer UUID.
2. **Model Book:** intended customer-visible model strategy; no durable publication domain exists yet.
3. **Owner portfolio:** real operator brokerage data from Robinhood/MCP; owner-only by policy.
4. **Trades:** global real and paper trade tables with free-text notes; currently not tenant/publication scoped.
5. **Track record:** public aggregation from the sibling trading tracker database.

Do not treat these as aliases.

## Repository and runtime baseline

- `origin/main`: `cef1fe604d84b2e9d4cc72384e3998387dc9a0bd` after merged PR #106 (`gesh-core` v0.6.1).
- Checked branch: `fix/audit-p0-p1-remediation` at `09f757c3b61a6d89da6588e3cc17e9853ec39519`.
- Checked branch is 12 commits ahead and 4 behind `origin/main`; the worktree contains unrelated tracked and untracked user changes.
- PR #105 head: `fe65a816cf33a76b710e8f9746f2ef32f5c0b2b2`.
- PR #105: OPEN, non-draft, conflicting/dirty, not merged. CI at that ref: 2,077 passed, 1 skipped, 62.16% coverage; isolation: 83 passed.
- Port-9000 launchd process started 2026-07-23 06:29 EEST with reload disabled. Several relevant files changed afterward.
- No runtime endpoint identifies commit SHA, build time/artifact digest, or schema version. Live behavior cannot be attributed to a single ref.

Do not restart the live process from the current dirty/conflicting worktree.

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

## Current release blockers

### P0 — owner-derived portfolio

Customer `/api/portfolio` returns a response with `source=robinhood_mcp`, ten owner-derived holdings, cash/account fields, stale/offline state, and `reconciles:false`.

Fresh numerical bridge:

- listed holdings: `$73,597.62`;
- reported portfolio equity: `$102,214.50`;
- missing holding component: `$28,616.88`;
- cash/deployable: `$26,402.39`;
- account total: `$128,616.89`;
- listed holdings + cash: `$100,000.01`.

This is not classic query IDOR. Authentication and query tamper resistance work. The normal customer dataset is wrong: `view=model_book` is merely response metadata, and the model is dynamically derived from a real owner snapshot without consent, approval, immutable version, entitlement, or rollback.

### P0 — real trade and private note

Customer `/api/trades` returns a global row with `is_paper:0`, execution/provenance fields, and a private free-text note. `/api/trades/stats` belongs to the same unscoped domain. The global tables have no tenant/publication key; code uses `SELECT *`.

### Other blockers

- Customer JavaScript still polls `/api/portfolio` and owner ECG/regime flows, then throws null-DOM errors; one repeats every 30 seconds.
- Track Record shows engine `+6.7%`, PF `0.91`, win rate `18%`, max drawdown `27%`, Deflated Sharpe `0.44`, and SPY `+nan%`. Headline sums per-trade percentages while PF uses dollar P&L; it is not a capital-matched portfolio return.
- Tested TSLA combined stance is `REDUCE` and mechanical consensus is `SELL`, but the trade setup still presents a long entry/stop/target.
- My Book has no cost basis for the baseline holding.
- No headings, main/nav landmarks, async live region, or complete accessible input names.
- True mobile device emulation is unverified; static CSS suggests possible 320px clipping.
- A missing `Content-Type` on a JSON mutation is accepted in current code.
- My Book can accept a syntactically valid unknown and create work before existence validation.
- Dirty-tree full test result: 1,639 passed, 4 failed, 59.15% coverage, 975 warnings. One failure was environment-induced; three genuine failures come from uncommitted `ai_analyzer.py` streaming changes whose test fakes implement only `.create()`.

## Target decision already made

Use:

- **B immediately:** customer portfolio/trades/stats fail closed.
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

## Exact next action

Create a clean hotfix branch from current `origin/main` and implement containment before attempting the durable model migration:

1. owner-gate customer `/api/portfolio`, `/api/trades`, and `/api/trades/stats`;
2. stop all customer initialization/interval calls to owner portfolio/ECG/regime functions;
3. add explicit build SHA/build time/schema version and private/no-store financial response headers;
4. add negative customer leak tests using arbitrary text and source sentinels;
5. run the focused, full, browser-console, and clean-deploy smoke gates on staging;
6. deploy only from the clean reviewed SHA, then verify that SHA live.

After containment, rebuild the useful PR #105 changes on current main rather than merging the conflicting branch blindly, and implement the immutable Model Book publication schema.

## Required evidence references

- Executive decision: `GESH_00_EXECUTIVE_STATE.md`
- Sources, timeline, endpoint baseline: `GESH_01_SOURCE_AND_TIMELINE.md`
- Architecture and target model: `GESH_02_ARCHITECTURE_AND_DATA_FLOW.md`
- Historical regression state: `GESH_03_REGRESSION_MATRIX.md`
- Current findings: `GESH_04_FINDINGS_REGISTER.md`
- Dependency-ordered tickets: `GESH_05_ENGINEERING_BACKLOG.md`
- Blocking test design: `GESH_06_TEST_AND_CI_PLAN.md`
- Recovery order and rollback points: `GESH_07_30_DAY_RECOVERY_SEQUENCE.md`

## Ready-to-paste continuation prompt

```text
Continue GESH from the canonical 2026-07-25 control pack at:
/Users/georgigaydarov/Projects/gesh-project/docs/GESH_CONTROL_PACK_2026-07-25/

Read all nine files, CLAUDE.md, and current AGENTS instructions before acting. Use Grok 4.5 for repository analysis/implementation as required. Re-fetch origin/main and PR #105 state; do not assume refs are unchanged. Preserve unrelated dirty-worktree changes and do not restart/deploy from that tree.

Start with the exact first-24-hour containment work in GESH_05/GESH_07:
1) create a clean branch from current origin/main;
2) owner-gate customer /api/portfolio, /api/trades, and /api/trades/stats;
3) stop customer polling of portfolio/ECG/regime owner flows;
4) add leak-sentinel, role/route, browser-console, build-identity, and no-store tests;
5) verify on a clean staging artifact with an observable SHA.

Do not place trades, move money, delete accounts, expose secrets, or inspect more private data than necessary. Treat My Book, curated Model Book, owner Robinhood data, global trades, and public track record as separate domains. Do not merge PR #105 wholesale while it is conflicting; port only reviewed changes after containment.

Return evidence: exact commit/ref, diff, tests, staging responses, deployed SHA, rollback point, and updated status for every affected canonical finding. Do not call a serializer-only change a complete Model Book fix. Continue next with the immutable model-publication schema using synthetic/paper provenance by default.
```
