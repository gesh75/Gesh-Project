from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from pathlib import Path

import pytest

from run_manifest import RunManifest, atomic_write_json, report_output_path

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


def test_degraded_report_can_never_replace_current_report(tmp_path: Path) -> None:
    current = tmp_path / "gesh_report.html"
    eng = {"run_manifest": {"run_id": "abc-123", "degraded": True}}
    assert report_output_path(tmp_path, eng, requested=current) == (
        tmp_path / "gesh_report.degraded.abc-123.html"
    )
    assert report_output_path(
        tmp_path, {"run_manifest": {"run_id": "abc-123", "degraded": False}}, requested=current
    ) == current
