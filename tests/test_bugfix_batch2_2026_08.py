"""Regression tests for the 2026-08 bug-fix batch #2 (financial + reliability).

Covers:
  F1  invest_radar sizes from `deployable` and fails closed on a non-positive
      total instead of defaulting the denominator to $1.
  F2  server._engine_holdings excludes price<=0 'zombie' rows from the
      price-dependent engines (Kelly/consensus) and reports them.
  R2  _ticker_get_store / _chart_cache_put evict expired entries on write.
  R3  risk_gate._quote_age_seconds treats a naive as_of as UTC (no false reject).

All offline.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

# ── F1: invest_radar sizing ──────────────────────────────────────────────────

def test_invest_radar_total_falls_back_to_portfolio_equity():
    import invest_radar as ir

    # No "total" key, but portfolio_equity present → must be usable (not $1).
    out = ir.scan_candidates(
        [], {"cash": 10_000.0, "portfolio_equity": 35_000.0}, screened=[])
    warns = " ".join(out["diagnostics"]["data_warnings"]).lower()
    assert "total unavailable" not in warns
    assert out["deployable_cash"] == 9_500.0  # 10k cash - 500 buffer


def test_invest_radar_fails_closed_when_total_missing():
    import invest_radar as ir

    out = ir.scan_candidates([], {"cash": 10_000.0}, screened=[])
    assert out["mode"] == "NO_ACTION"
    warns = " ".join(out["diagnostics"]["data_warnings"]).lower()
    assert "total unavailable" in warns


def test_invest_radar_sizes_from_deployable_not_cash():
    import invest_radar as ir

    # deployable ($2,000) is smaller than raw cash ($10,000): sizing must use it.
    out = ir.scan_candidates(
        [], {"cash": 10_000.0, "deployable": 2_000.0, "total": 35_000.0}, screened=[])
    assert out["deployable_cash"] == 1_500.0  # 2000 deployable - 500 buffer


# ── F2: engine-boundary zero-price filter ────────────────────────────────────

def test_engine_holdings_excludes_zero_price_zombie():
    import server

    real = {"symbol": "AAPL", "current_price": 100.0, "equity": 1_000.0}
    zombie = {"symbol": "NVDA", "current_price": 0.0, "equity": 101.99}  # stale equity
    valid, degraded = server._engine_holdings({"holdings": [real, zombie]})

    assert [h["symbol"] for h in valid] == ["AAPL"]
    assert degraded == ["NVDA"]


# ── R2: cache eviction on write ───────────────────────────────────────────────

def test_ticker_get_store_evicts_expired():
    import server

    server._ticker_get_cache[("regime", "OLDSYM")] = (
        time.monotonic() - 10_000, {"status": "ok", "data": {}})
    server._ticker_get_store("regime", "NEWSYM", {"status": "ok", "data": {}})

    assert ("regime", "OLDSYM") not in server._ticker_get_cache
    assert ("regime", "NEWSYM") in server._ticker_get_cache


def test_chart_cache_put_evicts_expired():
    import server

    server._chart_cache[("OLDSYM", "1d")] = (time.monotonic() - 10_000, {})
    server._chart_cache_put(("NEWSYM", "1d"), time.monotonic(), {"x": 1})

    assert ("OLDSYM", "1d") not in server._chart_cache
    assert ("NEWSYM", "1d") in server._chart_cache


# ── R3: risk-gate naive timestamp handling ───────────────────────────────────

def test_quote_age_naive_timestamp_treated_as_utc():
    import trading.risk_gate as rg

    fresh_naive = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    age = rg._quote_age_seconds({"as_of": fresh_naive})
    assert age is not None, "naive timestamp must not be treated as unknown age"
    assert age < 120


def test_quote_age_naive_stale_timestamp_measured():
    import trading.risk_gate as rg

    stale_naive = (datetime.now(timezone.utc).replace(tzinfo=None)
                   - timedelta(days=2)).isoformat()
    age = rg._quote_age_seconds({"as_of": stale_naive})
    assert age is not None
    assert age > 24 * 3600


def test_quote_age_aware_timestamp_still_works():
    import trading.risk_gate as rg

    aware = datetime.now(timezone.utc).isoformat()
    age = rg._quote_age_seconds({"as_of": aware})
    assert age is not None and age < 120
