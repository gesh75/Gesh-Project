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
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

HELPER = "snapshot_degraded_info"
STAMPING_SCRIPTS = ("run_engines2.py", "run_ai2.py")
GATE_SCRIPT = "run_paper_gate2.py"
NOW = datetime(2026, 7, 12, 15, 0, tzinfo=timezone.utc)


def _snap(age_seconds: float, source: str = "robinhood_mcp") -> dict:
    return {
        "fetched_at": (NOW - timedelta(seconds=age_seconds)).isoformat(),
        "source": source,
    }


def _extract_helper(script: str):
    """Exec ONLY the helper def (+ its default constant) from a chain script —
    the scripts run their whole pipeline at import, so a plain import is unsafe."""
    tree = ast.parse((REPO_ROOT / script).read_text())
    wanted: list[ast.stmt] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == HELPER:
            wanted.append(node)
        elif isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "DEGRADED_AFTER_S_DEFAULT"
            for t in node.targets
        ):
            wanted.append(node)
    assert any(isinstance(n, ast.FunctionDef) for n in wanted), (
        f"{script} must define {HELPER}() (degraded-run provenance stamp)"
    )
    ns: dict = {"os": os}
    exec(compile(ast.Module(body=wanted, type_ignores=[]), script, "exec"), ns)
    return ns[HELPER]


# ── snapshot_degraded_info: run_engines2.py + run_ai2.py (kept in sync) ───────

@pytest.mark.parametrize("script", STAMPING_SCRIPTS)
def test_fresh_snapshot_is_not_degraded(script: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GESH_DEGRADED_AFTER_S", raising=False)
    info = _extract_helper(script)(_snap(120), now=NOW)
    assert info == {
        "degraded": False,
        "snapshot_age_seconds": 120,
        "snapshot_source": "robinhood_mcp",
    }


@pytest.mark.parametrize("script", STAMPING_SCRIPTS)
def test_stale_snapshot_is_degraded_at_default_threshold(
    script: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GESH_DEGRADED_AFTER_S", raising=False)
    info = _extract_helper(script)(_snap(7200, source="robinhood_live"), now=NOW)
    assert info == {
        "degraded": True,
        "snapshot_age_seconds": 7200,
        "snapshot_source": "robinhood_live",
    }


@pytest.mark.parametrize("script", STAMPING_SCRIPTS)
def test_env_threshold_override_widens_the_window(
    script: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GESH_DEGRADED_AFTER_S", "10800")
    info = _extract_helper(script)(_snap(7200), now=NOW)
    assert info["degraded"] is False
    assert info["snapshot_age_seconds"] == 7200


@pytest.mark.parametrize("script", STAMPING_SCRIPTS)
def test_unknown_snapshot_age_is_degraded_fail_safe(
    script: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GESH_DEGRADED_AFTER_S", raising=False)
    info = _extract_helper(script)({}, now=NOW)
    assert info == {
        "degraded": True,
        "snapshot_age_seconds": None,
        "snapshot_source": "UNKNOWN",
    }


@pytest.mark.parametrize("script", STAMPING_SCRIPTS)
def test_stamp_is_wired_into_the_script_body(script: str) -> None:
    """The helper must actually be CALLED outside its own def, so the stamp
    lands in the dict each script persists to engine_results.json."""
    tree = ast.parse((REPO_ROOT / script).read_text())
    body_wo_def = [
        n for n in tree.body
        if not (isinstance(n, ast.FunctionDef) and n.name == HELPER)
    ]
    calls = [
        n for stmt in body_wo_def for n in ast.walk(stmt)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == HELPER
    ]
    assert calls, f"{script} must call {HELPER}(...) in its pipeline body"


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
