"""First execution tests for paper_trading.py — session gates, stops, verdicts.

The daily chain's paper book is the public proof of signal accuracy. These
tests pin: non-executable rows are dropped, allocation blocks are recorded
(not filled), Auto-trade is ignored in paper, stop/target math, long vs short
auto-close, and the paper-vs-live edge verdicts that gate scaling live size.
"""

from __future__ import annotations

import sys
from types import ModuleType

import pytest

# paper_trading imports price_source (pandas) and allocation_config (gesh_core).
# CI has both. This cloud image often has neither — stub only the missing
# modules so collection still works, and never replace an already-imported
# real module (would poison a full-suite run).
if "price_source" not in sys.modules:
    try:
        import pandas  # noqa: F401
    except ModuleNotFoundError:
        _ps = ModuleType("price_source")
        _ps.locked_yf_download = lambda *a, **k: None
        sys.modules["price_source"] = _ps

if "gesh_core" not in sys.modules:
    try:
        import gesh_core  # noqa: F401
    except ModuleNotFoundError:
        _ac = ModuleType("gesh_core.allocation_config")
        _ac.load_config = lambda: {}
        _ac.validate_action = lambda *a, **k: (True, "")
        _gc = ModuleType("gesh_core")
        _gc.allocation_config = _ac
        sys.modules["gesh_core"] = _gc
        sys.modules["gesh_core.allocation_config"] = _ac

import paper_trading as pt  # noqa: E402
import trade_log as tl  # noqa: E402

pytestmark = pytest.mark.unit


@pytest.fixture
def trade_db(tmp_path, monkeypatch):
    path = tmp_path / "paper_trading_isolated.db"
    monkeypatch.setattr(tl, "DB_PATH", path)
    return path


@pytest.fixture
def allow_all(monkeypatch):
    monkeypatch.setattr(pt, "load_config", lambda: {})
    monkeypatch.setattr(pt, "validate_action", lambda *a, **k: (True, ""))


def _plan(symbol="NVDA", action="BUY", shares=10.0, price=100.0, reason="test"):
    return {
        "symbol": symbol,
        "action": action,
        "shares": shares,
        "current_price": price,
        "signal_reason": reason,
    }


# ─── run_paper_session filters ────────────────────────────────────────────────


def test_hold_and_blank_actions_are_skipped(trade_db, allow_all):
    out = pt.run_paper_session(
        [_plan(action="HOLD"), _plan(action=""), _plan(action="WATCH")],
        holdings=[],
        total_portfolio=100_000,
    )
    assert out == []
    assert tl.get_trade_history(is_paper=True) == []


def test_zero_shares_or_price_never_logs(trade_db, allow_all):
    out = pt.run_paper_session(
        [_plan(shares=0), _plan(price=0), _plan(shares=-1)],
        holdings=[],
        total_portfolio=100_000,
    )
    assert out == []
    assert tl.get_trade_history(is_paper=True) == []


def test_allocation_block_is_recorded_not_filled(trade_db, monkeypatch):
    monkeypatch.setattr(pt, "load_config", lambda: {})
    monkeypatch.setattr(pt, "validate_action", lambda *a, **k: (False, "Max $ per trade exceeded"))
    out = pt.run_paper_session([_plan()], holdings=[], total_portfolio=100_000)
    assert len(out) == 1
    assert out[0]["blocked"] is True
    assert out[0]["reason"] == "Max $ per trade exceeded"
    assert "trade_id" not in out[0]
    assert tl.get_trade_history(is_paper=True) == []


def test_auto_trade_gate_is_ignored_in_paper(trade_db, monkeypatch):
    """Paper always runs — the Auto-trade live gate must not block a sim fill."""
    monkeypatch.setattr(pt, "load_config", lambda: {})
    monkeypatch.setattr(pt, "validate_action", lambda *a, **k: (False, "Auto-trade is disabled"))
    out = pt.run_paper_session([_plan()], holdings=[], total_portfolio=50_000)
    assert len(out) == 1
    assert out[0]["blocked"] is False
    assert out[0]["trade_id"]
    assert tl.get_trade_by_id(out[0]["trade_id"], is_paper=True)["outcome"] == "OPEN"


def test_buy_stop_and_target_use_default_percents(trade_db, allow_all):
    out = pt.run_paper_session([_plan(price=100.0)], holdings=[], total_portfolio=1)
    row = out[0]
    assert row["stop_loss"] == pytest.approx(95.0)  # 5% below
    assert row["target_price"] == pytest.approx(112.0)  # 12% above
    stored = tl.get_trade_by_id(row["trade_id"], is_paper=True)
    assert stored["stop_loss"] == pytest.approx(95.0)
    assert stored["target_price"] == pytest.approx(112.0)


def test_sell_stop_and_target_are_inverted(trade_db, allow_all):
    out = pt.run_paper_session([_plan(action="SELL", price=100.0)], holdings=[], total_portfolio=1)
    row = out[0]
    assert row["stop_loss"] == pytest.approx(105.0)  # 5% above (short)
    assert row["target_price"] == pytest.approx(88.0)  # 12% below


def test_symbol_is_uppercased_on_the_paper_row(trade_db, allow_all):
    out = pt.run_paper_session([_plan(symbol="hood")], holdings=[], total_portfolio=1)
    assert out[0]["symbol"] == "HOOD"
    assert tl.get_trade_by_id(out[0]["trade_id"], is_paper=True)["symbol"] == "HOOD"


def test_second_buy_sees_incremented_open_position_count(trade_db, monkeypatch):
    seen: list[int] = []

    def _va(action, symbol, trade_usd, open_positions=0, **kw):
        seen.append(open_positions)
        return True, ""

    monkeypatch.setattr(pt, "load_config", lambda: {})
    monkeypatch.setattr(pt, "validate_action", _va)
    holdings = [{"symbol": "CASH", "equity": 0}, {"symbol": "SPY", "equity": 500}]
    pt.run_paper_session(
        [_plan(symbol="AAA"), _plan(symbol="BBB")],
        holdings=holdings,
        total_portfolio=10_000,
    )
    # One live holding with equity>0, then first BUY increments before the second.
    assert seen == [1, 2]


# ─── Auto-close ───────────────────────────────────────────────────────────────


def test_long_hits_stop_and_records_loss(trade_db, allow_all, monkeypatch):
    created = pt.run_paper_session([_plan(price=100.0)], holdings=[], total_portfolio=1)
    monkeypatch.setattr(pt, "_fetch_current_prices", lambda _syms: {"NVDA": 94.0})
    closed = pt.check_and_close_paper_positions()
    assert len(closed) == 1
    assert closed[0]["outcome"] == "LOSS"
    assert "Stop hit" in (closed[0]["notes"] or "")
    assert tl.get_trade_by_id(created[0]["trade_id"], is_paper=True)["outcome"] == "LOSS"


def test_long_hits_target_and_records_win(trade_db, allow_all, monkeypatch):
    pt.run_paper_session([_plan(price=100.0)], holdings=[], total_portfolio=1)
    monkeypatch.setattr(pt, "_fetch_current_prices", lambda _syms: {"NVDA": 112.0})
    closed = pt.check_and_close_paper_positions()
    assert len(closed) == 1
    assert closed[0]["outcome"] == "WIN"
    assert "Target hit" in (closed[0]["notes"] or "")


def test_short_hits_stop_when_price_rises(trade_db, allow_all, monkeypatch):
    pt.run_paper_session([_plan(action="SELL", price=100.0)], holdings=[], total_portfolio=1)
    monkeypatch.setattr(pt, "_fetch_current_prices", lambda _syms: {"NVDA": 105.0})
    closed = pt.check_and_close_paper_positions()
    assert len(closed) == 1
    assert closed[0]["outcome"] == "LOSS"
    assert "Stop hit" in (closed[0]["notes"] or "")


def test_short_hits_target_when_price_falls(trade_db, allow_all, monkeypatch):
    pt.run_paper_session([_plan(action="SELL", price=100.0)], holdings=[], total_portfolio=1)
    monkeypatch.setattr(pt, "_fetch_current_prices", lambda _syms: {"NVDA": 88.0})
    closed = pt.check_and_close_paper_positions()
    assert len(closed) == 1
    assert closed[0]["outcome"] == "WIN"
    assert "Target hit" in (closed[0]["notes"] or "")


def test_missing_price_leaves_position_open(trade_db, allow_all, monkeypatch):
    created = pt.run_paper_session([_plan()], holdings=[], total_portfolio=1)
    monkeypatch.setattr(pt, "_fetch_current_prices", lambda _syms: {})
    assert pt.check_and_close_paper_positions() == []
    assert tl.get_trade_by_id(created[0]["trade_id"], is_paper=True)["outcome"] == "OPEN"


def test_price_inside_band_does_not_close(trade_db, allow_all, monkeypatch):
    created = pt.run_paper_session([_plan(price=100.0)], holdings=[], total_portfolio=1)
    monkeypatch.setattr(pt, "_fetch_current_prices", lambda _syms: {"NVDA": 100.0})
    assert pt.check_and_close_paper_positions() == []
    assert tl.get_trade_by_id(created[0]["trade_id"], is_paper=True)["outcome"] == "OPEN"


def test_stop_boundary_is_inclusive_for_longs(trade_db, allow_all, monkeypatch):
    """price <= stop closes a long — equality must not leave the position open."""
    pt.run_paper_session([_plan(price=100.0)], holdings=[], total_portfolio=1)
    monkeypatch.setattr(pt, "_fetch_current_prices", lambda _syms: {"NVDA": 95.0})
    closed = pt.check_and_close_paper_positions()
    assert len(closed) == 1
    assert "Stop hit" in (closed[0]["notes"] or "")


# ─── Summary verdicts ─────────────────────────────────────────────────────────


def test_paper_edge_above_five_recommends_scaling(trade_db, monkeypatch):
    monkeypatch.setattr(
        pt,
        "get_win_rate",
        lambda days=7, is_paper=False: {
            "win_rate_pct": 70.0 if is_paper else 60.0,
            "total_trades": 10,
            "total_realized_pnl": 0,
        },
    )
    monkeypatch.setattr(pt, "get_trade_history", lambda **k: [])
    summary = pt.get_paper_summary(days=7)
    assert summary["paper_edge"] == pytest.approx(10.0)
    assert "consider scaling" in summary["verdict"].lower()


def test_paper_edge_below_minus_five_blocks_scaling(trade_db, monkeypatch):
    monkeypatch.setattr(
        pt,
        "get_win_rate",
        lambda days=7, is_paper=False: {
            "win_rate_pct": 40.0 if is_paper else 50.0,
            "total_trades": 10,
            "total_realized_pnl": 0,
        },
    )
    monkeypatch.setattr(pt, "get_trade_history", lambda **k: [])
    summary = pt.get_paper_summary(days=7)
    assert summary["paper_edge"] == pytest.approx(-10.0)
    assert "do not increase auto-trade" in summary["verdict"].lower()


def test_paper_edge_inside_band_is_similar(trade_db, monkeypatch):
    monkeypatch.setattr(
        pt,
        "get_win_rate",
        lambda days=7, is_paper=False: {
            "win_rate_pct": 52.0 if is_paper else 50.0,
            "total_trades": 4,
            "total_realized_pnl": 0,
        },
    )
    monkeypatch.setattr(pt, "get_trade_history", lambda **k: [])
    summary = pt.get_paper_summary(days=7)
    assert summary["paper_edge"] == pytest.approx(2.0)
    assert "similarly" in summary["verdict"].lower()


def test_close_paper_position_writes_to_paper_table(trade_db, allow_all):
    created = pt.run_paper_session([_plan(price=100.0)], holdings=[], total_portfolio=1)
    row = pt.close_paper_position(created[0]["trade_id"], exit_price=108.0, notes="manual")
    assert row["outcome"] == "WIN"
    assert row["realized_pnl"] == pytest.approx(80.0)
    assert tl.get_win_rate(is_paper=False)["total_trades"] == 0


def test_price_fetch_exception_returns_empty_map(monkeypatch):
    """A Yahoo/RH outage must skip the stop check — never invent a close price."""

    def _boom(*_a, **_k):
        raise RuntimeError("feed down")

    monkeypatch.setattr(pt.price_source, "locked_yf_download", _boom)
    assert pt._fetch_current_prices(["NVDA"]) == {}
    assert pt._fetch_current_prices([]) == {}
