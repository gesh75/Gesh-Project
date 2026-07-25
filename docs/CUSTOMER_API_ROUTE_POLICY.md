# CUSTOMER_API_ROUTE_POLICY

**Stage A** — behavior-neutral documentation. Machine-readable source: `customer_route_policy.py` (exact lookup, no fallback). No runtime enforcement.

## Verified Inventory
- 95 runtime routes
- 95 exact policies

## Category Counts
**Access**

PUBLIC: 3 | CUSTOMER: 63 | OWNER_ONLY: 29

**Input**

NONE: 63 | CUSTOMER_BOOK: 9 | CURRENT_MODEL: 11 | HISTORICAL_DATASET: 12

**Output**

RAW_SAFE: 43 | PUBLISHED_PORTFOLIO: 1 | REQUEST_SCALED: 2 | DATASET_SCALED: 0 | SANITIZE_NO_SCALE: 7 | BLOCK: 42

**Missing**

NOT_APPLICABLE: 43 | TYPED_503: 23 | OWNER_ONLY: 29 | PASS_THROUGH: 0

## Owner Rule
All 28 routes recursively depending on `require_api_key` are OWNER_ONLY/BLOCK/OWNER_ONLY.

GET `/api/usage/summary` is also owner-only (current main returns aggregate cross-customer usage).

## Customer-Owned Groups
All mybook, account export/delete, preferences, watchlist, `/api/me` are CUSTOMER RAW_SAFE and never owner-scaled.

## Current-Model Exact Breakdown
- `/api/portfolio`: PUBLISHED_PORTFOLIO (uses own `data.calculated_total_equity`)
- SANITIZE_NO_SCALE (exact 7): `/api/market-data`, `/api/regime`, `/api/agents/sec/history`, `/api/agents/news/history`, `/api/fft-cycles`, `/api/benchmarks/{period}`, `/api/crypto-correlation`
- REQUEST_SCALED (exact 2): `/api/ecg`, `/api/yield/landscape` (request-scoped model snapshot basis)
- `/api/tax/harvest`: BLOCK

## Historical Restriction
Exact 12 BLOCK/TYPED_503 routes:

`/api/history`, `/api/history/{run_id}`, `/api/portfolio/history`, `/api/deep-analysis/cached`, `/api/consensus/cached`, `/api/kelly/cached`, `/api/monte-carlo/cached`, `/api/trades`, `/api/trades/stats`, `/api/paper/summary`, `/api/consensus/{symbol}`, `/api/explainability/{symbol}`

Both are blocked because run_consensus_holding always includes owner-derived ECG drawdown/streak from equity_snapshots, and explainability embeds it in node bullets/full narrative despite the intended stateless single-symbol surface. Valid future dataset basis must immutably prove: original composition or total basis, source timestamp, stable dataset/composition ID, one dataset-level normalization anchor.

## Unresolved Decisions
- No caller-specific usage summary on current main.
- tax-harvest lacks publishable cost-basis/lot provenance (current price alone insufficient).
- Historical datasets lack immutable bases → DATASET_SCALED empty.
- Stage B: implement request-scoped scaling/redaction without substituting later snapshots.
- Stage B must remove or prove/sanitize this owner-history dependency before exposing those routes.

Exact lookup. No fallback. Compact. Reviewable.