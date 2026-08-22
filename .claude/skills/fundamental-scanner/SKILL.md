---
name: fundamental-scanner
description: Fundamental momentum scanner — earnings acceleration, PEG, insider buying, undervalued stocks.
model: grok-4.6
---

# Skill: Fundamental Momentum Scanner

## Trigger phrases
"fundamental scan", "improving fundamentals", "before the market knows",
"undervalued stocks", "earnings acceleration", "fundamental opportunities",
"insider buying", "fundamental analysis", "revenue acceleration", "margin expansion",
"PEG ratio", "analyst revisions", "run fundamental scan"

## What this skill does
Detects stocks where fundamentals are IMPROVING but price has NOT yet moved —
the "before the market knows" edge. Scans 90+ stocks from a curated universe,
pre-scores with a numeric heuristic, sends the top 35 to Claude for deep
fundamental analysis, and returns the top 15 asymmetric opportunities.

## Entry points
```
GET /fundamental        → run_fundamental_scan() — fresh scan (slow, ~5min)
GET /fundamental/latest → get_latest_fundamental_scan() — cached DB result
```
Direct usage:
```python
from fundamental_agent import run_fundamental_scan, get_latest_fundamental_scan
result = run_fundamental_scan()  # scans full FUNDAMENTAL_UNIVERSE
cached = get_latest_fundamental_scan()  # from gesh.db
```

## Universe (fundamental_agent.py — FUNDAMENTAL_UNIVERSE)
90 stocks across: High-growth tech, Finance/value, Healthcare/biotech,
Energy, Consumer, Industrials/infra, Emerging/speculative, Small/mid value

## Data fetched per stock (_fetch_fundamentals_batch)
| Field | Source | Key metric |
|-------|--------|------------|
| revenue_growth | yfinance revenueGrowth | Quarterly YoY % |
| earnings_growth | yfinance earningsGrowth | |
| gross_margin | yfinance grossMargins | Profitability |
| op_margin | yfinance operatingMargins | Efficiency |
| forward_pe | yfinance forwardPE | Valuation |
| trailing_pe | yfinance trailingPE | |
| pb_ratio | yfinance priceToBook | |
| debt_equity | yfinance debtToEquity | Balance sheet |
| current_ratio | yfinance currentRatio | Liquidity |
| free_cashflow_b | yfinance freeCashflow | Cash generation |
| insider_signal | ticker.insider_transactions | BUYING/NEUTRAL/SELLING |
| analyst_upside | targetMeanPrice vs price | Wall St target gap |
| ret_1m, ret_3m | yfinance price history | Price momentum lag |

## Pre-score heuristic (_pre_score)
```python
score += min(revenue_growth, 60) × 0.4   # revenue quality
score += min(earnings_growth, 40) × 0.3  # earnings power
score += min(analyst_upside, 30) × 0.5   # analyst gap
score += 15  if forward_pe in (0, 30)    # reasonable valuation
score += 20  if insider_signal == "BUYING"
score -= 10  if insider_signal == "SELLING"
score -= 10  if ret_1m > 30              # price already moved
```
Top 35 by pre-score sent to Claude.

## AI output schema (FUNDAMENTAL_PROMPT)
- `scan_date`, `market_summary`, `stocks_analyzed`
- `top_opportunities[]` (15 stocks):
  - symbol, company_name, sector
  - `fundamental_score` (0-100)
  - `momentum_gap`: HIGH / MEDIUM / LOW (price lag vs fundamentals)
  - `revenue_trend`: ACCELERATING / STABLE / DECELERATING
  - `margin_trend`: EXPANDING / STABLE / CONTRACTING
  - `balance_sheet`: STRENGTHENING / STABLE / WEAKENING
  - `valuation_vs_growth`: CHEAP / FAIR / EXPENSIVE
  - `insider_activity`: BUYING / NEUTRAL / SELLING
  - `key_catalyst`: specific upcoming catalyst
  - `investment_horizon`: 1-3 months / 3-6 months / 6-12 months
  - `thesis`: 2-3 sentence "before the market knows" explanation
  - `risk`: main threat to thesis
  - pe_ratio, revenue_growth, earnings_growth, analyst_upside
- `sector_rotation_signals[]`: sector + INFLOW/OUTFLOW/NEUTRAL + reason
- `macro_headwinds[]`, `macro_tailwinds[]`

## AI model
- `claude-sonnet-4-6`, max_tokens: 6000
- Raw JSON response (not tool-forced) — regex-cleaned

## Key instructions for Claude
1. Focus on "ACCELERATING" revenue — not just positive growth, but getting better each quarter
2. Insider BUYING is the strongest signal — insiders know the business best
3. momentum_gap: HIGH = price significantly lags fundamentals (best entry)
4. investment_horizon must match catalyst timing — don't say 1-3 months for a 12-month story
5. Reference specific numbers: "Revenue grew 23% YoY in Q3, up from 14% in Q2" beats vague claims
6. PEG < 1.5 = growth at reasonable price — flag these prominently
7. Sector rotation signals help users shift portfolio weight toward strengthening sectors
8. Present top 5 as an "Action List" with thesis, catalyst date, and entry price context
9. Cross-reference fundamental scan results with current news sentiment — confirm the story
