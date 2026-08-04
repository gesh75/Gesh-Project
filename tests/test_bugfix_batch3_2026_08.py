"""Regression tests for the 2026-08 bug-fix batch #3 (security hardening).

Covers:
  S2  _require_owner_view fails closed for unauthenticated (401) and customer
      (403) requests to the operator cockpit routes, while allowing the owner.
  S3a CSRF/Origin enforcement now also covers /auth/* mutations.
  S3b /api/mybook/bulk caps the request body (413 on oversize).

All offline.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.testclient import TestClient


# ── S2: owner-view defense-in-depth gate ─────────────────────────────────────

def test_require_owner_view_blocks_unauthenticated_and_customers(monkeypatch):
    import server

    class _Req:
        cookies: dict = {}

    # Unauthenticated (auth enabled, no session) → 401, before any DB lookup.
    monkeypatch.setattr(server, "_session_email", lambda r: None)
    with pytest.raises(HTTPException) as ei:
        server._require_owner_view(_Req())
    assert ei.value.status_code == 401

    # Authenticated customer (non-owner) → 403.
    monkeypatch.setattr(server, "_session_email", lambda r: "cust@example.com")
    monkeypatch.setattr(server.auth_users, "is_owner", lambda em: False)
    with pytest.raises(HTTPException) as ec:
        server._require_owner_view(_Req())
    assert ec.value.status_code == 403

    # Owner → allowed (no raise).
    monkeypatch.setattr(server.auth_users, "is_owner", lambda em: True)
    server._require_owner_view(_Req())


@pytest.fixture
def client(tmp_path, monkeypatch):
    import auth_users as _au

    monkeypatch.setattr(_au, "AUTH_DB_PATH", tmp_path / "auth.db")
    _au.init_auth_db()
    import server

    return TestClient(server.app)


def test_watch_data_blocked_when_auth_enabled_no_session(client, monkeypatch):
    import auth_users

    # Auth ON, no session → the route must never be served (login wall 302, or the
    # owner-gate 401/403). The point is: not a 200 owner serve to an anonymous user.
    monkeypatch.setattr(auth_users, "auth_enabled", lambda: True)
    resp = client.get("/watch/data", follow_redirects=False)
    assert resp.status_code in (302, 401, 403)


# ── S3a: CSRF now covers /auth/* ─────────────────────────────────────────────

def test_auth_logout_refuses_cross_origin_with_cookie(client):
    import auth_users

    # CSRF only guards COOKIE-authenticated mutations (no cookie = no ambient
    # authority to forge). With a session cookie present, a cross-site Origin on a
    # /auth mutation must now be refused — /auth/* was previously outside CSRF scope.
    resp = client.post(
        "/auth/logout",
        headers={"Origin": "http://evil.test", "Cookie": f"{auth_users.COOKIE_NAME}=x"},
    )
    assert resp.status_code == 403
    assert "cross-origin" in resp.text.lower()


# ── S3b: bulk import body cap ────────────────────────────────────────────────

def test_mybook_bulk_rejects_oversize_body(client):
    # >256 KiB body → 413 before parsing (authenticated DoS guard). Auth is off in
    # tests, so _session_email resolves to the owner and passes the 401 check; the
    # size cap is what must fire.
    huge = "A" * 300_000
    resp = client.post("/api/mybook/bulk", json={"text": huge})
    assert resp.status_code == 413
