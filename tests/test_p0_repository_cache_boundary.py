"""Repository/cache proofs for the Phase 1 customer boundary."""

from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient

import server

OWNER = "owner@example.com"
CUSTOMER = "customer@example.com"


def _assert_private_no_store(response) -> None:
    directives = {
        item.strip().lower()
        for item in response.headers.get("cache-control", "").split(",")
        if item.strip()
    }
    assert {"private", "no-store"} <= directives
    assert response.headers.get("pragma", "").lower() == "no-cache"
    vary = {
        item.strip().lower()
        for item in response.headers.get("vary", "").split(",")
        if item.strip()
    }
    assert "cookie" in vary


@pytest.fixture
def warmable_boundary(monkeypatch):
    principal = {"email": OWNER}
    calls = {"portfolio": 0, "trades": 0, "stats": 0}
    payload = {
        "source": "robinhood_mcp",
        "account_number": "CACHE_OWNER_ACCOUNT",
        "as_of": "2026-07-25T12:00:00+00:00",
        "holdings": [{
            "symbol": "CACHE_OWNER_SYMBOL",
            "quantity": 1.0,
            "current_price": 100.0,
            "equity": 100.0,
            "average_buy_price": 50.0,
            "portfolio_percentage": 100.0,
        }],
        "equity_summary": {
            "portfolio_equity": 100.0,
            "cash": 50.0,
            "total": 150.0,
            "buying_power": 50.0,
            "adjusted_equity_previous_close": 140.0,
        },
        "watchlist": [],
    }

    def portfolio(*_args, **_kwargs):
        calls["portfolio"] += 1
        return json.loads(json.dumps(payload))

    def trades(*_args, **_kwargs):
        calls["trades"] += 1
        return [{
            "symbol": "CACHE_OWNER_SYMBOL",
            "notes": "CACHE_PRIVATE_NOTE",
            "realized_pnl": 42.5,
        }]

    def stats(*_args, **_kwargs):
        calls["stats"] += 1
        return {"total_realized_pnl": 42.5, "win_rate_pct": 100.0}

    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: True)
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda *_args: "allow")
    monkeypatch.setattr(
        server.auth_users,
        "authenticate_session",
        lambda _token: principal["email"],
    )
    monkeypatch.setattr(server.auth_users, "is_owner", lambda email: email == OWNER)
    monkeypatch.setattr(server, "build_data_payload", portfolio)
    monkeypatch.setattr(
        server,
        "load_snapshot_payload",
        lambda: pytest.fail("owner live payload unexpectedly used fallback cache"),
    )
    monkeypatch.setattr(server, "_maybe_log_snapshot", lambda _holdings: None)
    monkeypatch.setattr(server, "_attach_account_day_change", lambda _eq: None)
    monkeypatch.setattr(server, "_attach_freshness", lambda _payload: None)
    monkeypatch.setattr(server, "get_trade_history", trades)
    monkeypatch.setattr(server, "trade_summary_for_report", stats)

    client = TestClient(server.app, raise_server_exceptions=False)
    client.cookies.set(server.auth_users.COOKIE_NAME, "signed-session")
    return client, principal, calls


@pytest.mark.parametrize(
    ("path", "counter", "sentinel"),
    (
        ("/api/portfolio", "portfolio", "CACHE_OWNER_ACCOUNT"),
        ("/api/trades", "trades", "CACHE_PRIVATE_NOTE"),
        ("/api/trades/stats", "stats", "42.5"),
    ),
)
def test_owner_response_cannot_be_replayed_from_cache_to_customer(
    warmable_boundary, path, counter, sentinel
):
    client, principal, calls = warmable_boundary

    owner_response = client.get(path)
    assert owner_response.status_code == 200
    assert sentinel in owner_response.text
    assert calls[counter] == 1
    _assert_private_no_store(owner_response)

    principal["email"] = CUSTOMER
    customer_response = client.get(path)

    assert customer_response.status_code == 403
    assert sentinel not in customer_response.text
    assert calls[counter] == 1, "customer request reached owner repository/cache seam"
    _assert_private_no_store(customer_response)


def test_owner_and_auth_disabled_local_mode_keep_existing_behavior(
    warmable_boundary, monkeypatch
):
    client, principal, _calls = warmable_boundary

    principal["email"] = OWNER
    assert client.get("/api/portfolio").json()["data"]["source"] == "robinhood_mcp"
    assert client.get("/api/trades").json()["data"][0]["notes"] == "CACHE_PRIVATE_NOTE"
    assert client.get("/api/trades/stats").json()["data"]["win_rate_pct"] == 100.0

    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: False)
    principal["email"] = None
    assert client.get("/api/portfolio").status_code == 200
    assert client.get("/api/trades").status_code == 200
    assert client.get("/api/trades/stats").status_code == 200


def test_role_composed_dashboard_cannot_be_replayed_across_principals(
    warmable_boundary,
):
    client, principal, _calls = warmable_boundary

    principal["email"] = OWNER
    owner_response = client.get("/")
    assert owner_response.status_code == 200
    assert "function loadPortfolio()" in owner_response.text
    _assert_private_no_store(owner_response)

    principal["email"] = CUSTOMER
    customer_response = client.get("/")
    assert customer_response.status_code == 200
    assert "function loadPortfolio()" not in customer_response.text
    assert "/api/trades" not in customer_response.text
    _assert_private_no_store(customer_response)
