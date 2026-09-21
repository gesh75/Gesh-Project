"""Outage verdicts are not customer flips.

`record_confirmed_flips` filters both sides through `_is_real_verdict`.
BUY → INSUFFICIENT DATA is already pinned in test_retention_p1s.py; the
other `_NON_VERDICTS` markers (UNKNOWN / ERROR / N/A) and the reverse
direction (outage → real) were untested. Home tickets and the weekly
digest read this table — a data outage must never email a "flip".

`db` is stubbed before import so this file is stdlib + pytest (no gesh-core).
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

_db = types.ModuleType("db")
_db.DB_PATH = Path("/tmp/unused-signal-history.db")
sys.modules.setdefault("db", _db)

import signal_history as sh  # noqa: E402


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sh, "DB_PATH", tmp_path / "sig.db")


def _seed(sym: str, prior: str, new: str) -> None:
    sh.record_snapshots([{"symbol": sym, "verdict": prior, "score": 0.3}], date="2026-07-13")
    sh.record_snapshots([{"symbol": sym, "verdict": prior, "score": 0.3}], date="2026-07-14")
    sh.record_snapshots([{"symbol": sym, "verdict": new, "score": 0.1}], date="2026-07-15")
    sh.record_snapshots([{"symbol": sym, "verdict": new, "score": 0.1}], date="2026-07-16")


@pytest.mark.parametrize(
    "verdict,real",
    [
        ("BUY", True),
        ("STRONG BUY", True),
        ("HOLD", True),
        ("SELL", True),
        ("  buy  ", True),
        ("INSUFFICIENT DATA", False),
        ("UNKNOWN", False),
        ("ERROR", False),
        ("N/A", False),
        ("", False),
        (None, False),
    ],
)
def test_is_real_verdict_markers(verdict, real):
    assert sh._is_real_verdict(verdict) is real


@pytest.mark.parametrize("outage", ["INSUFFICIENT DATA", "UNKNOWN", "ERROR", "N/A"])
def test_outage_from_real_is_not_recorded(outage):
    _seed("NVDA", "BUY", outage)
    assert sh.detect_confirmed_flips(["NVDA"])  # live detector still sees the streak
    assert sh.record_confirmed_flips(["NVDA"]) == []
    assert sh.flips_since(["NVDA"], "2026-07-01") == []


@pytest.mark.parametrize("outage", ["UNKNOWN", "ERROR", "N/A"])
def test_recovery_from_outage_is_not_recorded(outage):
    """INSUFFICIENT → BUY is a feed coming back, not a signal change."""
    _seed("AAPL", outage, "BUY")
    assert sh.record_confirmed_flips(["AAPL"]) == []
    assert sh.flips_since(["AAPL"], "2026-07-01") == []


def test_real_flip_is_still_recorded():
    _seed("LLY", "BUY", "HOLD")
    new = sh.record_confirmed_flips(["LLY"])
    assert len(new) == 1
    assert new[0]["prior_verdict"] == "BUY"
    assert new[0]["new_verdict"] == "HOLD"
    assert sh.flips_since(["LLY"], "2026-07-15")[0]["new_verdict"] == "HOLD"


def test_flips_since_empty_or_blank_symbols_is_empty():
    _seed("NVDA", "BUY", "HOLD")
    sh.record_confirmed_flips(["NVDA"])
    assert sh.flips_since([], "2026-07-01") == []
    assert sh.flips_since(["", None, "  "], "2026-07-01") == []
    assert sh.flips_since(["AAPL"], "2026-07-01") == []  # other name
    assert len(sh.flips_since(["nvda"], "2026-07-01")) == 1  # casefold
