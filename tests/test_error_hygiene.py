"""8th-pass (Fable adversarial audit): customer-reachable error branches must never
echo the raw exception string to the client. `str(e)` on a DB failure can carry the
sqlite file path, "database disk image is malformed", or a table/column name — all
internal detail. The handler must log the detail server-side and return a generic
error while staying graceful (empty series so the sparkline just draws nothing).
"""

from __future__ import annotations

from starlette.testclient import TestClient

import db
import server

_SECRET = "/Users/owner/Projects/gesh_financial.db: database disk image is malformed (table paper_outcomes)"


def test_portfolio_history_error_hides_exception_detail(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError(_SECRET)

    # handler does `from db import get_portfolio_equity_history` at call time
    monkeypatch.setattr(db, "get_portfolio_equity_history", _boom, raising=False)
    # test the handler's error branch directly — auth wall is out of scope here
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: False)
    monkeypatch.setenv("GESH_API_KEY", "test-owner-key")
    client = TestClient(server.app, raise_server_exceptions=False)

    r = client.get(
        "/api/portfolio/history?points=10",
        headers={"X-API-Key": "test-owner-key"},
    )
    assert r.status_code == 200          # still graceful — sparkline draws nothing
    body = r.json()
    assert body["status"] == "error"
    assert body["series"] == []
    # the raw exception text must NOT reach the client
    assert _SECRET not in r.text
    assert "malformed" not in r.text
    assert "gesh_financial.db" not in r.text


def test_portfolio_history_requires_owner_auth_and_bounds_points(monkeypatch):
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: False)
    monkeypatch.setenv("GESH_API_KEY", "test-owner-key")
    client = TestClient(server.app, raise_server_exceptions=False)

    assert client.get("/api/portfolio/history").status_code == 401

    headers = {"X-API-Key": "test-owner-key"}
    assert client.get("/api/portfolio/history?points=-1", headers=headers).status_code == 422
    assert client.get("/api/portfolio/history?points=366", headers=headers).status_code == 422
