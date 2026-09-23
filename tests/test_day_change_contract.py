"""Day change on the customer book — API contract.

Audit Q-02 (2026-07-22): an 8-position test book rendered
"+$271,639 (5920.4%)". The dollar figure was Σ equity × (lifetime return vs
cost) and the percentage was the UNWEIGHTED MEAN of those lifetime returns, so
the two were not functions of each other and neither was a day change.

The server now computes it once, canonically (portfolio_valuation), and reports
UNAVAILABLE rather than 0.0 when the previous close is missing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

CUSTOMER = "cust@x.com"
OWNER = "georgy75@gmail.com"


@pytest.fixture
def au(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import auth_users as _au

    monkeypatch.setattr(_au, "AUTH_DB_PATH", tmp_path / "auth.db")
    _au.init_auth_db()
    for email in (CUSTOMER, OWNER):
        _au.create_user(email, "test-password")
    return _au


@pytest.fixture
def book(au, monkeypatch):
    """Client with steerable current AND prior closes."""
    import server

    monkeypatch.setattr(server.auth_users, "AUTH_DB_PATH", au.AUTH_DB_PATH)
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: True)
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: CUSTOMER)
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda p, c: "allow", raising=False)
    monkeypatch.setattr(server.auth_users, "is_owner", lambda e: e == OWNER, raising=False)

    prices = {"NVDA": 110.0, "AAPL": 190.0}
    priors = {"NVDA": 100.0, "AAPL": 200.0}
    monkeypatch.setattr(server, "_latest_close", lambda s: prices.get(s), raising=True)
    monkeypatch.setattr(
        server,
        "_latest_close_meta",
        lambda s: (prices.get(s), {"source": "yahoo", "substituted": False}),
        raising=True,
    )
    monkeypatch.setattr(server, "_prior_close", lambda s: priors.get(s), raising=True)
    c = TestClient(server.app, raise_server_exceptions=False)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    return server, c, prices, priors


def _val(c) -> dict:
    r = c.get("/api/mybook")
    assert r.status_code == 200, r.text
    return r.json()["data"]["valuation"]


def test_day_change_is_vs_prior_close_not_cost_basis(book):
    """The audit's exact shape: a position held from far below today's price."""
    _server, c, _p, _pr = book
    # 100 NVDA bought at $1, now $110, prior close $100 → today: +$1,000 (+10%)
    c.post("/api/mybook", json={"symbol": "NVDA", "shares": 100, "avg_cost": 1})
    v = _val(c)
    assert v["day_change_available"] is True
    assert v["day_change_amount"] == pytest.approx(1000.0)
    assert v["day_change_percent"] == pytest.approx(10.0)
    # the lifetime number is still there, correctly, and separately labeled
    assert v["unrealized_pnl"] == pytest.approx(10_900.0)
    assert v["unrealized_pnl_percent"] == pytest.approx(10_900.0)
    # and the two must never be confused: day change is 2 orders smaller here
    assert abs(v["day_change_amount"]) < abs(v["unrealized_pnl"])


def test_percent_and_amount_are_consistent(book):
    """The audit's '$271,639 vs 5920.4% don't match' failure mode."""
    _server, c, _p, _pr = book
    c.post("/api/mybook", json={"symbol": "NVDA", "shares": 7.5, "avg_cost": 40})
    c.post("/api/mybook", json={"symbol": "AAPL", "shares": 3, "avg_cost": 150})
    v = _val(c)
    implied = v["day_change_amount"] / v["prior_close_equity"] * 100
    assert implied == pytest.approx(v["day_change_percent"], abs=0.01)


def test_equity_equals_positions_plus_cash(book):
    _server, c, _p, _pr = book
    c.post("/api/mybook", json={"symbol": "NVDA", "shares": 2})
    c.post("/api/mybook", json={"symbol": "AAPL", "shares": 1})
    v = _val(c)
    assert v["total_equity"] == pytest.approx(v["positions_market_value"] + v["cash"])
    assert v["reconciles"] is True
    assert v["issues"] == []


def test_missing_prior_close_reports_unavailable_not_zero(book, monkeypatch):
    server, c, _p, _pr = book
    c.post("/api/mybook", json={"symbol": "NVDA", "shares": 2})
    monkeypatch.setattr(server, "_prior_close", lambda s: None, raising=True)
    v = _val(c)
    assert v["day_change_available"] is False
    assert v["day_change_amount"] is None  # NOT 0.0
    assert any("day_change" in u for u in v["unavailable"])


def test_missing_cost_basis_reports_unavailable_not_zero(book):
    _server, c, _p, _pr = book
    c.post("/api/mybook", json={"symbol": "NVDA", "shares": 2})  # no avg_cost
    v = _val(c)
    assert v["cost_basis_available"] is False
    assert v["unrealized_pnl"] is None
    assert v["total_cost_basis"] is None
    pos = c.get("/api/mybook").json()["data"]["positions"][0]
    # percent_change must be None, not a 0.0 the UI can mistake for "flat"
    assert pos["percent_change"] is None
    assert pos["equity_change"] is None


def test_equal_weight_book_reports_no_day_change(book):
    """Equal-weight dollars are illustrative by construction, so a day change
    computed from them would be fiction."""
    _server, c, _p, _pr = book
    c.post("/api/mybook/bulk", json={"symbols": ["NVDA", "AAPL"], "replace": True})
    d = c.get("/api/mybook").json()["data"]
    assert d["equal_weight"] is True
    assert d["valuation"]["day_change_available"] is False
    # Same contract as /api/mybook/summary: do not ship the $100k notional
    # as if it were the customer's real book value.
    assert d["total_equity"] is None


def test_positions_carry_their_own_prior_close_and_day_pct(book):
    _server, c, _p, _pr = book
    c.post("/api/mybook", json={"symbol": "AAPL", "shares": 4})
    pos = c.get("/api/mybook").json()["data"]["positions"][0]
    assert pos["prior_close"] == pytest.approx(200.0)
    assert pos["day_change_pct"] == pytest.approx(-5.0)  # 190 vs 200


def test_dashboard_does_not_recompute_day_change_from_percent_change():
    """Guard the specific formula that caused the defect from coming back."""
    html = Path("templates/dashboard.html").read_text()
    assert "percent_change||0)/100" not in html.replace(" ", "")
    assert "day_change_available" in html  # the UI reads the canonical flag


def test_dashboard_today_column_reads_day_change_pct_not_lifetime():
    """The Home book table's TODAY cell must not bind percent_change
    (lifetime vs cost). That is the Q-02 mix-up with a new label."""
    html = Path("templates/dashboard.html").read_text()
    start = html.index("async function loadMyBook")
    rest = html[start:]
    nxt = rest.find("\nasync function ")
    nxt2 = rest.find("\nfunction ", 1)
    cuts = [i for i in (nxt, nxt2) if i > 0]
    body = rest if not cuts else rest[: min(cuts)]
    row = body[body.index("pos.map(p=>") :]
    assert "p.day_change_pct" in row
    assert "p.percent_change" not in row
