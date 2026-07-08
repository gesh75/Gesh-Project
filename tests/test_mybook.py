"""Customer 'analyze MY portfolio' (v2 of the customer surface).

The customer enters their OWN positions (symbol + shares [+ avg cost]); the server
values them from public quotes and runs the owner engine suite on THEIR book —
deep AI analysis, Kelly, Monte Carlo. Everything is derived from
customer_positions + live prices (zero owner data), so /api/mybook is mask-exempt:
the customer must see their OWN dollars raw.

Same offline rules as the rest of the suite: prices/engines/LLM are stubbed.
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


# ─── storage layer ────────────────────────────────────────────────────────────

def test_set_get_remove_roundtrip(au):
    assert au.get_positions("a@x.com") == []
    assert au.set_position("a@x.com", "nvda", 10) is True
    assert au.set_position("a@x.com", "AAPL", 2.5, avg_cost=150.0) is True
    got = au.get_positions("a@x.com")
    assert [p["symbol"] for p in got] == ["NVDA", "AAPL"]     # uppercased, insertion order
    assert got[1]["shares"] == 2.5 and got[1]["avg_cost"] == 150.0
    assert au.set_position("a@x.com", "NVDA", 20) is True     # upsert = update
    assert [p for p in au.get_positions("a@x.com") if p["symbol"] == "NVDA"][0]["shares"] == 20
    au.remove_position("a@x.com", "nvda")
    assert [p["symbol"] for p in au.get_positions("a@x.com")] == ["AAPL"]


def test_positions_per_user_isolated(au):
    au.set_position("a@x.com", "NVDA", 1)
    au.set_position("b@x.com", "TSLA", 2)
    assert [p["symbol"] for p in au.get_positions("a@x.com")] == ["NVDA"]
    assert [p["symbol"] for p in au.get_positions("b@x.com")] == ["TSLA"]


def test_set_position_rejects_garbage(au):
    assert au.set_position("a@x.com", "NVDA", 0) is False       # zero shares
    assert au.set_position("a@x.com", "NVDA", -5) is False      # negative
    assert au.set_position("a@x.com", "NVDA", "lots") is False  # non-numeric
    assert au.set_position("a@x.com", "NVDA", 1, avg_cost="x") is False
    assert au.get_positions("a@x.com") == []


def test_position_cap_new_refused_update_allowed(au):
    for i in range(au._MAX_POSITIONS):
        assert au.set_position("a@x.com", f"SYM{i}", 1) is True
    assert au.set_position("a@x.com", "NEWSYM", 1) is False       # cap
    assert au.set_position("a@x.com", "SYM0", 99) is True         # update at cap OK
    assert len(au.get_positions("a@x.com")) == au._MAX_POSITIONS


# ─── endpoints (session-scoped, mask-exempt) ─────────────────────────────────

@pytest.fixture
def client(au, monkeypatch):
    import server
    monkeypatch.setattr(server.auth_users, "AUTH_DB_PATH", au.AUTH_DB_PATH)
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: True)
    monkeypatch.setattr(server, "_latest_close", lambda sym: {"NVDA": 100.0, "AAPL": 200.0}.get(sym),
                        raising=True)
    return server, TestClient(server.app, raise_server_exceptions=False)


def _as(server, monkeypatch, email):
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: email)
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda p, c: "allow", raising=False)
    monkeypatch.setattr(server.auth_users, "is_owner",
                        lambda e: e == "georgy75@gmail.com", raising=False)


_ANALYSIS = {"portfolio_score": 71, "portfolio_verdict": "HOLD", "portfolio_type": "Growth",
             "risk_score": 55, "diversification_score": 40, "market_context": "test",
             "holdings_analysis": [{"symbol": "NVDA", "verdict": "TRIM",
                                    "fair_value": {"pct_diff": -8.2}}],
             "portfolio_risks": []}


def _stub_engines(server, monkeypatch, counters=None):
    c = counters if counters is not None else {}
    c.setdefault("llm", 0)

    def _deep(payload, regime_context=None):
        c["llm"] += 1
        return dict(_ANALYSIS)
    monkeypatch.setattr(server, "deep_analyze_portfolio", _deep, raising=True)
    monkeypatch.setattr(server, "run_monte_carlo",
                        lambda holdings, eq, **k: {"median": 2100.0, "p5": 1500.0, "p95": 2600.0,
                                                   "var_95": 300.0, "var_95_pct": 15.0,
                                                   "prob_profit_pct": 60.0, "regime": "NEUTRAL"},
                        raising=True)

    def _kelly(holdings, total, upside, verdicts):
        c["kelly_verdicts"] = verdicts
        return {"holdings": [{"symbol": h["symbol"], "action": "HOLD",
                              "current_pct": 50.0, "optimal_pct": 40.0,
                              "delta_usd": -100.0} for h in holdings],
                "total_optimal_allocated_pct": 80.0, "cash_recommended_pct": 20.0}
    monkeypatch.setattr(server, "run_kelly_analysis", _kelly, raising=True)
    monkeypatch.setattr(server, "detect_regimes",
                        lambda syms: {"regimes": [], "regime_summary": "NEUTRAL macro"},
                        raising=True)
    monkeypatch.setattr(server, "get_analyst_targets", lambda syms: {}, raising=True)
    return c


def test_book_crud_and_valuation(client, monkeypatch):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    r = c.post("/api/mybook", json={"symbol": "nvda", "shares": 10})
    assert r.status_code == 200 and r.json()["status"] == "ok"
    r = c.post("/api/mybook", json={"symbol": "AAPL", "shares": 5, "avg_cost": 150})
    d = r.json()["data"]
    assert d["total_equity"] == 2000.0                       # 10×$100 + 5×$200
    nvda = [p for p in d["positions"] if p["symbol"] == "NVDA"][0]
    assert nvda["equity"] == 1000.0 and nvda["portfolio_percentage"] == 50.0
    c.delete("/api/mybook/NVDA")
    d = c.get("/api/mybook").json()["data"]
    assert [p["symbol"] for p in d["positions"]] == ["AAPL"]


def test_book_unpriced_symbol_reported_not_dropped_silently(client, monkeypatch):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    c.post("/api/mybook", json={"symbol": "ZZZZ9", "shares": 3})
    d = c.get("/api/mybook").json()["data"]
    assert d["unpriced"] == ["ZZZZ9"]
    assert d["positions"] == []


def test_book_bad_shares_is_400(client, monkeypatch):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    assert c.post("/api/mybook", json={"symbol": "NVDA", "shares": -1}).status_code == 400
    assert c.post("/api/mybook", json={"symbol": "NVDA"}).status_code == 400


def test_book_requires_session(client, monkeypatch):
    server, c = client
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: None)
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda p, c: "allow", raising=False)
    assert c.get("/api/mybook").status_code == 401
    assert c.post("/api/mybook", json={"symbol": "NVDA", "shares": 1}).status_code == 401


def test_book_per_user_isolation(client, monkeypatch):
    server, c = client
    _as(server, monkeypatch, "a@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    c.post("/api/mybook", json={"symbol": "NVDA", "shares": 1})
    _as(server, monkeypatch, "b@x.com")
    assert c.get("/api/mybook").json()["data"]["positions"] == []


# ─── /api/mybook/analyze ─────────────────────────────────────────────────────

def test_analyze_book_runs_engines_and_is_not_masked(client, monkeypatch):
    """A CUSTOMER session gets the full suite on their book, and their OWN dollars
    survive the masking middleware raw (mask-exempt path)."""
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    counters = _stub_engines(server, monkeypatch)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    c.post("/api/mybook", json={"symbol": "NVDA", "shares": 10})
    r = c.post("/api/mybook/analyze", json={})
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["analysis"]["portfolio_score"] == 71
    assert d["monte_carlo"]["var_95"] == 300.0
    assert d["kelly"]["holdings"][0]["symbol"] == "NVDA"
    assert d["book"]["total_equity"] == 1000.0            # raw dollars, not "•••"
    assert "•••" not in r.text
    assert counters["llm"] == 1


def test_analyze_book_kelly_receives_ai_verdicts(client, monkeypatch):
    """Parity with the owner pipeline: Kelly must receive the deep analysis's
    per-holding verdicts (verdict + fair-value gap), not an empty map — the
    verdict steers TRIM/ADD sizing."""
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    counters = _stub_engines(server, monkeypatch)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    c.post("/api/mybook", json={"symbol": "NVDA", "shares": 10})
    assert c.post("/api/mybook/analyze", json={}).status_code == 200
    assert counters["kelly_verdicts"] == {"NVDA": {"verdict": "TRIM", "pct_diff": -8.2}}


def test_analyze_book_anonymous_is_401_no_spend(client, monkeypatch):
    server, c = client
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: None)
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda p, c: "allow", raising=False)
    counters = _stub_engines(server, monkeypatch)
    r = c.post("/api/mybook/analyze", json={})
    assert r.status_code == 401
    assert counters["llm"] == 0


def test_analyze_book_empty_is_400_no_spend(client, monkeypatch):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    counters = _stub_engines(server, monkeypatch)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    r = c.post("/api/mybook/analyze", json={})
    assert r.status_code == 400
    assert counters["llm"] == 0


def test_analyze_book_cached_until_book_changes(client, monkeypatch):
    """Same book within the TTL = one LLM spend; changing a position invalidates
    the fingerprint and re-runs."""
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    counters = _stub_engines(server, monkeypatch)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    c.post("/api/mybook", json={"symbol": "NVDA", "shares": 10})
    assert c.post("/api/mybook/analyze", json={}).status_code == 200
    r2 = c.post("/api/mybook/analyze", json={})
    assert r2.status_code == 200 and r2.json()["cached"] is True
    assert counters["llm"] == 1
    c.post("/api/mybook", json={"symbol": "NVDA", "shares": 20})   # book changed
    r3 = c.post("/api/mybook/analyze", json={})
    assert r3.status_code == 200 and r3.json()["cached"] is False
    assert counters["llm"] == 2


def test_analyze_book_partial_engine_failure_still_returns(client, monkeypatch):
    """Kelly/MC are best-effort — an engine exploding degrades to an error field,
    it does not 500 the whole analysis."""
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    _stub_engines(server, monkeypatch)

    def _boom(*a, **k):
        raise RuntimeError("kelly exploded")
    monkeypatch.setattr(server, "run_kelly_analysis", _boom, raising=True)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    c.post("/api/mybook", json={"symbol": "NVDA", "shares": 10})
    r = c.post("/api/mybook/analyze", json={})
    assert r.status_code == 200
    assert "error" in r.json()["data"]["kelly"]
    assert r.json()["data"]["analysis"]["portfolio_score"] == 71
