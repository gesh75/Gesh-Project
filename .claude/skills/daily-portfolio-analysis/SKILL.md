---
name: daily-portfolio-analysis
description: >
  Runs the full GESH Financial daily portfolio analysis for Georgi's Robinhood account.
  Executes all 6 analytical engines (10-node consensus, Monte Carlo, Kelly criterion,
  regime detection, FFT cycles, AI fair value), reconciles signal conflicts between tools,
  produces a prioritised step-by-step action plan, and generates a beautiful animated HTML
  report saved to the gesh-project folder.
  
  Use this skill whenever the user says "run analysis", "daily analysis", "check my portfolio",
  "what should I do today", "run the GESH report", or when triggered by the 10:30 daily schedule.
  Also triggers automatically each morning at 10:30.
---

# GESH Daily Portfolio Analysis

## Overview

This skill runs every tool in the GESH Financial Observability stack against Georgi's live
Robinhood portfolio and produces a single HTML report with reconciled signals and clear actions.

**Project path:** `/sessions/great-serene-shannon/mnt/gesh-project/`
**Output report:** `gesh_report.html` (overwrites previous day's report)
**Database:** `gesh.db` (news/SEC) + `gesh_financial.db` (analysis history)

---

## Step 1 — Load Portfolio Data

Pull the latest holdings from the database (no Robinhood auth needed for analysis;
live fetch is optional if the server is running).

```python
import sqlite3, json, os, sys
sys.path.insert(0, '/sessions/great-serene-shannon/mnt/gesh-project')
os.chdir('/sessions/great-serene-shannon/mnt/gesh-project')

conn = sqlite3.connect('gesh_financial.db')
cur = conn.cursor()
cur.execute('SELECT payload_json, result_json FROM analysis_runs ORDER BY run_at DESC LIMIT 1')
row = cur.fetchone()
conn.close()

payload    = json.loads(row[0])
ai_result  = json.loads(row[1])
holdings   = payload['holdings']
equity_sum = payload['equity_summary']
equity_sum['cash'] = 165.33   # ← always use withdrawable_amount, NOT account["cash"]
total_eq   = equity_sum['portfolio_equity']
symbols    = [h['symbol'] for h in holdings]
```

**Cash field warning:** The Robinhood API `account["cash"]` field returns a negative
margin-accounting figure for Gold accounts — this is a known bug in `data_fetcher.py`
(now fixed to use `profile["withdrawable_amount"]`). Always display the withdrawable amount.

---

## Step 2 — Run All 6 Engines

Run these in order. Each takes ~10–30s due to yfinance calls.

```python
# Install deps if needed
# pip install yfinance pandas numpy --break-system-packages -q

from consensus import run_consensus_portfolio
from monte_carlo import run_monte_carlo
from kelly import run_kelly_analysis
from regime import detect_regimes

print("Running 10-node consensus...")
consensus = run_consensus_portfolio(holdings, total_eq)

print("Running Monte Carlo (1,000 paths, 21-day horizon)...")
mc = run_monte_carlo(holdings, equity_sum)

print("Running Kelly criterion...")
kelly = run_kelly_analysis(holdings, total_eq)

print("Detecting market regimes...")
regime = detect_regimes(symbols)
```

**ECG node note:** The ECG node currently returns an import error (`_drawdown` not found).
Mark it as ABSTAIN in the report — do not let it block the other 9 nodes.

---

## Step 3 — Reconcile Signal Conflicts

Signal conflicts arise because each tool measures a different thing:

| Tool | What it measures | Time horizon |
|------|-----------------|--------------|
| 10-node consensus | Live price action + momentum | Days–weeks |
| AI fair value (DCF) | Intrinsic value vs current price | Months–years |
| Kelly criterion | Optimal position SIZE given risk/reward | Now |
| Analyst targets | Wall St 12-month price targets | 12 months |
| Regime detection | Market structure (trending vs ranging) | Days |
| Monte Carlo | Portfolio-level risk distribution | 21 days |

**Resolution rules:**
- If Kelly says TRIM and AI says undervalued → it's a **weight** signal, not a sell. Trim to optimal size; keep the holding.
- If consensus says BUY and AI says overvalued → short-term momentum overrides valuation. Hold/add, but watch RSI for overbought (>72).
- If analyst says STRONG BUY and Kelly says AVOID → Kelly wins for position sizing. The analyst sees upside potential; Kelly accounts for actual volatility and drawdown risk.
- If regime says TRENDING and Kelly says TRIM → Kelly wins. Trending doesn't mean the allocation is right-sized.

---

## Step 4 — Build Prioritised Action Plan

For each holding, produce one of these actions based on combined signal:

1. **TRIM** — sell partial shares to reduce overweight position (not a bearish call)
2. **BUY / ADD** — buy more shares; specify exact share count and price level
3. **HOLD** — do nothing; specify any price alerts to set
4. **AVOID** — do not add; keep existing small position if already held

**Share count formula for BUY:**
```
target_equity    = kelly_optimal_pct / 100 * total_portfolio
current_equity   = holding['equity']
usd_to_deploy    = target_equity - current_equity
shares_to_buy    = floor(usd_to_deploy / current_price)
```

**Share count formula for TRIM:**
```
target_equity   = kelly_optimal_pct / 100 * total_portfolio
current_equity  = holding['equity']
usd_to_free     = current_equity - target_equity
shares_to_sell  = ceil(usd_to_free / current_price)
```

Always keep $500+ cash buffer. Never suggest using full buying power.

---

## Step 5 — Generate HTML Report

Save to `/sessions/great-serene-shannon/mnt/gesh-project/gesh_report.html`.

The report must include all of the following sections:

1. **Live ticker tape** — animated price strip across the top
2. **Portfolio stats row** — equity, real cash, buying power, risk score
3. **AI portfolio gauge** — semicircular score dial (0–100)
4. **Signal conflict resolution** — one card per conflict, showing both sides and the verdict
5. **Holdings cards** — one card per symbol with:
   - 10-node consensus grid (▲ bull / ▼ bear / – neutral / ? abstain)
   - Vote count + confidence bar
   - AI fair value track bar (current vs estimate)
   - Kelly action + USD delta
6. **Monte Carlo chart** — bar histogram of 21-day outcome distribution
7. **Kelly sizing chart** — grouped bar: current % vs optimal %
8. **Regime cards** — one per symbol (TRENDING / RANGING / VOLATILE + ADX/RSI/%B)
9. **Prioritised action table** — Priority / Symbol / Action / Amount / Rationale / Signals Agree

**Design constraints:**
- Dark theme: background `#080d14`, surface `#0d1520`, borders `#1a2840`
- Accent colours: cyan `#00d4ff`, green `#00e87a`, yellow `#f0b429`, red `#ff3c5a`
- Monospace font for all numbers and labels (`JetBrains Mono` or `Courier New`)
- Animated on load (fadeUp per section)
- Scan-line overlay for terminal aesthetic
- Chart.js from cdnjs for Monte Carlo and Kelly charts
- Single self-contained HTML file (no external CSS/JS files)
- Include disclaimer: "NOT FINANCIAL ADVICE — FOR INFORMATIONAL PURPOSES ONLY"

The existing `gesh_report.html` in the project folder is the reference implementation.
Read it and update the data in the JS `const HOLDINGS`, `REGIMES`, and `ACTIONS` arrays
with today's fresh analysis results.

---

## Step 5b — Paper Trade the Action Plan (optional but recommended)

After building the action plan, simulate it in paper mode before presenting:

```python
from paper_trading import run_paper_session, check_and_close_paper_positions
from trade_log import trade_summary_for_report

# Auto-close any existing paper positions that hit stop/target
closed = check_and_close_paper_positions()

# Simulate today's action plan
action_plan = [
    {"symbol": "NVDA", "action": "BUY", "shares": 5, "current_price": 875.0, "signal_reason": "..."},
    # ... one entry per P1/P2 action
]
paper_results = run_paper_session(action_plan, holdings, total_eq, scanner_source="daily-analysis")

# Include in report: paper vs live performance
trade_stats = trade_summary_for_report(days=7)
# trade_stats["paper"]["win_rate_pct"], trade_stats["real"]["win_rate_pct"], trade_stats["scanner_performance"]
```

Also run gate check on any BUY recommendations:
```python
from trading.contract import manual as manual_intent
from trading.risk_gate import evaluate as risk_gate_evaluate

for action in action_plan:
    if action["action"] in ("BUY", "SELL"):
        intent = manual_intent(action["symbol"], action["action"],
                               confidence=0.85, target_position_pct=0.02)
        verdict = risk_gate_evaluate(intent, payload)
        action["gate_approved"] = verdict.approved
        action["gate_reason"] = verdict.reason
```

## Step 6 — Save to Database

```python
import sqlite3, json
from datetime import datetime, timezone

conn = sqlite3.connect('gesh_financial.db')
conn.execute('''INSERT INTO analysis_runs
    (run_at, model, portfolio_score, risk_score, diversification,
     portfolio_type, portfolio_verdict, payload_json, result_json)
    VALUES (?,?,?,?,?,?,?,?,?)''', (
    datetime.now(timezone.utc).isoformat(),
    'claude-sonnet-4-6',
    ai_result.get('portfolio_score', 0),
    ai_result.get('risk_score', 0),
    ai_result.get('diversification_score', 0),
    ai_result.get('portfolio_type', ''),
    ai_result.get('portfolio_verdict', ''),
    json.dumps(payload),
    json.dumps(ai_result)
))
conn.commit()
conn.close()
```

---

## Output / Success Criteria

- ✅ `gesh_report.html` written to project folder and openable in browser
- ✅ All 5 holdings analysed with fresh yfinance data
- ✅ Signal conflicts identified and resolved with clear reasoning
- ✅ Action plan shows exact share counts and dollar amounts
- ✅ Monte Carlo + Kelly charts rendered with today's data
- ✅ Report saved to `gesh_financial.db` analysis_runs table

## Known Issues / Watchouts

- **ECG node**: Import error on `_drawdown` — mark ABSTAIN, don't crash
- **QQQ fundamentals**: No direct analyst node data — Analyst node returns ABSTAIN for ETFs
- **Robinhood auth timeout**: If live fetch times out (MFA required), fall back to latest DB snapshot
- **Cash field**: Always use `profile["withdrawable_amount"]`, never `account["cash"]`
