"""Canonical agent DB path — unified gesh_financial.db."""

from __future__ import annotations

import agent_db


def test_agent_db_path_defaults_to_gesh_financial(monkeypatch):
    monkeypatch.delenv("GESH_DB_PATH", raising=False)
    p = agent_db.agent_db_path()
    assert p.name == "gesh_financial.db"
    assert p.suffix == ".db"


def test_agent_db_path_env_override(monkeypatch, tmp_path):
    custom = tmp_path / "custom.db"
    monkeypatch.setenv("GESH_DB_PATH", str(custom))
    assert agent_db.agent_db_path() == custom