# Paper Session & Risk-Gate Runbook

Operator contract for the **daily-chain paper book** on port 9000
(`paper_trades` via `trade_log.py`) and the 8-gate + allocation path that
writes it. Autopilot tracker stats (`paper_positions` / `paper_outcomes` in
the trading repo) are a **different book** — do not merge them.

Agent skill (examples, weekly review): `.claude/skills/paper-trading/SKILL.md`.
Precision overlay (BUY-only meta-gate): `.claude/skills/gesh-precision-overlay/SKILL.md`.

**Nothing here places a live broker order.** `PAPER_MODE=true` stays the default.

---

## 1. Why two paper books

| Book | Writer | Table | What it proves |
|---|---|---|---|
| Daily chain | `run_paper_gate2.py` → `run_paper_session` | `paper_trades` | Kelly / override action plan vs the 8 gates |
| Autopilot | `trading/autopilot.py` → `trading/execution.py` | `orders` (status `SIMULATED`) + tracker | Consensus signal accuracy / autonomy gate |

Mixing them muddies win-rate. Only the chain should call `run_paper_gate2.py`.

---

## 2. Causal order (one intent)

```
intent → ENTRY_FLOOR (BUY) → PRECISION_OVERLAY (BUY) → 8 gates
      → optional broker review → recorder (allocation + paper row)
```

`gate_and_execute` in `run_paper_gate2.py` is the daily-chain implementation.
A REJECTED intent records nothing. A recorder block flips the gate result to
`approved=False` + `ALLOCATION=False` so "APPROVED" never means "not filled".

---

## 3. Session remaining cash (PRs #291 / #288)

`current_cash` is the session figure passed into `run_paper_session` and
re-threaded by the recorder after each accepted BUY/ADD.

| Rule | Why |
|---|---|
| Each accepted BUY/ADD subtracts notional | Later names see the $500 floor against **remaining** cash, not the original snapshot |
| TRIM/SELL proceeds are **not** added back | The session must not invent deployable cash from a paper close |
| Overflow / non-finite notional stays unknown | Bool `True` must not become $1; `inf` must not pass the floor |
| `None` cash skips the cash-floor check | Callers that **have** a cash figure must pass it |

The gate's own `CASH_FLOOR` still estimates post-trade cash per intent. The
session decrement is the extra constraint that stops a batch of BUYs from each
seeing the same starting pile (Astra R2).

---

## 4. Allocation caps without live permission (PRs #290 / #288)

`run_paper_session` calls `validate_action(..., require_live_permission=False)`.

Paper **skips** `auto_trade` (that flag is live-execution permission) but still
enforces:

1. Max $ per trade (`max_stock_per_trade` / `max_options_per_trade`)
2. Max open **paper** positions (`max_open_positions`, default 5)
3. Cash floor on **remaining** session cash (`min_cash_buffer`, default $500)

The open-position count is the number of distinct symbols with an open
`paper_trades` lot — **not** the live Robinhood book. A full TRIM/SELL that
closes the last lot for a symbol frees that in-session slot so a later BUY
can use it.

A blocked row is `{blocked: true, reason, code}` — it is not a fill.

---

## 5. DATA_FRESH ages the selected quote (PR #297)

Gate 8 used to age `payload.as_of` (the snapshot / transport stamp). A fresh
MCP snapshot then laundered a stale or missing per-symbol quote.

`trading/risk_gate._quote_age_seconds(portfolio, symbol=intent.symbol)` now:

1. Reads `quote_provenance[symbol].quote_as_of` if present
2. Else the holding / watchlist / `quotes` / `quote_as_of` map row
3. Uses container `as_of` **only** when no per-symbol row exists
4. A per-symbol row with a missing or unparseable stamp → fail closed
   (`Quote age unknown — no parseable quote timestamp for {SYM}`)

`run_paper_gate2.gate_and_execute` stamps `quote_provenance[symbol]` from
`quote_as_of_by_symbol` before `evaluate()` so DATA_FRESH cannot fall back to
the transport stamp.

PAPER mode uses `max_price_age_seconds_paper` (the daily chain's multi-minute
pipeline). LIVE stays on `MAX_PRICE_AGE_SECONDS` (default 300s). Future stamps
beyond `MAX_QUOTE_FUTURE_SKEW_SECONDS` reject.

Pins: `tests/test_risk_gate_quote_selection.py`, `tests/test_paper_gate_causality.py`.

---

## 6. Duplicate `client_order_id` is not a new fill (PR #296)

`trading/execution._client_order_id` is a deterministic SHA-256 of

```
symbol | action | generated_at[:16] (minute bucket) | target_position_pct
```

`intent_uuid` is **excluded** — it is minted per `TradeIntent` and would defeat
HTTP-retry idempotency.

A UNIQUE collision on `orders.client_order_id` returns:

```
ok=False, status="DUPLICATE",
error="duplicate client_order_id — existing order reused"
```

with the **existing** row's qty / fill fields. It must not increment fill
counts or look like a second SIMULATED fill.

Pins: `tests/test_execution_duplicate.py`.

---

## 7. Shared ledger path, fail closed (PR #292)

`trade_log.DB_PATH` uses the same `gesh_core.db_path.resolve_db_path` as
`db.py` and `trading.state` (`GESH_DB_PATH` → cwd `gesh_financial.db`).

An unusable path **fails closed**. Do not silently write `/tmp/gesh_trades.db`
— that split the paper book from the gate's `orders` / daily-loss ledger
(Astra R3). Tests that call `evaluate()` or `log_trade()` must sandbox
`DB_PATH` (see `tests/test_risk_gate_paper_mode.py`).

---

## 8. Run-scoped MERGE (PR #293)

`run_engines2.py` MERGE-loads the previous `engine_results.json`. Yesterday's
`action_plan` / `gate_results` / `paper_session` used to survive a degraded
skip and render as "N paper record(s) today" (GAPS F-07).

`run_manifest.clear_run_scoped_outputs()` drops these keys at the start of
the engines write:

`action_plan`, `gate_results`, `paper_session`, `trade_stats`,
`dod_delta`, `cash_unlock`

A research-only / degraded `run_paper_gate2` preflight writes **nothing**
back. If the report shows a paper audit, this run produced it.

Unknown / non-finite RSI is omitted (`available_rsi_by_symbol`) and ADX/RSI
in the Claude prompt format as `n/a` (`format_optional_metric`). `gen_report2`
still has a few `or 0` display paths — do not treat a report `RSI 0` as a
measured oversold without checking `engine_results.json`.

---

## 9. Precision overlay (gesh-core pin, PR #309)

After the entry floor and **before** the 8 gates, BUYs run
`precision_overlay_blocks`. SELL/TRIM/HOLD are never overlay-gated.

An overlay exception is logged and **not** blocking (fail-open on the overlay
only — the 8 gates still run). Kill-switch: `PRECISION_OVERLAY=0`.

---

## 10. Operator pitfalls

| Symptom | Cause |
|---|---|
| Every BUY in a batch clears CASH_FLOOR | Session cash is not being decremented — check `current_cash=` into the recorder |
| Paper session ignores `max_open_positions` | `require_live_permission` must stay False; the **other** caps must still run |
| DATA_FRESH passes on a name with no quote stamp | Provenance row missing and container `as_of` is being used as a stand-in |
| Retry of the same paper order looks like 2 fills | UNIQUE replay must return `DUPLICATE`, not `SIMULATED` |
| Gate daily-loss / paper book disagree | `trade_log` wrote a different DB than `trading.state` |
| Report shows yesterday's paper audit | MERGE did not `clear_run_scoped_outputs` |
| `/api/trades` / `/api/paper/*` 403 for a customer | Expected — `require_api_key` is owner session or `X-API-Key`, never a customer cookie |
