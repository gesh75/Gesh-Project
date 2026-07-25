"""Phase 1 P0 API contract for owner-domain data.

These are authorization tests, not masking tests.  A customer must be refused
before any owner portfolio, cache, or trade-repository seam is evaluated.
"""

from __future__ import annotations

import json
import logging

import pytest
from starlette.testclient import TestClient

import server

OWNER = "owner@example.com"
CUSTOMER = "customer@example.com"
PRIVATE_SENTINELS = (
    "robinhood_mcp",
    "OWNER_ONLY_SYMBOL",
    "OWNER_ACCOUNT_991",
    "PRIVATE_TRADE_NOTE_771",
    "918273.45",
    "551122.33",
)
DENIED_CONTRACTS = (
    ("/api/portfolio", "model_book_unavailable"),
    ("/api/trades", "customer_trade_ledger_unavailable"),
    ("/api/trades/stats", "customer_trade_ledger_unavailable"),
)


@pytest.fixture
def boundary(monkeypatch):
    principal = {"email": CUSTOMER}
    calls = {
        "portfolio": 0,
        "snapshot": 0,
        "snapshot_log": 0,
        "trades": 0,
        "stats": 0,
    }

    owner_payload = {
        "source": "robinhood_mcp",
        "account_number": "OWNER_ACCOUNT_991",
        "as_of": "2026-07-25T12:00:00+00:00",
        "holdings": [{
            "symbol": "OWNER_ONLY_SYMBOL",
            "quantity": 123.0,
            "current_price": 100.0,
            "equity": 12_300.0,
            "average_buy_price": 88.0,
            "portfolio_percentage": 1.32,
        }],
        "equity_summary": {
            "portfolio_equity": 918_273.45,
            "cash": 551_122.33,
            "total": 918_273.45,
            "buying_power": 551_122.33,
            "buying_power_leveraged": 1_102_244.66,
            "adjusted_equity_previous_close": 900_000.0,
        },
        "watchlist": [],
    }

    def build_payload(*_args, **_kwargs):
        calls["portfolio"] += 1
        return json.loads(json.dumps(owner_payload))

    def load_snapshot(*_args, **_kwargs):
        calls["snapshot"] += 1
        return json.loads(json.dumps(owner_payload))

    def log_snapshot(*_args, **_kwargs):
        calls["snapshot_log"] += 1

    def trade_rows(*_args, **_kwargs):
        calls["trades"] += 1
        return [{
            "symbol": "OWNER_ONLY_SYMBOL",
            "notes": "PRIVATE_TRADE_NOTE_771",
            "realized_pnl": 91_827.45,
            "is_paper": False,
        }]

    def trade_stats(*_args, **_kwargs):
        calls["stats"] += 1
        return {
            "total_realized_pnl": 91_827.45,
            "win_rate_pct": 87.5,
            "best_trade": {
                "symbol": "OWNER_ONLY_SYMBOL",
                "notes": "PRIVATE_TRADE_NOTE_771",
            },
        }

    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: True)
    monkeypatch.setattr(
        server.auth_users,
        "gate_decision",
        lambda *_args: "allow" if principal["email"] else "unauthorized",
    )
    monkeypatch.setattr(
        server.auth_users,
        "authenticate_session",
        lambda _token: principal["email"],
    )
    monkeypatch.setattr(server.auth_users, "is_owner", lambda email: email == OWNER)
    monkeypatch.setattr(server, "build_data_payload", build_payload)
    monkeypatch.setattr(server, "load_snapshot_payload", load_snapshot)
    monkeypatch.setattr(server, "_maybe_log_snapshot", log_snapshot)
    monkeypatch.setattr(server, "get_trade_history", trade_rows)
    monkeypatch.setattr(server, "trade_summary_for_report", trade_stats)
    monkeypatch.setattr(server, "_attach_account_day_change", lambda _eq: None)
    monkeypatch.setattr(server, "_attach_freshness", lambda _payload: None)

    client = TestClient(server.app, raise_server_exceptions=False)
    client.cookies.set(server.auth_users.COOKIE_NAME, "signed-session")
    return client, principal, calls


@pytest.mark.parametrize(("path", "code"), DENIED_CONTRACTS)
def test_customer_contract_is_stable_403_before_owner_data(boundary, path, code):
    client, _principal, calls = boundary

    response = client.get(path)

    assert response.status_code == 403
    assert response.json() == {
        "detail": "Owner data is unavailable to customer accounts.",
        "code": code,
    }
    assert "view" not in response.json()
    blob = response.text
    assert all(sentinel not in blob for sentinel in PRIVATE_SENTINELS)
    assert calls == {
        "portfolio": 0,
        "snapshot": 0,
        "snapshot_log": 0,
        "trades": 0,
        "stats": 0,
    }


@pytest.mark.parametrize(("path", "code"), DENIED_CONTRACTS)
@pytest.mark.parametrize(
    "query",
    (
        "view=owner",
        "role=owner",
        "tier=admin",
        "email=owner%40example.com",
        "user=owner",
        "user_id=1",
        "account=OWNER_ACCOUNT_991",
        "account_id=1",
        "is_owner=true",
        "admin=true",
        "paper=false&open_only=false&limit=500&days=36500",
    ),
)
def test_query_parameter_tampering_cannot_change_principal_scope(
    boundary, path, code, query
):
    client, _principal, calls = boundary

    response = client.get(
        f"{path}?{query}",
        headers={
            "X-Role": "owner",
            "X-User": OWNER,
            "X-Admin": "true",
            "X-Forwarded-User": OWNER,
        },
    )

    assert response.status_code == 403
    assert response.json()["code"] == code
    assert all(value == 0 for value in calls.values())


def test_unauthenticated_request_remains_401_not_owner(boundary):
    client, principal, calls = boundary
    principal["email"] = None

    response = client.get("/api/portfolio")

    assert response.status_code == 401
    assert all(value == 0 for value in calls.values())


def test_customer_denial_path_obeys_the_api_rate_limit(
    boundary, monkeypatch
):
    client, _principal, calls = boundary
    monkeypatch.setattr(server, "_rate_ok", lambda _ip, heavy=False: False)

    response = client.get("/api/portfolio")

    assert response.status_code == 429
    assert response.json() == {"detail": "Rate limit exceeded — slow down"}
    assert all(value == 0 for value in calls.values())


def test_customer_denial_warning_is_metadata_only_and_throttled(boundary, caplog):
    client, _principal, calls = boundary
    server._reset_throttled_logs()
    caplog.set_level(logging.WARNING, logger="gesh.server")
    try:
        assert client.get("/api/portfolio").status_code == 403
        assert client.get("/api/portfolio").status_code == 403
    finally:
        server._reset_throttled_logs()

    warnings = [
        record.getMessage()
        for record in caplog.records
        if "customer owner-data read refused" in record.getMessage()
    ]
    assert len(warnings) == 1
    assert "GET /api/portfolio" in warnings[0]
    assert CUSTOMER not in warnings[0]
    assert all(value == 0 for value in calls.values())
