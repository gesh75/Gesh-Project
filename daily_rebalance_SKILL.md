# Daily Portfolio Rebalance Skill

Executes Georgi's daily portfolio rebalance workflow: screenshot analysis → signal engines → trim/add calculation → trade execution confirmation → alert setup.

## Trigger phrases
"rebalance my portfolio", "what should I do today", "check my holdings", "should I trim anything", "daily rebalance", "what to buy or sell"

## Step 1 — Get Current Portfolio State

**Preferred: Robinhood screenshot**
If Georgi provides a screenshot, extract from it:
- Each symbol, share count, current price, unrealized P/L
- Total portfolio value

**Fallback: DB snapshot**
If no screenshot: read latest from `gesh_financial.db` holdings_snapshot table.

### Deployable cash — required before any sizing

Every sizing decision in this skill runs off **validated deployable cash**, defined as:

`deployable_cash = equity_summary["deployable"]`

This field is **settled and leverage-capped** (see `CLAUDE.md`). A screenshot does not
carry it — if portfolio state came from a screenshot, still obtain
`equity_summary["deployable"]` from the snapshot before sizing anything. Do not read a
"buying power" figure off the screenshot and treat it as deployable cash.

**Validate it.** The value is usable only if it is present, numeric, finite, and `>= 0`.

**Failure rule — stop, do not improvise.** If any of the following holds:
- `equity_summary` is unavailable, or
- the `deployable` field is missing, or
- its value is null, non-numeric, non-finite, or negative

then **stop the workflow immediately**. Produce no action plan, no trims, no adds, no
new-position screen. Return a typed-style failure instead:

```
DeployableCashUnavailable: cannot size trades — equity_summary["deployable"] is
<missing | null | non-numeric | non-finite | negative>. No rebalance plan produced.
```

Do **not** invent a value and do **not** substitute `$500` (that is the cash *buffer*
policy, never a stand-in for the cash *balance*). Do **not** fall back to
`profile["withdrawable_amount"]` (reads $0 while a deposit clears, `None` on Gold
accounts), `account["cash"]` (negative margin-accounting figure), or
`account["buying_power"]` (Gold **leveraged** margin). None of these is a deployability
proxy — a wrong number here mis-sizes every trade downstream.

## Step 2 — Run Signal Engines

For each holding, compute (or fetch from DB if fresh <2h):
1. RSI-14 (via yfinance 90d daily data)
2. Kelly optimal weight: f* = (μ−rf)/σ², cap 25%
3. Current weight = position_equity / total_portfolio
4. Consensus node verdict if available

## Step 3 — Generate Action Plan

Apply signal reconciliation rules in priority order:
1. **RSI > 75 → TRIM (P1)** regardless of Kelly — confirmed rule from 2026-05-08
2. **RSI > 80 → strong TRIM** — critical overbought, P1 urgent
3. **Kelly TRIM + AI undervalued** → trim to optimal size, keep holding
4. **Consensus BUY + AI overvalued** → hold but watch RSI >72
5. **Analyst STRONG BUY + Kelly AVOID** → Kelly wins for sizing

**Share count formulas:**
- TRIM: `ceil((current_equity − kelly_pct/100 × total) / price)`
- ADD: `floor((kelly_pct/100 × total − current_equity) / price)`
- Always maintain the **$500 minimum cash buffer**, measured against deployable cash:
  `projected_deployable = deployable_cash + Σ(trim proceeds) − Σ(add costs)` must stay `>= $500`

## Step 4 — Screen for New Positions

Only if `projected_deployable` (validated deployable cash after **all** planned trades,
trims and adds) exceeds the $500 minimum cash buffer:
- Run sector_filter → consolidation_zone scan on candidate universe
- Prioritize: RSI < 60, positive fundamentals, sector not overcrowded
- Prefer symbols NOT already in portfolio (diversification)

## Step 5 — Present Action Table

| Priority | Symbol | Action | Shares | $ Impact | RSI | Kelly% | Current% | Reason |
|----------|--------|--------|--------|----------|-----|--------|----------|--------|

P1 = execute today | P2 = on dip | P3 = watchlist

## Step 6 — Confirm Execution & Alerts

After Georgi confirms trades:
- Note price alerts requested (e.g., MU $740)
- State the **final validated deployable cash** (`projected_deployable`) and whether the
  $500 minimum cash buffer is satisfied
- Log trades via trade_log.py if accessible

## Output Standards
- Always cite specific RSI values and Kelly% in recommendations
- Never recommend >2% portfolio per trade (MAX_POSITION_PCT)
- Flag VaR 95% > 15% as warning
- Report cash as validated deployable cash — never a generic "buying power" figure
- End: "Deployable cash after trades: $X — [SAFE / LOW DRY POWDER]"
  (SAFE = `projected_deployable >= $500`, otherwise LOW DRY POWDER)
- If deployable cash could not be validated, emit the `DeployableCashUnavailable` failure
  from Step 1 instead of an action table — never a plan built on an assumed cash figure
