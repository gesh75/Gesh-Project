"""
tests/test_progress_view.py — the /progress founder cockpit (reader side).

The weekly tracker (gesh-project-trading) persists a historical status row to
weekly_snapshots. Readiness comes from the authoritative versioned gate DTO plus
the audited product/operations blockers; the retired September countdown is
never rendered as a launch gate.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import progress_view


def _seed_snapshot(db: Path, **over):
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS weekly_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT, week_ending TEXT UNIQUE, iso_week TEXT,
            snapshot_at TEXT, closed_n INTEGER, closed_delta INTEGER, win_rate REAL,
            sharpe REAL, max_drawdown REAL, agreement_rate REAL, ratings_n INTEGER,
            days_running INTEGER, days_to_gate INTEGER, days_to_sep1 INTEGER,
            required_pace REAL, actual_pace REAL, milestone_pct REAL, milestones_json TEXT,
            on_track INTEGER, status TEXT, top_blocker TEXT, created_at TEXT);
    """)
    row = {
        "week_ending": "2026-06-05", "iso_week": "2026-W23", "snapshot_at": "x",
        "closed_n": 6, "closed_delta": 4, "win_rate": 0.66, "sharpe": 0.7,
        "max_drawdown": 0.04, "agreement_rate": 0.0, "ratings_n": 0,
        "days_running": 15, "days_to_gate": 87, "days_to_sep1": 88,
        "required_pace": 2.0, "actual_pace": 4.0, "milestone_pct": 66.7,
        "milestones_json": json.dumps({"auth": True, "proof": True, "tos": True,
                                       "payment": False, "routes": True, "entry_stream": False}),
        "on_track": 1, "status": "ON TRACK", "top_blocker": "No payment path", "created_at": "x",
    }
    row.update(over)
    cols = ",".join(row)
    ph = ",".join("?" * len(row))
    con.execute(f"INSERT INTO weekly_snapshots ({cols}) VALUES ({ph})", list(row.values()))
    con.commit()
    con.close()


class TestLoadProgress:
    def test_empty_is_pending(self, tmp_path):
        db = tmp_path / "g.db"
        db.touch()
        data = progress_view.load_progress(db, today="2026-06-03")
        assert data["has_data"] is False
        assert data["signal_gate"]["required"] == 100
        assert data["readiness"]["product_beta"]["state"] == "BLOCKED"

    def test_missing_db_is_pending(self, tmp_path):
        data = progress_view.load_progress(tmp_path / "nope.db", today="2026-06-03")
        assert data["has_data"] is False

    def test_reads_latest_snapshot(self, tmp_path):
        db = tmp_path / "g.db"
        _seed_snapshot(db)
        data = progress_view.load_progress(db, today="2026-06-08")
        assert data["has_data"] is True
        assert data["latest"]["closed_n"] == 6
        assert data["latest"]["status"] == "ON TRACK"

    def test_retired_snapshot_deadline_is_context_only(self, tmp_path):
        db = tmp_path / "g.db"
        _seed_snapshot(db, days_to_sep1=88)         # snapshot value (stale)
        data = progress_view.load_progress(db, today="2026-08-25")
        assert "days_to_sep1" not in data
        assert data["latest"]["days_to_sep1"] == 88

    def test_authoritative_gate_is_injected_and_fail_closed(self, tmp_path):
        db = tmp_path / "g.db"
        db.touch()
        gate = {"version": 2, "cohort": "engine", "closes": 16, "required": 100,
                "criteria": {}, "pass": False, "summary": "negative expectancy",
                "fallback": False, "as_of": "2026-07-18T10:00:00Z"}
        data = progress_view.load_progress(db, gate_loader=lambda _path: gate)
        assert data["signal_gate"] == gate
        assert data["readiness"]["signal_evidence"]["state"] == "BLOCKED"

    def test_healthy_injected_gate_without_fallback_key_is_not_degraded(self, tmp_path):
        db = tmp_path / "g.db"
        db.touch()
        gate = {"version": 2, "cohort": "engine", "closes": 100, "required": 100,
                "criteria": {}, "pass": True, "summary": "all criteria passed"}
        data = progress_view.load_progress(db, gate_loader=lambda _path: gate)
        assert data["signal_gate"]["fallback"] is False


class TestRenderProgressHtml:
    def test_pending_state_renders(self, tmp_path):
        db = tmp_path / "g.db"
        db.touch()
        html = progress_view.render_progress_html(progress_view.load_progress(db, today="2026-06-03"))
        assert "<!doctype html" in html.lower()
        assert "PAID-BETA READINESS" in html.upper()
        assert "SIGNAL EVIDENCE" in html.upper()
        assert "OPERATIONS" in html.upper()
        assert "NOT FINANCIAL ADVICE" in html.upper()
        assert "DAYS TO SEP-1 ALPHA" not in html.upper()
        assert "30 gate" not in html

    def test_full_render(self, tmp_path):
        db = tmp_path / "g.db"
        _seed_snapshot(db)
        html = progress_view.render_progress_html(progress_view.load_progress(db, today="2026-06-08"))
        assert "of 100" in html                      # authoritative gate floor
        assert "Legal + privacy review" in html
        assert "Legacy weekly snapshot" in html
        assert "NOT FINANCIAL ADVICE" in html.upper()
        assert "<script" not in html.lower()        # self-contained (inline SVG)
        assert "6/30" not in html

    def test_off_track_shows_red_status(self, tmp_path):
        db = tmp_path / "g.db"
        _seed_snapshot(db, status="OFF TRACK", on_track=0, closed_delta=0, actual_pace=0.0)
        html = progress_view.render_progress_html(progress_view.load_progress(db, today="2026-06-08"))
        assert "OFF TRACK" in html                  # retained only as legacy context

    def test_corrupt_legacy_numeric_value_fails_soft_and_escapes(self, tmp_path):
        db = tmp_path / "g.db"
        _seed_snapshot(db, closed_n="<script>alert(1)</script>")
        html = progress_view.render_progress_html(progress_view.load_progress(db))
        assert "<script>alert(1)</script>" not in html
        assert "0 historical closes" in html
