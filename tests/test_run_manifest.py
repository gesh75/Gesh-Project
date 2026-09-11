from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from pathlib import Path

import pytest

from run_manifest import (
    RunManifest,
    atomic_write_json,
    available_rsi_by_symbol,
    chain_io_paths,
    clear_run_scoped_outputs,
    format_optional_metric,
    report_output_path,
)

UTC = timezone.utc


def test_manifest_is_frozen_and_defensively_copies_quotes() -> None:
    quotes = {"NVDA": datetime(2026, 7, 17, 19, 59, 59, tzinfo=UTC)}
    manifest = RunManifest.create(
        source="robinhood_mcp",
        snapshot_as_of=datetime(2026, 7, 17, 20, 0, tzinfo=UTC),
        degraded=False,
        quote_as_of=quotes,
        now=datetime(2026, 7, 17, 20, 1, tzinfo=UTC),
    )
    quotes["NVDA"] = datetime(2000, 1, 1, tzinfo=UTC)

    assert manifest.quote_as_of["NVDA"].year == 2026
    with pytest.raises(TypeError):
        manifest.quote_as_of["MU"] = datetime.now(UTC)  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        manifest.degraded = True  # type: ignore[misc]


def test_manifest_round_trip_and_atomic_embedding(tmp_path: Path) -> None:
    manifest = RunManifest.create(
        source="robinhood_mcp",
        snapshot_as_of="2026-07-17T20:00:00Z",
        degraded=True,
        quote_as_of={"NVDA": "2026-07-17T19:59:59.997524406Z"},
        now="2026-07-17T20:01:00Z",
    )
    path = tmp_path / "run_manifest.json"
    atomic_write_json(path, manifest.to_dict())

    saved = json.loads(path.read_text())
    restored = RunManifest.from_dict(saved)
    assert restored == manifest
    assert saved["run_id"] == manifest.run_id
    assert saved["quote_as_of"]["NVDA"].endswith("Z")


def test_chain_io_paths_default_to_owner_files(tmp_path: Path) -> None:
    snap, eng, man = chain_io_paths(tmp_path, env={})
    assert snap == tmp_path / "live_portfolio_snapshot.json"
    assert eng == tmp_path / "engine_results.json"
    assert man == tmp_path / "run_manifest.json"


def test_chain_io_paths_isolated_engine_results_keeps_manifest_beside_it(tmp_path: Path) -> None:
    isolated = tmp_path / "friend" / "engine_results.json"
    snap, eng, man = chain_io_paths(
        tmp_path,
        env={
            "GESH_SNAPSHOT_PATH": str(tmp_path / "friend" / "snapshot.json"),
            "GESH_ENGINE_RESULTS": str(isolated),
        },
    )
    assert snap == tmp_path / "friend" / "snapshot.json"
    assert eng == isolated
    assert man == tmp_path / "friend" / "run_manifest.json"


def test_degraded_report_can_never_replace_current_report(tmp_path: Path) -> None:
    current = tmp_path / "gesh_report.html"
    eng = {"run_manifest": {"run_id": "abc-123", "degraded": True}}
    assert report_output_path(tmp_path, eng, requested=current) == (
        tmp_path / "gesh_report.degraded.abc-123.html"
    )
    assert (
        report_output_path(
            tmp_path, {"run_manifest": {"run_id": "abc-123", "degraded": False}}, requested=current
        )
        == current
    )


def test_new_run_clears_gate_outputs_and_fossils_without_mutating_input() -> None:
    previous = {
        "action_plan": [{"symbol": "OLD"}],
        "gate_results": [{"approved": True}],
        "paper_session": {"created": [{"id": 1}]},
        "trade_stats": {"paper": {"wins": 1}},
        "dod_delta": {"stale": True},
        "cash_unlock": {"stale": True},
        "screener": {"picks": ["KEEP"]},
    }

    fresh = clear_run_scoped_outputs(previous)

    assert fresh == {"screener": {"picks": ["KEEP"]}}
    assert previous["action_plan"] == [{"symbol": "OLD"}]
    assert previous["dod_delta"] == {"stale": True}


@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), "not-a-number", True])
def test_optional_metric_formats_unavailable_as_na(value: object) -> None:
    assert format_optional_metric(value, ".1f") == "n/a"


def test_optional_metric_preserves_a_measured_zero() -> None:
    assert format_optional_metric(0, ".1f") == "0.0"
    assert format_optional_metric(12.345, ".1f") == "12.3"


def test_available_rsi_map_omits_unknown_and_preserves_measured_zero() -> None:
    rows = [
        {"symbol": "UNKNOWN", "rsi": None},
        {"symbol": "MISSING"},
        {"symbol": "NAN", "rsi": float("nan")},
        {"symbol": "ZERO", "rsi": 0},
        {"symbol": "HOT", "rsi": 72.5},
        {"rsi": 55},
    ]

    assert available_rsi_by_symbol(rows) == {"ZERO": 0.0, "HOT": 72.5}


def test_daily_chain_scripts_use_run_scope_and_optional_metric_helpers() -> None:
    root = Path(__file__).resolve().parents[1]
    engines = (root / "run_engines2.py").read_text()
    ai = (root / "run_ai2.py").read_text()
    gate = (root / "run_paper_gate2.py").read_text()

    assert "clear_run_scoped_outputs(results)" in engines
    assert engines.count("format_optional_metric(") >= 2
    assert ai.count("format_optional_metric(") >= 4
    assert "available_rsi_by_symbol(regimes_list)" in ai
    assert "available_rsi_by_symbol(regimes_list)" in gate
