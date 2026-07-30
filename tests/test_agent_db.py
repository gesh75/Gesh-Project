"""Canonical agent DB path — unified gesh_financial.db."""

from __future__ import annotations

import agent_db
import db as _db


def test_agent_db_path_defaults_to_gesh_financial(monkeypatch, tmp_path):
    # db.DB_PATH is isolated per-test (conftest._isolate_equity_snapshots_db) so
    # this asserts agent_db_path()'s fallback logic, not the ambient real value.
    monkeypatch.delenv("GESH_DB_PATH", raising=False)
    monkeypatch.setattr(_db, "DB_PATH", tmp_path / "gesh_financial.db", raising=False)
    p = agent_db.agent_db_path()
    assert p.name == "gesh_financial.db"
    assert p.suffix == ".db"


def test_agent_db_path_env_override(monkeypatch, tmp_path):
    custom = tmp_path / "custom.db"
    monkeypatch.setenv("GESH_DB_PATH", str(custom))
    assert agent_db.agent_db_path() == custom