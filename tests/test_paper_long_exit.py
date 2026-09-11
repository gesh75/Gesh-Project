"""GFD-008: TRIM/SELL close longs; P&L is (exit − entry) × shares."""

from __future__ import annotations

import pytest

import paper_trading
import trade_log


@pytest.fixture
def sandbox_db(tmp_path, monkeypatch):
    path = tmp_path / "paper.db"
    monkeypatch.setattr(trade_log, "DB_PATH", path)
    monkeypatch.setattr(trade_log, "_initialized_paths", set())
    monkeypatch.setattr(
        paper_trading,
        "validate_action",
        lambda *a, **k: (True, "ok"),
    )
    return path


def _buy(symbol="HOOD", shares=10.0, price=100.0):
    return paper_trading.run_paper_session(
        [{"symbol": symbol, "action": "BUY", "shares": shares, "current_price": price}],
        holdings=[],
        total_portfolio=10_000,
    )


def test_sell_closes_long_with_positive_pnl(sandbox_db):
    opened = _buy()
    assert opened[0]["blocked"] is False
    closed = paper_trading.run_paper_session(
        [{"symbol": "HOOD", "action": "SELL", "shares": 10, "current_price": 110}],
        holdings=[],
        total_portfolio=10_000,
    )
    assert closed[0]["blocked"] is False
    assert closed[0]["closed"] is True
    assert closed[0]["realized_pnl"] == pytest.approx(100.0)
    assert closed[0]["outcome"] == "WIN"
    still_open = trade_log.get_trade_history(open_only=True, is_paper=True)
    assert still_open == []


def test_trim_partial_keeps_remainder(sandbox_db):
    _buy(shares=10, price=100)
    trimmed = paper_trading.run_paper_session(
        [{"symbol": "HOOD", "action": "TRIM", "shares": 4, "current_price": 110}],
        holdings=[],
        total_portfolio=10_000,
    )
    assert trimmed[0]["realized_pnl"] == pytest.approx(40.0)
    open_rows = trade_log.get_trade_history(open_only=True, is_paper=True)
    assert len(open_rows) == 1
    assert open_rows[0]["shares"] == pytest.approx(6.0)
    assert open_rows[0]["action"] in ("BUY", "ADD")


def test_sell_without_open_long_is_blocked(sandbox_db):
    out = paper_trading.run_paper_session(
        [{"symbol": "NVDA", "action": "SELL", "shares": 5, "current_price": 120}],
        holdings=[],
        total_portfolio=10_000,
    )
    assert out[0]["blocked"] is True
    assert "No open paper long" in out[0]["reason"]


def test_close_trade_always_uses_long_pnl(sandbox_db):
    """Even a leftover SELL-labeled row closes as a long (GFD-008)."""
    tid = trade_log.log_trade(
        symbol="HOOD",
        action="SELL",
        shares=10,
        entry_price=100,
        is_paper=True,
    )
    rec = trade_log.close_trade(tid, exit_price=110, is_paper=True)
    assert rec["realized_pnl"] == pytest.approx(100.0)
    assert rec["outcome"] == "WIN"


def test_buy_stop_is_below_entry(sandbox_db):
    opened = _buy(price=100)
    assert opened[0]["stop_loss"] == pytest.approx(95.0)
    assert opened[0]["target_price"] == pytest.approx(112.0)
