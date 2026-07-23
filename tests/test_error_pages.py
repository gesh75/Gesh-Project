"""Browser refusals must be pages, not framework internals.

Audit BUG-08 (2026-07-22): navigating to /mybook rendered
`{"detail":"Not Found"}` and /invest rendered `{"detail":"Owner-only view"}` —
raw JSON in the browser window. API clients still need that JSON, so the split
is by what the caller asked for (Accept), not by guesswork.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

CUSTOMER = "cust@x.com"
OWNER = "georgy75@gmail.com"
HTML = {"Accept": "text/html,application/xhtml+xml"}
JSON = {"Accept": "application/json"}


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    import auth_users
    import server
    monkeypatch.setattr(auth_users, "AUTH_DB_PATH", tmp_path / "auth.db")
    auth_users.init_auth_db()
    for e in (CUSTOMER, OWNER):
        auth_users.create_user(e, "test-password")
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: True)
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: CUSTOMER)
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda p, c: "allow", raising=False)
    monkeypatch.setattr(server.auth_users, "is_owner", lambda e: e == OWNER, raising=False)
    monkeypatch.setattr(server, "_customer_scale_factor", lambda: 2.0, raising=True)
    c = TestClient(server.app, raise_server_exceptions=False)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    return server, c


def test_unknown_page_renders_html_not_json(client):
    _server, c = client
    r = c.get("/mybook", headers=HTML)
    assert r.status_code == 404
    assert "text/html" in r.headers["content-type"]
    assert '{"detail"' not in r.text
    assert "Page not found" in r.text


def test_owner_only_page_renders_html_not_json(client):
    _server, c = client
    r = c.get("/invest", headers=HTML)
    assert r.status_code == 403
    assert "text/html" in r.headers["content-type"]
    assert '{"detail"' not in r.text
    assert "operator console" in r.text


def test_error_page_offers_a_way_back(client):
    _server, c = client
    assert 'href="/"' in c.get("/nope", headers=HTML).text


def test_api_clients_still_get_json(client):
    _server, c = client
    r = c.get("/api/does-not-exist", headers=JSON)
    assert "application/json" in r.headers["content-type"]
    assert r.json()["detail"]


def test_api_paths_stay_json_even_for_a_browser_accept_header(client):
    """The dashboard's fetch() sends the document's Accept header; /api/* must
    never start returning HTML to it."""
    _server, c = client
    r = c.get("/api/does-not-exist", headers=HTML)
    assert "application/json" in r.headers["content-type"]


def test_error_page_escapes_its_content(client):
    _server, c = client
    r = c.get("/%3Cscript%3Ealert(1)%3C/script%3E", headers=HTML)
    assert "<script>alert(1)" not in r.text


def test_server_error_detail_is_not_rendered(client, monkeypatch):
    """A 5xx detail can carry internal context — the page must stay generic."""
    server, c = client
    from fastapi import HTTPException

    @server.app.get("/__boom_test")
    def _boom():
        raise HTTPException(status_code=503, detail="postgres://secret@internal:5432 down")

    r = c.get("/__boom_test", headers=HTML)
    assert r.status_code == 503
    assert "postgres" not in r.text and "internal:5432" not in r.text
