# Astra P1 verify (read-only)

**Repo:** `gesh75/Gesh-Project` at `main` `31690cb` (`chore: gap scan + improvement plan (#278)`).
**Method:** source read on this tree + last pre-shim bodies from git history. No merges, no trades, no brokerage/config edits, no production-code changes.
**Date:** 2026-09-10.

## Pinned gesh-core revision

Astra cited core `56f14d4e3a8f`. **No drift.**

| Locator | Value |
|---|---|
| Submodule | none (no `.gitmodules`) |
| Vendored path | none (`vendor/` is DCF cookbooks only) |
| `requirements.lock:35` | `gesh-core @ git+https://github.com/gesh75/gesh-core.git@56f14d4e3a8fd527a9637cc7fb1ce2042c11aee0` |
| `requirements.txt:79` | same 40-char SHA |
| `tests/test_dependency_sanity.py` `GESH_CORE_SHA` | same SHA |
| `tests/test_strict_config_integration.py` `PINNED_SHA` | same SHA |

Lock comment: `56f14d4` = v0.8.2 + PR #35 DCF tests + PR #46 CI fd-limit.

This VM could **not** import or clone the private tree: `GESH_CORE_PAT` returns GitHub `401`; default token `404`. Claims whose **current** body lives only in `gesh_core.*` are judged from (a) product callers still on this repo, (b) last pre-shim file at the peel commit, (c) tests that pin the 56f14d4 contract. That limit is marked on those rows.

Product shims that alias into the pin: `kelly.py`, `allocation_config.py`, `db.py`, `trading/config.py`, `trading/state.py`. Local (non-shim) copies used below: `paper_trading.py`, `trade_log.py`, `trading/execution.py`, `trading/risk_gate.py`, `trading/autopilot.py`, `run_paper_gate2.py`, `tests/conftest.py`.

## Findings

| # | Astra claim | Verdict | Where | Evidence |
|---|---|---|---|---|
| 1 | `paper_trading.run_paper_session` ignores `validate_action` "Auto-trade is OFF" and skips remaining allocation size/position checks | **CONFIRMED** | `paper_trading.run_paper_session` + last `allocation_config.validate_action` (now `gesh_core.allocation_config`) | Current paper path treats any reason containing `"Auto-trade"` as a non-block. Last local `validate_action` **returns on the auto-trade check** before max-$ / max-positions / cash-floor. `gesh_config.json` has `"auto_trade": false`. Daily-chain mitigation: `run_paper_gate2.gate_and_execute` still runs `risk_gate.evaluate` before the paper recorder — that is a different cap set, not `validate_action`. |
| 2 | `run_paper_gate2.build_action_plan` budgets each BUY independently vs `cash-$500` without reducing remaining cash | **CONFIRMED** | `run_paper_gate2.build_action_plan` | `usd = min(usd, max(0.0, cash - CASH_BUFFER_USD))` uses the **same** starting `cash` every iteration. No `cash -=` / remaining deployable. `gate_and_execute` also reuses one `gate_payload` per symbol (cash not decremented after an APPROVED BUY). |
| 3 | `trade_log` vs shared `trading/state` use different DB paths (`GESH_DB_PATH` vs repo-local `/tmp` fallback); `paper_trades` vs `orders` counters diverge | **CONFIRMED** | `trade_log._resolve_db` / `DB_PATH`; `trading/state.py` shim + last local `gesh_core.trading.state._resolve_db` | `trade_log` is repo-relative `gesh_financial.db` or `/tmp/gesh_trades.db` and **does not read `GESH_DB_PATH`**. State (shim comment + last local body) uses `GESH_DB_PATH` or repo `gesh_financial.db`, fallback `/tmp/gesh_trading_state.db`. `paper_trades` vs `orders` are **two books by design** (`CLAUDE.md` “Two paper books”). Counters diverge whenever the paths or tables differ. |
| 4 | `trading/execution._safe_log` / `place`: UNIQUE violation returns `None` but paper branch still syncs stops + fill alert + `ok=True` `SIMULATED` | **CONFIRMED** | `trading.execution._safe_log`, `trading.execution.place` | `_safe_log` swallows UNIQUE and returns `None`. Paper branch always `_sync_paper_stop` + `alert_order_fill` + `OrderResult(ok=True, status="SIMULATED")` with `order_row_id=None`. No `if row_id is None: return DUPLICATE`. |
| 5 | `risk_gate` freshness uses top-level `as_of`; future timestamps pass; autopilot may not use symbol quote timestamp (daily paper-chain adapter mitigation) | **CONFIRMED** | `trading.risk_gate._quote_age_seconds` / `evaluate`; `trading.autopilot.open_window`; `run_paper_gate2.gate_and_execute` | Age is `now - as_of` (or `equity_summary.as_of`). A future `as_of` yields **negative** age; `age <= max_age` passes. Autopilot calls `evaluate(intent, snapshot)` with `data_fetcher.build_data_payload` top-level `as_of` (newest watchlist `updated_at`, else fetch-time `now()`). Daily chain **does** overwrite `symbol_payload['as_of']` from `run_manifest.quote_as_of` per symbol. |
| 6 | shared `trading/config` + kelly: numeric risk settings use `float()` without finiteness/range (bools strict) | **LIKELY** | last `trading/config._env_float`; `tests/test_strict_config_integration.py` (pin 56f14d4) | Bools on the **pinned** package are strict (`ConfigError` / `_parse_strict_bool` — product tests require that on 56f14d4). Last local `_env_float` is `float(raw)` with only `TypeError`/`ValueError` → default; **no** `isfinite` / range. `inf`/`nan` would be accepted. Current `gesh_core.trading.config` / `gesh_core.kelly` numeric parsers were not readable here. Pre-shim `kelly.py` used constants (`MAX_POSITION_PCT = 0.25`); current tests call `kelly._max_position_pct()` / `_max_gross_exposure()` (gesh-core additions). |
| 7 | `tests/conftest` import-order: DB CREATE/DROP at import before fixtures | **REJECTED** (as written) | `tests/conftest.py` | This `conftest.py` has **no** module-level CREATE/DROP and does not import `db` / `trade_log` at import. Isolation fixtures import `db` only inside the fixture. Adjacent real probe (not this claim): `trade_log._resolve_db` CREATE/DROP `_tl_probe` at **import**, and collection-time `import trade_log` in several tests runs that before fixtures. Last local `trading/state._resolve_db` had the same probe pattern. |
| 8 | P2 `kelly._max_drawdown` misses first-observation loss from equity 1.0 | **LIKELY** | last `kelly._max_drawdown` (now `gesh_core.kelly._max_drawdown`) | Last local body: `cumulative = np.cumprod(1 + returns)` then peak-to-trough on that series — first point’s peak is itself, so a first-day loss vs starting equity `1.0` is **not** a drawdown. Product tests only assert `_max_drawdown(...) <= 0.0`. Current gesh-core body not fetched; no first-observation regression exists here. |

## Evidence quotes

### 1 — `paper_trading.run_paper_session`

```104:110:paper_trading.py
        # In paper mode we skip the auto_trade gate (paper always runs)
        if not ok and "Auto-trade" not in reason:
            created.append({
                "symbol": symbol, "action": action,
                "blocked": True, "reason": reason
            })
            continue
```

Pinned by `tests/test_paper_trading.py` (`test_auto_trade_gate_is_ignored_in_paper` uses reason `"Auto-trade is disabled"` and expects a fill).

Last local `validate_action` (peeled at `510dc94^`; current impl is the gesh-core shim):

```
    # 1. Auto-trade gate
    if not cfg["auto_trade"] and action in ("BUY", "ADD", "TRIM", "SELL"):
        return False, "Auto-trade is OFF — review and execute manually"

    # 2. Max $ per trade
    ...
    # 3. Max open positions
    ...
```

`gesh_config.json`: `"auto_trade": false`. With that default, paper never reaches checks 2–5 unless gesh-core later stopped fail-fasting (unverified on 56f14d4).

### 2 — `run_paper_gate2.build_action_plan`

```188:194:run_paper_gate2.py
            usd    = max(0.0, opt / 100 * total_eq - equity)
            usd    = min(usd, max(0.0, cash - CASH_BUFFER_USD))   # keep the $500 buffer
            shares = math.floor(usd / price)
            if shares > 0:
                action_plan.append({'symbol': sym, 'action': 'BUY', 'shares': shares,
                                    'current_price': price,
                                    'signal_reason': f'Kelly {k_act}: now {equity/total_eq*100:.1f}% vs optimal {opt:.1f}%'})
```

`tests/test_run_paper_gate2_action_plan.py` `test_action_plan_add_and_buy_both_emit_buy` sizes two names independently against the same `$5000` cash / `$4500` deployable and asserts both get 20 shares. That case still fits; the missing remaining-cash reduction is untested.

Same-batch gate payload (cash not reduced after an approval):

```254:255:run_paper_gate2.py
        symbol_payload = dict(gate_payload)
        symbol_payload['as_of'] = (quote_as_of_by_symbol or {}).get(a['symbol'])
```

### 3 — DB paths / two paper counters

```30:50:trade_log.py
_PRIMARY = Path(__file__).parent / "gesh_financial.db"
_FALLBACK = Path("/tmp/gesh_trades.db")

def _resolve_db() -> Path:
    ...
        con.execute("CREATE TABLE IF NOT EXISTS _tl_probe (x INTEGER)")
        con.execute("DROP TABLE IF EXISTS _tl_probe")
        ...
        return _PRIMARY
    except sqlite3.OperationalError:
        return _FALLBACK

DB_PATH = _resolve_db()
```

`trading/state.py` (shim) documents the other resolver:

```10:11:trading/state.py
DB path is cwd-relative (or GESH_DB_PATH), so this repo keeps its OWN
gesh_financial.db — the separate paper books stay separate (rule #44).
```

Last local `trading/state._resolve_db` (`5c871a9^`):

```
    primary = Path(os.getenv("GESH_DB_PATH",
                             str(Path(__file__).parent.parent / "gesh_financial.db")))
    ...
    fallback = Path("/tmp/gesh_trading_state.db")
```

`CLAUDE.md`: trade_log `paper_trades` = daily chain + manual sims; trading-repo/`orders` tracker = autopilot autonomy-gate book. Do not merge them.

### 4 — UNIQUE swallow then paper “fill”

```307:353:trading/execution.py
    if cfg.paper_mode:
        ...
        row_id = _safe_log(
            ...
            status="SIMULATED",
            ...
        )
        _sync_paper_stop(intent, fill_price, row_id)
        alert_order_fill(
            intent.symbol,
            intent.action,
            qty,
            fill_price,
            "paper",
            "SIMULATED",
            ...
        )
        return OrderResult(
            ok=True,
            status="SIMULATED",
            ...
            order_row_id=row_id,
            ...
        )
```

```439:447:trading/execution.py
def _safe_log(**kwargs) -> int | None:
    """Wrap log_order to swallow UNIQUE constraint violations as DUPLICATE."""
    try:
        return log_order(**kwargs)
    except sqlite3.IntegrityError as e:
        if "UNIQUE" in str(e).upper():
            return None
        raise
```

### 5 — freshness / future `as_of` / adapters

```116:136:trading/risk_gate.py
def _quote_age_seconds(portfolio: dict[str, Any]) -> float | None:
    """Best-effort: derive quote freshness from payload['as_of'] or holdings timestamp."""
    raw = portfolio.get("as_of") or portfolio.get("equity_summary", {}).get("as_of")
    if not raw:
        return None
    try:
        ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds()
```

```283:299:trading/risk_gate.py
    if rejection_reason is None:
        age = _quote_age_seconds(portfolio_snapshot)
        max_age = (cfg.max_price_age_seconds_paper if cfg.paper_mode
                   else cfg.max_price_age_seconds)
        if age is None:
            gates["DATA_FRESH"] = False
            rejection_reason = "Quote age unknown — snapshot missing 'as_of' timestamp"
        else:
            gates["DATA_FRESH"] = age <= max_age
```

Autopilot (no per-symbol `as_of` inject):

```363:364:trading/autopilot.py
            verdict = evaluate(intent, snapshot, cfg)
            result = place(intent, verdict, snapshot, cfg)
```

Daily-chain mitigation:

```423:425:run_paper_gate2.py
        # The DATA_FRESH gate (#8) fails closed without a provider quote timestamp.
        # gate_and_execute injects each symbol's own manifest quote_as_of; the
        # snapshot transport time is deliberately not treated as market freshness.
```

```254:255:run_paper_gate2.py
        symbol_payload = dict(gate_payload)
        symbol_payload['as_of'] = (quote_as_of_by_symbol or {}).get(a['symbol'])
```

If the manifest key is missing, this **overwrites** `as_of` with `None` and fail-closes (stricter than autopilot). A future per-symbol stamp would still pass.

### 6 — numeric `float()` vs strict bools

Last local `trading/config.py` (`5c871a9^`):

```
def _env_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default
```

`float("inf")` / `float("nan")` do not raise. Product tests on the **pin** only lock the three safety **bools**:

```7:12:tests/test_strict_config_integration.py
Contract (gesh-core d4e9f46, PR #8):
  * missing            -> documented conservative default (all three True)
  * "true"/"false"     -> accepted, case-insensitive, after trimming
  * blank              -> ConfigError
  * 1/0, yes/no, y/n, on/off, malformed -> ConfigError
```

### 7 — `tests/conftest.py`

No import-time DDL. Autouse fixtures import `db` only after pytest starts the fixture:

```91:103:tests/conftest.py
@pytest.fixture(autouse=True)
def _isolate_equity_snapshots_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ...
    import db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "equity_snapshots_isolated.db", raising=False)
```

#278 added `_isolate_deployment_origins` (`delenv APP_BASE_URL` / `GESH_ALLOWED_ORIGINS`) — CSRF isolation, not CREATE/DROP.

### 8 — `_max_drawdown` first observation

Last local `kelly._max_drawdown` (`6a08399^`):

```
def _max_drawdown(returns: np.ndarray) -> float:
    cumulative = np.cumprod(1 + returns)
    running_max = np.maximum.accumulate(cumulative)
    drawdown = (cumulative - running_max) / running_max
    return float(np.min(drawdown)) * 100  # negative %
```

Example: `returns = [-0.10]` → `cumulative = [0.9]`, `running_max = [0.9]`, `drawdown = [0]`. Loss from equity `1.0` is omitted. A prepend of `1.0` would report `-10`.

## Already-merged gap-scans

### Gesh-Project #278 — merged 2026-09-09 (`31690cb`)

Verifiable rescan + three small fixes. **None of the Astra P1s above.**

Closed as already-done (do not re-open): CVaR/GARCH `N/A` rendering; MCP `$0` quotes skipped; failover omitted numerics → `None`; billing foreign-sub rebind fence; `/track-record` public; CI pytest + customer-isolation on same-repo PRs; coverage floor 55; gesh-core pin `56f14d4`; no live secrets in git.

Fixed in that PR:

1. `daEsc` apostrophe → `&#39;` (HTML text/`value=` only; JS-in-`onclick` still open).
2. Autouse `delenv(APP_BASE_URL, GESH_ALLOWED_ORIGINS)` in `tests/conftest.py`.
3. Monte Carlo `calibration: degraded` banner + VaR badge (not `✓ OK`).

Left open there: stale paper/gate “today” (`paper_gate_ran_at`); owner-gating `/api/trades`; `run_ai2.py` `RSI or 0`; dead `_check_customer_quota`.

Sibling **#277** is still OPEN with the same title — out of scope here (do not merge).

### gesh-core #65

**UNKNOWN.** Private repo; PAT `401`. This product tree has no cite of a gesh-core gap-scan #65. Do not confuse with **Gesh-Project #65** (2026-07-15, `decision_quality.py` observability) — different repo and year.

## Recommended repair order

Do these as **separate** PRs. Do not loosen risk gates to make tests pass. Numeric/kelly bodies belong in gesh-core first (rule #46), then bump the 40-char pin here.

1. **#2 `build_action_plan` remaining cash** (product, local file). After each BUY, subtract `shares * price` from deployable cash (still honor the $500 floor). Add a two-name case where `2 × kelly` exceeds `cash - 500` and assert the second size shrinks or HOLDs. Optionally thread remaining cash into `gate_and_execute`’s payload so `CASH_FLOOR` sees the same running total.
2. **#1 paper `validate_action` after Auto-trade** (product). Keep ignoring the live auto-trade gate; **still apply** size / open-position / cash-floor reasons. Substring `"Auto-trade" not in reason` must not hide a concatenated failure. Confirm fail-fast vs accumulate on 56f14d4 `gesh_core.allocation_config.validate_action` before changing the core.
3. **#4 UNIQUE ≠ fill** (product `trading/execution.py`). On `_safe_log is None`, return `ok=False, status="DUPLICATE"`; skip `_sync_paper_stop` and `alert_order_fill`.
4. **#5 future `as_of` + autopilot stamp** (product `trading/risk_gate.py`, then autopilot). Reject `age < 0` (clock/future quote). Optionally inject per-symbol `updated_at` the way `gate_and_execute` does. Do not treat missing manifest `quote_as_of` as a silent pass.
5. **#3 path contract** (product `trade_log.py` + docs). Honor `GESH_DB_PATH` the same way `db` / `state` do, or document that `paper_trades` and `orders` are different files on purpose. Do not merge the two paper books.
6. **#6 numeric finiteness** (gesh-core `trading.config`, then pin bump). Reject non-finite / out-of-range floats the way bools already reject aliases. Product-only tests cannot see the current parser until the private tree is readable.
7. **#8 first-observation drawdown** (gesh-core `kelly._max_drawdown`, P2). Prepend equity `1.0` (or equivalent) and add a one-day `-10%` case. Pin bump after.
8. **#7 conftest CREATE/DROP** — no repair in `conftest.py`. If the adjacent import-time `_tl_probe` bothers CI/dev DBs, defer `_resolve_db()` until first connect and keep isolating `trade_log.DB_PATH` in fixtures.

Out of scope (per request): nltk bump, Learn, live trading, merging conflicting #64/#277, rewriting rotting drafts.
