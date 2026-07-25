"""GATE 3 — `reconciles` is a COMPUTED contract, never an unconditional literal.

`model_portfolio.publish()` previously emitted `reconciles: True` by construction.
The equity identity (total == positions + cash) does hold by construction for
FINITE inputs — but the literal also claimed it for NaN/inf and missing prices.
`publish()` now runs the SAME holdings through the canonical valuation and copies
a real verdict. Expectations here are computed independently of that helper.
"""
from __future__ import annotations

import pytest

import model_portfolio
import portfolio_valuation


def _payload(holdings, cash, *, reported_total=None):
    eq = {"portfolio_equity": sum(h.get("equity") or 0 for h in holdings
                                  if isinstance(h.get("equity"), (int, float))),
          "cash": cash}
    if reported_total is not None:
        eq["total"] = reported_total
    return {"as_of": "2026-07-24T00:00:00+00:00", "source": "robinhood_mcp",
            "holdings": holdings, "equity_summary": eq, "watchlist": []}


def _h(sym="NVDA", qty=10.0, price=100.0, equity=1000.0):
    return {"symbol": sym, "quantity": qty, "current_price": price, "equity": equity,
            "portfolio_percentage": 100.0, "type": "stock"}


# ─── unit: the verdict, independently checked ────────────────────────────────

def test_exact_reconciliation_is_true():
    out = model_portfolio.publish(_payload([_h()], 500.0))
    assert out["reconciles"] is True
    assert out["valuation_status"] == "reconciled"
    assert out["degraded_reason"] is None
    # independent check of the identity
    assert out["calculated_total_equity"] == pytest.approx(1000.0 + 500.0)


@pytest.mark.parametrize("mutate,exp_status,exp_reason", [
    (lambda p: p["holdings"][0].update(equity=float("nan")), "unavailable", "non_finite_value"),
    (lambda p: p["holdings"][0].update(current_price=float("inf")), "unavailable", "non_finite_value"),
    (lambda p: p["equity_summary"].update(cash=float("nan")), "unavailable", "non_finite_value"),
    (lambda p: p["holdings"][0].update(current_price=None), "degraded", "missing_price"),
    (lambda p: p["holdings"][0].update(current_price=0), "degraded", "missing_price"),
])
def test_degraded_and_non_finite_inputs_never_claim_reconciled(mutate, exp_status, exp_reason):
    p = _payload([_h()], 500.0)
    mutate(p)
    out = model_portfolio.publish(p)
    assert out["reconciles"] is False, f"claimed reconciled for {exp_reason}"
    assert out["valuation_status"] == exp_status
    assert out["degraded_reason"] == exp_reason


def test_broker_mismatch_but_valid_components_still_reconciles():
    """A reported_total that disagrees is source degradation of the EQUITY
    SUMMARY (handled server-side), not a failure of the published identity —
    publish() derives its own total from the rows, so it reconciles."""
    out = model_portfolio.publish(_payload([_h()], 500.0, reported_total=99999.0))
    assert out["reconciles"] is True
    assert out["calculated_total_equity"] == pytest.approx(1500.0)   # derived, not 99999


def test_cash_only_portfolio_reconciles():
    out = model_portfolio.publish(_payload([], 2500.0))
    assert out["reconciles"] is True and out["valuation_status"] == "reconciled"


def test_verdict_matches_an_independent_canonical_valuation():
    """Cross-check publish()'s verdict against a hand-built canonical valuation."""
    holdings = [_h("A", 5, 20.0, 100.0), _h("B", 3, 50.0, 150.0)]
    positions = [portfolio_valuation.Position.build(h["symbol"], h["quantity"],
                                                    current_price=h["current_price"])
                 for h in holdings]
    val = portfolio_valuation.value_positions(positions, cash=300.0)
    out = model_portfolio.publish(_payload(holdings, 300.0))
    assert out["reconciles"] == (val.reconciles and not val.unpriced_symbols)


def test_publish_never_raises_on_garbage():
    """A valuation that cannot be evaluated is an 'unavailable' verdict, not a 500."""
    out = model_portfolio.publish(_payload([{"symbol": "X", "quantity": "abc",
                                             "current_price": 1.0, "equity": 1.0}], 0.0))
    assert out["reconciles"] is False
    assert out["valuation_status"] in ("unavailable", "degraded")


# ─── dormant publication composition ─────────────────────────────────────────


def test_dormant_publication_degraded_payload_does_not_claim_reconciled():
    body = model_portfolio.publish(_payload([_h(price=float("nan"))], 500.0))
    assert body["reconciles"] is False, "publication claimed a NaN-priced book reconciles"
    assert body["valuation_status"] == "unavailable"


def test_dormant_publication_valid_payload_reconciles():
    body = model_portfolio.publish(_payload([_h()], 500.0))
    assert body["reconciles"] is True and body["valuation_status"] == "reconciled"


# ─── downstream: a degraded valuation blocks portfolio analysis ──────────────

def test_downstream_analysis_refuses_a_non_reconciling_book():
    """_assert_valuation_sound gates the mybook analysis on the canonical
    valuation — a degraded book must raise, not be analysed as if valid."""
    import server
    positions = [portfolio_valuation.Position.build("A", 10, current_price=100.0)]
    val = portfolio_valuation.value_positions(positions, cash=0)
    payload = {"holdings": [{"symbol": "A", "equity": 1000.0}],
               "valuation": {**val.as_dict(), "reconciles": False,
                             "issues": ["total_equity 1 != positions 2 + cash 3"]}}
    with pytest.raises(server.ValuationIntegrityError):
        server._assert_valuation_sound(payload, where="test")


def test_downstream_analysis_allows_a_reconciling_book():
    import server
    positions = [portfolio_valuation.Position.build("A", 10, current_price=100.0)]
    val = portfolio_valuation.value_positions(positions, cash=0)
    payload = {"holdings": [{"symbol": "A", "equity": 1000.0}], "valuation": val.as_dict()}
    server._assert_valuation_sound(payload, where="test")   # must not raise
    assert val.reconciles is True
