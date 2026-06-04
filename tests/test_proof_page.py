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
from pathlib import Path

import pytest

import proof_page


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
        assert data["gate"]["ready"] is False
        assert data["gate"]["closed_trades"]["pass"] is False
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

    def test_gate_passes_when_thresholds_met(self, tmp_path):
        # 30 consistent winners → gate closed_trades + win_rate pass
        db = _make_db(tmp_path, [{"exit": 112, "outcome": "WIN"} for _ in range(30)])
        data = proof_page.load_track_record(db, launch_date="2026-05-21", today="2026-08-30")
        assert data["gate"]["closed_trades"]["pass"] is True
        assert data["gate"]["win_rate"]["pass"] is True
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
        assert "1" in html and "30" in html          # N / 30 closed
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
