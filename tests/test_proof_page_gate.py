"""
tests/test_proof_page_gate.py — /track-record must evaluate readiness with the
AUTHORITATIVE autonomy gate (trading engine's versioned engine-cohort DTO from
trading/tracker.py gate_status_dto), never the retired 30-trade/win-rate/Sharpe/
drawdown rules over a mixed population. The page must render manual and engine
cohorts SEPARATELY (manual wins can never inflate engine stats) and must never
claim the gate is met below the engine-close floor.

No network. Reads a temp SQLite paper_outcomes table; the cross-repo DTO import
is stubbed at the proof_page._import_gate_status_fn seam except in the opt-in
real-engine integration test.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

import proof_page

ENGINE_HOME = Path(os.getenv(
    "GESH_TRADING_HOME",
    str(Path(__file__).parent.parent.parent / "gesh-project-trading"),
))


def _make_db(tmp_path: Path, outcomes: list[dict]) -> str:
    db = str(tmp_path / "tracker.db")
    con = sqlite3.connect(db)
    # Engine-DDL-compatible schema: the real gate_status_dto path runs the engine's
    # CREATE INDEX IF NOT EXISTS over paper_positions(symbol,status) and
    # signal_overrides(intent_id), so those columns must exist.
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
        CREATE TABLE signal_overrides (
            id INTEGER PRIMARY KEY AUTOINCREMENT, intent_id INTEGER, agree INTEGER
        );
        """
    )
    for i, o in enumerate(outcomes):
        con.execute(
            "INSERT INTO paper_outcomes (symbol, source, action, qty, entry_price,"
            " exit_price, entry_at, exit_at, pnl_usd, pnl_pct, outcome, hold_days)"
            " VALUES (?,?,?,1,100,?,?,?,?,?,?,4.0)",
            (o.get("symbol", f"S{i}"), o.get("source", "consensus"), "BUY",
             o["exit"], "2026-05-22T00:00:00+00:00", "2026-05-26T00:00:00+00:00",
             o["exit"] - 100, (o["exit"] - 100) / 100.0, o["outcome"]),
        )
    con.commit()
    con.close()
    return db


def _stub_gate(closes: int, *, required: int = 100, ok: bool = False,
               criteria: dict | None = None):
    """Fake trading.tracker.gate_status_dto with the v2 DTO shape."""
    def _fn(db_path=None):
        return {
            "version": 2, "cohort": "engine",
            "cohort_sources": ["consensus", "market_scan"],
            "closes": closes, "required": required,
            "criteria": criteria or {}, "pass": ok,
            "summary": "", "eta_note": "", "window": "since_launch", "as_of": "",
        }
    return _fn


ENGINE_LOSSES_8 = [{"exit": 95, "outcome": "LOSS", "source": "consensus"}] * 8
MANUAL_WINS_9 = [{"exit": 110, "outcome": "WIN", "source": "manual"}] * 9


# ── the gate is the versioned engine DTO ─────────────────────────────────────────

class TestGateDto:
    def test_gate_is_the_versioned_engine_dto(self, tmp_path, monkeypatch):
        monkeypatch.setattr(proof_page, "_import_gate_status_fn",
                            lambda: _stub_gate(8))
        db = _make_db(tmp_path, MANUAL_WINS_9 + ENGINE_LOSSES_8)
        data = proof_page.load_track_record(db, today="2026-07-12")
        assert data["gate"]["version"] == 2
        assert data["gate"]["cohort"] == "engine"
        assert data["gate"]["closes"] == 8          # engine cohort, not the mixed 17
        assert data["gate"]["required"] == 100
        assert data["gate"]["pass"] is False

    def test_import_failure_is_fail_closed(self, tmp_path, monkeypatch):
        def _boom():
            raise ImportError("engine repo not available")
        monkeypatch.setattr(proof_page, "_import_gate_status_fn", _boom)
        db = _make_db(tmp_path, [{"exit": 112, "outcome": "WIN",
                                  "source": "consensus"}] * 40)
        data = proof_page.load_track_record(db, today="2026-07-12")
        assert data["gate"]["version"] == 2
        assert data["gate"]["pass"] is False        # fail-closed, never fail-open
        assert data["gate"]["closes"] == 40         # honest local engine count
        html = proof_page.render_html(data)
        assert "Autonomy gate met" not in html
        assert "40 of 100" in html


# ── cohort separation ────────────────────────────────────────────────────────────

class TestCohortSeparation:
    def test_manual_wins_never_mix_into_engine_stats(self, tmp_path, monkeypatch):
        monkeypatch.setattr(proof_page, "_import_gate_status_fn",
                            lambda: _stub_gate(8))
        db = _make_db(tmp_path, MANUAL_WINS_9 + ENGINE_LOSSES_8)
        data = proof_page.load_track_record(db, today="2026-07-12")
        assert data["engine"]["closed"] == 8
        assert data["engine"]["wins"] == 0          # 9 manual wins must not leak in
        assert data["engine"]["win_rate"] == 0.0
        assert data["manual"]["closed"] == 9
        assert data["manual"]["wins"] == 9
        html = proof_page.render_html(data)
        assert 'data-k="engine-wins">0<' in html
        assert 'data-k="manual-wins">9<' in html

    def test_page_renders_both_cohorts(self, tmp_path, monkeypatch):
        monkeypatch.setattr(proof_page, "_import_gate_status_fn",
                            lambda: _stub_gate(8))
        db = _make_db(tmp_path, MANUAL_WINS_9 + ENGINE_LOSSES_8)
        html = proof_page.render_html(
            proof_page.load_track_record(db, today="2026-07-12"))
        assert 'data-cohort="engine"' in html
        assert 'data-cohort="manual"' in html


# ── never claims pass on a thin engine sample ────────────────────────────────────

class TestNeverClaimsPassThin:
    def test_retired_30_trade_gate_can_no_longer_pass_the_page(self, tmp_path,
                                                               monkeypatch):
        # 40 engine wins sailed through the OLD 30/55%/Sharpe/dd gate — the page
        # must now hold at "building" until 100 engine closes.
        monkeypatch.setattr(proof_page, "_import_gate_status_fn",
                            lambda: _stub_gate(40))
        db = _make_db(tmp_path, [{"exit": 112, "outcome": "WIN",
                                  "source": "consensus"}] * 40)
        html = proof_page.render_html(
            proof_page.load_track_record(db, today="2026-07-12"))
        assert "Autonomy gate met" not in html
        assert "40 of 100" in html
        assert "Building track record" in html

    def test_no_pass_claim_even_if_dto_says_pass_with_thin_closes(self, tmp_path,
                                                                  monkeypatch):
        # Belt-and-braces: a buggy/spoofed DTO claiming pass below its own floor
        # must still render as building.
        monkeypatch.setattr(proof_page, "_import_gate_status_fn",
                            lambda: _stub_gate(40, ok=True))
        db = _make_db(tmp_path, ENGINE_LOSSES_8)
        html = proof_page.render_html(
            proof_page.load_track_record(db, today="2026-07-12"))
        assert "Autonomy gate met" not in html

    def test_pass_claim_requires_dto_pass_and_full_sample(self, tmp_path,
                                                          monkeypatch):
        monkeypatch.setattr(proof_page, "_import_gate_status_fn",
                            lambda: _stub_gate(120, ok=True))
        db = _make_db(tmp_path, [{"exit": 112, "outcome": "WIN",
                                  "source": "consensus"}] * 10)
        html = proof_page.render_html(
            proof_page.load_track_record(db, today="2026-07-12"))
        assert "Autonomy gate met" in html


# ── real cross-repo integration (skipped when the engine repo is absent) ─────────

@pytest.mark.skipif(not (ENGINE_HOME / "trading" / "tracker.py").exists(),
                    reason="gesh-project-trading checkout not available")
class TestRealEngineIntegration:
    def test_real_dto_reads_the_page_db(self, tmp_path, monkeypatch):
        # Keep any engine table-init writes off the real DBs.
        monkeypatch.setenv("GESH_DB_PATH", str(tmp_path / "engine_state.db"))
        db = _make_db(tmp_path, MANUAL_WINS_9[:5]
                      + [{"exit": 96, "outcome": "LOSS", "source": "consensus"}] * 6)
        data = proof_page.load_track_record(db, today="2026-07-12")
        gate = data["gate"]
        assert gate["version"] == 2
        assert gate["cohort"] == "engine"
        assert gate.get("fallback") is None          # the REAL import path ran
        assert gate["closes"] == 6                   # engine cohort from OUR temp DB
        assert gate["pass"] is False
