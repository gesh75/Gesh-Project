"""First execution tests for proof_page._cohort_stats.

The public /track-record page derives engine / manual / all-source headline
numbers from this helper. A BREAKEVEN counted as a win, a no-loss book
advertised as PF 0 (instead of inf), or True/NaN inventing $1 would
mis-sell the autonomy gate.
"""

from __future__ import annotations

import math

import pytest

import proof_page as pp


def _row(
    *,
    outcome: str,
    pnl_usd: object = 0.0,
    pnl_pct: object = 0.0,
    qty: object = 1.0,
    entry_price: object = 100.0,
) -> dict:
    return {
        "outcome": outcome,
        "pnl_usd": pnl_usd,
        "pnl_pct": pnl_pct,
        "qty": qty,
        "entry_price": entry_price,
    }


def test_cohort_stats_empty_ledger_is_zero_not_proven():
    out = pp._cohort_stats([])
    assert out["closed"] == 0
    assert out["wins"] == 0
    assert out["losses"] == 0
    assert out["breakeven"] == 0
    assert out["win_rate"] == 0.0
    assert out["profit_factor_usd"] == 0.0
    assert out["realized_pnl_usd"] == 0.0
    assert out["gross_entry_notional_usd"] == 0.0
    assert out["pnl_to_gross_entry_notional"] == 0.0
    assert out["return_on_deployed_capital"] == 0.0
    assert out["win_rate_ci_95"] == (0.0, 0.0)


def test_cohort_stats_breakeven_is_in_denominator_not_a_win():
    out = pp._cohort_stats(
        [
            _row(outcome="WIN", pnl_usd=10.0, pnl_pct=0.10),
            _row(outcome="BREAKEVEN", pnl_usd=0.0, pnl_pct=0.0),
        ]
    )
    assert out["closed"] == 2
    assert out["wins"] == 1
    assert out["losses"] == 0
    assert out["breakeven"] == 1
    assert out["win_rate"] == 0.5


def test_cohort_stats_profit_factor_usd_bands():
    wins_only = pp._cohort_stats([_row(outcome="WIN", pnl_usd=8.0, pnl_pct=0.08)])
    assert wins_only["profit_factor_usd"] == float("inf")

    losses_only = pp._cohort_stats([_row(outcome="LOSS", pnl_usd=-4.0, pnl_pct=-0.04)])
    assert losses_only["profit_factor_usd"] == 0.0

    mixed = pp._cohort_stats(
        [
            _row(outcome="WIN", pnl_usd=8.0, pnl_pct=0.08, qty=1, entry_price=100),
            _row(outcome="LOSS", pnl_usd=-4.0, pnl_pct=-0.04, qty=1, entry_price=100),
        ]
    )
    assert mixed["profit_factor_usd"] == pytest.approx(2.0)
    assert mixed["realized_pnl_usd"] == pytest.approx(4.0)
    assert mixed["gross_entry_notional_usd"] == pytest.approx(200.0)
    assert mixed["pnl_to_gross_entry_notional"] == pytest.approx(0.02)
    assert mixed["return_on_deployed_capital"] == mixed["pnl_to_gross_entry_notional"]


def test_cohort_stats_cost_sensitivity_is_notional_times_bps():
    out = pp._cohort_stats([_row(outcome="WIN", pnl_usd=5.0, pnl_pct=0.01, qty=2, entry_price=250)])
    assert out["gross_entry_notional_usd"] == pytest.approx(500.0)
    assert out["cost_sensitivity_20bp_usd"] == pytest.approx(1.0)
    assert out["cost_sensitivity_50bp_usd"] == pytest.approx(2.5)


def test_cohort_stats_missing_and_non_numeric_money_is_zero_not_invented():
    out = pp._cohort_stats(
        [
            _row(outcome="WIN", pnl_usd=None, pnl_pct=None, qty=None, entry_price=None),
            _row(outcome="LOSS", pnl_usd=True, pnl_pct=True, qty=True, entry_price=True),
            _row(
                outcome="WIN",
                pnl_usd=float("nan"),
                pnl_pct=float("inf"),
                qty=0,
                entry_price=50,
            ),
        ]
    )
    assert out["realized_pnl_usd"] == 0.0
    assert out["sum_trade_returns"] == 0.0
    assert out["gross_entry_notional_usd"] == 0.0
    assert out["profit_factor_usd"] == 0.0
    assert math.isfinite(out["realized_pnl_usd"])


def test_cohort_stats_sum_trade_returns_is_not_a_portfolio_return():
    out = pp._cohort_stats(
        [
            _row(outcome="WIN", pnl_usd=50.0, pnl_pct=0.50, qty=1, entry_price=100),
            _row(outcome="LOSS", pnl_usd=-5.0, pnl_pct=-0.05, qty=10, entry_price=100),
        ]
    )
    # Raw sum of per-trade returns, not capital-weighted.
    assert out["sum_trade_returns"] == pytest.approx(0.45)
    assert out["total_pnl_pct"] == out["sum_trade_returns"]
    # Helper rounds the capital-weighted ratio to 4 dp (45 / 1100 → 0.0409).
    assert out["pnl_to_gross_entry_notional"] == 0.0409
