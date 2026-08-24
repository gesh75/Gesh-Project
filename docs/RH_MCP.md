# Robinhood MCP — operator runbook

Read-only Robinhood agent-MCP layer shipped in PR #239 (2026-08-23). The daily
chain already authenticated against `agent.robinhood.com/mcp/trading` for the
snapshot (3 tools). These modules wrap the rest of the **read** surface so the
VPN-safe path is a first-class data source, not a snapshot-only courier.

Narrative / agent notes: `CLAUDE.md` § "Robinhood MCP as a data source".
This page is the scannable contract: APIs, wiring, env, pitfalls.

**Nothing here places, cancels, or exercises an order.**

---

## 1. Why three modules

| Module | Stage | Job |
|---|---|---|
| `rh_mcp_data.py` | 1 — data | OHLCV, broker indicators, tax lots, quotes |
| `tax_lots_enrich.py` | 1b — wash | Purchase-side wash-sale check on the harvest report |
| `rh_order_review.py` | 2 — preflight | Simulate a gate-approved intent at the broker |

Data reads and order-shaped calls **do not share an import surface**. That is
why review is not folded into `rh_mcp_data.py`.

The MCP path survives the corporate VPN (rule #54). `price_source.py`'s
Robinhood tier is the thin **unauthenticated** quote endpoint; this layer is
the authenticated one.

---

## 2. Stage 1 — `rh_mcp_data.py`

### Safety invariant (code, not convention)

`_ALLOWED_TOOLS` is a frozenset of read-only names. `_call()` raises
`ValueError` **before a request is built** for anything else.
`place_equity_order`, `place_option_order`, `cancel_*`, and `exercise_option`
are absent. Guard tests parametrize every mutating verb and assert the
transport is never reached; a second test fails if an allowlisted name merely
*starts with* a mutating verb. Do not let `ruff --fix` "clean those up".

Network / auth / parse failures return `None` or an empty frame — never raise
(same contract as `price_source`). The allowlist violation is the one loud
exception (programming error).

### Public API

```python
from rh_mcp_data import (
    available, historicals, indicator, latest_indicator, atr,
    tax_lots, unrealized_by_lot, quotes, clear_caches,
)

available()                              # False when no session; never raises
df = historicals("NVDA", days=30)        # yfinance-shaped OHLCV; empty on fail
df.attrs["gesh_price_source"]            # "robinhood_mcp" when bars exist
atr("NVDA")                              # latest ATR(14), or None
latest_indicator("NVDA", "rsi", period=14)
lots = tax_lots("NVDA")                  # [] means UNKNOWN, not "no lots"
quotes(["NVDA", "BAC"])                  # {} on fail
clear_caches()                           # required in tests that reuse a symbol
```

| Helper | Returns on failure | Do not read as |
|---|---|---|
| `historicals()` | empty `DataFrame` | "this symbol has no history" |
| `indicator()` / `atr()` | `None` | `0` |
| `tax_lots()` | `[]` | "nothing harvestable" |
| `quotes()` | `{}` | "flat book" |

`historicals()` of several symbols uses a yfinance `group_by="column"`
MultiIndex (`frame["Close"]` is per-ticker). A bar without a close is dropped,
not zero-filled.

### Allowlisted tools

`get_equity_historicals`, `get_equity_technical_indicators`,
`get_equity_tax_lots`, `get_equity_quotes`, `get_equity_fundamentals`,
`get_equity_positions`, `get_equity_tradability`, `get_portfolio`,
`get_realized_pnl`, `get_pnl_trade_history`.

### Session + cache

Both this module and `rh_order_review` import `fetch_snapshot_direct` under
`except BaseException` (not `except Exception`). That module calls
`SystemExit` at import when `GESH_RH_ACCOUNT` is unset; `SystemExit` is a
`BaseException`. A bare `except Exception` would take down every caller.

Failed sessions retry after 300s. TTL caches (rule #42):

| Cache | Env | Default |
|---|---|---|
| OHLCV bars | `GESH_MCP_BARS_TTL_S` | `1800` (30 min) |
| Tax lots | `GESH_MCP_LOTS_TTL_S` | `3600` (60 min) |

---

## 3. Stage 1b — `tax_lots_enrich.py`

### The defect it actually closes

`gesh_core.tax_harvester.get_wash_status()` reads only `wash_sale_log`, and
that table is written only by `record_harvest()`. It sees a wash window opened
by a **prior harvest sale we made**. It is structurally blind to **purchases**.
The IRS window runs 30 days on **both** sides of a loss sale, so an ordinary
buy creates real exposure the base scan cannot see and would report ELIGIBLE.

Lot-level selection is **not** a harvest-size optimization on a book whose lots
lose together — measured harvestable loss was smaller at lot grain than at
blended position grain. Do not re-derive the opposite claim.

### Additive contract

`enrich(report, holdings)` returns a **new** dict. It never edits, removes, or
recomputes a pre-existing key (`eligible`, `summary`,
`total_harvestable_loss_usd`, …). A guard test asserts every original key is
byte-identical. `gen_report2.py` / `server.py` readers stay untouched.

Adds only:

| Key | Meaning |
|---|---|
| `lots_by_symbol` | per-name lot view (`lot_data` is `ok` or `unavailable`) |
| `purchase_wash_warnings` | names the base scan called ELIGIBLE that sit in a purchase-side window |
| `lot_coverage` | `{checked, available, unavailable}` — a **ratio**, so a total MCP outage cannot read as "no warnings found" |

`wash_risk_from_purchases` is omitted entirely when lots are unavailable — never
set `False`. Empty lots → `lot_data: "unavailable"`, never "no recent buys".

### Union is load-bearing

`get_harvest_summary()` exposes `eligible` / `in_wash_window` / `no_cost_basis`
and **discards** every `BELOW_THRESHOLD` candidate. With `MIN_LOSS_USD=500` a
book whose largest loss is under that threshold produces **zero losing rows**.
Deriving symbols from the report alone checks 0/0 and reports "no warnings" —
the false-clear this module exists to prevent.

`enrich()` unions the report's rows with **every holding currently at a loss**.
A sub-threshold loss still carries wash exposure the moment it is sold.

### Chain wiring

1. `run_engines2.py` step 7 — `get_harvest_summary()` then `enrich()`. Lot
   fetch failure is non-fatal; the base report is kept.
2. `gen_report2.py` — empty `candidates` means "no loss cleared $500", **not**
   "all positions profitable". Renders purchase-side warnings and a lot-coverage
   caveat when any symbol is `unavailable`.

```bash
./venv/bin/python tax_lots_enrich.py    # needs engine_results.json
```

---

## 4. Stage 2 — `rh_order_review.py`

Wraps `review_equity_order`. **Simulates** one equity order after the 9-gate
approve and **before** the paper recorder. Places nothing. `_REVIEW_TOOL` is
the only name this module will send.

### Status taxonomy (do not collapse)

| Status | Meaning | `checked` | `executable` |
|---|---|---|---|
| `OK` | broker simulated the order (`alerts` may still be non-empty) | True | True |
| `REJECTED` | broker refused the simulated order | True | False |
| `UNAUTHORIZED` | token cannot use the order family | False | False |
| `UNAVAILABLE` | no session / no `GESH_RH_ACCOUNT` | False | False |
| `ERROR` | bad shape, bad side, or exception | False | False |

`OK` / `REJECTED` are conclusive. The other three mean **"not checked"** — never
a pass, and never a silent veto. `ReviewResult.executable` is True **only** for
`OK`.

Current measured state (2026-08-21): the OAuth client registered by
`fetch_snapshot_direct.py` holds scope `internal` (read tools, not the order
family). Review returns `FORBIDDEN` / `UNAUTHORIZED`. The module activates by
itself if account trading authorization is ever granted — **owner-only
decision; this runbook does not grant it.** An `UNAUTHORIZED` verdict is
memoized per process so a batch costs one MCP call.

### Chain wiring (`run_paper_gate2.gate_and_execute`)

```
9-gate APPROVED → reviewer(action) → (optional veto) → paper recorder
```

- Import is best-effort; a broken module leaves `reviewer=None`.
- A reviewer exception lands on the gate result as `broker_review_error` and
  never breaks the chain.
- Only a conclusive `REJECTED` can veto, and only when blocking is on.
- Coverage prints as a ratio so an all-UNAUTHORIZED day cannot read as all-clear:

  `[gate] broker preflight: N/M conclusively checked · UNAUTHORIZED=M`

`review_action()` maps `BUY`/`ADD` → buy, `TRIM`/`SELL` → sell. Anything else
(`HOLD`, blank) is `ERROR` / "not an order".

```python
from rh_order_review import review, review_action, enabled, blocking

r = review("NVDA", "sell", quantity=1)
# r.status / r.checked / r.executable / r.alerts / r.message
```

---

## 5. Environment

| Var | Default | Meaning |
|---|---|---|
| `GESH_RH_ACCOUNT` | **required** | account number — env → `.env`; never hardcoded. Unset → `SystemExit` in `fetch_snapshot_direct` |
| `GESH_BROKER_REVIEW` | `1` (on) | run the preflight (it is read-only) |
| `GESH_REVIEW_BLOCKING` | `0` (off) | `REJECTED` vetoes the paper record. Off until the evidence exists |
| `GESH_MCP_BARS_TTL_S` | `1800` | OHLCV cache |
| `GESH_MCP_LOTS_TTL_S` | `3600` | tax-lot cache |

`GESH_REVIEW_BLOCKING=1` is the only switch that changes gate behaviour. Leave
it off unless you have measured conclusive `OK`/`REJECTED` coverage.

---

## 6. Snapshot builder pitfall (still on main)

`build_snapshot_from_mcp.py::_f()` defaults unparseable values to `0.0`, and
last-trade price goes through `_f()`. A missing / blank quote therefore writes
`price: 0.0`, `equity: 0.0`, `percent_change: -100` and folds that mark into
`portfolio_equity`. Kelly, Monte Carlo, and ECG then size against an
understated book.

`rh_mcp_data._f()` already returns `None` for empty — do not "align" the
snapshot builder the other way. A partial `get_equity_quotes` response should
fail the build **before** writing `live_portfolio_snapshot.json` so chain
fallback tiers can run. Until that lands, treat any new `price <= 0` row as
poison, not a real print.

---

## 7. Troubleshooting

| Symptom | Cause / what to do |
|---|---|
| `lot coverage: 0/0` | Enrich ran with no losing rows **and** no report candidates — check that `holdings` were passed (the union). `0/N` with `unavailable=N` is an MCP outage, not "no wash risk" |
| Report says "all positions profitable" | Stale copy of `gen_report2`. Current text is the $500 threshold + sub-threshold loser count |
| `[gate] broker preflight: 0/N … UNAUTHORIZED=N` | Expected today (scope `internal`). Not an all-clear, not a veto |
| Reviewer never runs | `GESH_BROKER_REVIEW=0`, or 9-gate rejected (review is post-approve only) |
| `SystemExit` / every engine dies at import | `GESH_RH_ACCOUNT` unset — must be in the process env (launchd does not read `.env` unless the script exports it) |
| `direct MCP fetch failed` in `/tmp/gesh_fullchain.log` | Tier 1 is broken; the chain silently paid the `claude -p` courier. See CLAUDE.md snapshot fallback |
| Tests reuse a symbol and see stale bars/lots | Call `rh_mcp_data.clear_caches()` / `rh_order_review.reset()` |

---

## 8. Verify

```bash
./venv/bin/python -m pytest \
  tests/test_rh_mcp_integration.py \
  tests/test_tax_lots_enrich.py \
  tests/test_gen_report2_tax.py -q
```

Offline only — mocks sit at `_get_session` / `loader`. Do not hit the live
MCP from the sandbox.
