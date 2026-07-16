"""GET/POST /api/preferences — the "re-enable from the dashboard" path the
unsubscribe pages (/alerts/unsubscribe, /digest/unsubscribe) promise but that
did not exist until now (audit finding #3). Wires the existing
auth_users.set_alerts_opt_out/set_digest_opt_out/get_* helpers behind a
session-gated pair of routes; only keys present in the POST body change."""
from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient


@pytest.fixture
def au(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import auth_users as _au
    monkeypatch.setattr(_au, "AUTH_DB_PATH", tmp_path / "auth.db")
    _au.init_auth_db()
    return _au


@pytest.fixture
def client(au, monkeypatch):
    import server
    monkeypatch.setattr(server.auth_users, "AUTH_DB_PATH", au.AUTH_DB_PATH)
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: True)
    return server, TestClient(server.app, raise_server_exceptions=False)


def _as(server, monkeypatch, email):
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: email)
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda p, c: "allow", raising=False)
    monkeypatch.setattr(server.auth_users, "is_owner",
                        lambda e: e == "georgy75@gmail.com", raising=False)


def test_get_defaults_both_flags_false(client, monkeypatch):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    d = c.get("/api/preferences").json()["data"]
    assert d == {"alerts_opt_out": False, "digest_opt_out": False}


def test_post_roundtrip_flips_both_flags(client, monkeypatch, au):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    r = c.post("/api/preferences", json={"alerts_opt_out": True, "digest_opt_out": True})
    assert r.status_code == 200
    assert r.json()["data"] == {"alerts_opt_out": True, "digest_opt_out": True}
    assert au.get_alerts_opt_out("cust@x.com") is True
    assert au.get_digest_opt_out("cust@x.com") is True
    # roundtrip back off
    r = c.post("/api/preferences", json={"alerts_opt_out": False, "digest_opt_out": False})
    assert r.json()["data"] == {"alerts_opt_out": False, "digest_opt_out": False}


def test_post_partial_body_only_changes_provided_key(client, monkeypatch, au):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    au.set_alerts_opt_out("cust@x.com", True)
    au.set_digest_opt_out("cust@x.com", False)
    # only flip digest — alerts must be left untouched
    r = c.post("/api/preferences", json={"digest_opt_out": True})
    assert r.json()["data"] == {"alerts_opt_out": True, "digest_opt_out": True}
    assert au.get_alerts_opt_out("cust@x.com") is True


def test_get_requires_session(client, monkeypatch):
    server, c = client
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: None)
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda p, c: "allow", raising=False)
    assert c.get("/api/preferences").status_code == 401


def test_post_requires_session(client, monkeypatch):
    server, c = client
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: None)
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda p, c: "allow", raising=False)
    assert c.post("/api/preferences", json={"alerts_opt_out": True}).status_code == 401


def test_per_user_isolation(client, monkeypatch, au):
    server, c = client
    _as(server, monkeypatch, "a@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    c.post("/api/preferences", json={"alerts_opt_out": True})
    _as(server, monkeypatch, "b@x.com")
    d = c.get("/api/preferences").json()["data"]
    assert d == {"alerts_opt_out": False, "digest_opt_out": False}


def test_reenable_via_preferences_after_unsubscribe_link(client, monkeypatch, au):
    """End-to-end of the promise on the unsubscribe page: click the email link
    (sets opt_out True), then flip it back via the new dashboard endpoint."""
    server, c = client
    tok = au.issue_token("cust@x.com", ttl=86400, typ="alerts_unsub")
    assert c.get(f"/alerts/unsubscribe?token={tok}").status_code == 200
    assert au.get_alerts_opt_out("cust@x.com") is True
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    r = c.post("/api/preferences", json={"alerts_opt_out": False})
    assert r.json()["data"]["alerts_opt_out"] is False
    assert au.get_alerts_opt_out("cust@x.com") is False
