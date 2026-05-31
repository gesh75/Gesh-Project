# Daily Portfolio Rebalance Skill

Executes Georgi's daily portfolio rebalance workflow: screenshot analysis → signal engines → trim/add calculation → trade execution confirmation → alert setup.

## Trigger phrases
"rebalance my portfolio", "what should I do today", "check my holdings", "should I trim anything", "daily rebalance", "what to buy or sell"

## Step 1 — Get Current Portfolio State

**Preferred: Robinhood screenshot**
If Georgi provides a screenshot, extract from it:
- Each symbol, share count, current price, unrealized P/L
- Total portfolio value and buying power

**Fallback: DB snapshot**
If no screenshot: read latest from `gesh_financial.db` holdings_snapshot table.
**Never** use `account["cash"]` — always `profile["withdrawable_amount"]`.

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
- Always maintain $500 minimum cash buffer after trades

## Step 4 — Screen for New Positions

If buying power > $500 after planned trims:
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
- State final cash remaining and whether buffer is safe
- Log trades via trade_log.py if accessible

## Output Standards
- Always cite specific RSI values and Kelly% in recommendations
- Never recommend >2% portfolio per trade (MAX_POSITION_PCT)
- Flag VaR 95% > 15% as warning
- End: "Cash after trades: $X — [SAFE / LOW DRY POWDER]"
