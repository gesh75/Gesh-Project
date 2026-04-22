# Trade Signal Generator Skill

## Purpose
Generate high-quality trade signals for Georgi's Robinhood portfolio using the
multi-timeframe scanner pipeline: sector pre-filter → consolidation zone detection →
full 6-engine analysis → TradeIntent production.

Trigger phrases: "generate signals", "what are the signals for X", "run consensus on X",
"signal check", "scan for setups", "find consolidation zones", "what's setting up"

---

## Step-by-step process

### 1. Select scanner mode
Read current mode from `GET /api/config` → `scanner_timeframe`.
Default is `daily`. Use the correct period/interval for each mode:
- `daily` → 60d / 1d — key level breakouts
- `1HR` → 5d / 1h — intraday swing setups
- `5MIN` → 1d / 5m — momentum scalps
- `premarket` → 1d / 5m — gap + volume plays

### 2. Get candidate symbols
- Current holdings: from `GET /api/portfolio`
- Watchlist: from `GET /api/portfolio` → `watchlist`
- Or user-specified list

### 3. Sector pre-filter (cheap — always first)
Call `POST /api/scanner/sector` with the candidate list.
**Only proceed with symbols in `report.hot`** (volume ratio ≥ 1.2× 20d avg).
Weak-sector symbols produce more false signals — skip them.

Output format:
```
🔥 HOT sectors: XLK (2.3×), XLF (1.5×)
❄️ COLD sectors: XLE (0.8×) — skipped
```

### 4. Consolidation zone scan (fast — second)
Call `POST /api/scanner/consolidation` with `hot_symbols` from step 3.
Use the interval/period matching the scanner mode.
**Only proceed with symbols in `report.found`** (R/R ≥ 2.0×).

For each found setup, report:
```
Symbol | Zone low–high | Entry | Stop | Target | R/R | Source
AAPL   | $195–$197     | $196  | $194.0 | $201  | 2.5× | prev_zone
```

### 5. Full 6-engine analysis on zone symbols only
For each symbol with a valid CZ setup:

a. **Consensus** — `POST /api/consensus` or `run_consensus_holding(symbol, snapshot)`
   - Requires ≥5 active nodes for valid verdict
   - STRONG BUY (≥0.55) or BUY (≥0.25) = proceed

b. **Regime** — `GET /api/regime`
   - TRENDING or BREAKOUT-READY = favorable
   - RANGING = lower conviction, note in output

c. **Kelly** — `POST /api/kelly`
   - ADD signal = size up
   - TRIM signal = reduce, do not add even with STRONG BUY consensus

d. **FFT Cycles** — `GET /api/fft-cycles/{symbol}`
   - Phase near TROUGH = bullish timing
   - Phase near PEAK = wait for pullback

e. **News sentiment** — check `GET /api/agents/news/history`
   - Score < −30 = caution regardless of technical signal
   - 7-day swing > 30 pts = flag as ALERT

f. **ECG** — from portfolio or `GET /api/ecg`
   - SUSPENDED = no new positions for this symbol

### 6. Signal reconciliation (MANDATORY)
Apply in priority order before producing final action:
1. Kelly TRIM + AI undervalued → weight-only signal, trim to optimal
2. Consensus BUY + AI overvalued → hold/add cautiously, watch RSI >72
3. Analyst STRONG BUY + Kelly AVOID → Kelly wins for sizing
4. Regime RANGING + Consensus BUY → reduce position size 50%

### 7. Build TradeIntent
For each actionable symbol call `POST /api/trading/dry-run` to verify gate:
```json
{
  "symbol": "AAPL",
  "action": "BUY",
  "confidence": 0.82,
  "target_position_pct": 0.02,
  "stop_loss": 194.00,
  "take_profit": 201.00,
  "rationale": "CZ breakout + consensus STRONG BUY + XLK sector hot"
}
```
Report gate verdict inline. If REJECTED, show which gate failed and why.

---

## Output format

### Signal Summary Table
```
Symbol | Sector | CZ R/R | Consensus  | Kelly | Regime      | FFT   | Action | Confidence
AAPL   | XLK🔥  | 2.5×   | STRONG BUY | ADD   | TRENDING    | TROUGH| BUY    | 87%
NVDA   | XLK🔥  | 3.1×   | BUY        | HOLD  | BREAKOUT    | MID   | BUY    | 71%
JPM    | XLF🔥  | —      | HOLD       | TRIM  | RANGING     | PEAK  | HOLD   | —
```

### For each BUY/SELL signal, include:
- Entry: $X.XX (zone midpoint)
- Stop: $X.XX (zone_low − 0.5%)
- Target: $X.XX (prev zone high or 2:1 R/R)
- Risk per share: $X.XX | Position size: X shares at Kelly optimal Z%
- Gate check: APPROVED / REJECTED (reason)
- Signal rationale: 1–2 sentences tying sector + CZ + consensus + regime together

### For HOLD signals:
- Current position size vs Kelly optimal
- Distance to next consolidation zone
- What would change the signal to BUY

---

## Rules specific to this skill

- NEVER recommend a BUY if ECG = SUSPENDED for that symbol
- NEVER skip sector pre-filter — it is cheap and eliminates 40–60% of false setups
- NEVER skip consolidation zone — entries without defined levels have undefined risk
- ALWAYS report R/R ratio — reject setups below 2.0×
- ALWAYS call dry-run gate before presenting a signal as actionable
- Share counts: BUY = floor((kelly_pct/100 × total − current_equity) / price)
- Maintain ≥$500 cash buffer — never recommend trades that breach it
- paper_mode = True by default — all recommendations are for paper simulation first
