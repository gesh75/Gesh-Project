"""
tests/test_proof_page.py — public signal-accuracy proof page generator.

The proof page is the #1 sales asset for the Sep-2026 alpha: a public, read-only
track-record of paper-trade signal accuracy. It must render HONESTLY at 0 closed
trades ("building track record, day X of 180") and auto-fill as the close-scan
accumulates closed trades in paper_outcomes.

No network. Reads a temp SQLite paper_outcomes table.
"""

from __future__ import annotations

import sqlite3
import sys
import threading
import time
import types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import proof_page


def test_spy_cache_refresh_is_single_flight_and_time_bounded(monkeypatch):
    calls = []

    class FakeTicker:
        def __init__(self, symbol):
            assert symbol == "SPY"

        def history(self, **kwargs):
            calls.append(kwargs)
            time.sleep(0.02)
            raise TimeoutError

    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(Ticker=FakeTicker))
    proof_page._SPY_CACHE.clear()

    start = threading.Barrier(8)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(
            lambda _: (start.wait(), proof_page._spy_return_since("2026-05-21"))[1],
            range(8),
        ))

    assert results == [None] * 8
    assert len(calls) == 1
    assert calls[0]["timeout"] == proof_page._SPY_REQUEST_TIMEOUT_S


def _make_db(tmp_path: Path, outcomes: list[dict]) -> str:
    db = str(tmp_path / "tracker.db")
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE paper_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, position_id INTEGER, symbol TEXT,
            source TEXT, action TEXT, qty REAL, entry_price REAL, exit_price REAL,
            entry_at TEXT, exit_at TEXT, pnl_usd REAL, pnl_pct REAL, outcome TEXT,
            hold_days REAL
        );
        CREATE TABLE paper_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, status TEXT
        );
        CREATE TABLE signal_overrides (id INTEGER PRIMARY KEY AUTOINCREMENT, agree INTEGER);
        """
    )
    for i, o in enumerate(outcomes):
        qty = float(o.get("qty", 1.0))
        entry_price = float(o.get("entry_price", 100.0))
        exit_price = float(o["exit"])
        entry_at = o.get("entry_at", "2026-05-21T00:00:00+00:00")
        exit_at = o.get("exit_at", "2026-05-25T00:00:00+00:00")
        con.execute(
            "INSERT INTO paper_outcomes (symbol, source, action, qty, entry_price,"
            " exit_price, entry_at, exit_at, pnl_usd, pnl_pct, outcome, hold_days)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                o.get("symbol", f"S{i}"),
                o.get("source", "consensus"),
                "BUY",
                qty,
                entry_price,
                exit_price,
                entry_at,
                exit_at,
                (exit_price - entry_price) * qty,
                (exit_price - entry_price) / entry_price,
                o["outcome"],
                4.0,
            ),
        )
    con.commit()
    con.close()
    return db


def _make_daily_analysis_db(tmp_path: Path, trades: list[dict]) -> str:
    db = str(tmp_path / "daily-analysis.db")
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE paper_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            action TEXT NOT NULL,
            shares REAL NOT NULL,
            entry_price REAL NOT NULL,
            entry_time TEXT NOT NULL,
            exit_price REAL,
            exit_time TEXT,
            scanner_source TEXT,
            realized_pnl REAL,
            pnl_pct REAL,
            outcome TEXT DEFAULT 'OPEN'
        );
        """
    )
    for index, trade in enumerate(trades):
        con.execute(
            "INSERT INTO paper_trades "
            "(symbol, action, shares, entry_price, entry_time, exit_price, exit_time, "
            "scanner_source, realized_pnl, pnl_pct, outcome) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                trade.get("symbol", f"D{index}"),
                trade.get("action", "BUY"),
                trade.get("shares", 1.0),
                trade.get("entry_price", 100.0),
                trade.get("entry_time", "2026-07-01T14:30:00+00:00"),
                trade.get("exit_price"),
                trade.get("exit_time"),
                trade.get("scanner_source", "daily-analysis"),
                trade.get("realized_pnl"),
                trade.get("pnl_pct"),
                trade.get("outcome", "OPEN"),
            ),
        )
    con.commit()
    con.close()
    return db


# ── data assembly ──────────────────────────────────────────────────────────────


class TestLoadTrackRecord:
    def test_empty_db_is_honest(self, tmp_path):
        db = _make_db(tmp_path, [])
        data = proof_page.load_track_record(db, launch_date="2026-05-21", today="2026-06-03")
        assert data["closed_trades"] == 0
        assert data["win_rate"] == 0.0
        assert data["gate"]["version"] == 2  # authoritative engine-cohort DTO
        assert data["gate"]["pass"] is False
        assert data["gate"]["closes"] == 0
        assert data["days_running"] == 13  # 2026-05-21 → 2026-06-03
        assert data["days_target"] == 180
        assert data["equity_curve"] == [1.0]  # starts at 1.0, no trades

    def test_computes_win_rate_and_counts(self, tmp_path):
        db = _make_db(
            tmp_path,
            [
                {"exit": 110, "outcome": "WIN"},
                {"exit": 105, "outcome": "WIN"},
                {"exit": 90, "outcome": "LOSS"},
                {"exit": 100, "outcome": "BREAKEVEN"},
            ],
        )
        data = proof_page.load_track_record(db, launch_date="2026-05-21", today="2026-06-03")
        assert data["closed_trades"] == 4
        assert data["wins"] == 2
        assert data["losses"] == 1
        assert data["win_rate"] == pytest.approx(0.5, abs=0.01)
        assert len(data["equity_curve"]) == 5  # 1.0 + 4 steps
        assert data["equity_curve"][-1] > 1.0  # net positive

    def test_retired_30_trade_gate_no_longer_passes(self, tmp_path):
        # 30 consistent winners sailed through the RETIRED local 30-trade gate; the
        # authoritative engine gate (trading/tracker.py gate_status_dto) requires
        # 100 ENGINE closes — the page must hold fail-closed until then.
        db = _make_db(tmp_path, [{"exit": 112, "outcome": "WIN"} for _ in range(30)])
        data = proof_page.load_track_record(db, launch_date="2026-05-21", today="2026-08-30")
        assert data["gate"]["pass"] is False
        assert data["gate"]["closes"] == 30
        assert data["gate"]["required"] > 30
        assert data["closed_trades"] == 30

    def test_page_cohorts_use_the_same_launch_and_quantity_eligibility_as_gate(
        self,
        tmp_path,
        monkeypatch,
    ):
        def _unavailable():
            raise ImportError("force fail-closed DTO for deterministic local count")

        monkeypatch.setattr(proof_page, "_import_gate_status_fn", _unavailable)
        db = _make_db(
            tmp_path,
            [
                {
                    "symbol": "PRE",
                    "exit": 80,
                    "outcome": "LOSS",
                    "exit_at": "2026-05-20T23:59:59+00:00",
                },
                {
                    "symbol": "ZERO",
                    "exit": 120,
                    "outcome": "WIN",
                    "qty": 0,
                    "exit_at": "2026-05-26T00:00:00+00:00",
                },
                {
                    "symbol": "VALID",
                    "exit": 90,
                    "outcome": "LOSS",
                    "exit_at": "2026-05-27T00:00:00+00:00",
                },
            ],
        )

        data = proof_page.load_track_record(db, launch_date="2026-05-21", today="2026-06-03")
        assert data["closed_trades"] == 1
        assert data["engine"]["closed"] == 1
        assert data["engine"]["wins"] == 0
        assert data["gate"]["closes"] == 1
        assert data["excluded_ledger_rows"] == 2
        assert [t["symbol"] for t in data["trades"]] == ["VALID"]

        rendered = proof_page.render_html(data)
        assert "2 legacy/ineligible ledger rows excluded" in rendered
        assert "every gate-eligible losing trade" in rendered
        assert "Complete gate-eligible closed-trade ledger" in rendered
        assert "PRE" not in rendered
        assert "ZERO" not in rendered


# ── rendering ──────────────────────────────────────────────────────────────────


class TestRenderHtml:
    def test_contains_mandatory_disclaimers(self, tmp_path):
        db = _make_db(tmp_path, [])
        html = proof_page.render_html(proof_page.load_track_record(db, today="2026-06-03"))
        up = html.upper()
        assert "NOT FINANCIAL ADVICE" in up
        assert "HYPOTHETICAL" in up or "SIMULATED" in up
        assert "PAST PERFORMANCE" in up

    def test_shows_sample_size_banner_when_thin(self, tmp_path):
        db = _make_db(tmp_path, [{"exit": 110, "outcome": "WIN"}])
        html = proof_page.render_html(proof_page.load_track_record(db, today="2026-06-03"))
        assert "1 of 100" in html  # N of 100 ENGINE closes
        assert "building" in html.lower() or "track record" in html.lower()

    def test_is_self_contained_no_external_scripts(self, tmp_path):
        db = _make_db(tmp_path, [{"exit": 110, "outcome": "WIN"}])
        html = proof_page.render_html(proof_page.load_track_record(db, today="2026-06-03"))
        # No external CDN/script dependency (equity curve is inline SVG).
        assert "<script" not in html.lower()
        assert "http://" not in html and "cdnjs" not in html
        assert "<svg" in html.lower()  # inline equity curve

    def test_renders_trades_table_rows(self, tmp_path):
        db = _make_db(
            tmp_path,
            [
                {"symbol": "NVDA", "exit": 130, "outcome": "WIN"},
                {"symbol": "TSLA", "exit": 80, "outcome": "LOSS"},
            ],
        )
        html = proof_page.render_html(proof_page.load_track_record(db, today="2026-06-03"))
        assert "NVDA" in html
        assert "TSLA" in html

    def test_complete_ledger_is_not_truncated_after_50_trades(self, tmp_path):
        outcomes = [
            {"symbol": f"S{i:02}", "exit": 101 + i / 100, "outcome": "WIN"} for i in range(51)
        ]
        db = _make_db(tmp_path, outcomes)
        html = proof_page.render_html(proof_page.load_track_record(db, today="2026-07-31"))

        assert "S00" in html
        assert "S50" in html
        assert "Complete gate-eligible closed-trade ledger" in html


class TestProofPageEscaping:
    """8th-pass (Fable audit): /track-record is a PUBLIC page rendered OUTSIDE the
    dashboard XSS guard, and CSP carries 'unsafe-inline' — so a `<` in a DB-sourced
    symbol/source would execute. Every DB string must be HTML-escaped."""

    def test_db_strings_are_html_escaped(self, tmp_path):
        db = _make_db(
            tmp_path,
            [
                {
                    "symbol": "<script>alert(1)</script>",
                    "source": "<img src=x onerror=alert(2)>",
                    "exit": 130,
                    "outcome": "WIN",
                },
            ],
        )
        html = proof_page.render_html(proof_page.load_track_record(db, today="2026-06-03"))
        # raw injection must NOT survive (row cell AND the by-source summary)
        assert "<script>alert(1)</script>" not in html
        assert "<img src=x onerror=alert(2)>" not in html
        # escaped form is what renders
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
        assert "&lt;img src=x onerror=alert(2)&gt;" in html


def _overlapping_unequal_notional_db(tmp_path: Path) -> str:
    return _make_db(
        tmp_path,
        [
            {
                "symbol": "A",
                "source": "consensus",
                "qty": 2,
                "entry_price": 100.0,
                "exit": 110.0,
                "entry_at": "2026-06-01T14:00:00+00:00",
                "exit_at": "2026-06-10T14:00:00+00:00",
                "outcome": "WIN",
            },
            {
                "symbol": "B",
                "source": "consensus",
                "qty": 1,
                "entry_price": 300.0,
                "exit": 285.0,
                "entry_at": "2026-06-05T14:00:00+00:00",
                "exit_at": "2026-06-12T14:00:00+00:00",
                "outcome": "LOSS",
            },
        ],
    )


def test_engine_headline_metrics_use_dollars_and_deployed_capital(tmp_path):
    data = proof_page.load_track_record(
        _overlapping_unequal_notional_db(tmp_path),
        launch_date="2026-05-21",
        today="2026-07-01",
    )

    engine = data["engine"]
    assert engine["realized_pnl_usd"] == 5.0
    assert engine["gross_entry_notional_usd"] == 500.0
    assert engine["pnl_to_gross_entry_notional"] == 0.01
    assert engine["return_on_deployed_capital"] == 0.01  # compatibility alias
    assert engine["sum_trade_returns"] == 0.05
    assert engine["win_rate_ci_95"] == (0.0945, 0.9055)
    assert engine["cost_sensitivity_20bp_usd"] == 1.00
    assert engine["cost_sensitivity_50bp_usd"] == 2.50
    assert engine["profit_factor_usd"] == 1.3333


def test_daily_analysis_book_is_separate_and_combined_context_is_arithmetic(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(proof_page, "_spy_return_since", lambda _start: None)
    tracker_db = _overlapping_unequal_notional_db(tmp_path)
    daily_db = _make_daily_analysis_db(
        tmp_path,
        [
            {
                "symbol": "DAILY_LOSS_A",
                "shares": 2,
                "entry_price": 100,
                "exit_price": 90,
                "exit_time": "2026-07-02T20:00:00+00:00",
                "realized_pnl": -20,
                "pnl_pct": -10.0,
                "outcome": "LOSS",
            },
            {
                "symbol": "DAILY_LOSS_B",
                "shares": 1,
                "entry_price": 300,
                "exit_price": 290,
                "exit_time": "2026-07-03T20:00:00+00:00",
                "realized_pnl": -10,
                "pnl_pct": -3.3333,
                "outcome": "LOSS",
            },
            {
                "symbol": "DAILY_OPEN",
                "shares": 3,
                "entry_price": 50,
                "entry_time": "<img src=x onerror=alert(1)>",
                "outcome": "OPEN",
            },
            {
                "symbol": "OTHER_BOOK",
                "scanner_source": "manual",
                "shares": 1,
                "entry_price": 100,
                "exit_price": 110,
                "exit_time": "2026-07-03T20:00:00+00:00",
                "realized_pnl": 10,
                "pnl_pct": 0.10,
                "outcome": "WIN",
            },
            {
                "symbol": "INVALID_INCONSISTENT_PNL",
                "shares": 1,
                "entry_price": 100,
                "exit_price": 150,
                "exit_time": "2026-07-03T20:00:00+00:00",
                "realized_pnl": 5000,
                "pnl_pct": 0.50,
                "outcome": "WIN",
            },
        ],
    )

    data = proof_page.load_track_record(
        tracker_db,
        daily_analysis_db_path=daily_db,
        launch_date="2026-05-21",
        today="2026-07-04",
    )

    daily = data["daily_analysis"]
    assert daily["status"] == "AVAILABLE"
    assert daily["closed"] == 2
    assert daily["wins"] == 0
    assert daily["losses"] == 2
    assert daily["realized_pnl_usd"] == -30.0
    assert daily["gross_entry_notional_usd"] == 500.0
    assert daily["open_positions"] == 1
    assert daily["excluded_invalid_rows"] == 1

    combined = data["combined_automated"]
    assert combined == {
        "available": True,
        "closed": 4,
        "wins": 1,
        "losses": 3,
        "breakeven": 0,
        "win_rate": 0.25,
        "realized_pnl_usd": -25.0,
        "gross_entry_notional_usd": 1000.0,
    }
    assert data["engine"]["closed"] == 2
    assert data["gate"]["closes"] == 2

    rendered = proof_page.render_html(data)
    assert 'data-book="daily-analysis"' in rendered
    assert 'data-k="daily-analysis-closed">2<' in rendered
    assert 'data-k="daily-analysis-realized-pnl">-$30.00<' in rendered
    assert 'data-k="combined-automated-closed">4<' in rendered
    assert 'data-k="combined-automated-realized-pnl">-$25.00<' in rendered
    assert "not included in the headline or autonomy gate" in rendered
    assert "arithmetic context only" in rendered
    assert "not a portfolio return" in rendered
    assert "<img src=x onerror=alert(1)>" not in rendered
    assert "&lt;img src=x onerror=alert(1)&gt;" in rendered


def test_daily_analysis_accepts_every_canonical_action(tmp_path):
    daily_db = _make_daily_analysis_db(
        tmp_path,
        [
            {
                "symbol": "BUY_LONG",
                "action": "BUY",
                "exit_price": 110,
                "exit_time": "2026-07-02T20:00:00+00:00",
                "realized_pnl": 10,
                "pnl_pct": 10,
                "outcome": "WIN",
            },
            {
                "symbol": "ADD_LONG",
                "action": "ADD",
                "exit_price": 90,
                "exit_time": "2026-07-02T20:00:00+00:00",
                "realized_pnl": -10,
                "pnl_pct": -10,
                "outcome": "LOSS",
            },
            {
                "symbol": "TRIM_SHORT",
                "action": "TRIM",
                "exit_price": 90,
                "exit_time": "2026-07-02T20:00:00+00:00",
                "realized_pnl": 10,
                "pnl_pct": 10,
                "outcome": "WIN",
            },
            {
                "symbol": "SELL_SHORT",
                "action": "SELL",
                "exit_price": 110,
                "exit_time": "2026-07-02T20:00:00+00:00",
                "realized_pnl": -10,
                "pnl_pct": -10,
                "outcome": "LOSS",
            },
        ],
    )

    daily = proof_page._load_daily_analysis_book(daily_db)

    assert daily["closed"] == 4
    assert daily["wins"] == 2
    assert daily["losses"] == 2
    assert daily["realized_pnl_usd"] == 0
    assert daily["excluded_invalid_rows"] == 0


def test_daily_analysis_rejects_contradictory_open_and_outcome_rows(tmp_path):
    daily_db = _make_daily_analysis_db(
        tmp_path,
        [
            {"symbol": "VALID_OPEN", "action": "ADD", "outcome": "OPEN"},
            {
                "symbol": "CLOSED_AS_OPEN",
                "exit_price": 110,
                "exit_time": "2026-07-02T20:00:00+00:00",
                "realized_pnl": 10,
                "pnl_pct": 10,
                "outcome": "OPEN",
            },
            {
                "symbol": "INVALID_OPEN_ACTION",
                "action": "HOLD",
                "outcome": "OPEN",
            },
            {
                "symbol": "MATERIAL_BREAKEVEN",
                "exit_price": 150,
                "exit_time": "2026-07-02T20:00:00+00:00",
                "realized_pnl": 50,
                "pnl_pct": 50,
                "outcome": "BREAKEVEN",
            },
            {
                "symbol": "VALID_BREAKEVEN",
                "exit_price": 100,
                "exit_time": "2026-07-02T20:00:00+00:00",
                "realized_pnl": 0,
                "pnl_pct": 0,
                "outcome": "BREAKEVEN",
            },
        ],
    )

    daily = proof_page._load_daily_analysis_book(daily_db)

    assert daily["closed"] == 1
    assert daily["breakeven"] == 1
    assert daily["open_positions"] == 1
    assert daily["realized_pnl_usd"] == 0
    assert daily["excluded_invalid_rows"] == 3


def test_daily_analysis_book_failure_is_visible_without_path_leak(tmp_path, monkeypatch):
    monkeypatch.setattr(proof_page, "_spy_return_since", lambda _start: None)
    tracker_db = _overlapping_unequal_notional_db(tmp_path)
    missing = tmp_path / "private-owner-path" / "missing.db"

    data = proof_page.load_track_record(
        tracker_db,
        daily_analysis_db_path=str(missing),
        today="2026-07-04",
    )
    rendered = proof_page.render_html(data)

    assert data["daily_analysis"] == {
        "status": "UNAVAILABLE",
        "reason": "ledger unavailable",
    }
    assert data["combined_automated"] == {"available": False}
    assert "Second automated book unavailable" in rendered
    assert "combined automated totals are unavailable" in rendered
    assert str(missing) not in rendered
    assert not missing.exists()


def test_headline_renders_defensible_metrics_and_uncertainty(tmp_path, monkeypatch):
    monkeypatch.setattr(proof_page, "_spy_return_since", lambda _start: 0.01)
    data = proof_page.load_track_record(
        _overlapping_unequal_notional_db(tmp_path),
        launch_date="2026-05-21",
        today="2026-07-01",
    )
    rendered = proof_page.render_html(data)

    assert 'data-k="headline-realized-pnl-usd">+$5.00<' in rendered
    assert 'data-k="headline-gross-entry-notional">$500.00<' in rendered
    assert 'data-k="headline-pnl-to-gross-entry-notional">+1.0%<' in rendered
    assert "P&amp;L / gross entry notional" in rendered
    assert "Approx. Wilson 95% interval 9.5%–90.5%" in rendered
    assert "not adjusted for overlapping/correlated trades" in rendered
    assert "20–50 bp additional execution-cost stress" in rendered
    assert "$1.00–$2.50" in rendered
    assert "SPY calendar context +1.0% (not exposure-matched)" in rendered
    assert "Gate profit factor uses gross-dollar P&amp;L" in rendered
    assert "not independently audited" in rendered
    assert "mutable SQLite ledger" in rendered
    assert "never backfilled" not in rendered
    assert "does not deduct fees or slippage" not in rendered
    assert "return on deployed capital" not in rendered.lower()


def test_synthetic_curve_and_trade_return_sum_are_not_portfolio_performance(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(proof_page, "_spy_return_since", lambda _start: 0.01)
    data = proof_page.load_track_record(
        _overlapping_unequal_notional_db(tmp_path),
        launch_date="2026-05-21",
        today="2026-07-01",
    )
    rendered = proof_page.render_html(data)
    headline = rendered[rendered.index("Headline") : rendered.index("Cohorts")]

    assert "Illustrative sequential trade-return index" in rendered
    assert "Cumulative paper equity" not in rendered
    assert "sum of individual trade returns" in rendered
    assert "not a portfolio return" in rendered
    assert "+5.0%" not in headline
    assert "SPY over the same period" not in headline
    assert "This is the number to judge the product on." not in rendered
