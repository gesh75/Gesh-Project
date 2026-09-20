"""First execution tests for paper_trading.py — session gates, stops, verdicts.

The daily chain's paper book is the public proof of signal accuracy. These
tests pin: non-executable rows are dropped, allocation blocks are recorded
(not filled), Auto-trade is ignored in paper, stop/target math, long vs short
auto-close, and the paper-vs-live edge verdicts that gate scaling live size.

GFD-008 / PR #238: SELL/TRIM inverted stops and short auto-close below encode
current ``main``. After #238 those actions FIFO-close longs and these tests
must be rewritten.
"""

from __future__ import annotations

import json
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
    monkeypatch.setattr(pt, "validate_action", lambda *a, **k: (False, "Max $ per trade exceeded"))
    out = pt.run_paper_session([_plan()], holdings=[], total_portfolio=100_000)
    assert len(out) == 1
    assert out[0]["blocked"] is True
    assert out[0]["reason"] == "Max $ per trade exceeded"
    assert "trade_id" not in out[0]
    assert tl.get_trade_history(is_paper=True) == []


def _paper_cfg(tmp_path, monkeypatch, **overrides):
    """Point paper_trading's real validate_action at an isolated config."""
    cfg = {
        "auto_trade": False,
        "max_stock_per_trade": 5_000.0,
        "max_options_per_trade": 2_000.0,
        "max_open_positions": 5,
        "min_cash_buffer": 500.0,
        "scanner_timeframe": "daily",
        "paper_mode": True,
    }
    cfg.update(overrides)
    path = tmp_path / "gesh_config.json"
    path.write_text(json.dumps(cfg))
    monkeypatch.setenv("GESH_CONFIG_PATH", str(path))
    return path


def test_auto_trade_off_does_not_block_valid_paper_fill(trade_db, tmp_path, monkeypatch):
    """Paper skips live permission only — an under-cap BUY still fills."""
    _paper_cfg(tmp_path, monkeypatch, auto_trade=False, max_stock_per_trade=5_000.0)
    out = pt.run_paper_session(
        [_plan(shares=2.0, price=100.0)],
        holdings=[],
        total_portfolio=50_000,
        current_cash=100_000,
    )
    assert len(out) == 1
    assert out[0].get("blocked") is not True
    assert out[0]["trade_id"]
    assert tl.get_trade_by_id(out[0]["trade_id"], is_paper=True)["outcome"] == "OPEN"


def test_paper_over_cap_blocked_when_auto_trade_off(trade_db, tmp_path, monkeypatch):
    """Astra R1: $200 vs $100 cap is rejected even with auto_trade OFF."""
    _paper_cfg(tmp_path, monkeypatch, auto_trade=False, max_stock_per_trade=100.0)
    out = pt.run_paper_session(
        [_plan(shares=2.0, price=100.0)],
        holdings=[],
        total_portfolio=10_000,
        current_cash=100_000,
    )
    assert len(out) == 1
    assert out[0]["blocked"] is True
    assert "exceeds max" in out[0]["reason"]
    assert "trade_id" not in out[0]
    assert tl.get_trade_history(is_paper=True) == []


def test_paper_over_cap_blocked_when_auto_trade_on(trade_db, tmp_path, monkeypatch):
    _paper_cfg(tmp_path, monkeypatch, auto_trade=True, max_stock_per_trade=100.0)
    out = pt.run_paper_session(
        [_plan(shares=2.0, price=100.0)],
        holdings=[],
        total_portfolio=10_000,
        current_cash=100_000,
    )
    assert out[0]["blocked"] is True
    assert "exceeds max" in out[0]["reason"]
    assert tl.get_trade_history(is_paper=True) == []


def test_paper_position_cap_blocked_when_auto_trade_off(trade_db, tmp_path, monkeypatch):
    """Cap applies to open *paper* lots, not the live holdings list."""
    _paper_cfg(tmp_path, monkeypatch, auto_trade=False, max_open_positions=2)
    seeded = pt.run_paper_session(
        [_plan(symbol="AAA", shares=1.0, price=10.0),
         _plan(symbol="BBB", shares=1.0, price=10.0)],
        holdings=[],
        total_portfolio=10_000,
        current_cash=100_000,
    )
    assert all(not r.get("blocked") for r in seeded)
    out = pt.run_paper_session(
        [_plan(symbol="CCC", shares=1.0, price=10.0)],
        holdings=[],
        total_portfolio=10_000,
        current_cash=100_000,
    )
    assert out[0]["blocked"] is True
    assert "max open positions" in out[0]["reason"]
    assert "trade_id" not in out[0]


def test_paper_buy_fills_when_live_book_exceeds_position_cap(
    trade_db, tmp_path, monkeypatch,
):
    """Live names must not consume the paper session's open-position cap."""
    _paper_cfg(tmp_path, monkeypatch, auto_trade=False, max_open_positions=2)
    live = [{"symbol": f"L{i}", "equity": 1.0} for i in range(8)]
    out = pt.run_paper_session(
        [_plan(shares=1.0, price=10.0)],
        holdings=live,
        total_portfolio=10_000,
        current_cash=100_000,
    )
    assert len(out) == 1
    assert out[0].get("blocked") is not True
    assert out[0]["trade_id"]
    assert tl.get_trade_by_id(out[0]["trade_id"], is_paper=True)["outcome"] == "OPEN"


def test_paper_cash_floor_blocked_when_auto_trade_off(trade_db, tmp_path, monkeypatch):
    _paper_cfg(tmp_path, monkeypatch, auto_trade=False, min_cash_buffer=500.0)
    out = pt.run_paper_session(
        [_plan(shares=1.0, price=100.0)],
        holdings=[],
        total_portfolio=10_000,
        current_cash=550.0,
    )
    assert out[0]["blocked"] is True
    assert "minimum buffer" in out[0]["reason"]
    assert tl.get_trade_history(is_paper=True) == []


def test_paper_session_buys_share_one_cash_buffer(trade_db, tmp_path, monkeypatch):
    """Each BUY must see remaining cash, not the session's starting cash.

    $600 cash − $80 first BUY leaves $520. A second $80 BUY would leave $440,
    below the $500 floor. Independently checking both against $600 would fill
    both and overspend the buffer.
    """
    _paper_cfg(tmp_path, monkeypatch, auto_trade=False, min_cash_buffer=500.0)
    out = pt.run_paper_session(
        [
            _plan(symbol="AAA", shares=1.0, price=80.0),
            _plan(symbol="BBB", shares=1.0, price=80.0),
        ],
        holdings=[],
        total_portfolio=10_000,
        current_cash=600.0,
    )
    assert len(out) == 2
    assert out[0].get("blocked") is not True
    assert out[0]["trade_id"]
    assert out[1]["blocked"] is True
    assert "minimum buffer" in out[1]["reason"]
    assert "trade_id" not in out[1]
    open_rows = tl.get_trade_history(open_only=True, is_paper=True)
    assert [r["symbol"] for r in open_rows] == ["AAA"]


def test_paper_session_does_not_spend_trim_proceeds(trade_db, tmp_path, monkeypatch):
    """Paper TRIM/SELL cash is unsettled — later BUYs cannot spend it."""
    _paper_cfg(tmp_path, monkeypatch, auto_trade=False, min_cash_buffer=500.0)
    out = pt.run_paper_session(
        [
            _plan(symbol="AAA", shares=1.0, price=80.0),
            _plan(symbol="AAA", action="SELL", shares=1.0, price=80.0),
            _plan(symbol="BBB", shares=1.0, price=80.0),
        ],
        holdings=[],
        total_portfolio=10_000,
        current_cash=600.0,
    )
    assert out[0].get("blocked") is not True
    assert out[1].get("closed") is True
    assert out[2]["blocked"] is True
    assert "minimum buffer" in out[2]["reason"]
    assert tl.get_trade_history(open_only=True, is_paper=True) == []


def test_buy_stop_and_target_use_default_percents(trade_db, allow_all):
    out = pt.run_paper_session([_plan(price=100.0)], holdings=[], total_portfolio=1)
    row = out[0]
    assert row["stop_loss"] == pytest.approx(95.0)  # 5% below
    assert row["target_price"] == pytest.approx(112.0)  # 12% above
    stored = tl.get_trade_by_id(row["trade_id"], is_paper=True)
    assert stored["stop_loss"] == pytest.approx(95.0)
    assert stored["target_price"] == pytest.approx(112.0)


def test_sell_without_open_long_is_blocked(trade_db, allow_all):
    """GFD-008: SELL does not open an inverted short."""
    out = pt.run_paper_session([_plan(action="SELL", price=100.0)], holdings=[], total_portfolio=1)
    row = out[0]
    assert row["blocked"] is True
    assert "No open paper long" in row["reason"]
    assert "stop_loss" not in row


def test_symbol_is_uppercased_on_the_paper_row(trade_db, allow_all):
    out = pt.run_paper_session([_plan(symbol="hood")], holdings=[], total_portfolio=1)
    assert out[0]["symbol"] == "HOOD"
    assert tl.get_trade_by_id(out[0]["trade_id"], is_paper=True)["symbol"] == "HOOD"


def test_second_buy_sees_incremented_open_position_count(trade_db, monkeypatch):
    seen: list[int] = []

    def _va(action, symbol, trade_usd, open_positions=0, **kw):
        seen.append(open_positions)
        return True, ""

    monkeypatch.setattr(pt, "validate_action", _va)
    holdings = [{"symbol": "CASH", "equity": 0}, {"symbol": "SPY", "equity": 500}]
    pt.run_paper_session(
        [_plan(symbol="AAA"), _plan(symbol="BBB")],
        holdings=holdings,
        total_portfolio=10_000,
    )
    # Paper book starts empty; first BUY increments before the second.
    # Live holdings (SPY) must not seed the paper cap.
    assert seen == [0, 1]


def test_full_trim_releases_session_position_cap(trade_db, tmp_path, monkeypatch):
    """A full TRIM must free the slot so a later BUY in the same plan can fill."""
    _paper_cfg(tmp_path, monkeypatch, auto_trade=False, max_open_positions=1)
    out = pt.run_paper_session(
        [
            _plan(symbol="AAA", shares=2.0, price=10.0),
            _plan(symbol="AAA", action="TRIM", shares=2.0, price=11.0),
            _plan(symbol="BBB", shares=1.0, price=10.0),
        ],
        holdings=[],
        total_portfolio=10_000,
        current_cash=100_000,
    )
    assert out[0].get("blocked") is not True
    assert out[1].get("closed") is True
    assert out[2].get("blocked") is not True
    assert out[2]["trade_id"]
    open_rows = tl.get_trade_history(open_only=True, is_paper=True)
    assert [r["symbol"] for r in open_rows] == ["BBB"]


def test_partial_trim_keeps_session_position_cap(trade_db, tmp_path, monkeypatch):
    """A partial TRIM leaves the name open — the cap slot stays occupied."""
    _paper_cfg(tmp_path, monkeypatch, auto_trade=False, max_open_positions=1)
    out = pt.run_paper_session(
        [
            _plan(symbol="AAA", shares=2.0, price=10.0),
            _plan(symbol="AAA", action="TRIM", shares=1.0, price=11.0),
            _plan(symbol="BBB", shares=1.0, price=10.0),
        ],
        holdings=[],
        total_portfolio=10_000,
        current_cash=100_000,
    )
    assert out[0].get("blocked") is not True
    assert out[1].get("closed") is True
    assert out[2]["blocked"] is True
    assert "max open positions" in out[2]["reason"]
    assert "trade_id" not in out[2]
    open_rows = tl.get_trade_history(open_only=True, is_paper=True)
    assert [r["symbol"] for r in open_rows] == ["AAA"]


def test_trim_with_no_open_long_does_not_decrement_position_cap(
    trade_db, tmp_path, monkeypatch,
):
    """A no-op TRIM must not free a slot the book never held."""
    _paper_cfg(tmp_path, monkeypatch, auto_trade=False, max_open_positions=1)
    seeded = pt.run_paper_session(
        [_plan(symbol="AAA", shares=1.0, price=10.0)],
        holdings=[],
        total_portfolio=10_000,
        current_cash=100_000,
    )
    assert seeded[0].get("blocked") is not True
    out = pt.run_paper_session(
        [
            _plan(symbol="ZZZ", action="TRIM", shares=1.0, price=10.0),
            _plan(symbol="BBB", shares=1.0, price=10.0),
        ],
        holdings=[],
        total_portfolio=10_000,
        current_cash=100_000,
    )
    assert out[0]["blocked"] is True
    assert "No open paper long" in out[0]["reason"]
    assert out[1]["blocked"] is True
    assert "max open positions" in out[1]["reason"]
    open_rows = tl.get_trade_history(open_only=True, is_paper=True)
    assert [r["symbol"] for r in open_rows] == ["AAA"]


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


def test_lone_sell_does_not_create_a_short_to_auto_close(trade_db, allow_all, monkeypatch):
    """A SELL with no open long is blocked; auto-close must not invent a short."""
    out = pt.run_paper_session([_plan(action="SELL", price=100.0)], holdings=[], total_portfolio=1)
    assert out[0]["blocked"] is True
    monkeypatch.setattr(pt, "_fetch_current_prices", lambda _syms: {"NVDA": 105.0})
    assert pt.check_and_close_paper_positions() == []
    monkeypatch.setattr(pt, "_fetch_current_prices", lambda _syms: {"NVDA": 88.0})
    assert pt.check_and_close_paper_positions() == []


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
