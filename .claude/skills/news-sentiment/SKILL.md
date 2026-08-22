---
name: news-sentiment
description: News sentiment scan for holdings and symbols — Yahoo, Finnhub, Polymarket mood.
model: grok-4.6
---

# Skill: News Sentiment AI Agent

## Trigger phrases
"news sentiment", "what's the news on", "scan news", "sentiment for my holdings",
"any news about", "what are people saying about", "news score", "market mood",
"news analysis", "sentiment tracker"

## What this skill does
Scans financial news for portfolio holdings and watchlist symbols using multiple
free sources, then runs Claude AI sentiment scoring on each article batch.
Results are persisted to SQLite (gesh.db → news_sentiment table) for rolling
7-day trend tracking.

## Entry points
```
GET /news           → scan_news(symbols) — fetches + analyzes fresh news
GET /news/history   → get_news_history(symbols) — cached DB results
```
Direct usage:
```python
from news_agent import scan_news, get_news_history
result = scan_news(["NVDA", "AAPL", "MSFT"])
```

## News sources (news_agent.py)
| Source | Method | Key needed? |
|--------|--------|-------------|
| Yahoo Finance RSS | _fetch_yahoo_news() | No |
| Finnhub company news | _fetch_finnhub_news() | Optional (FINNHUB_API_KEY) |

Max 12 articles per symbol (deduplicated by title)

## Sentiment output schema (NEWS_PROMPT)
Per symbol:
- `overall_sentiment`: VERY_POSITIVE / POSITIVE / NEUTRAL / NEGATIVE / VERY_NEGATIVE
- `sentiment_score`: integer -100 to +100
- `impact_level`: HIGH / MEDIUM / LOW
- `article_count`: number analyzed
- `key_themes[]`: top 3 themes
- `positive_signals[]`: specific positive facts from news
- `negative_signals[]`: specific negative facts
- `notable_events[]`: earnings, products, lawsuits, exec changes, partnerships
- `momentum_narrative`: 2-3 sentence company news narrative
- `investor_action`: WATCH_CLOSELY / RESEARCH_MORE / MONITOR / NO_ACTION
- `confidence`: HIGH / MEDIUM / LOW

## Market mood calculation
```
avg_score = mean(all sentiment_scores)
mood = RISK_ON if avg > 20 else RISK_OFF if avg < -20 else NEUTRAL
```

## AI model for sentiment
- `claude-haiku-4-5-20251001` (fast + cheap for batch news processing)
- max_tokens: 1200 per symbol

## Rolling score (7-day)
`_compute_rolling_score(symbol, days=7)` → average score from DB over last 7 days
Compares current score vs rolling to detect sentiment momentum shifts.

## Key instructions for Claude
1. Use Haiku for news sentiment — it's fast and sufficient for article classification
2. HIGH impact = earnings miss/beat, executive departure, regulatory action, M&A
3. A sentiment_score swing of >30 points vs rolling 7-day average = ALERT
4. Present sentiment as a table: Symbol | Score | Trend | Impact | Action
5. Always mention the most impactful specific event (e.g., "NVDA beat EPS by 15%")
6. Market mood RISK_OFF = consider reducing speculative exposure
7. When news_agent has no data (article_count=0), note "no recent news found" — do not fabricate sentiment
8. Cross-reference sentiment with regime: RISK_OFF + MOMENTUM-HIGH = high pullback risk
