"""Guard tests for lot-level tax-harvest enrichment.

The critical tests are `test_unavailable_lots_never_read_as_no_wash_risk` and
`test_below_threshold_positions_are_still_checked`. Both pin the same failure
mode: a false all-clear. A harvester that says "no wash warnings" because it
could not look, or because the base report silently dropped the row, is worse
than one that says nothing.
"""
from __future__ import annotations

from datetime import date

import pytest

import tax_lots_enrich as T


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


TODAY = date(2026, 8, 21)


# ── recent_purchases ─────────────────────────────────────────────────────────
def test_recent_purchases_flags_inside_window():
    lots = [_lot("a", 4, 100.0, 95.0, "2026-08-20"), _lot("b", 4, 100.0, 95.0, "2026-01-01")]
    got = T.recent_purchases(lots, today=TODAY)
    assert [x["lot_id"] for x in got] == ["a"]
    assert got[0]["days_ago"] == 1


def test_recent_purchases_boundary_is_inclusive_at_30_days():
    lots = [_lot("a", 1, 100.0, 95.0, "2026-07-22"),   # exactly 30d
            _lot("b", 1, 100.0, 95.0, "2026-07-21")]   # 31d — outside
    got = [x["lot_id"] for x in T.recent_purchases(lots, today=TODAY)]
    assert got == ["a"]


def test_recent_purchases_ignores_unparseable_dates():
    assert T.recent_purchases([_lot("a", 1, 100.0, 95.0, "not-a-date")], today=TODAY) == []


# ── harvest_plan ─────────────────────────────────────────────────────────────
def test_harvest_plan_selects_only_losing_lots():
    lots = [_lot("win", 4, 90.0, 100.0, "2026-01-01"),
            _lot("lose", 2, 120.0, 100.0, "2026-01-01")]
    plan = T.harvest_plan(lots)
    assert plan["lot_ids"] == ["lose"]
    assert plan["loss_usd"] == pytest.approx(-40.0)


def test_harvest_plan_excludes_non_selectable_lots():
    lots = [_lot("x", 2, 120.0, 100.0, "2026-01-01", selectable=False)]
    assert T.harvest_plan(lots)["lot_ids"] == []


def test_harvest_plan_splits_short_and_long_term():
    lots = [_lot("s", 1, 120.0, 100.0, "2026-06-01", term="st"),
            _lot("l", 1, 130.0, 100.0, "2024-01-01", term="lt")]
    plan = T.harvest_plan(lots)
    assert plan["short_term_loss_usd"] == pytest.approx(-20.0)
    assert plan["long_term_loss_usd"] == pytest.approx(-30.0)


def test_harvest_plan_on_empty_input_is_zero_not_error():
    plan = T.harvest_plan([])
    assert plan["lot_count"] == 0 and plan["loss_usd"] == 0


# ── symbol_lot_view degradation ──────────────────────────────────────────────
def test_unavailable_lots_never_read_as_no_wash_risk():
    """The false-clear this module exists to prevent."""
    v = T.symbol_lot_view("NVDA", 100.0, loader=lambda s, p: [], today=TODAY)
    assert v["lot_data"] == T.UNAVAILABLE
    assert "wash_risk_from_purchases" not in v, (
        "an unavailable view must not carry a wash-risk verdict at all — "
        "absent is honest, False is a lie"
    )


def test_loader_exception_degrades_to_unavailable():
    def boom(_s, _p):
        raise RuntimeError("VPN reset")

    v = T.symbol_lot_view("NVDA", 100.0, loader=boom, today=TODAY)
    assert v["lot_data"] == T.UNAVAILABLE and "VPN reset" in v["reason"]


def test_available_view_reports_days_until_safe():
    lots = [_lot("a", 4, 110.0, 100.0, "2026-08-14")]      # 7d ago
    v = T.symbol_lot_view("ZS", 100.0, loader=lambda s, p: lots, today=TODAY)
    assert v["lot_data"] == T.OK
    assert v["wash_risk_from_purchases"] is True
    assert v["wash_risk_days_remaining"] == 23


def test_old_lots_carry_no_purchase_wash_risk():
    lots = [_lot("a", 4, 110.0, 100.0, "2026-05-11")]
    v = T.symbol_lot_view("NVDA", 100.0, loader=lambda s, p: lots, today=TODAY)
    assert v["wash_risk_from_purchases"] is False
    assert v["wash_risk_days_remaining"] == 0


# ── enrich ───────────────────────────────────────────────────────────────────
def _holdings():
    return [
        {"symbol": "ZS", "price": 100.0, "average_buy_price": 110.0},    # losing
        {"symbol": "MA", "price": 100.0, "average_buy_price": 90.0},     # winning
    ]


def test_below_threshold_positions_are_still_checked():
    """get_harvest_summary drops BELOW_THRESHOLD rows; enrich must not miss them."""
    report = {"eligible": [], "in_wash_window": [], "no_cost_basis": [],
              "total_harvestable_loss_usd": 0.0}
    lots = [_lot("a", 4, 110.0, 100.0, "2026-08-20")]
    out = T.enrich(report, _holdings(), loader=lambda s, p: lots, today=TODAY)
    assert "ZS" in out["lots_by_symbol"], "a sub-$500 loss still has wash exposure"
    assert [w["ticker"] for w in out["purchase_wash_warnings"]] == ["ZS"]


def test_enrich_skips_profitable_holdings():
    report = {"eligible": [], "in_wash_window": []}
    out = T.enrich(report, _holdings(), loader=lambda s, p: [_lot("a", 1, 110.0, 100.0, "2026-08-20")],
                   today=TODAY)
    assert "MA" not in out["lots_by_symbol"]


def test_enrich_is_additive_and_never_mutates_base_report():
    report = {"eligible": [], "in_wash_window": [], "total_harvestable_loss_usd": 0.0,
              "summary": "Tax harvest: $0 eligible across 0 position(s)."}
    snapshot = dict(report)
    out = T.enrich(report, _holdings(), loader=lambda s, p: [], today=TODAY)
    assert report == snapshot, "input report must not be mutated"
    assert out["total_harvestable_loss_usd"] == 0.0
    assert out["summary"] == snapshot["summary"]
    assert out is not report


def test_lot_coverage_reports_a_ratio_not_a_bare_count():
    """A total MCP outage must be visible, not indistinguishable from 'all clear'."""
    report = {"eligible": [], "in_wash_window": []}
    out = T.enrich(report, _holdings(), loader=lambda s, p: [], today=TODAY)
    cov = out["lot_coverage"]
    assert cov == {"checked": 1, "available": 0, "unavailable": 1}
    assert out["purchase_wash_warnings"] == []      # empty because unknown, and coverage says so


def test_enrich_tolerates_garbage_cost_basis():
    holdings = [{"symbol": "ZS", "price": 100.0, "average_buy_price": "n/a"}]
    out = T.enrich({"eligible": []}, holdings, loader=lambda s, p: [], today=TODAY)
    assert out["lot_coverage"]["checked"] == 0


def test_enrich_passes_through_non_dict_report():
    assert T.enrich(None, _holdings()) is None
