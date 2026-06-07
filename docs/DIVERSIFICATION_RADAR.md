# 💎 Diversification Radar — New-Name Opportunity Scanner

> Advisory-only daily screen that recommends **new, non-held** names to diversify
> into when there is extra cash — closing the long-standing gap where the daily
> report only ever suggested adding to **existing** holdings. Built June 2026 via a
> research → design → adversarial-review workflow. **PAPER_MODE / autonomy gate
> unaffected; nothing is ever auto-traded.**

## Why

The 10:30 daily analysis scores and recommends actions on the **current portfolio
only**. When the account has spare cash, the tool kept proposing *more of what you
already own* — useless for diversification. The radar adds a parallel, advisory lane:
scan the market, exclude what's held, and surface the best *new* names (or, when no
slots/cash are free, the swap that would most improve diversification).

## What it does (pipeline)

```
screen_market(95-name universe)         ← single cached yfinance call
  → exclude current holdings + data-sanity floor
  → 5-factor composite score (per-sub-metric z, then weighted sum)
  → rank → top-K survivors (bounds the expensive stage)
  → 1 batched 1y frame → EWMA vol + correlation-to-book
  → 1 batched detect_regimes → multi-timeframe entry gate (+ hard vetoes)
  → correlation filter (drop names ≥0.70 corr to the book)
  → sector-cap + two-pass greedy diversification + cash-aware sizing
  → consensus on the ≤4 FINAL picks only
  → {mode, candidates[], swap_suggestions[], existing_sector_weights, diagnostics}
```

### The 5-factor model (`invest_radar.WEIGHTS`)

| Factor | Weight | Source (reused) | Notes |
|---|---|---|---|
| Quality | 0.30 | `screen_market` revenue/earnings growth | Highest IR, defensive; **growth only** (pb excluded to avoid double-count) |
| Momentum | 0.20 | `ret_1w/ret_1m` ÷ EWMA vol | Risk-adjusted (vol-scaled) to cut momentum-crash tails |
| Value | 0.20 | `pe_ratio`, `pb_ratio` (sign-flipped) | Sector-relative when a bucket has ≥4 names |
| Analyst revisions | 0.15 | `analyst_upside`, `num_analysts`, `reco_key` | Down-weighted when <3 analysts; capped so the trend cluster (mom+rev) ≤0.35 |
| Low volatility | 0.15 | `volatility_models.ewma_vol` | Negated annualized vol; lowers radar drawdown |

Each **sub-metric is z-scored before averaging** (so growth ~0.15 isn't washed out by
pb ~5–30), winsorized at ±3 SD, and **weights renormalize** when a factor lacks ≥3
valid points so a dark factor never silently dilutes the composite.

### Multi-timeframe entry gate (day/week/month)

A confluence fraction over six independent dimensions (direction, ADX trend strength,
%B pullback location, RSI-cooled, FFT cycle trough/rising, sector hot) acts as a 0.5–1.0
**multiplier**, not an additive factor. **Hard vetoes** drop a name on: month-trend
down (`ret_1m<0`), overbought-and-extended (`RSI>70 & %B>0.9`), or a bearish regime.
Missing data never manufactures a confluence point.

### Cash-aware sizing & diversification

- Reads `allocation_config`: `min_cash_buffer ($500)`, `max_open_positions (5)`,
  `max_stock_per_trade ($5000)`. `deployable = max(0, cash − buffer)`.
- Each pick sized at the **minimum binding constraint**: half-Kelly, inverse-vol
  risk-parity slice, 7% hard cap, `max_stock_per_trade`, and remaining budget. Sub-1-share
  suggestions are dropped.
- **Sector caps**: blocks *adding* to a sector that would exceed 30% post-trade — but
  never blocks buys into under-weight / new sectors, even when the book's top-3 already
  exceeds 70% (blocking those would defeat diversification, since deploying cash can only
  raise the top-3 share). Two-pass greedy selection prefers *new* sectors first.
- **Modes**: `BUY` (sized picks) · `SWAP_ONLY` (no room/cash — surfaces the same ranked
  diversifiers **unsized** as "fund via swap" previews **plus** a trim-to-fund swap idea)
  · `NO_ACTION` (no survivors).

#### Config gating — why you see SWAP_ONLY vs BUY (operational)

Two independent gates must BOTH open for actionable (sized) buys; otherwise the radar
shows unsized previews so you still see *where* new cash should go:

| Gate | Open when | Config key |
|---|---|---|
| **Open slots** | `max_open_positions − len(holdings) > 0` | `max_open_positions` (default 5) |
| **Deployable cash** | `cash − min_cash_buffer > 0` | `min_cash_buffer` (default $500) |

Consequences to remember:
- Raising `max_open_positions` **alone** does *not* flip to BUY if `cash ≤ min_cash_buffer`
  — you'll get open slots but `deployable = $0`, so candidates stay **previews**.
- A small deployable balance sizes each pick to its inverse-vol fair share; names whose
  share rounds to **< 1 whole share are dropped** — so a few hundred dollars typically
  yields **one** affordable name, not the full sector spread.
- These are **paper/advisory** config values in `gesh_config.json` (read by the product's
  `validate_action` + the radar). The `-trading` autopilot uses its **own** env `RiskConfig`
  and is unaffected; with `auto_trade=false` nothing executes regardless. Lowering
  `min_cash_buffer` removes a safety floor — restore it after any deploy-preview experiment.

> **Live account at build time** (8 holdings > 5 slots, $448 < $500 buffer) correctly
> resolves to **SWAP_ONLY** and surfaces the book's real concentration: **Technology 71%**.

## Integration

| Seam | File | Change |
|---|---|---|
| Compute + persist | `run_ai2.py` | Calls `scan_candidates`, writes `engine_results.json['diversification_candidates']`, injects a compact summary into the deep-analysis prompt. Fail-soft — a radar error never breaks the 10:30 pipeline. |
| AI awareness | `ai_analyzer.py` | `portfolio_recommendations` items gain optional `category`/`symbol` (backward-compatible) so Claude may surface advisory `DIVERSIFICATION` ideas. |
| Daily report | `gen_report2.py` | New "💎 Diversification Radar" section (every cell `html.escape`d). |
| Dashboard panel | `invest_view.py`, `server.py` | `GET /invest` + `GET /invest/data` (internal, login-wall gated like `/watch`), 12s poll, read-only. |
| Nav | `dashboard.html`, `watch_view.py`, `progress_view.py` | 💎 Invest cross-links. |

## Safety invariants (enforced + tested)

- **Advisory-only**: never builds a `TradeIntent`; never imports `trading.execution` /
  `risk_gate` / `autopilot` (asserted via AST import-graph test); never auto-trades;
  never touches the autonomy gate.
- **Read-only**: yfinance + the screener; writes nothing to `gesh_financial.db`.
- **No PII**: only `cash`/`total` are read from `equity_summary`; `withdrawable_amount`,
  `average_buy_price`, quantities never appear in output, prompt, report, or `/invest`.
- **Fail-soft**: any data failure → honest empty contract; never raises into the pipeline.
- **Bounded network**: ~4 batched calls + consensus on ≤4 picks (live run ≈ 20 s) — avoids
  the 200–400-call yfinance rate-limit cascade.
- **XSS-safe**: report cells `html.escape`d; `/invest` payload neutralises `</` and
  `esc()`s every field before `innerHTML`.

## Adversarial review — bugs caught in design before any code

The design workflow's 3-lens review (the `/ultrareview` substitute) surfaced and this
build fixed, before implementation: a **dead regime veto**, an **`ewma_vol(ndarray)`
type bug** that would have silently nulled 2 of 5 factors + risk-parity sizing,
**scale-mixing / pb double-count** in Quality/Value, a confluence **precedence bug**, a
**200–400-call rate-limit cascade**, **stored-XSS** in the report, and **PII passthrough**.

## Tests

`test_invest_radar.py` (24) + `test_invest_view.py` (6): winsorized-z math, factor
monotonicity + coverage renorm, timing vetoes, sizing constraints, SWAP_ONLY/BUY/NO_ACTION
modes, holdings exclusion, no-PII, immutability, fail-soft, import-graph safety, and
render escaping. Run: `./venv/bin/python3 -m pytest test_invest_radar.py test_invest_view.py -q`.

_Internal launch-tracking record — paper / advisory only; not financial advice._
