# Skill: AI Stock Screener

## Trigger phrases
"screen the market", "find best stocks", "weekly picks", "top stocks this week",
"screen for opportunities", "what should I buy", "screener", "market picks"

## What this skill does
Runs the multi-factor AI stock screener. Fetches 1-week price/fundamental data
for a universe of candidates via `screen_market()` (market_data.py), then sends
the top candidates to Claude via `analyze_screener()` (ai_analyzer.py) which
selects the best 6–20 investment opportunities using a balanced scoring model.

## Entry point
```
POST /screen   → server.py → screen_market() → analyze_screener(candidates)
```
Or directly:
```python
from market_data import screen_market
from ai_analyzer import analyze_screener

candidates = screen_market()   # returns list of stock dicts with price + fundamentals
result = analyze_screener(candidates)
```

## Scoring model (SCREENER_SYSTEM_PROMPT weights)
| Factor | Weight | What it measures |
|--------|--------|-----------------|
| MOMENTUM | 25% | 1W + 1M price action, volume confirmation |
| VALUE | 20% | P/E vs growth (PEG), P/B, analyst upside |
| GROWTH | 25% | Revenue + earnings growth trajectory |
| ANALYST CONSENSUS | 20% | Strong buy/buy ratings, price target upside |
| QUALITY | 10% | Business model durability, balance sheet, sector tailwinds |

## Selection rules Claude must follow
- Exactly 20 picks total (or fewer if universe is small)
- Mix required: 2-3 large-cap stability + 5-7 mid-cap growth + 2-3 value plays + 2-3 contrarian/turnaround
- Never select ETFs (SPY, QQQ, IWM) as final picks
- 1-2 picks may be WATCH (not BUY but monitoring worthy)
- Spread scores meaningfully — not all 75-85
- Order by composite_score descending

## Output schema (SCREENER_SCHEMA in ai_analyzer.py)
- `screen_summary`: market context sentence
- `market_bias`: RISK_ON / NEUTRAL / RISK_OFF
- `top_sectors[]`: best sectors this week
- `picks[]`:
  - symbol, company_name, sector
  - composite_score, momentum_score, value_score, growth_score, analyst_score (0-100)
  - verdict: BUY / WATCH / AVOID
  - key_thesis: why this stock
  - main_risk: biggest threat to the thesis
  - price, ret_1w, ret_1m
  - analyst_target, analyst_upside, num_analysts, recommendation

## AI model
- `claude-sonnet-4-6`, max_tokens: 6000
- Tool-forced JSON via `return_screener_picks`

## Key instructions for Claude
1. Always present picks as a ranked table sorted by composite_score
2. Highlight the top 3 picks with specific entry thesis and risk/reward
3. Flag any picks with HIGH short interest (squeeze potential)
4. Note sector concentration — warn if picks cluster in one sector
5. Include at least one contrarian/beaten-down pick with a recovery thesis
6. When presenting to user: group by verdict (BUY first, then WATCH)
