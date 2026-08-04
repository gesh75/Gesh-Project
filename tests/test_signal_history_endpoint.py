"""GET /api/signals/history/{symbol} — 30-day consensus verdict/score series that
feeds the ticker-card sparkline (feature 3). Session-gated (require_customer_or_owner,
same as the ticker card), mechanical read of signal_history — no LLM, no quota.
Empty history is a normal 200, never a 500."""
from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

import signal_history as sh


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import auth_users as au
    import server
    monkeypatch.setattr(au, "AUTH_DB_PATH", tmp_path / "auth.db")
    au.init_auth_db()
    monkeypatch.setattr(server.auth_users, "AUTH_DB_PATH", au.AUTH_DB_PATH)
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: True)
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: "cust@x.com")
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda p, c: "allow", raising=False)
    au.create_user("cust@x.com", "test-password")
    au.add_watch("cust@x.com", "NVDA")
    au.add_watch("cust@x.com", "ZZZZ9")
    monkeypatch.setattr(sh, "DB_PATH", tmp_path / "sig.db")
    c = TestClient(server.app, raise_server_exceptions=False)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    return server, c


def test_history_endpoint_returns_series(client):
    server, c = client
    sh.record_snapshots([{"symbol": "NVDA", "verdict": "BUY", "score": 0.4,
                          "confidence": 80}], date="2026-07-15")
    r = c.get("/api/signals/history/NVDA")
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["symbol"] == "NVDA" and d["history"][0]["verdict"] == "BUY"


def test_history_endpoint_empty_is_ok_not_500(client):
    server, c = client
    r = c.get("/api/signals/history/ZZZZ9")
    assert r.status_code == 200 and r.json()["data"]["history"] == []


def test_history_endpoint_requires_session(client, monkeypatch):
    server, c = client
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: None)
    assert c.get("/api/signals/history/NVDA").status_code in (401, 403)


def test_history_endpoint_rejects_another_customers_symbol(client):
    server, c = client
    server.auth_users.create_user("other@x.com", "test-password")
    server.auth_users.add_watch("other@x.com", "ALICEONLY")
    sh.record_snapshots([{"symbol": "ALICEONLY", "verdict": "BUY", "score": 0.4,
                          "confidence": 80}], date="2026-07-15")

    r = c.get("/api/signals/history/ALICEONLY")

    assert r.status_code == 403
    assert "history" not in r.text


def test_history_endpoint_is_cached(client, monkeypatch):
    """Second call within the 5-min TTL must not re-query signal_history — it
    should be served from the shared `_ticker_get_cached('sighist', ...)` bucket,
    matching the other per-ticker GETs in server.py."""
    server, c = client
    sh.record_snapshots([{"symbol": "NVDA", "verdict": "BUY", "score": 0.4,
                          "confidence": 80}], date="2026-07-15")
    calls = {"n": 0}
    real_get_history = sh.get_history

    def _counting(symbol, days=30):
        calls["n"] += 1
        return real_get_history(symbol, days=days)

    monkeypatch.setattr(sh, "get_history", _counting)
    c.get("/api/signals/history/NVDA")
    c.get("/api/signals/history/NVDA")
    assert calls["n"] == 1
