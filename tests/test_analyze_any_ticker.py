"""Customer 'analyze any ticker' v1 — per-symbol GET endpoints, /api/me role, and
the Alpaca-backed intraday chart path. These endpoints return only PUBLIC market
data (signals on public prices) so a customer can analyze any stock they choose,
stateless — no owner data. See docs/superpowers/specs/2026-07-06-customer-analyze-any-ticker-design.md.
"""

from __future__ import annotations

import pandas as pd
from starlette.testclient import TestClient

import price_source
import server


def _client(monkeypatch) -> TestClient:
    # focus on the endpoints, not the auth wall (its own tests cover the wall)
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: False)
    return TestClient(server.app, raise_server_exceptions=False)


# ── Task 1: per-ticker regime + fundamentals ─────────────────────────────────

def test_regime_symbol_returns_single_stock_regime(monkeypatch):
    monkeypatch.setattr(
        "regime.detect_regimes",
        lambda syms: {"regimes": [{"symbol": syms[0], "regime": "TRENDING",
                                   "adx": 31.2, "rsi": 55.0, "pct_b": 0.6,
                                   "bb_width": 0.1, "icon": "📈", "description": "up"}],
                      "regime_summary": "x"}, raising=False)
    r = _client(monkeypatch).get("/api/regime/nvda")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["data"]["symbol"] == "NVDA"        # _safe_symbol uppercased
    assert body["data"]["regime"] == "TRENDING"


def test_regime_symbol_bad_ticker_is_clean_4xx_not_500(monkeypatch):
    monkeypatch.setattr("regime.detect_regimes",
                        lambda syms: {"regimes": [], "regime_summary": "none"}, raising=False)
    r = _client(monkeypatch).get("/api/regime/@@@")
    assert r.status_code in (400, 404)             # never 500


def test_regime_symbol_no_data_is_404(monkeypatch):
    monkeypatch.setattr("regime.detect_regimes",
                        lambda syms: {"regimes": [], "regime_summary": "none"}, raising=False)
    r = _client(monkeypatch).get("/api/regime/NVDA")
    assert r.status_code == 404


def test_fundamentals_symbol_returns_single_row(monkeypatch):
    monkeypatch.setattr("fundamental_agent._fetch_fundamentals_batch",
                        lambda syms: [{"symbol": syms[0], "momentum_gap": "LOW", "score": 62}],
                        raising=False)
    r = _client(monkeypatch).get("/api/fundamentals/aapl")
    assert r.status_code == 200
    assert r.json()["data"]["symbol"] == "AAPL"
    assert r.json()["data"]["score"] == 62


def test_fundamentals_symbol_error_row_is_404(monkeypatch):
    monkeypatch.setattr("fundamental_agent._fetch_fundamentals_batch",
                        lambda syms: [{"symbol": syms[0], "error": "no data"}], raising=False)
    r = _client(monkeypatch).get("/api/fundamentals/AAPL")
    assert r.status_code == 404


# ── Task 2: /api/me role ─────────────────────────────────────────────────────

def test_me_owner(monkeypatch):
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: True)
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda t: "owner@x.com")
    monkeypatch.setattr(server.auth_users, "is_owner", lambda e: True)
    c = TestClient(server.app, raise_server_exceptions=False)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    assert c.get("/api/me").json()["role"] == "owner"


def test_me_customer(monkeypatch):
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: True)
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda t: "cust@x.com")
    monkeypatch.setattr(server.auth_users, "is_owner", lambda e: False)
    c = TestClient(server.app, raise_server_exceptions=False)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    assert c.get("/api/me").json()["role"] == "customer"


def test_me_auth_disabled_is_owner(monkeypatch):
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: False)
    c = TestClient(server.app, raise_server_exceptions=False)
    assert c.get("/api/me").json()["role"] == "owner"   # solo/dev mode = owner


# ── Task 3: Alpaca intraday chart path ───────────────────────────────────────

def test_chart_intraday_uses_intraday_5m(monkeypatch):
    df = pd.DataFrame({"Close": [10.0, 10.5, 11.0, 10.8, 11.2]},
                      index=pd.date_range("2026-07-06 09:30", periods=5, freq="5min"))
    monkeypatch.setattr(price_source, "intraday_5m", lambda sym, span: df, raising=False)
    r = _client(monkeypatch).get("/api/chart/NVDA?period=1d")
    assert r.status_code == 200
    assert len(r.json()["prices"]) == 5


def test_chart_intraday_absent_is_honest_not_500(monkeypatch):
    monkeypatch.setattr(price_source, "intraday_5m", lambda sym, span: None, raising=False)
    r = _client(monkeypatch).get("/api/chart/NVDA?period=5d")
    assert r.status_code == 200
    assert r.json().get("intraday_unavailable") is True


def test_intraday_5m_no_keys_returns_none(monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    assert price_source.intraday_5m("NVDA", "1d") is None


# ── analyst targets + news headlines per-ticker (v1.1 enrichment) ─────────────

def test_analyst_symbol_returns_targets(monkeypatch):
    monkeypatch.setattr(
        "market_data.get_analyst_targets",
        lambda syms: {syms[0]: {"target_mean": 200.0, "num_analysts": 40,
                                "recommendation": "buy", "upside_pct": 12.5, "current": 178.0}},
        raising=False)
    r = _client(monkeypatch).get("/api/analyst/nvda")
    assert r.status_code == 200
    assert r.json()["data"]["target_mean"] == 200.0
    assert r.json()["data"]["num_analysts"] == 40


def test_analyst_symbol_no_data_404(monkeypatch):
    monkeypatch.setattr("market_data.get_analyst_targets", lambda syms: {}, raising=False)
    r = _client(monkeypatch).get("/api/analyst/NVDA")
    assert r.status_code == 404


def test_analyst_symbol_error_row_404_not_leaked(monkeypatch):
    # yfinance failure returns {sym: {"error": "..."}} — must 404, never 200-with-error
    monkeypatch.setattr("market_data.get_analyst_targets",
                        lambda syms: {syms[0]: {"error": "curl (35) Connection reset by peer"}},
                        raising=False)
    r = _client(monkeypatch).get("/api/analyst/NVDA")
    assert r.status_code == 404
    assert "Connection reset" not in r.text   # internal error never reaches the client


def test_news_symbol_returns_trimmed_headlines(monkeypatch):
    monkeypatch.setattr(
        "news_agent._fetch_all_news",
        lambda sym, days_back=7: [{"title": "NVDA soars", "url": "http://x", "source": "Yahoo",
                                   "published": "2026-07-06", "description": "long body"}],
        raising=False)
    r = _client(monkeypatch).get("/api/news/NVDA")
    assert r.status_code == 200
    items = r.json()["data"]
    assert items[0]["title"] == "NVDA soars"
    assert items[0]["source"] == "Yahoo"
    assert "description" not in items[0]   # headlines only — no heavy body, no LLM


def test_news_symbol_empty_is_ok_not_404(monkeypatch):
    monkeypatch.setattr("news_agent._fetch_all_news", lambda sym, days_back=7: [], raising=False)
    r = _client(monkeypatch).get("/api/news/NVDA")
    assert r.status_code == 200
    assert r.json()["data"] == []
