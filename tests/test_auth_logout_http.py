"""Sign-out must retire the copied cookie, and only drop the browser cookie after that write."""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

import auth_users
import server

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def isolated_auth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GESH_AUTH_DB", str(tmp_path / "auth.db"))
    monkeypatch.setenv("GESH_AUTH_SECRET", "test-secret-deterministic")
    monkeypatch.setenv("GESH_AUTH_ENABLED", "1")
    monkeypatch.setattr(auth_users, "AUTH_DB_PATH", tmp_path / "auth.db")
    auth_users.init_auth_db()


@pytest.fixture
def https_client() -> TestClient:
    return TestClient(server.app, base_url="https://testserver", raise_server_exceptions=False)


def _session(email: str = "a@b.com") -> str:
    auth_users.create_user(email, "pw")
    return auth_users.issue_token(email)


def test_logout_blacklists_the_copied_cookie_and_clears_it_securely(
    isolated_auth: None, https_client: TestClient
) -> None:
    token = _session()
    cookies = {auth_users.COOKIE_NAME: token}
    assert https_client.get("/api/watchlist", cookies=cookies).status_code == 200

    logged_out = https_client.post("/auth/logout", cookies=cookies)
    assert logged_out.status_code == 200
    clearing = logged_out.headers["set-cookie"].lower()
    assert "gesh_session=" in clearing
    assert "secure" in clearing
    assert "httponly" in clearing
    assert "samesite=lax" in clearing
    assert "path=/" in clearing
    assert https_client.get("/api/watchlist", cookies=cookies).status_code == 401


def test_failed_revoke_keeps_the_session_cookie(
    isolated_auth: None, https_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = _session()
    cookies = {auth_users.COOKIE_NAME: token}
    monkeypatch.setattr(auth_users, "revoke_session", lambda _token: "failed")

    logged_out = https_client.post("/auth/logout", cookies=cookies)
    assert logged_out.status_code == 503
    assert "set-cookie" not in {name.lower() for name in logged_out.headers}
    assert https_client.get("/api/watchlist", cookies=cookies).status_code == 200


def test_dashboard_sign_out_waits_for_a_successful_logout() -> None:
    html = (REPO / "templates" / "dashboard.html").read_text()
    button = html.split('id="sign-out"', 1)[1].split("</script>", 1)[0]
    assert 'fetch("/auth/logout"' in button
    assert 'method: "POST"' in button
    assert "JSON.stringify" not in button
    assert "response.ok" in button
    assert 'location.href = "/login"' in button
    assert button.index("response.ok") < button.index('location.href = "/login"')
