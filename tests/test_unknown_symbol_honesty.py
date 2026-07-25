"""Unknown data must stay unknown.

Audit BUG-05 / finding J (2026-07-22): analyzing the nonexistent ticker ZZZQQ
produced a page of confident-looking values —

    Analyst    Consensus: Hold          (yfinance defaults `recommendation`)
    Cycle      Direction: Falling
    Fundamentals  "yfinance · just now"  (company_name echoed back, all nulls)
    Earnings   "no earnings source (set FINNHUB_API_KEY for VPN-safe data)"

Every one of those endpoints returned HTTP 200. Two defects: providers that
answer with a SHAPED-BUT-EMPTY row were treated as coverage, and an internal
environment-variable name was rendered as customer copy.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

import server


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: False)
    server._ticker_get_cache.clear()
    return TestClient(server.app, raise_server_exceptions=False)


# ─── the shaped-but-empty predicates ─────────────────────────────────────────

def test_analyst_row_with_no_coverage_is_empty():
    """The exact yfinance shape for an unknown ticker."""
    assert server._analyst_row_is_empty({
        "current": None, "target_mean": None, "target_high": None,
        "target_low": None, "target_median": None, "num_analysts": 0,
        "recommendation": "Hold", "reco_cls": "reco-hold", "upside_pct": None,
    }) is True


def test_analyst_row_with_real_coverage_is_kept():
    assert server._analyst_row_is_empty(
        {"num_analysts": 34, "recommendation": "Buy", "target_mean": 210.0}) is False
    # a price target alone is coverage, even with no analyst count
    assert server._analyst_row_is_empty(
        {"num_analysts": 0, "recommendation": "Hold", "target_mean": 210.0}) is False


def test_fundamentals_row_that_only_echoes_the_ticker_is_empty():
    assert server._fundamentals_row_is_empty({
        "symbol": "ZZZQQ", "company_name": "ZZZQQ", "sector": "", "price": None,
        "ret_1m": None, "revenue_growth": None, "trailing_pe": None,
    }) is True


def test_fundamentals_row_with_any_real_metric_is_kept():
    assert server._fundamentals_row_is_empty(
        {"symbol": "AAPL", "company_name": "Apple Inc.", "price": 213.5}) is False
    assert server._fundamentals_row_is_empty(
        {"symbol": "X", "company_name": "X", "sector": "Technology"}) is False


# ─── end to end ──────────────────────────────────────────────────────────────

def test_unknown_ticker_analyst_is_404_not_a_fabricated_hold(client, monkeypatch):
    import market_data
    monkeypatch.setattr(market_data, "rh_analyst", lambda s: None, raising=False)
    monkeypatch.setattr(market_data, "get_analyst_targets", lambda syms: {
        "ZZZQQ": {"current": None, "target_mean": None, "num_analysts": 0,
                  "recommendation": "Hold", "upside_pct": None}}, raising=False)
    r = client.get("/api/analyst/ZZZQQ")
    assert r.status_code == 404, f"fabricated coverage served: {r.text[:200]}"
    assert "Hold" not in r.text


def test_unknown_ticker_fundamentals_is_404_not_an_empty_card(client, monkeypatch):
    import fundamental_agent
    import market_data
    monkeypatch.setattr(market_data, "rh_fundamentals", lambda s: None, raising=False)
    monkeypatch.setattr(fundamental_agent, "_fetch_fundamentals_batch",
                        lambda syms: [{"symbol": "ZZZQQ", "company_name": "ZZZQQ",
                                       "sector": "", "price": None,
                                       "revenue_growth": None}], raising=False)
    r = client.get("/api/fundamentals/ZZZQQ")
    assert r.status_code == 404
    assert "just now" not in r.text


def test_known_ticker_still_served(client, monkeypatch):
    """The guard must not start 404-ing real symbols."""
    import market_data
    monkeypatch.setattr(market_data, "rh_analyst",
                        lambda s: {"num_analysts": 41, "recommendation": "Buy",
                                   "target_mean": 220.0}, raising=False)
    r = client.get("/api/analyst/AAPL")
    assert r.status_code == 200
    assert r.json()["data"]["recommendation"] == "Buy"


def test_earnings_unavailable_copy_names_no_internal_config(client, monkeypatch):
    import earnings_viz
    monkeypatch.setattr(earnings_viz, "_from_finnhub", lambda s: None, raising=False)
    monkeypatch.setattr(earnings_viz, "_from_yfinance", lambda s: None, raising=False)
    r = client.get("/api/earnings/ZZZQQ")
    assert r.status_code == 200                    # a shaped "unavailable" is fine here
    body = r.json()["data"]
    assert body["available"] is False
    assert "API_KEY" not in r.text and "FINNHUB" not in r.text
    assert "diagnostic" not in body, "operator diagnostics must not ship to the client"
