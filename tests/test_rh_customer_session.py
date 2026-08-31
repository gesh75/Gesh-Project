"""Customer requests must not spend an already-live owner robin_stocks session.

PR #258 closed the Yahoo-401 pickle *restore*. The remaining hole: rh_analyst
and rh_fundamentals called get_ratings / get_fundamentals whenever a session
was already live, ignoring price_source._ALLOW_AUTHENTICATED_RH. Customer
/api/analyst/{symbol} and /api/fundamentals/{symbol} therefore borrowed the
owner session even after the middleware wrap.
"""

from __future__ import annotations

import pytest

import price_source


def _spy_ratings(called: dict):
    def _fn(*_a, **_k):
        called["ratings"] = called.get("ratings", 0) + 1
        return {
            "summary": {
                "num_buy_ratings": 10,
                "num_hold_ratings": 0,
                "num_sell_ratings": 0,
            }
        }

    return _fn


def _spy_fundamentals(called: dict):
    def _fn(*_a, **_k):
        called["fundamentals"] = called.get("fundamentals", 0) + 1
        return [
            {
                "pe_ratio": "20.0",
                "pb_ratio": "3.0",
                "market_cap": "1000",
                "high_52_weeks": "50.0",
                "low_52_weeks": "10.0",
                "dividend_yield": "0.01",
                "sector": "Technology",
                "industry": "Software",
            }
        ]

    return _fn


def test_rh_analyst_skips_live_session_under_customer_context(monkeypatch):
    import market_data

    called: dict = {}
    monkeypatch.setattr(
        "robin_stocks.robinhood.stocks.get_ratings",
        _spy_ratings(called),
        raising=False,
    )
    with price_source.unauthenticated_rh_fallback():
        assert market_data.rh_analyst("NVDA") is None
    assert called.get("ratings", 0) == 0


def test_rh_fundamentals_skips_live_session_under_customer_context(monkeypatch):
    import market_data

    called: dict = {}
    monkeypatch.setattr(
        "robin_stocks.robinhood.stocks.get_fundamentals",
        _spy_fundamentals(called),
        raising=False,
    )
    with price_source.unauthenticated_rh_fallback():
        assert market_data.rh_fundamentals("NVDA") is None
    assert called.get("fundamentals", 0) == 0


def test_rh_analyst_still_uses_session_on_owner_default(monkeypatch):
    import market_data

    called: dict = {}
    monkeypatch.setattr(
        "robin_stocks.robinhood.stocks.get_ratings",
        _spy_ratings(called),
        raising=False,
    )
    assert price_source._ALLOW_AUTHENTICATED_RH.get() is True
    out = market_data.rh_analyst("NVDA")
    assert called["ratings"] == 1
    assert out["recommendation"] == "Buy"
    assert out["num_analysts"] == 10


def test_rh_fundamentals_still_uses_session_on_owner_default(monkeypatch):
    import market_data

    called: dict = {}
    monkeypatch.setattr(
        "robin_stocks.robinhood.stocks.get_fundamentals",
        _spy_fundamentals(called),
        raising=False,
    )
    assert price_source._ALLOW_AUTHENTICATED_RH.get() is True
    out = market_data.rh_fundamentals("NVDA")
    assert called["fundamentals"] == 1
    assert out["pe_ratio"] == 20.0
    assert out["sector"] == "Technology"


def test_customer_rh_flag_resets_after_context():
    assert price_source._ALLOW_AUTHENTICATED_RH.get() is True
    with price_source.unauthenticated_rh_fallback():
        assert price_source._ALLOW_AUTHENTICATED_RH.get() is False
    assert price_source._ALLOW_AUTHENTICATED_RH.get() is True


def _customer_client(tmp_path, monkeypatch, *, owner: bool):
    from starlette.testclient import TestClient

    import auth_users as _au
    import server

    email = "owner@x.com" if owner else "cust@x.com"
    monkeypatch.setattr(_au, "AUTH_DB_PATH", tmp_path / "auth.db")
    _au.init_auth_db()
    _au.create_user(email, "test-password")
    monkeypatch.setattr(server.auth_users, "AUTH_DB_PATH", _au.AUTH_DB_PATH)
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: True)
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: email)
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda p, c: "allow")
    monkeypatch.setattr(server.auth_users, "is_owner", lambda e: owner)
    client = TestClient(server.app, raise_server_exceptions=False)
    client.cookies.set(_au.COOKIE_NAME, "fake-session")
    return client


def test_customer_analyst_endpoint_disables_authenticated_rh(tmp_path, monkeypatch):
    pytest.importorskip("gesh_core")
    import market_data

    seen: list[bool] = []

    def _spy(*_a, **_k):
        seen.append(price_source._ALLOW_AUTHENTICATED_RH.get())
        raise RuntimeError("spy-stop")

    monkeypatch.setattr(market_data, "rh_analyst", _spy)
    client = _customer_client(tmp_path, monkeypatch, owner=False)
    client.get("/api/analyst/AAPL")
    assert seen == [False]


def test_customer_fundamentals_endpoint_disables_authenticated_rh(tmp_path, monkeypatch):
    pytest.importorskip("gesh_core")
    import market_data

    seen: list[bool] = []

    def _spy(*_a, **_k):
        seen.append(price_source._ALLOW_AUTHENTICATED_RH.get())
        raise RuntimeError("spy-stop")

    monkeypatch.setattr(market_data, "rh_fundamentals", _spy)
    client = _customer_client(tmp_path, monkeypatch, owner=False)
    client.get("/api/fundamentals/AAPL")
    assert seen == [False]


def test_owner_analyst_endpoint_keeps_authenticated_rh(tmp_path, monkeypatch):
    pytest.importorskip("gesh_core")
    import market_data

    seen: list[bool] = []

    def _spy(*_a, **_k):
        seen.append(price_source._ALLOW_AUTHENTICATED_RH.get())
        raise RuntimeError("spy-stop")

    monkeypatch.setattr(market_data, "rh_analyst", _spy)
    client = _customer_client(tmp_path, monkeypatch, owner=True)
    client.get("/api/analyst/AAPL")
    assert seen == [True]
