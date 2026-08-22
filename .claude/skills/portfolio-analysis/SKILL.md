---
name: portfolio-analysis
description: Deep AI portfolio analysis — snowflake scores, fair value, verdicts on holdings.
model: grok-4.6
---

# Skill: Portfolio Analysis (Deep AI)

## Trigger phrases
"analyze my portfolio", "deep analysis", "portfolio breakdown", "what does my portfolio look like",
"snowflake scores", "fair value", "portfolio score", "verdicts on my holdings"

## What this skill does
Runs the full deep AI portfolio analysis pipeline against live Robinhood data.
Produces per-holding scores (valuation / growth / health / momentum / dividend),
fair-value estimates, 3-month and 12-month price predictions, sector breakdown,
and portfolio-level risk/recommendation report — using `deep_analyze_portfolio()`
from `ai_analyzer.py`.

## Entry point
```
GET /analyze/deep   → server.py → deep_analyze_portfolio(payload, regime_context)
```
Or call directly:
```python
from ai_analyzer import deep_analyze_portfolio
from data_fetcher import build_data_payload
from regime import detect_regimes

payload = build_data_payload()
regime = detect_regimes([h["symbol"] for h in payload["holdings"]])
result = deep_analyze_portfolio(payload, regime["regime_summary"])
```

## Output schema (DEEP_ANALYSIS_SCHEMA in ai_analyzer.py)
- `portfolio_score` (0-100) — overall portfolio quality
- `portfolio_verdict` — one-line summary
- `portfolio_type` — e.g. "Growth-Heavy Tech"
- `risk_score` (0-100) — portfolio risk level
- `market_context` — macro narrative
- `diversification_score` (0-100)
- `holdings_analysis[]` — per symbol:
  - `scores`: valuation, growth, health, momentum, dividend (0-100 each)
  - `fair_value`: estimate, current, status, pct_diff
  - `risks[]`: text + severity (LOW/MEDIUM/HIGH/CRITICAL)
  - `rewards[]`: text
  - `prediction`: 3m, 12m, rationale
  - `verdict`: BUY / HOLD / REDUCE / AVOID
  - `analyst_note`
- `portfolio_risks[]`
- `portfolio_recommendations[]`: action, priority, rationale
- `sector_breakdown[]`: sector, allocation_pct, assessment

## Scoring guide (enforce realism — don't cluster 50-60)
| Score | Valuation | Growth | Health | Momentum | Dividend |
|-------|-----------|--------|--------|----------|----------|
| 0-20  | Massively overvalued | Revenues declining | <3mo runway | Down >50% | No dividend |
| 50    | Fair value | 5-10% growth | Average balance sheet | Sideways | 2-3% yield |
| 80-100| Deeply undervalued | >50% revenue growth | Net-cash fortress | Strong uptrend | >5% safe yield |

## AI model
- Use `claude-sonnet-4-6` (MODEL env var, default sonnet)
- max_tokens: 8000
- Tool-forced JSON via `return_portfolio_analysis` tool schema

## Key instructions for Claude
1. Always inject regime context before calling deep_analyze_portfolio
2. Reference actual dollar figures and percentages from the payload
3. Fair value estimates must be grounded in DCF or P/E logic — not guesses
4. Predictions (3m, 12m) must include a specific rationale tied to fundamentals
5. Portfolio recommendations must be prioritized: P1 (urgent), P2 (this week), P3 (monitor)
6. Flag CRITICAL risks immediately at the top of the response
7. Never cluster scores — spread them meaningfully across the 0-100 range
