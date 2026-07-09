---
name: kelly-sizer
description: Kelly criterion position sizing, trim/add, rebalance recommendations.
model: grok-build
---

# Skill: Kelly Criterion Position Sizer

## Trigger phrases
"position sizing", "how much should I buy", "kelly criterion", "optimal allocation",
"trim or add", "overweight", "underweight", "rebalance", "kelly analysis",
"position size for", "should I add more", "portfolio rebalance"

## What this skill does
Calculates mathematically optimal position sizes using the Kelly Criterion for
each holding. Blends continuous (log-normal) and discrete (win-rate) Kelly
formulas, applies Half-Kelly for safety, incorporates analyst upside and AI
valuation penalties, and recommends ADD / HOLD / TRIM / AVOID actions.

## Entry point
```
GET /kelly          → run_kelly_analysis(holdings, total_portfolio)
```
Direct usage:
```python
from kelly import run_kelly_analysis
result = run_kelly_analysis(holdings, total_portfolio,
                            analyst_targets=targets_dict,
                            ai_verdicts=verdicts_dict)
```

## Kelly formulas (kelly.py)
### Continuous Kelly (60% weight)
```
f* = (μ - r) / σ²
μ  = mean daily return
σ  = std daily return
r  = risk-free daily rate (≈0)
```
### Discrete Kelly (40% weight)
```
f* = W - (1-W)/R
W  = win rate (% days positive)
R  = avg_win / avg_loss (payoff ratio)
```
### Final blended size
```
raw_blend  = 0.6 * continuous + 0.4 * discrete
+ analyst_upside boost (max +20%)
× ai_penalty (0.5 for AVOID/SELL, 0.6 for overvalued >15%, 0.8 for >5%)
half_kelly = raw_blend × 0.5
final_pct  = clamp(half_kelly, 0, 25%)
```

## Action signals
| Signal | Condition |
|--------|-----------|
| AVOID | final_pct == 0 |
| TRIM | current_pct > final_pct × 1.3 |
| ADD | current_pct < final_pct × 0.6 |
| HOLD | otherwise |

## Output per holding
- `symbol`, `current_pct`, `optimal_pct`, `recommended_usd`
- `delta_usd`: dollar amount to add (+) or trim (-)
- `delta_pct`: allocation change in %
- `action`: ADD / HOLD / TRIM / AVOID
- `win_rate`: % of days with positive return
- `payoff_ratio`: avg win / avg loss
- `sharpe`: annualised Sharpe ratio
- `ann_volatility`: annualised vol %
- `volatility_tier`: LOW / MEDIUM / HIGH / EXTREME
- `max_drawdown`: worst historical drawdown %
- `pnl_pct`: unrealised P&L %
- `raw_kelly`, `half_kelly`: Kelly percentages
- `analyst_upside`, `ai_penalty`

## Portfolio summary
- `total_optimal_allocated_pct`: sum of all optimal allocations
- `cash_recommended_pct`: suggested cash reserve
- `trim_list`: symbols to reduce
- `add_list`: symbols to add
- `avoid_list`: symbols to exit

## Volatility tiers
| Tier | Ann. Vol | Examples |
|------|---------|---------|
| LOW | <30% | SPY, large-cap value |
| MEDIUM | 30-60% | Most growth tech |
| HIGH | 60-100% | Biotech, small-cap |
| EXTREME | >100% | Crypto proxies (MSTR), micro-caps |

## Key instructions for Claude
1. Always present Kelly results as: Symbol | Current % | Optimal % | Delta $ | Action
2. EXTREME volatility tiers get hard-capped at 10% regardless of Kelly output
3. When total_optimal_allocated_pct > 100%, Kelly signals too much conviction — advise diversification
4. cash_recommended_pct < 5% = warning: no dry powder for opportunities
5. Combine Kelly signals with regime: ADD + BREAKOUT-READY = strong conviction entry
6. ADD + MOMENTUM-HIGH = caution, wait for pullback before adding
7. Kelly is a sizing tool, not a buy/sell signal — always cross-check with fundamentals and sentiment
8. Show the math: "Kelly says 18% optimal, Half-Kelly caps at 9%, your current 12% means TRIM"
