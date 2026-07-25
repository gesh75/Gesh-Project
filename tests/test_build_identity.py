"""Safe, immutable build-identity contract for release artifacts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import server

VALID_BUILD_INFO = {
    "service": "gesh-financial",
    "commit_sha": "a" * 40,
    "built_at": "2026-07-25T16:00:00Z",
    "dirty": False,
}


def _write(path: Path, value) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _auth_disabled_client(monkeypatch) -> TestClient:
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: False)
    return TestClient(server.app, raise_server_exceptions=False)


def test_version_endpoint_returns_only_validated_release_identity(tmp_path, monkeypatch):
    build_info = tmp_path / "GESH_BUILD_INFO.json"
    _write(build_info, VALID_BUILD_INFO)
    monkeypatch.setattr(server, "BUILD_INFO_PATH", build_info)

    response = _auth_disabled_client(monkeypatch).get("/api/version")

    assert response.status_code == 200
    assert response.json() == VALID_BUILD_INFO
    assert set(response.json()) == {"service", "commit_sha", "built_at", "dirty"}


def test_version_endpoint_is_principal_neutral_not_customer_masked(tmp_path, monkeypatch):
    build_info = tmp_path / "GESH_BUILD_INFO.json"
    _write(build_info, VALID_BUILD_INFO)
    monkeypatch.setattr(server, "BUILD_INFO_PATH", build_info)
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: True)
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda _p, _c: "allow")
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda _token: "customer@x.test")
    monkeypatch.setattr(server.auth_users, "is_owner", lambda _email: False)
    client = TestClient(server.app, raise_server_exceptions=False)
    client.cookies.set(server.auth_users.COOKIE_NAME, "customer-token")

    response = client.get("/api/version")

    assert response.status_code == 200
    assert response.json() == VALID_BUILD_INFO
    assert "view" not in response.json()


def test_missing_build_identity_fails_closed_without_path_disclosure(tmp_path, monkeypatch):
    missing = tmp_path / "private" / "GESH_BUILD_INFO.json"
    monkeypatch.setattr(server, "BUILD_INFO_PATH", missing)

    response = _auth_disabled_client(monkeypatch).get("/api/version")

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Build identity is unavailable.",
        "code": "build_identity_unavailable",
    }
    assert str(missing) not in response.text


@pytest.mark.parametrize(
    "invalid",
    [
        {**VALID_BUILD_INFO, "dirty": True},
        {**VALID_BUILD_INFO, "commit_sha": "a" * 39},
        {**VALID_BUILD_INFO, "commit_sha": "g" * 40},
        {**VALID_BUILD_INFO, "built_at": "2026-07-25T18:00:00+02:00"},
        {**VALID_BUILD_INFO, "built_at": "not-a-time"},
        {**VALID_BUILD_INFO, "service": "other-service"},
        {**VALID_BUILD_INFO, "branch": "secret/internal"},
    ],
)
def test_invalid_or_non_allowlisted_identity_fails_closed(
    invalid, tmp_path, monkeypatch
):
    build_info = tmp_path / "GESH_BUILD_INFO.json"
    _write(build_info, invalid)
    monkeypatch.setattr(server, "BUILD_INFO_PATH", build_info)

    response = _auth_disabled_client(monkeypatch).get("/api/version")

    assert response.status_code == 503
    assert response.json()["code"] == "build_identity_unavailable"
    assert "branch" not in response.text
    assert "other-service" not in response.text


def test_malformed_identity_never_echoes_file_content(tmp_path, monkeypatch):
    build_info = tmp_path / "GESH_BUILD_INFO.json"
    secret_sentinel = "DO-NOT-ECHO-THIS"
    build_info.write_text("{broken:" + secret_sentinel, encoding="utf-8")
    monkeypatch.setattr(server, "BUILD_INFO_PATH", build_info)

    response = _auth_disabled_client(monkeypatch).get("/api/version")

    assert response.status_code == 503
    assert response.json()["code"] == "build_identity_unavailable"
    assert secret_sentinel not in response.text
