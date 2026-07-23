"""`content_version()` must identify a composition, not a spelling of it."""
from __future__ import annotations

from decimal import Decimal

import pytest

from model_portfolio import content_version as V


def _h(sym, qty):
    return [{"symbol": sym, "quantity": qty}]


@pytest.mark.parametrize("a,b", [
    (_h("aapl", 1), _h(" AAPL ", 1)),
    (_h("A", 1), _h("A", 1.0)),
    (_h("A", 1), _h("A", "1.0000")),
    (_h("A", 1), _h("A", Decimal("1.0000"))),
    (_h("A", 100), _h("A", Decimal("1E+2"))),
    (_h("A", 5) + _h("A", 5), _h("A", 10)),
    (_h("A", 1) + _h("B", 2), _h("B", 2) + _h("A", 1)),
    (_h("A", 1) + _h("Z", 0), _h("A", 1)),
])
def test_equivalent_compositions_share_a_version(a, b):
    assert V(a, 0) == V(b, 0)


@pytest.mark.parametrize("cash_a,cash_b", [(100, "100.00"), (0, -0.0),
                                           (0, Decimal("0.000")), (2.50, "2.5")])
def test_equivalent_cash_shares_a_version(cash_a, cash_b):
    assert V(_h("A", 1), cash_a) == V(_h("A", 1), cash_b)


@pytest.mark.parametrize("a,b", [
    (_h("A", 1), _h("A", 2)),
    (_h("A", 1), _h("B", 1)),
    (_h("A", 1), _h("A", 1) + _h("B", 1)),
    (_h("A", 1) + _h("B", 1), _h("A", 1)),
])
def test_different_compositions_differ(a, b):
    assert V(a, 0) != V(b, 0)


def test_cash_is_in_scope_and_changes_the_version():
    """Documented scope: composition AND cash."""
    assert V(_h("A", 1), 100) != V(_h("A", 1), 200)


@pytest.mark.parametrize("bad", [-1, float("nan"), float("inf"), "abc", None])
def test_invalid_quantity_is_rejected_not_silently_hashed(bad):
    with pytest.raises(ValueError):
        V(_h("A", bad), 0)


def test_missing_symbol_is_rejected():
    with pytest.raises(ValueError):
        V([{"symbol": "  ", "quantity": 1}], 0)


def test_version_is_stable_across_calls_and_shaped_as_documented():
    a = V(_h("A", 1), 0)
    assert a == V(_h("A", 1), 0)
    assert a.startswith("v") and len(a) == 13


def test_docstring_does_not_claim_editorial_approval():
    assert "NOT an approval" in V.__doc__
    assert "composition" in V.__doc__.lower()
