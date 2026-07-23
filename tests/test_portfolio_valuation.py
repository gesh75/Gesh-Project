"""Canonical valuation fixtures — the arithmetic the whole product hangs on.

Audit Q-02/Q-03 (2026-07-22): "Day change" was computed from cost basis
(rendering "+$271,639 (5920.4%)" on a test book) and the model portfolio's
headline total did not equal its own components. These fixtures pin the
definitions so neither can come back.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from portfolio_valuation import MONEY_TOLERANCE, Position, to_decimal, value_positions


def P(sym, qty, *, cost=None, price=None, prior=None):
    return Position.build(sym, qty, average_cost=cost, current_price=price, prior_close=prior)


# ─── boundary conversion ─────────────────────────────────────────────────────

def test_to_decimal_avoids_binary_drift():
    assert to_decimal(0.1) == Decimal("0.1")
    assert to_decimal(2.7971) == Decimal("2.7971")
    assert to_decimal("3.5") == Decimal("3.5")
    assert to_decimal(None) is None
    assert to_decimal(True) is None          # bool is not a quantity
    assert to_decimal("abc") is None
    assert to_decimal(float("nan")) is None
    assert to_decimal(float("inf")) is None


def test_fractional_quantities_are_exact():
    v = value_positions([P("A", "0.000001", price="100")], cash=0)
    assert v.positions_market_value == Decimal("0.00")
    # exact decimal product, not a float approximation of it
    v2 = value_positions([P("A", "2.7971", price="374.01")], cash=0)
    assert v2.positions_market_value == (Decimal("2.7971") * Decimal("374.01")).quantize(
        Decimal("0.01"))
    assert v2.positions_market_value == Decimal("1046.14")


# ─── the equity identity ─────────────────────────────────────────────────────

def test_total_equity_is_positions_plus_cash():
    v = value_positions([P("A", 10, price=100), P("B", 5, price=200)], cash=1000)
    assert v.positions_market_value == Decimal("2000.00")
    assert v.total_equity == Decimal("3000.00")
    assert v.reconciles


def test_empty_portfolio():
    v = value_positions([], cash=0)
    assert v.total_equity == Decimal("0.00")
    assert v.reconciles
    # 0.00 here is DERIVED (nothing held moves), not a stand-in for unknown —
    # the percentage, which genuinely cannot be formed, is the one that is None.
    assert v.day_change_amount == Decimal("0.00")
    assert v.day_change_percent is None
    assert any("prior-close equity is zero" in u for u in v.unavailable)


def test_cash_only_portfolio():
    """Cash does not move intraday, so 0.00 is a computed fact, not a fabrication."""
    v = value_positions([], cash=2500)
    assert v.total_equity == Decimal("2500.00")
    assert v.day_change_amount == Decimal("0.00")
    assert v.day_change_percent == Decimal("0.0000")


def test_broker_reported_total_is_evidence_not_override():
    """Audit Q-03: the model book showed a broker total ($100k after scaling)
    while its own components summed to less. The derived total wins; the
    difference is reported, never absorbed."""
    v = value_positions([P("A", 10, price=100)], cash=500, reported_total=1250)
    assert v.total_equity == Decimal("1500.00")          # derived, not 1250
    assert v.reported_total == Decimal("1250")
    assert v.reported_total_delta == Decimal("-250.00")
    assert any("broker-reported total" in i for i in v.issues)
    assert v.reconciles                                   # identity still holds


# ─── day change ──────────────────────────────────────────────────────────────

def test_day_change_uses_prior_close_not_cost_basis():
    """The exact defect: a position up 60x on cost but flat today."""
    v = value_positions([P("A", 100, cost=1, price=60, prior=60)], cash=0)
    assert v.day_change_amount == Decimal("0.00")
    assert v.day_change_percent == Decimal("0.0000")
    # …while the lifetime number is still available, separately and correctly
    assert v.unrealized_pnl == Decimal("5900.00")
    assert v.unrealized_pnl_percent == Decimal("5900.0000")


def test_positive_day():
    v = value_positions([P("A", 10, price=110, prior=100)], cash=0)
    assert v.prior_close_equity == Decimal("1000.00")
    assert v.day_change_amount == Decimal("100.00")
    assert v.day_change_percent == Decimal("10.0000")


def test_negative_day():
    v = value_positions([P("A", 10, price=90, prior=100)], cash=100)
    assert v.day_change_amount == Decimal("-100.00")
    assert v.day_change_percent == Decimal("-9.0909")


def test_zero_change_day():
    v = value_positions([P("A", 3, price=50, prior=50)], cash=0)
    assert v.day_change_amount == Decimal("0.00")
    assert v.day_change_percent == Decimal("0.0000")


def test_day_change_percent_always_matches_amount():
    v = value_positions(
        [P("A", 7.5, price=133.33, prior=120.11), P("B", 3, price=9.99, prior=10.55)],
        cash=812.34)
    implied = v.day_change_amount / v.prior_close_equity * 100
    assert abs(implied - v.day_change_percent) < Decimal("0.01")
    assert v.reconciles


def test_missing_prior_close_disables_day_change_rather_than_zeroing():
    v = value_positions([P("A", 10, price=110, prior=100), P("B", 1, price=50)], cash=0)
    assert v.day_change_amount is None
    assert v.day_change_percent is None
    assert any("day_change" in u and "B" in u for u in v.unavailable)


def test_zero_prior_close_equity_disables_percent():
    v = value_positions([P("A", 0.0000001, price=1, prior=0.0000001)], cash=0)
    assert v.day_change_percent is None
    assert any("prior-close equity is zero" in u for u in v.unavailable)


# ─── cost basis ──────────────────────────────────────────────────────────────

def test_missing_cost_basis_disables_returns():
    """Audit Q-04: avg_cost null/0 must not silently become a 0 cost basis
    (which reads as an infinite gain)."""
    v = value_positions([P("A", 10, price=100), P("B", 5, cost=50, price=60)], cash=0)
    assert v.total_cost_basis is None
    assert v.unrealized_pnl is None
    assert v.unrealized_pnl_percent is None
    assert any("cost basis" in u for u in v.unavailable)


def test_zero_cost_is_treated_as_absent_not_free():
    v = value_positions([P("A", 10, cost=0, price=100)], cash=0)
    assert v.total_cost_basis is None


def test_unrealized_pnl_when_complete():
    v = value_positions([P("A", 10, cost=90, price=100)], cash=0)
    assert v.total_cost_basis == Decimal("900.00")
    assert v.unrealized_pnl == Decimal("100.00")
    assert v.unrealized_pnl_percent == Decimal("11.1111")


# ─── unpriced handling ───────────────────────────────────────────────────────

def test_unpriced_positions_excluded_and_reported():
    v = value_positions([P("A", 10, price=100), P("ZZZZ", 5)], cash=0)
    assert v.positions_market_value == Decimal("1000.00")
    assert v.unpriced_symbols == ("ZZZZ",)
    assert any("unpriced" in i for i in v.issues)


def test_negative_or_zero_price_counts_as_unpriced():
    v = value_positions([P("A", 10, price=0), P("B", 10, price=-5)], cash=0)
    assert set(v.unpriced_symbols) == {"A", "B"}
    assert v.positions_market_value == Decimal("0.00")


# ─── invariants ──────────────────────────────────────────────────────────────

def test_invariant_failure_is_detectable():
    from dataclasses import replace
    good = value_positions([P("A", 10, price=100)], cash=0)
    broken = replace(good, total_equity=Decimal("9999.00"))
    assert not broken.reconciles
    assert broken.invariant_failures()


def test_as_dict_is_json_safe_and_flags_availability():
    v = value_positions([P("A", 10, price=100)], cash=0)
    d = v.as_dict()
    assert isinstance(d["total_equity"], float)
    assert d["day_change_available"] is False
    assert d["cost_basis_available"] is False
    assert d["reconciles"] is True


def test_large_book_stays_within_tolerance():
    positions = [P(f"S{i}", "3.333333", cost="11.11", price="17.77", prior="17.55")
                 for i in range(60)]
    v = value_positions(positions, cash="1234.56")
    assert v.reconciles
    assert abs(v.total_equity - (v.positions_market_value + v.cash)) <= MONEY_TOLERANCE


def test_bad_quantity_rejected_at_construction():
    with pytest.raises(ValueError):
        Position.build("A", "not-a-number")
