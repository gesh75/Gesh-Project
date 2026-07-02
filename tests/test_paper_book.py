"""Paper-book position ledger (audit PR1) + stop persistence (audit PR2).

The paper book must be reconstructed from SIMULATED paper fills in `orders` —
NOT from the real Robinhood holdings in the snapshot. Before this fix a paper
SELL sized against the real account (where the symbol was never held), always
collapsed to qty=0/SKIPPED, and no paper position could ever close.

All DB writes go to a sandboxed temp file (trading repo rule #39).
"""
from __future__ import annotations

import types

import pytest

from trading import execution, state
from trading.config import RiskConfig
from trading.contract import manual
from trading.risk_gate import GateVerdict


@pytest.fixture
def sandbox_db(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "DB_PATH", tmp_path / "test_state.db")
    state.init_trading_tables()
    return state


def _fill(st, sym: str, action: str, qty: float, price: float, status: str = "SIMULATED"):
    st.log_order(
        intent_id=1, decision_id=None, mode="paper",
        client_order_id=f"t-{sym}-{action}-{qty}-{price}-{status}", status=status,
        symbol=sym, action=action, qty=qty, target_price=price,
        fill_price=price, fill_qty=qty,
    )


def _snap(equity: float, watch_sym: str, watch_price: float) -> dict:
    return {
        "equity_summary": {"portfolio_equity": equity},
        "holdings": [],
        "watchlist": [{"symbol": watch_sym, "last_trade_price": watch_price}],
    }


# ─── paper_open_positions ─────────────────────────────────────────────────────

def test_paper_open_positions_nets_buys_and_sells(sandbox_db):
    _fill(sandbox_db, "HOOD", "BUY", 10.0, 100.0)
    _fill(sandbox_db, "HOOD", "SELL", 4.0, 110.0)
    pos = sandbox_db.paper_open_positions()
    assert pos["HOOD"]["qty"] == pytest.approx(6.0)
    assert pos["HOOD"]["avg_cost"] == pytest.approx(100.0)  # average-cost method


def test_fully_closed_position_disappears(sandbox_db):
    _fill(sandbox_db, "HOOD", "BUY", 5.0, 100.0)
    _fill(sandbox_db, "HOOD", "SELL", 5.0, 90.0)
    assert "HOOD" not in sandbox_db.paper_open_positions()


def test_rejected_skipped_and_error_rows_create_no_position(sandbox_db):
    _fill(sandbox_db, "NVDA", "BUY", 3.0, 100.0, status="REJECTED")
    _fill(sandbox_db, "NVDA", "BUY", 3.0, 100.0, status="SKIPPED")
    _fill(sandbox_db, "NVDA", "BUY", 3.0, 100.0, status="ERROR")
    assert sandbox_db.paper_open_positions() == {}


# ─── _estimate_qty must read the paper book in paper mode ─────────────────────

def test_paper_sell_sizes_from_paper_book_not_real_holdings(sandbox_db):
    _fill(sandbox_db, "HOOD", "BUY", 5.0, 100.0)
    cfg = RiskConfig(paper_mode=True, paper_cash_usd=100_000.0)
    intent = types.SimpleNamespace(action="SELL", symbol="HOOD", target_position_pct=0.0)
    qty, price = execution._estimate_qty(intent, _snap(10_000.0, "HOOD", 120.0), cfg)
    assert price == 120.0
    assert qty == pytest.approx(5.0)  # full close of the PAPER position


def test_paper_buy_delta_counts_existing_paper_position(sandbox_db):
    _fill(sandbox_db, "HOOD", "BUY", 5.0, 100.0)  # paper equity @120 = $600
    cfg = RiskConfig(paper_mode=True, paper_cash_usd=100_000.0)
    intent = types.SimpleNamespace(action="BUY", symbol="HOOD", target_position_pct=0.10)
    qty, _ = execution._estimate_qty(intent, _snap(10_000.0, "HOOD", 120.0), cfg)
    # target $1000, already $600 paper → buy the $400 delta only
    assert qty == pytest.approx(400.0 / 120.0, abs=1e-4)


def test_live_mode_still_sizes_from_real_holdings(sandbox_db):
    _fill(sandbox_db, "XYZ", "BUY", 99.0, 1.0)  # paper book must be IGNORED live
    cfg = RiskConfig(paper_mode=False)
    snapshot = {
        "equity_summary": {"portfolio_equity": 10_000.0},
        "holdings": [{"symbol": "XYZ", "quantity": 5.0, "current_price": 160.0, "equity": 800.0}],
    }
    intent = types.SimpleNamespace(action="SELL", symbol="XYZ", target_position_pct=0.0)
    qty, _ = execution._estimate_qty(intent, snapshot, cfg)
    assert qty == pytest.approx(5.0)


# ─── stop persistence ─────────────────────────────────────────────────────────

def test_record_and_deactivate_stops(sandbox_db):
    sandbox_db.record_stop(symbol="HOOD", entry_price=100.0, stop_price=95.0,
                           take_profit_price=108.0, volatility_tier="MEDIUM")
    stops = sandbox_db.active_stops()
    assert stops["HOOD"]["stop_price"] == pytest.approx(95.0)
    sandbox_db.deactivate_stops("HOOD")
    assert "HOOD" not in sandbox_db.active_stops()


def test_paper_buy_fill_records_stop(sandbox_db, monkeypatch):
    monkeypatch.setattr(execution, "alert_order_fill", lambda *a, **k: None)
    cfg = RiskConfig(paper_mode=True, paper_cash_usd=100_000.0)
    intent = manual("HOOD", "BUY", confidence=0.9, target_position_pct=0.02)
    verdict = GateVerdict(approved=True, reason=None, gates={}, intent_id=1, decision_id=1)
    result = execution.place(intent, verdict, _snap(10_000.0, "HOOD", 100.0), cfg)
    assert result.status == "SIMULATED"
    stops = sandbox_db.active_stops()
    assert "HOOD" in stops
    assert stops["HOOD"]["stop_price"] < result.fill_price  # stop below entry
    assert stops["HOOD"]["take_profit_price"] > result.fill_price


def test_full_paper_close_retires_the_stop(sandbox_db, monkeypatch):
    monkeypatch.setattr(execution, "alert_order_fill", lambda *a, **k: None)
    cfg = RiskConfig(paper_mode=True, paper_cash_usd=100_000.0)
    buy = manual("HOOD", "BUY", confidence=0.9, target_position_pct=0.02)
    verdict = GateVerdict(approved=True, reason=None, gates={}, intent_id=1, decision_id=1)
    execution.place(buy, verdict, _snap(10_000.0, "HOOD", 100.0), cfg)
    assert "HOOD" in sandbox_db.active_stops()

    sell = manual("HOOD", "SELL", confidence=1.0, target_position_pct=0.0)
    result = execution.place(sell, verdict, _snap(10_000.0, "HOOD", 110.0), cfg)
    assert result.status == "SIMULATED"
    assert result.fill_qty == pytest.approx(result.qty)
    assert "HOOD" not in sandbox_db.active_stops()
    assert "HOOD" not in sandbox_db.paper_open_positions()
