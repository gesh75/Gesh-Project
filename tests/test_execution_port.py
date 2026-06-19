"""Pins the execution safeguards ported from the engine repo (Phase 1.5, Codex C1):
delta-to-target BUY sizing and SELL trim-to-target (no phantom full-close).
Pure _estimate_qty calls — no DB, no network.
"""
from __future__ import annotations

import types

import pytest

from trading import execution


def _snap(equity, sym, qty, price, sym_equity):
    return {
        "equity_summary": {"portfolio_equity": equity},
        "holdings": [{"symbol": sym, "quantity": qty, "current_price": price, "equity": sym_equity}],
    }


def test_buy_uses_delta_to_target_not_full_target():
    # equity 10k, target 10% -> $1000; already hold $400 -> buy only the $600 delta
    intent = types.SimpleNamespace(action="BUY", symbol="XYZ", target_position_pct=0.10)
    qty, price = execution._estimate_qty(intent, _snap(10_000.0, "XYZ", 4.0, 100.0, 400.0))
    assert qty == pytest.approx(6.0, abs=1e-3)   # 600/100, not 1000/100


def test_sell_at_or_below_target_is_noop():
    intent = types.SimpleNamespace(action="SELL", symbol="XYZ", target_position_pct=0.10)
    qty, _ = execution._estimate_qty(intent, _snap(10_000.0, "XYZ", 5.0, 160.0, 800.0))
    assert qty == 0.0   # was a full-position close in the old product copy


def test_sell_full_close_when_target_zero():
    intent = types.SimpleNamespace(action="SELL", symbol="XYZ", target_position_pct=0.0)
    qty, _ = execution._estimate_qty(intent, _snap(10_000.0, "XYZ", 5.0, 160.0, 800.0))
    assert qty == pytest.approx(5.0, abs=1e-3)   # 800/160 -> genuine full close
