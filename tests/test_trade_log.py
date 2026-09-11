"""First execution tests for trade_log.py — P&L, paper/live isolation, win-rate.

trade_log is the persistence layer for both live `trade_log` and `paper_trades`.
A sign error, a leaked paper row into live stats, or a ZeroDivision on a
corrupt $0 entry would silently invert the weekly review and the paper-vs-live
edge. These tests pin the money math and the table fence with a throwaway DB.

GFD-008 / PR #238 (APPROVED, still open): current ``main`` treats TRIM/SELL as
short entries (inverted P&L and inverted stops). #238 changes ``close_trade``
to long-only ``(exit - entry) * shares`` and makes paper TRIM/SELL FIFO-close
open longs. These short-path assertions encode today's contract and will need
to be rewritten when #238 lands — do not merge this file after #238 without
rebasing those tests.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import trade_log as tl
from gesh_core.db_path import resolve_db_path

pytestmark = pytest.mark.unit


@pytest.fixture
def trade_db(tmp_path, monkeypatch):
    """Point trade_log at a per-test sqlite file. Never touch gesh_financial.db."""
    path = tmp_path / "trade_log_isolated.db"
    monkeypatch.setattr(tl, "DB_PATH", path)
    # New path is not in _initialized_paths, so _ensure_schema still runs.
    return path


def _open(symbol="NVDA", action="BUY", shares=10.0, entry=100.0, *, paper=False, **kw):
    return tl.log_trade(
        symbol=symbol,
        action=action,
        shares=shares,
        entry_price=entry,
        stop_loss=kw.get("stop_loss"),
        target_price=kw.get("target_price"),
        scanner_source=kw.get("scanner_source", "daily-analysis"),
        signal_reason=kw.get("signal_reason", "test"),
        notes=kw.get("notes"),
        is_paper=paper,
    )


# ─── P&L direction ────────────────────────────────────────────────────────────


def test_long_buy_close_above_entry_is_win(trade_db):
    tid = _open(action="BUY", shares=10.0, entry=100.0)
    row = tl.close_trade(tid, exit_price=110.0)
    assert row["outcome"] == "WIN"
    assert row["realized_pnl"] == pytest.approx(100.0)  # (110-100)*10
    assert row["pnl_pct"] == pytest.approx(10.0)


def test_long_add_uses_same_direction_as_buy(trade_db):
    tid = _open(action="ADD", shares=4.0, entry=50.0)
    row = tl.close_trade(tid, exit_price=40.0)
    assert row["outcome"] == "LOSS"
    assert row["realized_pnl"] == pytest.approx(-40.0)
    assert row["pnl_pct"] == pytest.approx(-20.0)


def test_leftover_sell_row_closes_as_long(trade_db):
    """GFD-008: close_trade is long-only even if the open row was labeled SELL."""
    tid = _open(action="SELL", shares=10.0, entry=100.0)
    row = tl.close_trade(tid, exit_price=90.0)
    assert row["outcome"] == "LOSS"
    assert row["realized_pnl"] == pytest.approx(-100.0)  # (90-100)*10
    assert row["pnl_pct"] == pytest.approx(-10.0)


def test_leftover_trim_row_closes_as_long(trade_db):
    tid = _open(action="TRIM", shares=5.0, entry=20.0)
    row = tl.close_trade(tid, exit_price=22.0)
    assert row["outcome"] == "WIN"
    assert row["realized_pnl"] == pytest.approx(10.0)  # (22-20)*5
    assert row["pnl_pct"] == pytest.approx(10.0)


# ─── BREAKEVEN band and $0-entry guard ────────────────────────────────────────


def test_pnl_within_one_cent_is_breakeven(trade_db):
    """|pnl| <= 0.01 is BREAKEVEN — a rounding tick is not a win or a loss."""
    tid = _open(action="BUY", shares=1.0, entry=100.0)
    row = tl.close_trade(tid, exit_price=100.0)
    assert row["outcome"] == "BREAKEVEN"
    assert row["realized_pnl"] == pytest.approx(0.0)


def test_pnl_just_outside_band_is_loss(trade_db):
    tid = _open(action="BUY", shares=1.0, entry=100.0)
    row = tl.close_trade(tid, exit_price=99.98)  # pnl = -0.02 < -0.01
    assert row["outcome"] == "LOSS"
    assert row["realized_pnl"] == pytest.approx(-0.02)


def test_pnl_just_outside_band_is_win(trade_db):
    tid = _open(action="BUY", shares=1.0, entry=100.0)
    row = tl.close_trade(tid, exit_price=100.02)  # pnl = +0.02 > 0.01
    assert row["outcome"] == "WIN"
    assert row["realized_pnl"] == pytest.approx(0.02)


def test_pnl_inside_band_on_the_win_side_is_breakeven(trade_db):
    """Half-cent is inside |pnl| <= 0.01. Avoid exact 0.01 — float makes 100.01-100 > 0.01."""
    tid = _open(action="BUY", shares=1.0, entry=100.0)
    row = tl.close_trade(tid, exit_price=100.005)
    assert row["outcome"] == "BREAKEVEN"


def test_zero_entry_price_does_not_raise_and_pct_degrades_to_zero(trade_db):
    """Corrupt entry_price=0 must not ZeroDivisionError; dollar P&L still lands."""
    tid = _open(action="BUY", shares=8.0, entry=0.0)
    row = tl.close_trade(tid, exit_price=12.5)
    assert row["realized_pnl"] == pytest.approx(100.0)  # (12.5-0)*8
    assert row["pnl_pct"] == pytest.approx(0.0)
    assert row["outcome"] == "WIN"


def test_zero_entry_price_on_sell_labeled_row_also_degrades_pct(trade_db):
    tid = _open(action="SELL", shares=2.0, entry=0.0)
    row = tl.close_trade(tid, exit_price=5.0)
    # long-only: (5-0)*2 = +10 → WIN; pct guarded
    assert row["realized_pnl"] == pytest.approx(10.0)
    assert row["pnl_pct"] == pytest.approx(0.0)
    assert row["outcome"] == "WIN"


# ─── Paper / live fence ───────────────────────────────────────────────────────


def test_paper_row_never_appears_in_live_history(trade_db):
    """Tables have independent AUTOINCREMENT ids — isolate by symbol, not id."""
    _open(symbol="HOOD", paper=False)
    _open(symbol="CAT", paper=True)
    live_syms = {r["symbol"] for r in tl.get_trade_history(is_paper=False)}
    paper_syms = {r["symbol"] for r in tl.get_trade_history(is_paper=True)}
    assert live_syms == {"HOOD"}
    assert paper_syms == {"CAT"}


def test_paper_close_does_not_write_live_pnl(trade_db):
    paper_id = _open(symbol="CAT", action="BUY", shares=2.0, entry=200.0, paper=True)
    tl.close_trade(paper_id, exit_price=220.0, is_paper=True)
    live_stats = tl.get_win_rate(is_paper=False)
    paper_stats = tl.get_win_rate(is_paper=True)
    assert live_stats["total_trades"] == 0
    assert live_stats["total_realized_pnl"] == 0
    assert paper_stats["total_trades"] == 1
    assert paper_stats["wins"] == 1
    assert paper_stats["total_realized_pnl"] == pytest.approx(40.0)


def test_close_does_not_cross_tables_when_autoincrement_ids_collide(trade_db):
    """Both tables start at id=1 — is_paper must select the table, not the integer."""
    live_id = _open(symbol="HOOD", action="BUY", shares=2.0, entry=50.0, paper=False)
    paper_id = _open(symbol="CAT", action="BUY", shares=3.0, entry=80.0, paper=True)
    assert live_id == paper_id == 1
    tl.close_trade(live_id, exit_price=55.0, is_paper=False)
    paper_row = tl.get_trade_by_id(paper_id, is_paper=True)
    live_row = tl.get_trade_by_id(live_id, is_paper=False)
    assert paper_row["outcome"] == "OPEN"
    assert paper_row["realized_pnl"] is None
    assert live_row["outcome"] == "WIN"
    assert live_row["realized_pnl"] == pytest.approx(10.0)
    tl.close_trade(paper_id, exit_price=70.0, is_paper=True)
    assert tl.get_trade_by_id(live_id, is_paper=False)["realized_pnl"] == pytest.approx(10.0)
    assert tl.get_trade_by_id(paper_id, is_paper=True)["realized_pnl"] == pytest.approx(-30.0)
    assert tl.get_win_rate(is_paper=False)["total_trades"] == 1
    assert tl.get_win_rate(is_paper=True)["total_trades"] == 1


def test_symbol_is_uppercased_and_lookup_is_casefold(trade_db):
    tid = _open(symbol="nvda")
    row = tl.get_trade_by_id(tid)
    assert row["symbol"] == "NVDA"
    hist = tl.get_trade_history(symbol="nvda")
    assert [r["id"] for r in hist] == [tid]


def test_close_missing_id_raises(trade_db):
    with pytest.raises(ValueError, match="not found"):
        tl.close_trade(9999, exit_price=1.0)


# ─── Win-rate / scanner rollups ───────────────────────────────────────────────


def test_win_rate_excludes_open_rows_from_closed_stats(trade_db):
    _open(symbol="AAA")  # stays OPEN
    win_id = _open(symbol="BBB")
    tl.close_trade(win_id, exit_price=110.0)
    stats = tl.get_win_rate()
    assert stats["total_trades"] == 1
    assert stats["wins"] == 1
    assert stats["open_trades"] == 1
    assert stats["win_rate_pct"] == pytest.approx(100.0)


def test_win_rate_mixes_wins_losses_breakevens(trade_db):
    win_id = _open(symbol="W", shares=1.0, entry=10.0)
    loss_id = _open(symbol="L", shares=1.0, entry=10.0)
    even_id = _open(symbol="B", shares=1.0, entry=10.0)
    tl.close_trade(win_id, exit_price=12.0)  # +2 WIN
    tl.close_trade(loss_id, exit_price=8.0)  # -2 LOSS
    tl.close_trade(even_id, exit_price=10.0)  # 0 BREAKEVEN
    stats = tl.get_win_rate()
    assert stats["total_trades"] == 3
    assert stats["wins"] == 1
    assert stats["losses"] == 1
    assert stats["breakevens"] == 1
    assert stats["win_rate_pct"] == pytest.approx(33.3)
    assert stats["total_realized_pnl"] == pytest.approx(0.0)


def test_empty_book_win_rate_is_zeros_not_none(trade_db):
    stats = tl.get_win_rate()
    assert stats["total_trades"] == 0
    assert stats["win_rate_pct"] == 0
    assert stats["best_trade"] is None
    assert stats["worst_trade"] is None


def test_scanner_performance_splits_by_source(trade_db):
    a = _open(symbol="A", scanner_source="1HR-scan")
    b = _open(symbol="B", scanner_source="premarket")
    tl.close_trade(a, exit_price=110.0)
    tl.close_trade(b, exit_price=90.0)
    rows = {r["scanner_source"]: r for r in tl.get_scanner_performance()}
    assert rows["1HR-scan"]["wins"] == 1
    assert rows["1HR-scan"]["win_rate_pct"] == pytest.approx(100.0)
    assert rows["premarket"]["losses"] == 1
    assert rows["premarket"]["win_rate_pct"] == pytest.approx(0.0)


def test_close_appends_notes_rather_than_clobbering(trade_db):
    tid = _open(notes="entry-note")
    row = tl.close_trade(tid, exit_price=110.0, notes="Target hit")
    assert "entry-note" in (row["notes"] or "")
    assert "Target hit" in (row["notes"] or "")


# ── Astra R3: same resolver as state/db; no silent /tmp fallback ─────────────


def test_trade_log_resolver_is_shared_db_path():
    assert tl._resolve_db is resolve_db_path


def test_trade_log_honors_gesh_db_path(tmp_path, monkeypatch):
    target = tmp_path / "explicit_trades.db"
    monkeypatch.setenv("GESH_DB_PATH", str(target))
    assert tl._resolve_db() == target


def test_trade_log_unusable_path_does_not_fall_back_to_tmp(tmp_path, monkeypatch):
    target = tmp_path / "corrupt.db"
    target.write_bytes(b"not a sqlite database")
    monkeypatch.setenv("GESH_DB_PATH", str(target))
    with pytest.raises(sqlite3.DatabaseError):
        tl._resolve_db()
