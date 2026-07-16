"""Daily per-symbol consensus snapshots — substrate for alerts/sparkline/digest.

The flip detector implements the anti-flapping rule from the 2026-07 product
research (Danelfin's #1 Trustpilot complaint is day-to-day rating whiplash):
a verdict change only counts once it has persisted N consecutive snapshots.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import signal_history as sh


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sh, "DB_PATH", tmp_path / "sig.db")


def _row(sym, verdict, score=0.3, confidence=80.0):
    return {"symbol": sym, "verdict": verdict, "score": score, "confidence": confidence}


def test_record_and_history_roundtrip():
    assert sh.record_snapshots([_row("NVDA", "BUY")], date="2026-07-14") == 1
    sh.record_snapshots([_row("NVDA", "HOLD", score=0.1)], date="2026-07-15")
    hist = sh.get_history("NVDA", days=30)
    assert [h["date"] for h in hist] == ["2026-07-14", "2026-07-15"]  # oldest→newest
    assert hist[-1]["verdict"] == "HOLD" and hist[-1]["score"] == 0.1


def test_record_same_day_upserts_not_duplicates():
    sh.record_snapshots([_row("NVDA", "BUY")], date="2026-07-15")
    sh.record_snapshots([_row("NVDA", "SELL")], date="2026-07-15")
    hist = sh.get_history("NVDA")
    assert len(hist) == 1 and hist[0]["verdict"] == "SELL"


def test_flip_needs_persistence():
    # BUY BUY HOLD — HOLD has held only 1 day → NOT confirmed yet
    sh.record_snapshots([_row("NVDA", "BUY")], date="2026-07-13")
    sh.record_snapshots([_row("NVDA", "BUY")], date="2026-07-14")
    sh.record_snapshots([_row("NVDA", "HOLD")], date="2026-07-15")
    assert sh.detect_confirmed_flips(["NVDA"], persistence_days=2) == []
    # one more HOLD day → confirmed
    sh.record_snapshots([_row("NVDA", "HOLD")], date="2026-07-16")
    flips = sh.detect_confirmed_flips(["NVDA"], persistence_days=2)
    assert len(flips) == 1
    f = flips[0]
    assert f["symbol"] == "NVDA" and f["prior_verdict"] == "BUY" and f["new_verdict"] == "HOLD"
    assert f["confirmed_since"] == "2026-07-15"


def test_stable_verdict_is_not_a_flip():
    for d in ("2026-07-13", "2026-07-14", "2026-07-15"):
        sh.record_snapshots([_row("NVDA", "BUY")], date=d)
    assert sh.detect_confirmed_flips(["NVDA"]) == []


def test_flip_detector_ignores_symbols_with_short_history():
    sh.record_snapshots([_row("NEWSYM", "BUY")], date="2026-07-15")
    assert sh.detect_confirmed_flips(["NEWSYM"]) == []


def test_bad_rows_skipped_not_fatal():
    n = sh.record_snapshots([_row("NVDA", "BUY"), {"symbol": "", "verdict": "X"},
                             {"no": "symbol"}], date="2026-07-15")
    assert n == 1
