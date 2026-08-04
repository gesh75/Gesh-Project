"""Regression: blocking trading/scanner handlers must offload via asyncio.to_thread.

Grok review P1 on PR #186 — the grep showed to_thread wrappers, but nothing pinned
that dry-run / simulate-session / consolidation-batch actually go through the
thread pool, or that a threaded failure still maps to a safe error envelope.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    import auth_users
    import server

    monkeypatch.setattr(auth_users, "AUTH_DB_PATH", tmp_path / "auth.db")
    auth_users.init_auth_db()
    monkeypatch.setattr(auth_users, "auth_enabled", lambda: False)
    monkeypatch.setenv("GESH_API_KEY", "test-to-thread-key")
    monkeypatch.delenv("GESH_AUTH_ENABLED", raising=False)
    return TestClient(server.app, raise_server_exceptions=False)


def _headers() -> dict[str, str]:
    return {"X-API-Key": "test-to-thread-key"}


def test_trading_dry_run_calls_to_thread(client, monkeypatch):
    import server

    seen: dict[str, object] = {}

    class _Verdict:
        def to_dict(self):
            return {"approved": True, "reason": None, "gates": {}}

    async def fake_to_thread(fn, *args, **kwargs):
        seen["fn"] = fn
        seen["args"] = args
        # Execute the offloaded work so the handler still returns a real envelope.
        return fn(*args, **kwargs) if callable(fn) and not args and not kwargs else _Verdict()

    monkeypatch.setattr(server.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(server, "_resolved_payload", lambda: {"holdings": [], "equity_summary": {}})
    monkeypatch.setattr(
        server,
        "manual_intent",
        lambda **kw: SimpleNamespace(symbol=kw["symbol"], action=kw["action"]),
    )
    monkeypatch.setattr(server, "risk_gate_evaluate", lambda intent, payload: _Verdict())

    resp = client.post(
        "/api/trading/dry-run",
        headers=_headers(),
        json={"symbol": "SPY", "action": "BUY", "confidence": 0.9},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["data"]["approved"] is True
    assert "fn" in seen  # handler went through asyncio.to_thread


def test_trading_dry_run_threaded_exception_maps_to_safe_500(client, monkeypatch):
    import server

    async def boom_to_thread(fn, *args, **kwargs):
        raise RuntimeError("synthetic gate failure — must not leak path detail")

    monkeypatch.setattr(server.asyncio, "to_thread", boom_to_thread)
    monkeypatch.setattr(
        server,
        "manual_intent",
        lambda **kw: SimpleNamespace(symbol=kw["symbol"], action=kw["action"]),
    )

    resp = client.post(
        "/api/trading/dry-run",
        headers=_headers(),
        json={"symbol": "SPY", "action": "BUY"},
    )
    assert resp.status_code == 500
    detail = resp.json().get("detail", "")
    assert "synthetic gate failure" not in detail
    assert "RuntimeError" not in detail


def test_simulate_session_calls_to_thread(client, monkeypatch):
    import server

    seen: dict[str, object] = {}

    async def fake_to_thread(fn, *args, **kwargs):
        seen["called"] = True
        # Return the envelope the real _run would produce — do not execute RH/gov work.
        return {
            "status": "ok",
            "count": 0,
            "cap_reached": False,
            "cap_reason": None,
            "use_cached": True,
            "results": [],
        }

    monkeypatch.setattr(server.asyncio, "to_thread", fake_to_thread)

    resp = client.post(
        "/api/trading/simulate-session",
        headers=_headers(),
        json={"use_cached": True, "symbols": []},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert seen.get("called") is True


def test_simulate_session_threaded_exception_maps_to_safe_500(client, monkeypatch):
    import server

    async def boom_to_thread(fn, *args, **kwargs):
        raise RuntimeError("/tmp/secret-db.sqlite blew up")

    monkeypatch.setattr(server.asyncio, "to_thread", boom_to_thread)

    resp = client.post(
        "/api/trading/simulate-session",
        headers=_headers(),
        json={},
    )
    assert resp.status_code == 500
    assert "secret-db" not in resp.text


def test_consolidation_batch_calls_to_thread(client, monkeypatch):
    import server

    seen: dict[str, object] = {}

    async def fake_to_thread(fn, *args, **kwargs):
        seen["fn"] = fn
        seen["args"] = args
        seen["kwargs"] = kwargs
        return {"found": [], "summary": "ok"}

    monkeypatch.setattr(server.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(server, "_validate_timeframe", lambda *a, **k: None)

    resp = client.post(
        "/api/scanner/consolidation",
        headers=_headers(),
        json={"symbols": ["SPY"], "interval": "5m", "period": "1d", "min_rr": 2.0},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert seen["fn"] is server.scan_consolidations
    assert seen["args"][0] == ["SPY"]


def test_consolidation_batch_threaded_exception_maps_to_safe_500(client, monkeypatch):
    import server

    async def boom_to_thread(fn, *args, **kwargs):
        raise RuntimeError("yfinance crumb path /home/secret")

    monkeypatch.setattr(server.asyncio, "to_thread", boom_to_thread)
    monkeypatch.setattr(server, "_validate_timeframe", lambda *a, **k: None)

    resp = client.post(
        "/api/scanner/consolidation",
        headers=_headers(),
        json={"symbols": ["SPY"], "interval": "5m", "period": "1d"},
    )
    assert resp.status_code == 500
    assert "/home/secret" not in resp.text
