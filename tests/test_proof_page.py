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
        con.execute(
            "INSERT INTO paper_outcomes (symbol, source, action, qty, entry_price,"
            " exit_price, entry_at, exit_at, pnl_usd, pnl_pct, outcome, hold_days)"
            " VALUES (?,?,?,1,100,?,?,?,?,?,?,?)",
            (o.get("symbol", f"S{i}"), o.get("source", "consensus"), "BUY",
             o["exit"], "2026-05-21T00:00:00+00:00", "2026-05-25T00:00:00+00:00",
             o["exit"] - 100, (o["exit"] - 100) / 100.0, o["outcome"], 4.0),
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
        assert data["gate"]["version"] == 2       # authoritative engine-cohort DTO
        assert data["gate"]["pass"] is False
        assert data["gate"]["closes"] == 0
        assert data["days_running"] == 13          # 2026-05-21 → 2026-06-03
        assert data["days_target"] == 180
        assert data["equity_curve"] == [1.0]        # starts at 1.0, no trades

    def test_computes_win_rate_and_counts(self, tmp_path):
        db = _make_db(tmp_path, [
            {"exit": 110, "outcome": "WIN"},
            {"exit": 105, "outcome": "WIN"},
            {"exit": 90, "outcome": "LOSS"},
            {"exit": 100, "outcome": "BREAKEVEN"},
        ])
        data = proof_page.load_track_record(db, launch_date="2026-05-21", today="2026-06-03")
        assert data["closed_trades"] == 4
        assert data["wins"] == 2
        assert data["losses"] == 1
        assert data["win_rate"] == pytest.approx(0.5, abs=0.01)
        assert len(data["equity_curve"]) == 5       # 1.0 + 4 steps
        assert data["equity_curve"][-1] > 1.0        # net positive

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
        assert "1 of 100" in html                    # N of 100 ENGINE closes
        assert "building" in html.lower() or "track record" in html.lower()

    def test_is_self_contained_no_external_scripts(self, tmp_path):
        db = _make_db(tmp_path, [{"exit": 110, "outcome": "WIN"}])
        html = proof_page.render_html(proof_page.load_track_record(db, today="2026-06-03"))
        # No external CDN/script dependency (equity curve is inline SVG).
        assert "<script" not in html.lower()
        assert "http://" not in html and "cdnjs" not in html
        assert "<svg" in html.lower()                # inline equity curve

    def test_renders_trades_table_rows(self, tmp_path):
        db = _make_db(tmp_path, [
            {"symbol": "NVDA", "exit": 130, "outcome": "WIN"},
            {"symbol": "TSLA", "exit": 80, "outcome": "LOSS"},
        ])
        html = proof_page.render_html(proof_page.load_track_record(db, today="2026-06-03"))
        assert "NVDA" in html
        assert "TSLA" in html


class TestProofPageEscaping:
    """8th-pass (Fable audit): /track-record is a PUBLIC page rendered OUTSIDE the
    dashboard XSS guard, and CSP carries 'unsafe-inline' — so a `<` in a DB-sourced
    symbol/source would execute. Every DB string must be HTML-escaped."""

    def test_db_strings_are_html_escaped(self, tmp_path):
        db = _make_db(tmp_path, [
            {"symbol": "<script>alert(1)</script>",
             "source": "<img src=x onerror=alert(2)>", "exit": 130, "outcome": "WIN"},
        ])
        html = proof_page.render_html(proof_page.load_track_record(db, today="2026-06-03"))
        # raw injection must NOT survive (row cell AND the by-source summary)
        assert "<script>alert(1)</script>" not in html
        assert "<img src=x onerror=alert(2)>" not in html
        # escaped form is what renders
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
        assert "&lt;img src=x onerror=alert(2)&gt;" in html
