"""Per-user watchlist — saved tickers for the customer 'analyze any ticker' view.

Storage lives in the auth DB keyed by (email, symbol); the endpoints scope every
read/write to the SESSION user, so one customer can never see or mutate another's
list. Symbols are public tickers (no owner dollars), so masking doesn't apply —
but per-user isolation does, and these tests lock it.
"""

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


# ── auth_users storage layer ─────────────────────────────────────────────────

def test_add_get_remove_roundtrip(au):
    assert au.get_watch("a@x.com") == []
    au.add_watch("a@x.com", "nvda")
    au.add_watch("a@x.com", "AAPL")
    assert au.get_watch("a@x.com") == ["NVDA", "AAPL"]   # uppercased, insertion order
    au.remove_watch("a@x.com", "nvda")
    assert au.get_watch("a@x.com") == ["AAPL"]


def test_add_is_idempotent(au):
    assert au.add_watch("a@x.com", "NVDA") is True
    assert au.add_watch("a@x.com", "NVDA") is True     # dup → still True, not duplicated
    assert au.get_watch("a@x.com") == ["NVDA"]


def test_watchlist_is_per_user_isolated(au):
    au.add_watch("a@x.com", "NVDA")
    au.add_watch("b@x.com", "TSLA")
    assert au.get_watch("a@x.com") == ["NVDA"]
    assert au.get_watch("b@x.com") == ["TSLA"]          # B never sees A's list


def test_add_capped(au):
    for i in range(60):
        au.add_watch("a@x.com", f"SYM{i}")
    got = au.get_watch("a@x.com")
    assert len(got) == au._MAX_WATCH                    # cap enforced


# ── endpoints (session-scoped) ───────────────────────────────────────────────

@pytest.fixture
def client(au, monkeypatch):
    import server
    monkeypatch.setattr(server.auth_users, "AUTH_DB_PATH", au.AUTH_DB_PATH)
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: True)
    return server, TestClient(server.app, raise_server_exceptions=False)


def _as(server, monkeypatch, email):
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: email)
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda p, c: "allow", raising=False)


def test_endpoint_add_then_list(client, monkeypatch):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    assert c.post("/api/watchlist", json={"symbol": "nvda"}).status_code == 200
    r = c.get("/api/watchlist")
    assert r.status_code == 200
    assert "NVDA" in r.json()["data"]


def test_endpoint_delete(client, monkeypatch):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    c.post("/api/watchlist", json={"symbol": "NVDA"})
    c.delete("/api/watchlist/NVDA")
    assert c.get("/api/watchlist").json()["data"] == []


def test_endpoint_requires_session(client, monkeypatch):
    server, c = client
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: None)
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda p, c: "allow", raising=False)
    assert c.get("/api/watchlist").status_code == 401


def test_endpoint_per_user_isolation(client, monkeypatch):
    server, c = client
    _as(server, monkeypatch, "a@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    c.post("/api/watchlist", json={"symbol": "NVDA"})
    _as(server, monkeypatch, "b@x.com")          # switch session to user B
    assert "NVDA" not in c.get("/api/watchlist").json()["data"]
