"""THE contract for the customer surface: the owner's portfolio never reaches it.

Every customer-facing engine added from here on must pass this file. It exists
because the customer product is being widened from "view the owner's research"
to "run the owner's engines on YOUR book", and each engine brought across is a
fresh opportunity to hand a paying stranger the owner's positions.

The failure mode is not theoretical. `consensus.node_ecg` calls
`get_equity_history(symbol, days=30)`, which is keyed by SYMBOL ALONE — there is
no user dimension in `equity_snapshots`. A customer holding AMD (which is in the
owner's real book) would receive the OWNER's AMD equity curve as a signal input.
Any engine reading a symbol-keyed owner table has the same defect, so the rule
is: a customer response may contain only (a) symbols the customer entered and
(b) public market data for those symbols.

Two invariants are asserted structurally rather than by example, so they keep
holding as surfaces are added:

  * ROUTE COVERAGE — every `/api/mybook/*` route is walked, so a new customer
    endpoint cannot quietly skip the leak check.
  * CLEAR IS TOTAL — the button wipes the user's book completely and touches
    nobody else's.
"""

from __future__ import annotations

import re

import pytest
from starlette.testclient import TestClient

# The owner's real book, from live_portfolio_snapshot.json. None of these may
# ever appear in a customer response unless the CUSTOMER typed them.
OWNER_SYMBOLS = {
    "AMD", "BAC", "BKNG", "BLK", "COF", "DELL", "HOOD", "JD",
    "JMKE", "MPC", "MU", "NVDA", "PANW", "REGN", "RTX", "SCHW",
}
OWNER_EQUITY = 33758.10

# Deliberately disjoint from OWNER_SYMBOLS so any owner symbol appearing in a
# customer response is unambiguous contamination, never an honest coincidence.
CUSTOMER_SYMBOLS = ["ZZZA", "ZZZB"]

OWNER_EMAIL = "georgy75@gmail.com"
CUSTOMER_EMAIL = "cust@x.com"
OTHER_CUSTOMER = "other@x.com"


@pytest.fixture
def au(tmp_path, monkeypatch):
    import auth_users as _au

    monkeypatch.setattr(_au, "AUTH_DB_PATH", tmp_path / "auth.db")
    _au.init_auth_db()
    for email in (CUSTOMER_EMAIL, OTHER_CUSTOMER, OWNER_EMAIL):
        _au.create_user(email, "test-password")
    return _au


@pytest.fixture
def client(au, monkeypatch):
    import server

    monkeypatch.setattr(server.auth_users, "AUTH_DB_PATH", au.AUTH_DB_PATH)
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: True)
    # Every synthetic ticker used anywhere in this file must be priced, or the
    # book drops it as unpriced and an isolation assertion passes vacuously.
    prices = {s: 10.0 for s in (*CUSTOMER_SYMBOLS, "ZZZC", "ZZZD")}
    prices.update({s: 50.0 for s in OWNER_SYMBOLS})
    monkeypatch.setattr(server, "_latest_close", lambda sym: prices.get(sym), raising=True)
    monkeypatch.setattr(
        server,
        "_latest_close_meta",
        lambda sym: (prices.get(sym), {"source": "yahoo", "substituted": False}),
        raising=True,
    )
    return server, TestClient(server.app, raise_server_exceptions=False)


def _as(server, monkeypatch, email):
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: email)
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda p, c: "allow", raising=False)
    monkeypatch.setattr(
        server.auth_users, "is_owner", lambda e: e == OWNER_EMAIL, raising=False
    )


def _seed_book(c: TestClient) -> None:
    for sym in CUSTOMER_SYMBOLS:
        c.post("/api/mybook", json={"symbol": sym, "shares": 10})


def _owner_symbols_in(text: str) -> set[str]:
    """Owner tickers appearing as standalone words. Substring matching would
    false-positive on ordinary prose ('JD' inside 'ADJUSTED'), which would make
    the guard noisy and get it disabled — precisely the wrong outcome."""
    words = set(re.findall(r"\b[A-Z]{1,5}\b", text))
    return words & OWNER_SYMBOLS


# ─── the leak checks ─────────────────────────────────────────────────────────

def test_customer_book_never_contains_owner_symbols(client, monkeypatch):
    server, c = client
    _as(server, monkeypatch, CUSTOMER_EMAIL)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    _seed_book(c)

    r = c.get("/api/mybook")
    assert r.status_code == 200
    assert not _owner_symbols_in(r.text), f"owner symbols leaked: {_owner_symbols_in(r.text)}"


def test_customer_book_never_contains_owner_equity(client, monkeypatch):
    """The owner's portfolio total is the single most sensitive number here."""
    server, c = client
    _as(server, monkeypatch, CUSTOMER_EMAIL)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    _seed_book(c)

    r = c.get("/api/mybook")
    for form in (str(OWNER_EQUITY), "33758", "35594"):
        assert form not in r.text, f"owner equity {form!r} leaked into customer book"


def test_every_mybook_route_is_free_of_owner_symbols(client, monkeypatch):
    """Structural, not by-example: walks every registered /api/mybook route so a
    newly added customer surface cannot skip this check by never being listed."""
    server, c = client
    _as(server, monkeypatch, CUSTOMER_EMAIL)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    _seed_book(c)

    routes = [
        r for r in server.app.routes
        if getattr(r, "path", "").startswith("/api/mybook")
        and "{" not in getattr(r, "path", "")
    ]
    assert routes, "no /api/mybook routes discovered — the walk is vacuous"

    checked = 0
    for route in routes:
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            # analyze spends an LLM call and is covered by its own test below
            if route.path.endswith("/analyze"):
                continue
            resp = (
                c.get(route.path) if method == "GET"
                else c.post(route.path, json={})
            )
            # 4xx is fine — we only care that no owner data rides along
            leaked = _owner_symbols_in(resp.text)
            assert not leaked, f"{method} {route.path} leaked owner symbols: {leaked}"
            assert "33758" not in resp.text, f"{method} {route.path} leaked owner equity"
            checked += 1
    assert checked >= 5, f"only {checked} route/method pairs walked — too few to trust"


def test_analysis_payload_carries_only_customer_symbols(client, monkeypatch):
    """The engine suite is where owner data would most plausibly ride in: the
    engines were written for the owner's book. Assert the payload they receive
    contains the customer's symbols and none of the owner's."""
    import server

    seen: dict = {}

    def _capture(payload):
        seen["symbols"] = [h.get("symbol") for h in payload.get("holdings", [])]
        seen["equity"] = payload["equity_summary"]["portfolio_equity"]
        return {"analysis": {"portfolio_score": 1}, "kelly": {}, "monte_carlo": {},
                "radar": {}, "detailed_coverage": {}, "book": {}}

    srv, c = client
    _as(srv, monkeypatch, CUSTOMER_EMAIL)
    monkeypatch.setattr(server, "_run_book_engines", _capture, raising=True)
    c.cookies.set(srv.auth_users.COOKIE_NAME, "tok")
    _seed_book(c)

    for _ in range(60):
        if c.post("/api/mybook/analyze", json={}).status_code != 202:
            break

    assert seen, "engine suite never ran — the assertion below would be vacuous"
    assert set(seen["symbols"]) == set(CUSTOMER_SYMBOLS)
    assert not (set(seen["symbols"]) & OWNER_SYMBOLS)
    assert seen["equity"] != OWNER_EQUITY


def test_ecg_node_must_abstain_without_a_per_user_equity_history(client, monkeypatch):
    """`equity_snapshots` has NO user column — get_equity_history(symbol) returns
    the OWNER's curve for any symbol. Until a per-user equity history exists, any
    customer-facing consensus MUST force this node to abstain rather than vote on
    owner data. This test pins the requirement; it fails the moment someone wires
    consensus to customers without handling it."""
    import inspect

    import consensus

    src = inspect.getsource(consensus.node_ecg)
    assert "get_equity_history" in src, "node_ecg changed — re-verify the leak analysis"

    # get_equity_history takes no user/tenant argument, which is the whole
    # problem: its result is the owner's curve regardless of who is asking.
    import db

    assert "user" not in inspect.signature(db.get_equity_history).parameters, (
        "get_equity_history gained a user parameter — if equity history is now "
        "per-user, this guard and the customer-consensus design must be revisited"
    )

    # If a customer-scoped consensus entrypoint exists, it must expose a way to
    # disable the ECG node. Absent that, customer consensus must not be wired.
    customer_consensus = getattr(consensus, "run_consensus_for_customer", None)
    if customer_consensus is not None:
        import inspect
        params = inspect.signature(customer_consensus).parameters
        assert any(
            p in params for p in ("allow_ecg", "ecg_enabled", "include_ecg")
        ), "customer consensus exists but offers no way to disable the owner-fed ECG node"


# ─── clear + cross-customer isolation ────────────────────────────────────────

def test_clear_wipes_the_whole_book(client, monkeypatch):
    server, c = client
    _as(server, monkeypatch, CUSTOMER_EMAIL)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    _seed_book(c)
    assert c.get("/api/mybook").json()["data"]["positions"]

    assert c.post("/api/mybook/clear").status_code == 200
    assert c.get("/api/mybook").json()["data"]["positions"] == []


def test_clear_touches_nobody_elses_book(client, monkeypatch):
    server, c = client
    _as(server, monkeypatch, OTHER_CUSTOMER)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    c.post("/api/mybook", json={"symbol": "ZZZC", "shares": 5})

    _as(server, monkeypatch, CUSTOMER_EMAIL)
    _seed_book(c)
    c.post("/api/mybook/clear")

    _as(server, monkeypatch, OTHER_CUSTOMER)
    survivors = [p["symbol"] for p in c.get("/api/mybook").json()["data"]["positions"]]
    assert survivors == ["ZZZC"], "clearing one book disturbed another user's"


def test_one_customer_cannot_read_another_customers_book(client, monkeypatch):
    server, c = client
    _as(server, monkeypatch, CUSTOMER_EMAIL)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    _seed_book(c)

    _as(server, monkeypatch, OTHER_CUSTOMER)
    positions = c.get("/api/mybook").json()["data"]["positions"]
    assert positions == [], "another customer's positions were visible"
