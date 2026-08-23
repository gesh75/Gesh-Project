---
name: risk-manager
description: Portfolio risk — VaR, Monte Carlo stress tests, drawdown and exposure analysis.
model: grok-4.6
---

# Skill: AI Risk Manager

## Trigger phrases
"risk analysis", "how risky is my portfolio", "VaR", "value at risk", "stress test",
"monte carlo", "worst case scenario", "drawdown risk", "portfolio risk score",
"quick risk check", "am I overexposed"

## What this skill does
Combines three risk engines into a unified risk report:

1. **Quick Risk Analysis** — `analyze_portfolio()` in ai_analyzer.py
   - Plain-text output, 6 sections: overview, sector risk, market context, key risks, insights, risk score
   - Uses claude-sonnet-4-6, max_tokens 2048

2. **Monte Carlo Stress Test** — `run_monte_carlo()` in monte_carlo.py
   - 1000 simulations over 21 trading days (1 month horizon)
   - Uses true correlated covariance matrix (not naive weighted avg)
   - Outputs: VaR 95%, CVaR 95%, P5/P25/P50/P75/P95 outcomes, probability of profit/loss

3. **Market Regime Context** — `detect_regimes()` in regime.py
   - Per-symbol: ADX (trend strength), Bollinger Band width (volatility), RSI (momentum)
   - Regime labels: TRENDING / RANGING / BREAKOUT-READY / MOMENTUM-HIGH / MOMENTUM-LOW

## Entry points
```
GET /analyze        → quick risk (plain text)
GET /analyze/deep   → full deep analysis with regime injection
GET /monte-carlo    → stress test simulation
GET /regime         → regime detection only
```

## Monte Carlo output fields
| Field | Meaning |
|-------|---------|
| current_equity | Starting portfolio value |
| p5 / p95 | 5th / 95th percentile final values |
| var_95 | 95th-percentile dollar loss |
| cvar_95 | Average loss in worst 5% scenarios |
| var_95_pct | VaR as % of portfolio |
| prob_profit_pct | % of simulations ending in profit |
| prob_loss_10pct | % of sims losing >10% |
| prob_loss_20pct | % of sims losing >20% |
| buckets[] | Histogram for charting |
| per_symbol[] | Per-holding daily vol % and weight % |

## Risk scoring guide (SYSTEM_PROMPT in ai_analyzer.py)
1. PORTFOLIO OVERVIEW — equity, cash, buying power, position count
2. SECTOR & CONCENTRATION RISK — overexposure flags, top 3 by weight, diversification score
3. MARKET CONTEXT — SPY/QQQ signals, beta divergence
4. KEY RISK FACTORS — top 3-5 risks with severity LOW/MEDIUM/HIGH/CRITICAL
5. ACTIONABLE INSIGHTS — 3-5 specific prioritized recommendations
6. RISK SCORE — 1-10 with one-sentence justification

## Regime interpretation guide
| Regime | Strategy implication |
|--------|---------------------|
| TRENDING | Momentum strategies, let winners run |
| RANGING | Mean-reversion, tighten stops |
| BREAKOUT-READY | Watch for volume confirmation; coiling for a move |
| MOMENTUM-HIGH | RSI >70 — risk of pullback; consider trimming |
| MOMENTUM-LOW | RSI <30 — potential reversal; look for support |

## Key instructions for Claude
1. Always run Monte Carlo before declaring "safe" — gut feel is not VaR
2. CRITICAL risks must be stated in dollar terms (e.g., "$X,XXX at risk")
3. Regime context must be injected into deep analysis prompts
4. When VaR >15% of portfolio, flag immediately as RED
5. Present Monte Carlo results as: "In the worst 5% of scenarios, you could lose $X"
6. Sector concentration above 40% in one sector = CONCENTRATION WARNING
7. If portfolio beta >1.5, warn about amplified drawdowns in market corrections
