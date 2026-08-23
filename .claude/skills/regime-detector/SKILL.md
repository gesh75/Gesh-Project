---
name: regime-detector
description: ADX/Bollinger/RSI regime detection and technical context for symbols.
model: grok-4.6
---

# Skill: Market Regime Detector

## Trigger phrases
"market regime", "what regime is", "trending or ranging", "detect regime",
"ADX", "RSI overbought", "Bollinger bands", "breakout setup", "is X overbought",
"momentum check", "technical analysis", "regime context"

## What this skill does
Computes three technical indicators per symbol from 200 days of yfinance OHLCV data
and classifies each into a market regime. The regime_summary string is designed
for injection into Claude prompts to improve analysis accuracy.

## Entry point
```
GET /regime         → detect_regimes(symbols)
```
Direct usage:
```python
from regime import detect_regimes
result = detect_regimes(["NVDA", "SPY", "QQQ"])
# result["regime_summary"] → inject into AI prompts
```

## Indicators computed (regime.py)
| Indicator | Period | Purpose |
|-----------|--------|---------|
| ADX | 14 | Trend strength (0-100; >25 = strong trend) |
| Bollinger Band Width | 20 | Volatility compression detection |
| RSI | 14 | Momentum overbought/oversold |
| %B (Bollinger %B) | 20 | Price position within bands |

## Regime classification logic
```
ADX >= 30 + RSI >= 70  → MOMENTUM-HIGH  🔥 (overbought trend)
ADX >= 30 + RSI <= 30  → MOMENTUM-LOW   🥶 (oversold trend)
ADX >= 30              → TRENDING       📈 (strong directional)
BB compressed + ADX>18 → BREAKOUT-READY ⚡ (coiling for move)
else                   → RANGING        ↔️ (sideways/mean-revert)
```
Compression = BB_width < 75% of 20-day average AND BB_width < 4.0%

## Output structure
```python
{
  "regimes": [
    {
      "symbol": "NVDA",
      "regime": "TRENDING",
      "adx": 38.2,
      "bb_width": 6.1,
      "rsi": 62.0,
      "pct_b": 71.0,
      "icon": "📈",
      "description": "Strong directional trend. Momentum strategies favoured.",
      "prompt_context": "NVDA: TRENDING (ADX=38, BB-Width=6.1%, RSI=62, %B=71%) — ..."
    }
  ],
  "regime_summary": "## Market Regime Context\n  • NVDA: TRENDING ..."
}
```

## Strategy implications per regime
| Regime | Strategy | Position sizing | Stop placement |
|--------|----------|----------------|----------------|
| TRENDING | Ride momentum, let winners run | Normal to full | Trailing stop 2 ATR |
| RANGING | Mean-revert, fade extremes | Reduced | Tight, at bands |
| BREAKOUT-READY | Wait for volume confirm, then enter | Build on breakout | Below compression low |
| MOMENTUM-HIGH | Reduce / take partial profits | Trim | Tight trailing |
| MOMENTUM-LOW | Accumulate if fundamentals support | Scale in | Below recent low |

## Config (regime.py)
```python
LOOKBACK_DAYS = 200   # calendar days of history
ADX_PERIOD    = 14
BB_PERIOD     = 20
RSI_PERIOD    = 14
```

## Key instructions for Claude
1. ALWAYS inject regime_summary into deep_analyze_portfolio calls
2. Regime changes (e.g., TRENDING → BREAKOUT-READY) are more significant than the regime itself
3. When ADX is falling from high levels → trend exhaustion, consider trimming
4. %B > 90 = price touching upper band = potential reversal in RANGING stocks
5. %B < 10 = price touching lower band = potential bounce in RANGING stocks
6. BREAKOUT-READY stocks: volume must confirm — price alone is not enough
7. Present regimes as a compact table: Symbol | Regime | ADX | RSI | %B | Strategy
