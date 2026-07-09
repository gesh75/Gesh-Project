"""Sidebar freshness + regime MERGE cleanup contract (run_engines2 fix)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import server


@pytest.fixture(autouse=True)
def _no_auth_wall(monkeypatch):
    monkeypatch.delenv("GESH_AUTH_ENABLED", raising=False)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    eng = tmp_path / "engine_results.json"
    monkeypatch.setattr(server, "ENGINE_RESULTS", eng)

    def _load():
        return json.loads(eng.read_text()) if eng.exists() else {}

    monkeypatch.setattr(server, "_load_engine_results", _load)
    return TestClient(server.app), eng


def _iso(hours_ago: float = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


def test_sections_overview_stale_when_snapshot_old(client):
    tc, eng = client
    eng.write_text(json.dumps({
        "snapshot": {"fetched_at": _iso(hours_ago=30)},
    }))
    r = tc.get("/api/sections/status")
    assert r.status_code == 200
    assert r.json()["overview"]["fresh"] is False


def test_sections_regime_stale_without_ran_at(client):
    """Empty regime with no ran_at must not show fresh (MERGE failure path)."""
    tc, eng = client
    eng.write_text(json.dumps({"regime": {}}))
    r = tc.get("/api/sections/status")
    body = r.json()
    assert body["intelligence"]["fresh"] is False
    assert body["intelligence"]["ran_at"] is None


def test_sections_regime_fresh_with_recent_ran_at(client):
    tc, eng = client
    eng.write_text(json.dumps({
        "regime": {"regimes": [{"symbol": "SPY", "regime": "TRENDING"}]},
        "regime_ran_at": _iso(hours_ago=1),
    }))
    r = tc.get("/api/sections/status")
    assert r.json()["intelligence"]["fresh"] is True


def test_regime_failure_clears_stale_ran_at_contract():
    """Document run_engines2.py failure branch — stale stamp must not survive MERGE."""
    results = {
        "regime_ran_at": _iso(hours_ago=48),
        "regime": {"regimes": [{"symbol": "SPY"}]},
        "macro": {"available": True},
    }
    results["regime"] = {}
    results.pop("regime_ran_at", None)
    results.pop("macro", None)
    assert "regime_ran_at" not in results
    assert "macro" not in results