---
name: isolating-friend-books
description: >
  Use when analyzing a friend's portfolio, a screenshot of someone else's
  allocation, EUR/UCITS holdings, or running the GESH chain on any book that
  is not the owner's Robinhood snapshot. Also use when tempted to reuse
  live_portfolio_snapshot.json, engine_results.json, or analysis_runs for a
  non-owner book.
model: grok-4.6
---

# Isolating friend books

Owner RH, customer `/mybook`, and every friend book are **separate**. Mixing
them is a privacy leak and a data-corruption bug. Not financial advice.

**REQUIRED BACKGROUND:** daily-portfolio-analysis for the chain; gesh-daily-report
if you generate HTML.

## Which book is this?

| Book | Path / env | Write analysis_runs? |
|---|---|---|
| Owner RH (daily chain) | repo `live_portfolio_snapshot.json` | yes |
| Customer | `/api/mybook` — their typed book | no (customer DB) |
| Isolated friend chain (2026-08-23) | `exports/friend_book/` | **no** |
| Screenshot-only (e.g. 2026-08-25 EUR) | no snapshot — narrate the image | **no** |

A new screenshot is a **new book**. Do not merge it into `exports/friend_book/`
or the owner snapshot.

## Isolated chain (only if they ask for a full report)

```bash
export GESH_SNAPSHOT_PATH=/abs/path/friend/snapshot.json
export GESH_ENGINE_RESULTS=/abs/path/friend/engine_results.json
export GESH_REPORT_OUTPUT=/abs/path/friend/gesh_report.html
export GESH_SKIP_ANALYSIS_DB=1
# then the usual engines → AI → gen_report2, never run_daily_analysis.sh
```

`GESH_SKIP_ANALYSIS_DB=1` is load-bearing: `run_ai2.py` otherwise writes
`analysis_runs` / `equity_snapshots` into the owner's dashboard DB.

## Screenshot-only (default)

Do not fetch owner quotes. Do not invent missing weights. Cover:

1. Overlapping “world” ETFs = same beta twice, not extra diversification.
2. Money-market / overnight cash ETFs (XEON-class) are **cash**, not equity.
3. Bond ETFs (EUNA-class) are bonds.
4. A country single-stock cluster is a **satellite bet**, not “just Europe.”
5. Look through the world ETF before counting extra semis / mega-caps.
6. An opaque Other/unclassified bucket is **unknown**, not diversified.
7. Microcaps ≠ core ETFs.

Never print the friend's dollars in public copy. Never quote owner holdings
to “compare.”

## Excuses

| Excuse | Reality |
|---|---|
| "I'll just swap a few holdings in the owner snapshot" | That writes the friend's book into tomorrow's chain. |
| "Same friend as August 23" | Prove it. Screenshot-only EUR ≠ `exports/friend_book`. |
| "Quick compare to my RH weights" | Isolation violation. Analyze their weights only. |
| "Run the 10:30 chain on this screenshot" | Chain overwrites owner files. Isolated env or stop. |
