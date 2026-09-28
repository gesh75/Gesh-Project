---
name: paper-trading
description: >
  Use when running paper sessions, checking the risk gate, reviewing win rate,
  or when a paper TRIM/close looks like a Robinhood fill. Also use for
  "paper closed so I sold", paper_trades vs orders vs tracker books, dry-run,
  and PAPER_MODE vs live.
model: grok-4.6
---

# Paper Trading & Execution Layer Skill

## Purpose
Simulate, track, and evaluate trades through the full GESH execution pipeline:
paper trading → risk gate → trade logging → performance review.
This skill covers everything from "run this in paper mode" to "how are my signals performing."

Trigger phrases: "paper trade", "simulate this trade", "run paper session", "check paper performance",
"what's my win rate", "how are my signals doing", "dry run", "gate check", "execution layer",
"log this trade", "close my trade", "paper vs live", "scanner performance",
"paper closed so I sold", "paper book vs robinhood"

---

## Components

### allocation_config.py — Config & Validation Gate
Before ANY trade recommendation, call `validate_action()`:
```python
from allocation_config import validate_action, load_config
ok, reason = validate_action(
    action,         # "BUY" / "SELL" / "TRIM"
    symbol,
    trade_usd,      # total dollar value
    open_positions, # current count
    current_cash,   # optional
    is_options,     # False for stocks
)
```
Gates checked (in order):
1. `auto_trade` gate — if False, block all live execution
2. Max $ per trade — default $5,000 stocks / $2,000 options
3. Max open positions — default 5
4. Cash floor — default $500 minimum remaining
5. Price filter — scanner mode price range (if price provided)

Config is editable via `POST /api/config`. Never hardcode limits.

### paper_trading.py — Simulation Engine
Operator contract (remaining cash, DATA_FRESH, DUPLICATE, shared DB):
`docs/PAPER_SESSION.md`.

Use `run_paper_session()` to simulate a full action plan. Pass `current_cash`
whenever the caller has a deployable figure — each accepted BUY/ADD subtracts
notional so a later name still sees the $500 floor. TRIM/SELL proceeds are
not added back. Paper skips `auto_trade` but still enforces size / open-paper-lot
/ cash-floor caps; a full TRIM frees the in-session slot.
```python
from paper_trading import run_paper_session, check_and_close_paper_positions, get_paper_summary

# Simulate from daily analysis action_plan
results = run_paper_session(
    action_plan=[{"symbol":"NVDA","action":"BUY","shares":5,"current_price":875.0,"signal_reason":"..."}],
    holdings=payload["holdings"],
    total_portfolio=total_equity,
    scanner_source="daily-analysis",
    current_cash=equity_summary.get("deployable"),
)

# Auto-close positions that hit stop or target
closed = check_and_close_paper_positions()

# Compare paper vs live
summary = get_paper_summary(days=7)
# summary["paper_edge"] > 5% → consider scaling live allocation
# summary["paper_edge"] < -5% → do NOT increase auto_trade
```

Via API: `POST /api/paper/run`, `POST /api/paper/check-closes`, `GET /api/paper/summary`

### trade_log.py — Audit Trail
Every real or paper trade should be logged:
```python
from trade_log import log_trade, close_trade, get_win_rate, get_scanner_performance

# Log entry
trade_id = log_trade(
    symbol="NVDA", action="BUY", shares=5, entry_price=875.0,
    stop_loss=831.25, target_price=962.0,
    scanner_source="daily-analysis",
    signal_reason="Consensus STRONG BUY + FFT trough + XLK hot",
    is_paper=False,  # True for paper trades
)

# Close when exit
record = close_trade(trade_id, exit_price=940.0, notes="Target hit")

# Performance stats
stats = get_win_rate(days=30)
scanner_perf = get_scanner_performance()  # ranked by win rate per scanner source
```

Via API: `GET /api/trades`, `POST /api/trades/log`, `POST /api/trades/{id}/close`, `GET /api/trades/stats`

### trading/risk_gate.py — 8-Gate Safety System
The risk gate sits between every signal and every order:
```python
from trading.contract import manual as manual_intent
from trading.risk_gate import evaluate

intent = manual_intent("NVDA", "BUY", confidence=0.85, target_position_pct=0.02,
                        stop_loss=831.25, take_profit=962.0)
verdict = evaluate(intent, portfolio_snapshot)
# verdict.approved = True/False
# verdict.reason = rejection reason if failed
# verdict.gates = {gate_name: True/False} — per-gate breakdown
```

Gates (priority order):
1. KILL_SWITCH — `~/.gesh/KILL` file present → reject all
2. MARKET_HOURS — outside 09:30–16:00 ET weekdays
3. CONFIDENCE_FLOOR — below MIN_CONFIDENCE (default 0.75)
4. POSITION_CAP — above MAX_POSITION_PCT (default 2%)
5. ORDER_RATE — exceeded MAX_ORDERS_PER_DAY (default 3)
6. CASH_FLOOR — post-trade cash below MIN_CASH_BUFFER_USD ($500)
7. DAILY_LOSS — portfolio P&L today ≤ −MAX_DAILY_LOSS_PCT (1%)
8. DATA_FRESH — age the **selected symbol's** quote (`quote_provenance` /
   holding / watchlist), not the snapshot `as_of`. Missing stamp fails closed.
   PAPER uses `max_price_age_seconds_paper`.

Via API: `POST /api/trading/dry-run`

### trading/execution.py — Paper/Live Broker Wrapper
```python
from trading.execution import place

result = place(intent, verdict, snapshot)
# result.status: SIMULATED | REJECTED | ERROR | DUPLICATE
# result.fill_price, result.fill_qty — for paper fills
```
**PAPER_MODE=true by default.** Live execution raises NotImplementedError until explicitly implemented.
Idempotency: SHA-256 of `symbol|action|minute-bucket|target_pct` (`intent_uuid`
excluded). A UNIQUE replay returns `status="DUPLICATE"` and reuses the existing
row — it is not a second fill.

Via API: `POST /api/trading/paper-order`, `POST /api/trading/consensus-order`

---

## Full pipeline walkthrough

### Daily paper trading session
1. Run daily analysis: `POST /api/deep-analysis`
2. Extract action_plan from analysis result
3. Run paper session: `POST /api/paper/run` with action_plan
4. Check auto-closes from previous day: `POST /api/paper/check-closes`
5. Review performance: `GET /api/paper/summary?days=7`

### Signal → Gate → Execute flow
1. Generate signal (consensus or manual)
2. Dry-run gate: `POST /api/trading/dry-run`
3. If APPROVED → `POST /api/trading/paper-order`
4. Log to trade_log (paper_trading does this automatically)
5. Monitor paper position until stop or target hit

### Weekly performance review
1. `GET /api/trades/stats?days=7` — win rate, P&L, scanner performance
2. `GET /api/paper/summary?days=7` — paper vs live edge
3. Review `get_scanner_performance()` — which scanner mode wins most
4. If `paper_edge > 5%` for 30+ days → consider increasing live allocation cautiously

---

## Output format for paper trade reports

### Session Summary
```
📄 PAPER SESSION — {scanner_source} — {date}
Portfolio: ${total_equity:,.0f}

Trades Simulated:
Symbol | Action | Shares | Price  | Stop   | Target | USD
NVDA   | BUY    | 5      | $875   | $831   | $962   | $4,375
AAPL   | TRIM   | 3      | $196   | $205   | $183   | $588
(blocked: JPM — max open positions reached)

Gate Checks: 2 APPROVED / 1 BLOCKED / 0 REJECTED
```

### Performance Report
```
📊 PAPER PERFORMANCE (7d)
Win rate:  67% (4W / 2L) | Avg win: +3.2% | Avg loss: −1.8%
Total P&L: +$842 | Edge vs live: +4.1%
Verdict: Performing similarly to live — continue paper testing

🏆 Best scanner: daily-analysis (75% win rate, +$620)
📉 Worst scanner: premarket (33% win rate, −$180)
```

---

## Rules specific to this skill

- NEVER set `is_paper=False` when `auto_trade=False` in config
- ALWAYS call `validate_action()` before recommending any trade size
- ALWAYS log trades via `log_trade()` for win rate tracking
- paper_edge > 5% threshold requires 30+ trades minimum to be meaningful
- KILL_SWITCH file = `touch ~/.gesh/KILL` — creates instantly, remove to re-enable
- `PAPER_MODE=false` + `auto_trade=True` = LIVE MONEY. Never suggest this without explicit user instruction and 30+ days clean paper history
- Report outcomes by scanner_source — this is how Georgi learns which setups work
- Paper TRIM/close is **not** a Robinhood fill. Do not sell on RH because the
  paper book closed a name. Two books by design (daily-chain `paper_trades` vs
  autopilot tracker). **REQUIRED BACKGROUND:** gesh-daily-report.
- `trade_log` shares `gesh_core.db_path` with `trading.state`. Unusable paths
  fail closed — never fall back to `/tmp/gesh_trades.db`.
- `/api/trades` and `/api/paper/*` are owner / `X-API-Key` only. A customer
  session is 403.
