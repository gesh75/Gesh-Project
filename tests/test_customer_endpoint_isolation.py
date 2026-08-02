"""Cross-endpoint customer isolation matrix (BLOCKER 1).

`/api/portfolio` got the published serializer; TEN other customer-reachable GETs
kept calling `_resolved_payload()` and fed the RAW owner payload straight into
their engines. The response mask scales NUMBERS on the way out — it does nothing
about private non-numeric fields on the way IN (source tags, quote sessions,
execution state), and nothing at all about a field added to the owner payload
next month.

These tests inject sentinels at MULTIPLE NESTING LEVELS, including fields that
do not exist today, and assert structurally (exact allowed key sets, recursively)
rather than by substring.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import model_portfolio

OWNER = "georgy75@gmail.com"
CUSTOMER = "cust@x.com"
SCALE = 2.0

# Customer-reachable GETs that consume portfolio context.
CUSTOMER_CONTEXT_ROUTES = [
    "/api/portfolio", "/api/ecg", "/api/regime", "/api/market-data",
    "/api/agents/sec/history", "/api/agents/news/history", "/api/fft-cycles",
    "/api/tax/harvest", "/api/benchmarks/1mo", "/api/crypto-correlation",
    "/api/yield/landscape",
]

# Owner-only analysis routes: a customer must never reach the engines at all.
OWNER_ANALYSIS_ROUTES = [
    "/api/analyze", "/api/deep-analysis", "/api/monte-carlo", "/api/kelly",
    "/api/consensus", "/api/screener", "/api/gemini/analyze",
    "/api/trading/dry-run", "/api/paper/run",
]

# Sentinels planted at four different depths, including keys that do not exist
# in production today — the point is that UNKNOWN fields cannot flow through.
SENTINELS = {
    "SRC-SENTINEL": "top-level source tag",
    "SESSION-SENTINEL": "nested quote-session identifier",
    "ACCT-SENTINEL": "brokerage account identifier",
    "HOLDING-SENTINEL": "unknown field on a holding",
    "EQUITY-SENTINEL": "unknown field inside equity_summary",
    "DEEP-SENTINEL": "three levels down",
    "88.7711": "owner average buy price",
    "21632.44": "owner leveraged buying power",
    "750.05": "owner pending deposits",
    "SUSPENDED": "owner ECG execution state",
}


def _owner_payload(**over) -> dict:
    base = {
        "as_of": "2026-07-23T00:00:00+00:00",
        "source": "robinhood_mcp SRC-SENTINEL",
        "account_number": "ACCT-SENTINEL",
        "quote_provenance": {"NVDA": {"provider": "robinhood_mcp",
                                      "session": "SESSION-SENTINEL",
                                      "nested": {"deeper": "DEEP-SENTINEL"}}},
        "risk_gate": {"kill_switch": False, "orders_today": 3},
        "paper_session": {"fills": 2},
        "holdings": [{
            "symbol": "NVDA", "quantity": 15.7, "current_price": 213.85,
            "equity": 3372.01, "portfolio_percentage": 100.0, "type": "stock",
            "average_buy_price": 88.7711, "percent_change": 140.9,
            "equity_change": 1972.01, "ecg_state": "SUSPENDED",
            "unknown_future_field": "HOLDING-SENTINEL",
        }],
        "equity_summary": {
            "portfolio_equity": 26052.67, "cash": 8948.15, "total": 35750.87,
            "buying_power_leveraged": 21632.44, "withdrawable_amount": 8948.15,
            "pending_deposits": 750.05,
            "adjusted_equity_previous_close": 34954.40,
            "unknown_future_field": "EQUITY-SENTINEL",
        },
        "watchlist": [],
    }
    base.update(over)
    return base


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    import auth_users
    import server
    monkeypatch.setattr(auth_users, "AUTH_DB_PATH", tmp_path / "auth.db")
    auth_users.init_auth_db()
    for e in (OWNER, CUSTOMER):
        auth_users.create_user(e, "test-password")
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: True)
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda p, c: "allow", raising=False)
    monkeypatch.setattr(server.auth_users, "is_owner", lambda e: e == OWNER, raising=False)
    monkeypatch.setattr(server, "_maybe_log_snapshot", lambda h: None, raising=True)
    monkeypatch.setattr(server, "_customer_scale_factor", lambda: SCALE, raising=True)
    return server, monkeypatch


def _client(server, monkeypatch, role: str, payload: dict | None = None,
            raiser: type[Exception] | None = None):
    def _build(_wl):
        if raiser is not None:
            raise raiser("provider down")
        return json.loads(json.dumps(payload if payload is not None else _owner_payload()))
    monkeypatch.setattr(server, "build_data_payload", _build, raising=True)
    monkeypatch.setattr(server, "load_snapshot_payload",
                        lambda: json.loads(json.dumps(payload if payload is not None
                                                      else _owner_payload())), raising=True)
    email = {"owner": OWNER, "customer": CUSTOMER}.get(role)
    monkeypatch.setattr(server.auth_users, "authenticate_session",
                        lambda tok: email, raising=True)
    c = TestClient(server.app, raise_server_exceptions=False)
    if email:
        c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    return c


def _walk_strings(obj, path="$"):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk_strings(v, f"{path}.{k}")
            yield f"{path}.KEY", str(k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk_strings(v, f"{path}[{i}]")
    else:
        yield path, str(obj)


# ─── the boundary serializer, structurally ───────────────────────────────────

def test_context_holdings_have_exactly_the_allowed_keys():
    ctx = model_portfolio.build_customer_model_context(_owner_payload())
    for h in ctx["holdings"]:
        assert set(h) == set(model_portfolio.CONTEXT_HOLDING_FIELDS)


def test_context_equity_summary_has_no_unlisted_keys():
    ctx = model_portfolio.build_customer_model_context(_owner_payload())
    assert set(ctx["equity_summary"]) <= set(model_portfolio.CONTEXT_EQUITY_FIELDS)


def test_context_recomputes_total_from_components():
    """A customer analysis must never be based on a broker headline the
    components do not support (35,750.87 vs 26,052.67 + 8,948.15)."""
    ctx = model_portfolio.build_customer_model_context(_owner_payload())
    eq = ctx["equity_summary"]
    assert eq["total"] == pytest.approx(eq["portfolio_equity"] + eq["cash"], abs=0.02)
    assert eq["total"] != pytest.approx(35_750.87)


@pytest.mark.parametrize("sentinel", sorted(SENTINELS))
def test_context_carries_no_sentinel_at_any_depth(sentinel):
    ctx = model_portfolio.build_customer_model_context(_owner_payload())
    for path, value in _walk_strings(ctx):
        assert sentinel not in value, f"{sentinel} survived at {path}"


# ─── the endpoint matrix ─────────────────────────────────────────────────────

@pytest.mark.parametrize("route", CUSTOMER_CONTEXT_ROUTES)
@pytest.mark.parametrize("sentinel", sorted(SENTINELS))
def test_customer_route_emits_no_owner_sentinel(env, route, sentinel):
    server, monkeypatch = env
    c = _client(server, monkeypatch, "customer")
    r = c.get(route)
    assert r.status_code == 200, f"{route} -> {r.status_code}"
    for path, value in _walk_strings(r.json()):
        assert sentinel not in value, f"{sentinel} at {path} in {route}"


@pytest.mark.parametrize("route", CUSTOMER_CONTEXT_ROUTES)
def test_customer_route_survives_an_offline_snapshot(env, route):
    """The degraded path must be sanitised too — it is a different code path."""
    server, monkeypatch = env
    payload = {**_owner_payload(), "offline": True, "snapshot_age_seconds": 900}
    c = _client(server, monkeypatch, "customer", payload=payload)
    r = c.get(route)
    assert r.status_code == 200
    blob = json.dumps(r.json())
    for sentinel in SENTINELS:
        assert sentinel not in blob, f"{sentinel} leaked on the offline path of {route}"


@pytest.mark.parametrize("route", CUSTOMER_CONTEXT_ROUTES)
def test_customer_route_survives_an_empty_portfolio(env, route):
    server, monkeypatch = env
    payload = {"as_of": "2026-07-23T00:00:00+00:00", "holdings": [],
               "equity_summary": {"portfolio_equity": 0.0, "cash": 0.0, "total": 0.0},
               "watchlist": [], "source": "robinhood_mcp SRC-SENTINEL"}
    c = _client(server, monkeypatch, "customer", payload=payload)
    r = c.get(route)
    assert r.status_code in (200, 404), f"{route} -> {r.status_code}"
    assert "SRC-SENTINEL" not in r.text


@pytest.mark.parametrize("route", OWNER_ANALYSIS_ROUTES)
def test_customer_cannot_reach_the_analysis_engines_at_all(env, route):
    """The strongest form of the guarantee: the owner payload never reaches an
    AI prompt / Kelly / Monte-Carlo run for a customer, because the customer
    cannot invoke those routes."""
    server, monkeypatch = env
    c = _client(server, monkeypatch, "customer")
    r = c.post(route, json={}, headers={"Origin": "http://testserver",
                                        "Content-Type": "application/json"})
    assert r.status_code == 403, f"{route} reachable by a customer ({r.status_code})"


@pytest.mark.parametrize("route", CUSTOMER_CONTEXT_ROUTES)
def test_unauthenticated_request_is_treated_as_a_customer(env, route):
    """Fails closed — no session must never mean owner."""
    server, monkeypatch = env
    c = _client(server, monkeypatch, "anon")
    r = c.get(route)
    blob = r.text
    for sentinel in SENTINELS:
        assert sentinel not in blob, f"{sentinel} served to an unauthenticated caller at {route}"


def test_owner_still_receives_the_real_payload(env):
    """One-directional boundary: the operator must not lose their own data."""
    server, monkeypatch = env
    c = _client(server, monkeypatch, "owner")
    d = c.get("/api/portfolio").json()["data"]
    assert d["equity_summary"]["buying_power_leveraged"] == pytest.approx(21632.44)
    assert d["holdings"][0]["average_buy_price"] == pytest.approx(88.7711)


def test_owner_analysis_context_is_not_the_model_context(env):
    """The owner's engines must keep seeing owner data, not the published view."""
    server, monkeypatch = env
    _client(server, monkeypatch, "owner")

    class _Req:
        cookies = {server.auth_users.COOKIE_NAME: "tok"}
    ctx = server._context_payload(_Req())
    assert "model_context" not in ctx
    assert ctx["holdings"][0]["average_buy_price"] == pytest.approx(88.7711)


def test_customer_analysis_context_is_the_model_context(env):
    server, monkeypatch = env
    _client(server, monkeypatch, "customer")

    class _Req:
        cookies = {server.auth_users.COOKIE_NAME: "tok"}
    ctx = server._context_payload(_Req())
    assert ctx["model_context"] is True
    assert "average_buy_price" not in ctx["holdings"][0]


def test_provider_error_does_not_fall_back_to_raw_owner_data(env):
    """A provider failure must degrade through the same boundary."""
    server, monkeypatch = env
    from data_fetcher import RobinhoodOfflineError
    c = _client(server, monkeypatch, "customer", raiser=RobinhoodOfflineError)
    for route in ("/api/portfolio", "/api/ecg", "/api/regime"):
        r = c.get(route)
        for sentinel in SENTINELS:
            assert sentinel not in r.text, f"{sentinel} leaked via the error path of {route}"
