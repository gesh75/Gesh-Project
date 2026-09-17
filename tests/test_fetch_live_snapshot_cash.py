"""Tier-3 robin_stocks snapshot cash math (Gold BP must never become deployable).

``fetch_live_snapshot.fetch()`` is the last-resort daily-chain path when MCP
fails. Kelly, the paper gate, and the report size from
``equity_summary.deployable`` / ``buying_power`` (the back-compat alias). If
Gold ``account['buying_power']`` leaks into those keys, live sizing uses
margin. The helper is the same arithmetic ``fetch()`` writes.
"""

from __future__ import annotations

import inspect

import fetch_live_snapshot
from fetch_live_snapshot import equity_summary_from_rh


def _account(**overrides) -> dict:
    row = {
        "portfolio_cash": "8948.00",
        "buying_power": "21632.00",
        "uncleared_deposits": "0",
        "cash": "-1200.00",  # Gold margin-accounting figure — must be ignored
    }
    row.update(overrides)
    return row


def _profile(**overrides) -> dict:
    row = {
        "withdrawable_amount": "0",
        "equity": "50000.00",
        "adjusted_equity_previous_close": "49800.00",
    }
    row.update(overrides)
    return row


def test_gold_leveraged_buying_power_never_becomes_deployable():
    summary = equity_summary_from_rh(_account(), _profile(), positions_equity=41052.0)
    assert summary["deployable"] == 8948.0
    assert summary["cash"] == 8948.0
    assert summary["buying_power"] == 8948.0
    assert summary["settled_cash"] == 8948.0
    assert summary["buying_power_leveraged"] == 21632.0
    assert summary["margin_buying_power"] == 21632.0
    assert summary["total"] == 50000.0


def test_leveraged_bp_below_settled_caps_deployable():
    summary = equity_summary_from_rh(
        _account(portfolio_cash="10000", buying_power="5000"),
        _profile(),
        positions_equity=0.0,
    )
    assert summary["deployable"] == 5000.0
    assert summary["buying_power"] == 5000.0
    assert summary["settled_cash"] == 10000.0
    assert summary["buying_power_leveraged"] == 5000.0


def test_zero_buying_power_does_not_zero_settled_cash():
    summary = equity_summary_from_rh(
        _account(portfolio_cash="8948", buying_power="0"),
        _profile(),
        positions_equity=100.0,
    )
    assert summary["deployable"] == 8948.0
    assert summary["buying_power_leveraged"] == 0.0


def test_pending_deposits_are_not_added_to_deployable():
    summary = equity_summary_from_rh(
        _account(uncleared_deposits="750.50"),
        _profile(),
        positions_equity=0.0,
    )
    assert summary["pending_deposits"] == 750.50
    assert summary["uncleared_deposits"] == 750.50
    assert summary["deployable"] == 8948.0


def test_withdrawable_fallback_only_when_settled_cash_missing():
    summary = equity_summary_from_rh(
        _account(portfolio_cash="0", buying_power="0"),
        _profile(withdrawable_amount="2000"),
        positions_equity=0.0,
    )
    assert summary["settled_cash"] == 2000.0
    assert summary["deployable"] == 2000.0
    assert summary["withdrawable_amount"] == 2000.0


def test_fetch_writes_helper_summary():
    source = inspect.getsource(fetch_live_snapshot.fetch)
    assert "equity_summary_from_rh" in source
    assert "equity_summary': equity_summary" in source


def test_account_cash_margin_figure_is_ignored():
    summary = equity_summary_from_rh(
        _account(portfolio_cash="8948", cash="-99999"),
        _profile(),
        positions_equity=0.0,
    )
    assert summary["deployable"] == 8948.0
    assert summary["cash"] == 8948.0
