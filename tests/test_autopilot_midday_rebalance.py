"""First-execution tests for trading.autopilot.midday_rebalance().

The 12:30 ET job emits intents when |current − Kelly target| > 5 percentage
points. The comparison is strict ``> 0.05`` on fractions (10% vs 15% is
exactly 5pp and must stay quiet). A missing ``portfolio_percentage`` is derived
from equity / portfolio_equity; if that is unknowable the symbol is skipped. HOLD / missing-target
intents must not be coerced to flatten-to-zero; SELL's genuine ``0.0`` still
fires.

Fully offline: login, snapshot, consensus, gate, and broker are stubbed.
"""

from __future__ import annotations

import types

import pytest

pytest.importorskip("gesh_core", reason="gesh-core not installed in this environment")

from trading import autopilot
from trading.contract import TradeIntent

pytestmark = pytest.mark.unit


def _snapshot(pct=10.0, symbol="MSTR") -> dict:
    holding = {
        "symbol": symbol,
        "equity": 10_000.0,
        "average_buy_price": 250.0,
        "current_price": 300.0,
    }
    if pct is not None:
        holding["portfolio_percentage"] = pct
    return {
        "as_of": "2026-06-03T16:30:00+00:00",
        "equity_summary": {"portfolio_equity": 100_000.0},
        "holdings": [holding],
        "watchlist": [],
    }


def _drive(monkeypatch, snapshot, target_pct, *, approved=True, action="BUY"):
    placed: list[TradeIntent] = []

    def fake_from_consensus(row, max_position_pct=0.02):
        return TradeIntent(
            symbol=row.get("symbol") or "MSTR",
            action=action,
            confidence=0.9,
            source="consensus",
            target_position_pct=target_pct,
        )

    monkeypatch.setattr(autopilot, "_ensure_logged_in", lambda: True)
    monkeypatch.setattr(autopilot, "_load_data_fetcher", lambda: (lambda _: snapshot))
    monkeypatch.setattr(
        autopilot, "_load_consensus", lambda: (lambda *a, **k: {"symbol": "MSTR"})
    )
    monkeypatch.setattr(autopilot, "from_consensus", fake_from_consensus)
    monkeypatch.setattr(
        autopilot,
        "evaluate",
        lambda intent, snap, cfg=None: types.SimpleNamespace(
            approved=approved, reason="ok" if approved else "rejected"
        ),
    )
    monkeypatch.setattr(autopilot, "place", lambda intent, *a, **k: placed.append(intent))
    autopilot.midday_rebalance()
    return placed


def test_drift_below_five_percent_does_not_place(monkeypatch):
    placed = _drive(monkeypatch, _snapshot(10.0), target_pct=0.14)
    assert placed == []


def test_drift_above_five_percent_places_once(monkeypatch):
    placed = _drive(monkeypatch, _snapshot(10.0), target_pct=0.16)
    assert len(placed) == 1
    assert placed[0].symbol == "MSTR"
    assert placed[0].target_position_pct == 0.16


def test_drift_exactly_five_percent_is_within_tolerance(monkeypatch):
    placed = _drive(monkeypatch, _snapshot(10.0), target_pct=0.15)
    assert placed == []


def test_missing_portfolio_percentage_is_derived_from_equity(monkeypatch):
    """M-06: absent weight is UNKNOWN, not 0%. Derive it: 10k / 100k = 10%."""
    # target 14% vs derived 10% -> 4pp drift -> quiet (the old "0%" read as 14pp)
    assert _drive(monkeypatch, _snapshot(pct=None), target_pct=0.14) == []
    # target 20% vs derived 10% -> 10pp drift -> places
    assert len(_drive(monkeypatch, _snapshot(pct=None), target_pct=0.20)) == 1


@pytest.mark.parametrize("bad_total", [None, 0.0, -1.0, float("nan")])
def test_unknowable_weight_skips_instead_of_trading_against_zero(monkeypatch, bad_total):
    snap = _snapshot(pct=None)
    snap["equity_summary"]["portfolio_equity"] = bad_total
    assert _drive(monkeypatch, snap, target_pct=0.20) == []


@pytest.mark.parametrize("bad_pct", ["n/a", float("nan"), True])
def test_garbage_portfolio_percentage_falls_back_to_derived_weight(monkeypatch, bad_pct):
    placed = _drive(monkeypatch, _snapshot(pct=bad_pct), target_pct=0.14)
    assert placed == []  # derived 10% vs 14% = 4pp, not 14pp


def test_blank_symbol_is_skipped(monkeypatch):
    snap = _snapshot(10.0)
    snap["holdings"][0]["symbol"] = ""
    placed = _drive(monkeypatch, snap, target_pct=0.20)
    assert placed == []


def test_rejected_verdict_still_reaches_place(monkeypatch):
    placed = _drive(monkeypatch, _snapshot(10.0), target_pct=0.20, approved=False)
    assert len(placed) == 1


def test_no_holdings_is_noop(monkeypatch):
    snap = _snapshot(10.0)
    snap["holdings"] = []
    placed = _drive(monkeypatch, snap, target_pct=0.20)
    assert placed == []


def test_auth_failure_never_places(monkeypatch):
    placed: list = []
    monkeypatch.setattr(autopilot, "_ensure_logged_in", lambda: False)
    monkeypatch.setattr(autopilot, "_load_data_fetcher", lambda: (lambda _: _snapshot()))
    monkeypatch.setattr(autopilot, "_load_consensus", lambda: (lambda *a, **k: {}))
    monkeypatch.setattr(autopilot, "place", lambda *a, **k: placed.append(a))
    autopilot.midday_rebalance()
    assert placed == []


def test_hold_with_missing_target_does_not_place(monkeypatch):
    """HOLD leaves target_position_pct=None. Coercing that to 0.0 would treat a
    10% holding as 10pp of flatten drift, write a qty-0 SIMULATED HOLD, and
    consume the daily order cap."""
    placed = _drive(monkeypatch, _snapshot(10.0), target_pct=None, action="HOLD")
    assert placed == []


def test_buy_without_target_does_not_place(monkeypatch):
    placed = _drive(monkeypatch, _snapshot(10.0), target_pct=None, action="BUY")
    assert placed == []


def test_sell_flatten_to_zero_still_places(monkeypatch):
    """SELL's genuine target 0.0 is not the HOLD missing-target case."""
    placed = _drive(monkeypatch, _snapshot(10.0), target_pct=0.0, action="SELL")
    assert len(placed) == 1
    assert placed[0].action == "SELL"
    assert placed[0].target_position_pct == 0.0
