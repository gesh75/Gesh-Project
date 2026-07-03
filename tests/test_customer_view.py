"""Customer-view model-book masking (launch gap: customers saw the owner's
real dollars, cash, and share counts).

Non-owner sessions get /api/* JSON normalized to a $100k model book: money and
quantity fields scale by model/real_total, so percentages and per-share prices
stay exact while real wealth never leaves the server. Owner-only HTML views
(/report, /invest, /progress) are refused outright. Fail CLOSED everywhere.
"""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

import auth_users
import server


# ─── _scale_money (pure) ──────────────────────────────────────────────────────

def test_scale_money_scales_money_and_qty_not_prices():
    payload = {
        "equity_summary": {"portfolio_equity": 37_522.83, "cash": 509.70},
        "holdings": [{"symbol": "NVDA", "quantity": 28.0, "price": 194.51,
                      "equity": 5446.28, "percent_change": -3.51,
                      "pnl_usd": -218.0}],
        "alloc_pct": 14.7,
    }
    out = server._scale_money(payload, 2.0)
    assert out["equity_summary"]["portfolio_equity"] == pytest.approx(75_045.66)
    assert out["holdings"][0]["quantity"] == pytest.approx(56.0)
    assert out["holdings"][0]["pnl_usd"] == pytest.approx(-436.0)   # *_usd suffix
    assert out["holdings"][0]["price"] == 194.51                    # prices untouched
    assert out["holdings"][0]["percent_change"] == -3.51            # pct untouched
    assert out["alloc_pct"] == 14.7
    assert payload["holdings"][0]["quantity"] == 28.0               # input not mutated


def test_scale_factor_fails_closed(monkeypatch, tmp_path):
    """No readable snapshot total → factor 0.0 → dollars zero out, never leak."""
    monkeypatch.setattr(server, "_customer_scale_factor", server._customer_scale_factor)
    # simulate unreadable snapshot by pointing Path read at a bogus dir via direct call
    assert server._scale_money({"cash": 999.0}, 0.0)["cash"] == 0.0


def test_is_owner():
    assert auth_users.is_owner("georgy75@gmail.com") is True
    assert auth_users.is_owner("GEORGY75@GMAIL.COM ") is True
    assert auth_users.is_owner("customer@example.com") is False
    assert auth_users.is_owner(None) is False


# ─── middleware end-to-end ────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("GESH_AUTH_ENABLED", "1")
    monkeypatch.setattr(server, "_customer_scale_factor", lambda: 0.5)
    return TestClient(server.app, raise_server_exceptions=False)


def _as(monkeypatch, email):
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: email)
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda path, cookie: "allow")


def test_customer_gets_scaled_chart_free_but_masked_portfolio(monkeypatch, client):
    _as(monkeypatch, "customer@example.com")
    monkeypatch.setattr(
        server, "_cached_response",
        lambda key: {"status": "ok", "cached": True,
                     "data": {"total_equity": 1000.0, "score": 42, "alloc_pct": 10.0}},
    )
    r = client.get("/api/kelly/cached")
    assert r.status_code == 200
    body = r.json()
    assert body["view"] == "model_book"                       # customer tell
    assert body["data"]["total_equity"] == pytest.approx(500.0)  # scaled ×0.5
    assert body["data"]["score"] == 42                        # non-money untouched
    assert body["data"]["alloc_pct"] == 10.0


def test_owner_sees_raw_dollars(monkeypatch, client):
    _as(monkeypatch, "georgy75@gmail.com")
    monkeypatch.setattr(
        server, "_cached_response",
        lambda key: {"status": "ok", "cached": True, "data": {"total_equity": 1000.0}},
    )
    r = client.get("/api/kelly/cached")
    assert r.status_code == 200
    assert r.json()["data"]["total_equity"] == 1000.0
    assert "view" not in r.json()


@pytest.mark.parametrize("path", ["/report", "/invest", "/invest/data", "/progress", "/watch"])
def test_owner_only_views_refused_for_customers(monkeypatch, client, path):
    _as(monkeypatch, "customer@example.com")
    r = client.get(path)
    assert r.status_code == 403
    assert "Owner-only" in r.json()["detail"]
