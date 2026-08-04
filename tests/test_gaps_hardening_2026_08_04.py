"""Regression tests for gap-hardening: healthz, backups paths, login cookie notice."""

from __future__ import annotations

from pathlib import Path

import ops_backup


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


def test_healthz_optional_build_sha(client, monkeypatch):
    monkeypatch.setenv("GESH_BUILD_SHA", "abc123deadbeef")
    # Re-import not required — healthz reads env each call.
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json().get("build") == "abc123deadbeef"


def test_ops_backup_targets_are_repo_relative_not_hardcoded_users():
    for t in ops_backup.TARGETS:
        assert "/Users/" not in str(t), f"hardcoded host path: {t}"
    # Default product DBs live under this repo root.
    base = Path(ops_backup.__file__).resolve().parent
    assert base / "gesh_financial.db" in ops_backup.TARGETS
    assert base / "gesh_auth.db" in ops_backup.TARGETS


def test_ops_backup_honors_gesh_home_env(monkeypatch, tmp_path):
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
