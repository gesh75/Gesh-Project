"""Regression tests for gap-hardening: healthz, backups paths, login cookie notice."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import ops_backup
import server


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("GESH_AUTH_ENABLED", raising=False)
    return TestClient(server.app, raise_server_exceptions=False)


def test_healthz_includes_uptime_and_no_secrets(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "auth" in body
    assert isinstance(body["uptime_seconds"], int)
    assert body["uptime_seconds"] >= 0
    # Public probe must never leak paths, emails, or portfolio figures.
    blob = str(body).lower()
    for forbidden in ("/users/", "portfolio", "equity", "password", "secret", "@"):
        assert forbidden not in blob


def test_healthz_optional_build_sha(client, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GESH_BUILD_SHA", "abc123deadbeef")
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json().get("build") == "abc123deadbeef"


def test_ops_backup_targets_are_repo_relative_not_hardcoded_users():
    # "Repo-relative" means every target resolves INSIDE the configured base. A string
    # check for "/Users/" is wrong: a checkout under a home directory (this Mac, any
    # worktree) legitimately contains it, which made the suite red by default (T-01).
    base = Path(os.getenv("GESH_HOME", Path(ops_backup.__file__).resolve().parent))
    trading = Path(os.getenv("GESH_TRADING_HOME", base.parent / "gesh-project-trading"))
    for t in ops_backup.TARGETS:
        assert Path(t).is_relative_to(base) or Path(t).is_relative_to(trading), (
            f"target is not under the configured roots ({base} / {trading}): {t}"
        )
    assert base / "gesh_financial.db" in ops_backup.TARGETS
    assert base / "gesh_auth.db" in ops_backup.TARGETS


def test_ops_backup_honors_gesh_home_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("GESH_HOME", str(tmp_path))
    monkeypatch.setenv("GESH_TRADING_HOME", str(tmp_path / "trading"))
    import importlib

    importlib.reload(ops_backup)
    try:
        assert ops_backup.TARGETS[0] == tmp_path / "gesh_financial.db"
        assert any(p.name == "crypto_financial.db" for p in ops_backup.TARGETS)
    finally:
        monkeypatch.delenv("GESH_HOME", raising=False)
        monkeypatch.delenv("GESH_TRADING_HOME", raising=False)
        importlib.reload(ops_backup)


def test_login_page_discloses_session_cookie(client):
    r = client.get("/login")
    assert r.status_code == 200
    text = r.text
    assert "session cookie" in text.lower() or "HttpOnly session cookie" in text
    assert "/privacy" in text
