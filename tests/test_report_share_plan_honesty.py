"""Share-plan parsers must not invent size, dollars, or an APPROVED gate.

``report_share_plan._finite_float`` is the shared money reader for Kelly lots,
gate share-match, and paper-close P&amp;L. bool is a numeric subclass
(``float(True) == 1.0``) and inf/NaN are not measurements — they must not
become 1 share, $1, or an executable BUY.
"""

from __future__ import annotations

import pytest

from report_share_plan import (
    _finite_float,
    _fmt_shares,
    _gate_status,
    share_count,
    share_plan_html,
    share_plan_rows,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        ("x", None),
        (True, None),
        (False, None),
        (float("nan"), None),
        (float("inf"), None),
        (float("-inf"), None),
        (0.0, 0.0),
        (13, 13.0),
        ("4.25", 4.25),
    ],
)
def test_finite_float_rejects_bool_and_non_finite(value, expected):
    got = _finite_float(value)
    if expected is None:
        assert got is None
    else:
        assert got == expected


def test_fmt_shares_bool_and_inf_are_na_not_one():
    assert _fmt_shares(True) == "N/A"
    assert _fmt_shares(False) == "N/A"
    assert _fmt_shares(float("inf")) == "N/A"
    assert _fmt_shares(float("nan")) == "N/A"
    assert _fmt_shares(0.0) == "0"
    assert _fmt_shares(13) == "13"


def test_share_count_bool_delta_or_price_is_not_a_one_dollar_lot():
    shares, action = share_count(delta_usd=True, price=10.0, quantity=5)
    assert shares is None
    assert action == "HOLD"
    shares, action = share_count(delta_usd=100.0, price=True, quantity=5)
    assert shares is None
    assert action == "HOLD"
    shares, action = share_count(delta_usd=float("inf"), price=10.0, quantity=5)
    assert shares is None
    assert action == "HOLD"


def test_share_count_keeps_measured_zero_as_hold_zero():
    shares, action = share_count(delta_usd=0.0, price=41.78, quantity=23.7)
    assert shares == 0
    assert action == "HOLD"


def test_gate_status_bool_plan_shares_cannot_approve():
    row = {
        "symbol": "GTLB",
        "size_action": "ADD",
        "lot_action": "ADD",
        "shares": 13,
        "kelly_action": "ADD",
    }
    eng = {
        "action_plan": [{"symbol": "GTLB", "action": "BUY", "shares": True}],
        "gate_results": [{"symbol": "GTLB", "action": "BUY", "approved": True}],
    }
    status = _gate_status(row, eng)
    assert status == "not gated"
    assert "APPROVED" not in status


def test_gate_status_inf_plan_shares_cannot_approve():
    row = {
        "symbol": "GTLB",
        "size_action": "ADD",
        "lot_action": "ADD",
        "shares": 13,
        "kelly_action": "ADD",
    }
    eng = {
        "action_plan": [{"symbol": "GTLB", "action": "BUY", "shares": float("inf")}],
        "gate_results": [{"symbol": "GTLB", "action": "BUY", "approved": True}],
    }
    assert "APPROVED" not in _gate_status(row, eng)


def test_share_plan_html_bool_price_does_not_fabricate_add():
    eng = {
        "holdings": [{"symbol": "GTLB", "price": True, "quantity": 10, "equity": 400.0}],
        "equity_summary": {"total": 10000.0},
        "kelly": {
            "GTLB": {
                "action": "ADD",
                "current_pct": 4.0,
                "optimal_pct": 8.0,
                "delta_usd": 400.0,
            }
        },
        "action_plan": [],
        "gate_results": [],
    }
    rows = share_plan_rows(eng)
    assert rows[0]["shares"] is None
    assert rows[0]["size_action"] == "HOLD"
    html = share_plan_html(eng)
    gtlb = html[html.find("GTLB") : html.find("GTLB") + 400]
    assert "APPROVED" not in html
    assert ">HOLD<" in gtlb
    assert ">ADD<" not in gtlb


def test_finite_float_overflow_token_is_unknown():
    assert _finite_float("1e999") is None
    assert _finite_float("nan") is None
