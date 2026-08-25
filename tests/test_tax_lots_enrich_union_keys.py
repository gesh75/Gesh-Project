"""Cover the report-side union keys `enrich()` walks that the original
`test_tax_lots_enrich.py` suite never exercised.

`get_harvest_summary()` typically discards `below_threshold` / `candidates`,
but `enrich()` still unions those keys when a producer *does* populate them.
A profitable mark today does not erase purchase-side wash exposure on a row
the base scan already surfaced — dropping it is the same false-clear the
module exists to prevent.

Lives in its own file so it does not collide with open coverage PR #247
(which edits `tests/test_tax_lots_enrich.py`).
"""
from __future__ import annotations

from datetime import date

import tax_lots_enrich as T

TODAY = date(2026, 8, 21)


def _lot(lot_id, qty, cps, price, open_date, term="st", selectable=True):
    return {
        "lot_id": lot_id, "quantity": qty, "cost_per_share": cps,
        "cost_basis": round(qty * cps, 2), "open_date": open_date,
        "term": term, "selectable": selectable,
        "current_price": price,
        "unrealized_usd": round((price - cps) * qty, 2),
        "unrealized_pct": round((price / cps - 1) * 100, 2),
        "days_held": None,
    }


def _recent_loader(_symbol, _price):
    return [_lot("a", 4, 110.0, 100.0, "2026-08-20")]


def test_below_threshold_report_row_is_checked_even_when_now_profitable():
    """A row the harvester kept as below-threshold still has a 30-day window."""
    report = {
        "eligible": [],
        "in_wash_window": [],
        "below_threshold": [{"ticker": "NVDA", "loss_usd": -80.0}],
    }
    holdings = [{"symbol": "NVDA", "price": 120.0, "average_buy_price": 100.0}]
    out = T.enrich(report, holdings, loader=_recent_loader, today=TODAY)
    assert "NVDA" in out["lots_by_symbol"]
    assert [w["ticker"] for w in out["purchase_wash_warnings"]] == ["NVDA"]
    assert out["lot_coverage"]["checked"] == 1


def test_candidates_report_row_is_checked_even_when_now_profitable():
    report = {
        "eligible": [],
        "candidates": [{"symbol": "ZS", "loss_usd": -40.0}],
    }
    holdings = [{"symbol": "ZS", "price": 130.0, "average_buy_price": 100.0}]
    out = T.enrich(report, holdings, loader=_recent_loader, today=TODAY)
    assert "ZS" in out["lots_by_symbol"]
    assert [w["ticker"] for w in out["purchase_wash_warnings"]] == ["ZS"]


def test_report_ticker_without_a_priced_holding_is_not_invented():
    """Union is not a free-for-all: no price → cannot form a lot view."""
    report = {"below_threshold": [{"ticker": "GHOST"}]}
    out = T.enrich(report, [], loader=_recent_loader, today=TODAY)
    assert out["lots_by_symbol"] == {}
    assert out["lot_coverage"] == {"checked": 0, "available": 0, "unavailable": 0}


def test_union_keys_do_not_mutate_the_base_report():
    report = {
        "eligible": [],
        "below_threshold": [{"ticker": "NVDA"}],
        "total_harvestable_loss_usd": 0.0,
    }
    snapshot = {
        "eligible": [],
        "below_threshold": [{"ticker": "NVDA"}],
        "total_harvestable_loss_usd": 0.0,
    }
    holdings = [{"symbol": "NVDA", "price": 120.0, "average_buy_price": 100.0}]
    out = T.enrich(report, holdings, loader=_recent_loader, today=TODAY)
    assert report == snapshot
    assert out["total_harvestable_loss_usd"] == 0.0
    assert out["below_threshold"] == snapshot["below_threshold"]
