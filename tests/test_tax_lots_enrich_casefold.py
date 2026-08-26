"""Wash-sale union must not miss a name because of ticker case.

``enrich()`` already uppercases report tickers and the losing-holdings walk.
The price map used to keep the holding's raw ``symbol``, so a lowercase
Robinhood/customer symbol plus an uppercase report ticker produced
``checked=0`` — the same false-clear this module exists to prevent.

Lives in its own file so it does not collide with open coverage PRs #247 / #250.
"""
from __future__ import annotations

from datetime import date

import tax_lots_enrich as T

TODAY = date(2026, 8, 21)


def _lot(lot_id, qty, cps, price, open_date):
    return {
        "lot_id": lot_id,
        "quantity": qty,
        "cost_per_share": cps,
        "cost_basis": round(qty * cps, 2),
        "open_date": open_date,
        "term": "st",
        "selectable": True,
        "current_price": price,
        "unrealized_usd": round((price - cps) * qty, 2),
        "unrealized_pct": round((price / cps - 1) * 100, 2),
        "days_held": None,
    }


def _recent_loader(_symbol, _price):
    return [_lot("a", 4, 110.0, 100.0, "2026-08-20")]


def test_enrich_report_ticker_matches_lowercase_holding():
    """Report NVDA + holding nvda must still be wash-checked."""
    report = {"eligible": [{"ticker": "NVDA", "loss_usd": -40.0}]}
    holdings = [{"symbol": "nvda", "price": 100.0, "average_buy_price": 110.0}]
    out = T.enrich(report, holdings, loader=_recent_loader, today=TODAY)
    assert "NVDA" in out["lots_by_symbol"]
    assert [w["ticker"] for w in out["purchase_wash_warnings"]] == ["NVDA"]
    assert out["lot_coverage"]["checked"] == 1


def test_enrich_lowercase_losing_holding_is_unioned_without_report_row():
    """A sub-threshold loser still has purchase-side wash exposure."""
    report = {"eligible": [], "in_wash_window": []}
    holdings = [{"symbol": "zs", "price": 100.0, "average_buy_price": 110.0}]
    out = T.enrich(report, holdings, loader=_recent_loader, today=TODAY)
    assert "ZS" in out["lots_by_symbol"]
    assert [w["ticker"] for w in out["purchase_wash_warnings"]] == ["ZS"]


def test_enrich_mixed_case_report_ticker_uses_uppercase_price_map():
    report = {"candidates": [{"symbol": "nVdA"}]}
    holdings = [{"symbol": "NVDA", "current_price": 100.0, "avg_cost": 90.0}]
    out = T.enrich(report, holdings, loader=_recent_loader, today=TODAY)
    assert "NVDA" in out["lots_by_symbol"]
    assert out["lot_coverage"]["checked"] == 1
