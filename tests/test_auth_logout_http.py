"""Sign-out must retire the copied cookie, and a new login must retire the previous one."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
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


def _sign_without_iat(user_id: str) -> str:
    claims = {
        "sub": user_id,
        "exp": int(time.time()) + 900,
        "typ": "session",
        "ver": 2,
        "jti": "stale-session-jti",
    }
    payload = auth_users._b64u(json.dumps(claims, separators=(",", ":")).encode())
    signature = auth_users._b64u(
        hmac.new(auth_users._secret(), payload.encode(), hashlib.sha256).digest()
    )
    return f"{payload}.{signature}"


def _login(client: TestClient, email: str = "a@b.com", password: str = "pw") -> str:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200
    token = response.cookies.get(auth_users.COOKIE_NAME)
    assert token
    return str(token)


def test_login_again_retires_the_previous_session(
    isolated_auth: None, https_client: TestClient
) -> None:
    auth_users.create_user("a@b.com", "pw")
    older = _login(https_client)
    newer = _login(https_client)
    assert older != newer
    user_id = auth_users.resolve_user_id("a@b.com")
    with auth_users._conn() as con:
        row = con.execute(
            "SELECT user_id FROM revoked_sessions WHERE jti!=?",
            ("unused",),
        ).fetchone()
    assert row is not None
    assert row["user_id"] == user_id
    assert https_client.get("/api/watchlist", cookies={auth_users.COOKIE_NAME: older}).status_code == 401
    assert https_client.get("/api/watchlist", cookies={auth_users.COOKIE_NAME: newer}).status_code == 200


def test_login_retires_a_session_minted_before_rotation_existed(
    isolated_auth: None, https_client: TestClient
) -> None:
    auth_users.create_user("a@b.com", "pw")
    user_id = auth_users.resolve_user_id("a@b.com")
    assert user_id is not None
    stale = _sign_without_iat(user_id)
    assert https_client.get("/api/watchlist", cookies={auth_users.COOKIE_NAME: stale}).status_code == 200
    current = _login(https_client)
    assert https_client.get("/api/watchlist", cookies={auth_users.COOKIE_NAME: stale}).status_code == 401
    assert https_client.get("/api/watchlist", cookies={auth_users.COOKIE_NAME: current}).status_code == 200


def test_magic_link_login_retires_the_previous_session(
    isolated_auth: None, https_client: TestClient
) -> None:
    auth_users.create_user("a@b.com", "pw")
    older = _login(https_client)
    magic = auth_users.issue_magic_token("a@b.com")
    assert magic
    landed = https_client.get(f"/auth/magic?token={magic}", follow_redirects=False)
    assert landed.status_code == 303
    newer = landed.cookies.get(auth_users.COOKIE_NAME)
    assert newer
    assert newer != older
    assert https_client.get("/api/watchlist", cookies={auth_users.COOKIE_NAME: older}).status_code == 401
    assert https_client.get("/api/watchlist", cookies={auth_users.COOKIE_NAME: newer}).status_code == 200


def test_dashboard_sign_out_waits_for_a_successful_logout() -> None:
    html = (REPO / "templates" / "dashboard.html").read_text()
    button = html.split('id="sign-out"', 1)[1].split("</script>", 1)[0]
    assert 'fetch("/auth/logout"' in button
    assert 'method: "POST"' in button
    assert "JSON.stringify" not in button
    assert "response.ok" in button
    assert 'location.href = "/login"' in button
    assert button.index("response.ok") < button.index('location.href = "/login"')
