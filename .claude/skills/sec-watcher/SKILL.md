---
name: sec-watcher
description: SEC EDGAR filing watch — 10-K, 10-Q, 8-K alerts and filing analysis.
model: grok-4.6
---

# Skill: SEC Filing Watcher Agent

## Trigger phrases
"SEC filings", "watch EDGAR", "any new 10-K", "10-Q filings", "8-K alerts",
"SEC news", "filing analysis", "earnings filings", "management guidance",
"what did SEC say about", "red flags in filings", "EDGAR watch"

## What this skill does
Monitors SEC EDGAR for new 10-K, 10-Q, and 8-K filings for portfolio holdings
and watchlist. When a filing is found, Claude extracts financial intelligence:
revenue trend, cash burn, debt changes, management guidance tone, risk factor
changes, red flags, and positive signals. No API key required — EDGAR is public.

## Entry points
```
GET /sec            → watch_filings(symbols) — check EDGAR + analyze new filings
GET /sec/history    → get_filing_history(symbols) — cached DB results
```
Direct usage:
```python
from sec_agent import watch_filings, get_filing_history
result = watch_filings(["NVDA", "AAPL"])
```

## EDGAR sources (sec_agent.py)
- `EDGAR_RSS`: company filing feed by CIK number
- `EDGAR_SEARCH`: full-text search by ticker + date range
- Filing types monitored: 10-K (annual), 10-Q (quarterly), 8-K (material events)
- CIK resolution: `_get_cik(symbol)` via EDGAR company search
- User-Agent header required: `GeshFinancial/1.0 georgy75@gmail.com`

## Filing analysis output schema (SEC_ANALYSIS_PROMPT)
- `filing_type`: 10-K / 10-Q / 8-K
- `company`: company name
- `period`: reporting period
- `sentiment`: POSITIVE / NEUTRAL / NEGATIVE / MIXED
- `revenue_trend`: ACCELERATING / STABLE / DECELERATING / DECLINING / N/A
- `key_findings[]`: specific facts with numbers
- `red_flags[]`: genuine concerns only (not noise)
- `positive_signals[]`: specific strengths or catalysts
- `guidance_tone`: RAISED / MAINTAINED / LOWERED / WITHDRAWN / N/A
- `guidance_detail`: one sentence on forward guidance
- `action_signal`: BULLISH / NEUTRAL / BEARISH
- `summary`: 3-4 sentence plain English investor summary
- `urgency`: HIGH / MEDIUM / LOW

## Urgency guide
| Level | Trigger |
|-------|---------|
| HIGH | Earnings miss/beat >10%, major guidance change, unexpected executive departure, regulatory action |
| MEDIUM | Guidance maintained, routine quarterly, margin compression |
| LOW | Routine filing, no material changes |

## DB persistence (gesh.db → sec_filings table)
Each analyzed filing stored with: symbol, filing_type, form_url, analysis_json, created_at

## AI model
- `claude-sonnet-4-6` for filing analysis (complex document understanding)
- max_tokens: 2000 per filing

## Key instructions for Claude
1. RED FLAGS only if genuinely alarming — avoid false positives that erode trust
2. Always reference specific numbers from filings (revenue %, EPS, debt/equity ratios)
3. Compare guidance vs prior quarter when available ("raised from X to Y")
4. 8-K urgency: executive departure = HIGH, routine disclosure = LOW
5. Present findings as: Filing | Date | Verdict | Key Finding | Action
6. HIGH urgency filings should be surfaced immediately with a 1-line alert
7. Cross-reference 10-Q findings with current stock momentum from regime.py
8. Management guidance language analysis: words like "cautious", "uncertain", "headwinds" = BEARISH signal
