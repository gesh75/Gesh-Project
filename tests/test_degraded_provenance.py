"""Degraded-run provenance guards for the daily chain.

A run driven by a snapshot older than GESH_DEGRADED_AFTER_S (default 3600s)
must never produce fresh-looking output: run_engines2.py and run_ai2.py stamp
{degraded, snapshot_age_seconds, snapshot_source} into engine_results.json,
and run_paper_gate2.py's entry guard skips the whole step (no paper records).

run_engines2.py / run_ai2.py execute their pipelines at import time, so the
shared ``snapshot_degraded_info`` helper is extracted via ast and exec'd
standalone — this also pins the two duplicated copies to identical behavior.
run_paper_gate2.py must be import-safe (main() + __main__ guard); the
structural assertion runs from SOURCE before any import, so a RED state can
never execute the real paper pipeline (DB writes, Telegram) inside a test.
"""
from __future__ import annotations

import ast
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

GATE_SCRIPT = "run_paper_gate2.py"
NOW = datetime(2026, 7, 12, 15, 0, tzinfo=timezone.utc)


def _snap(age_seconds: float, source: str = "robinhood_mcp") -> dict:
    return {
        "fetched_at": (NOW - timedelta(seconds=age_seconds)).isoformat(),
        "source": source,
    }


from run_manifest import snapshot_degraded_info

# ── snapshot_degraded_info: run_engines2.py + run_ai2.py (kept in sync) ───────

def test_fresh_snapshot_is_not_degraded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GESH_DEGRADED_AFTER_S", raising=False)
    info = snapshot_degraded_info(_snap(120), now=NOW)
    assert info == {
        "degraded": False,
        "snapshot_age_seconds": 120,
        "snapshot_source": "robinhood_mcp",
    }


def test_stale_snapshot_is_degraded_at_default_threshold(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GESH_DEGRADED_AFTER_S", raising=False)
    info = snapshot_degraded_info(_snap(7200, source="robinhood_live"), now=NOW)
    assert info == {
        "degraded": True,
        "snapshot_age_seconds": 7200,
        "snapshot_source": "robinhood_live",
    }


def test_env_threshold_override_widens_the_window(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GESH_DEGRADED_AFTER_S", "10800")
    info = snapshot_degraded_info(_snap(7200), now=NOW)
    assert info["degraded"] is False
    assert info["snapshot_age_seconds"] == 7200


def test_unknown_snapshot_age_is_degraded_fail_safe(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GESH_DEGRADED_AFTER_S", raising=False)
    info = snapshot_degraded_info({}, now=NOW)
    assert info == {
        "degraded": True,
        "snapshot_age_seconds": None,
        "snapshot_source": "UNKNOWN",
    }


@pytest.mark.parametrize("script", ("run_engines2.py", "run_ai2.py"))
def test_scripts_use_shared_manifest_contract_without_sibling_precedence(script: str) -> None:
    src = (REPO_ROOT / script).read_text()
    assert "from run_manifest import" in src
    assert "sys.path.insert(0, str(TRADING))" not in src


# ── run_paper_gate2.py entry guard ────────────────────────────────────────────

def _import_paper_gate():
    """Assert import safety from SOURCE before importing — in the RED state the
    module body would run the real paper pipeline on import."""
    tree = ast.parse((REPO_ROOT / GATE_SCRIPT).read_text())
    top_fns = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    assert {"main", "degraded_skip"} <= top_fns, (
        f"{GATE_SCRIPT} must be import-safe: pipeline in main() with a "
        f"degraded_skip() entry guard"
    )
    assert any(
        isinstance(n, ast.If) and "__main__" in ast.dump(n.test) for n in tree.body
    ), f"{GATE_SCRIPT} must gate execution behind if __name__ == '__main__'"
    import run_paper_gate2
    return run_paper_gate2


def test_entry_guard_skips_on_engine_results_stamp() -> None:
    gate = _import_paper_gate()
    reason = gate.degraded_skip(
        {"degraded": True, "snapshot_age_seconds": 7200}, environ={}
    )
    assert reason and "7200" in reason


def test_entry_guard_skips_on_manifest_stamp() -> None:
    gate = _import_paper_gate()
    reason = gate.degraded_skip(
        {"run_manifest": {"degraded": True, "run_id": "run-1"}}, environ={}
    )
    assert reason and "run-1" in reason


def test_degraded_preflight_returns_frozen_audit_only_outcome() -> None:
    gate = _import_paper_gate()
    outcome = gate.preflight_outcome(
        {"run_manifest": {"degraded": True, "run_id": "run-1"}}, environ={}
    )
    assert outcome is not None
    assert outcome.actionable is False
    assert outcome.paper_session == ()
    with pytest.raises(Exception):
        outcome.actionable = True


def test_entry_guard_skips_on_chain_env_flag() -> None:
    gate = _import_paper_gate()
    assert gate.degraded_skip({}, environ={"GESH_DEGRADED": "1"})
    assert gate.degraded_skip({}, environ={"GESH_DEGRADED": "0"}) is None
    assert gate.degraded_skip({}, environ={}) is None
    assert gate.degraded_skip({"degraded": False}, environ={}) is None


def test_degraded_main_creates_no_paper_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Behavioral: a degraded engine_results.json short-circuits main() before
    any paper/trade-log import — nothing written back, no records, exit 0."""
    monkeypatch.delenv("GESH_DEGRADED", raising=False)
    gate = _import_paper_gate()
    results = {
        "degraded": True,
        "snapshot_age_seconds": 86000,
        "snapshot_source": "robinhood_live",
        "holdings": [{"symbol": "MSTR", "price": 200.0, "equity": 1000.0,
                      "quantity": 5}],
        "equity_summary": {"total": 10000.0, "cash": 9000.0},
        "kelly": {"MSTR": {"action": "ADD", "optimal_pct": 20}},
    }
    path = tmp_path / "engine_results.json"
    path.write_text(json.dumps(results))
    before = path.read_text()

    rc = gate.main(results_path=path)

    assert rc == 0
    assert path.read_text() == before          # no write-back at all
    after = json.loads(path.read_text())
    assert "paper_session" not in after
    assert "action_plan" not in after


# ── gen_report2.py provenance banner (source-level; script runs on import) ────

def test_report_surfaces_degraded_banner() -> None:
    src = (REPO_ROOT / "gen_report2.py").read_text()
    assert "degraded_banner_html" in src, (
        "gen_report2.py must build a degraded-run banner from the "
        "engine_results provenance stamp"
    )
    assert src.count("degraded_banner_html") >= 2, (
        "degraded_banner_html must be interpolated into the report body, "
        "not just defined"
    )


def test_chain_marks_degraded_report_as_non_publishing() -> None:
    src = (REPO_ROOT / "run_daily_analysis.sh").read_text()
    assert "GESH_RUN_DEGRADED" in src
    assert "gesh_report.degraded" in src
