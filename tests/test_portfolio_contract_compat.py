"""Route-to-render contract for the changed customer /api/portfolio shape.

The customer contract changed substantially: no `equity_summary`, no owner cost
basis, nullable day-change fields. `loadPortfolio` was still calling
`renderStats(d.equity_summary)` unconditionally — `undefined` for a customer,
i.e. a TypeError swallowed by the enclosing catch, which silently flipped the
connection badge to offline and skipped the macro load.

These assert the SCHEMA the route emits and that the production rendering path
consumes exactly that schema, including its null and degraded states.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import model_portfolio
import server

OWNER = "georgy75@gmail.com"
CUSTOMER = "cust@x.com"

_PAYLOAD = {
    "as_of": "2026-07-23T00:00:00+00:00", "source": "robinhood_mcp",
    "holdings": [{"symbol": "NVDA", "quantity": 10.0, "current_price": 100.0,
                  "equity": 1000.0, "portfolio_percentage": 100.0, "type": "stock",
                  "average_buy_price": 50.0, "percent_change": 100.0}],
    "equity_summary": {"portfolio_equity": 1000.0, "cash": 500.0, "total": 1600.0,
                       "adjusted_equity_previous_close": 1400.0},
    "watchlist": [],
}


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    import auth_users
    monkeypatch.setattr(auth_users, "AUTH_DB_PATH", tmp_path / "auth.db")
    auth_users.init_auth_db()
    for e in (OWNER, CUSTOMER):
        auth_users.create_user(e, "test-password")
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: True)
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda p, c: "allow", raising=False)
    monkeypatch.setattr(server.auth_users, "is_owner", lambda e: e == OWNER, raising=False)
    monkeypatch.setattr(server.auth_users, "authenticate_session",
                        lambda tok: CUSTOMER, raising=True)
    monkeypatch.setattr(server, "build_data_payload",
                        lambda wl: json.loads(json.dumps(_PAYLOAD)), raising=True)
    monkeypatch.setattr(server, "_maybe_log_snapshot", lambda h: None, raising=True)
    monkeypatch.setattr(server, "_customer_scale_factor", lambda: 1.0, raising=True)
    c = TestClient(server.app, raise_server_exceptions=False)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    return c


# ─── the emitted schema ──────────────────────────────────────────────────────

def test_customer_payload_matches_the_published_schema(client):
    d = client.get("/api/portfolio").json()["data"]
    assert set(d) <= set(model_portfolio.PUBLISHED_TOP_LEVEL_FIELDS), (
        f"unlisted field(s): {sorted(set(d) - set(model_portfolio.PUBLISHED_TOP_LEVEL_FIELDS))}")
    for required in ("model_portfolio_id", "version", "published_at", "holdings",
                     "cash", "calculated_total_equity", "source_label", "stale"):
        assert required in d, f"missing contract field {required}"
    assert "equity_summary" not in d
    for h in d["holdings"]:
        assert set(h) == set(model_portfolio.PUBLISHED_HOLDING_FIELDS)
        assert "average_buy_price" not in h and "percent_change" not in h


def test_nullable_fields_are_typed_as_documented(client):
    d = client.get("/api/portfolio").json()["data"]
    assert isinstance(d["calculated_total_equity"], (int, float))
    assert isinstance(d["day_change_available"], bool)
    for k in ("day_change", "day_change_pct", "data_as_of"):
        assert d[k] is None or isinstance(d[k], (int, float, str))


# ─── the production rendering path consumes exactly that ─────────────────────

def _customer_bundle() -> str:
    return server._render_dashboard("customer")


def test_rendering_path_does_not_dereference_equity_summary_blindly():
    bundle = _customer_bundle()
    assert "renderStats(d.equity_summary)" not in bundle, (
        "customer bundle still dereferences a field the customer contract removed")
    assert "normalizePortfolioEquity" in bundle


def test_owner_only_panels_are_guarded_before_render():
    """The panels' DOM lives in the owner partial; calling their renderers in a
    customer document would throw on a null element."""
    bundle = _customer_bundle()
    m = re.search(r"function renderPortfolioPanels\(d\)\{(.*?)\n\}", bundle, re.S)
    assert m, "renderPortfolioPanels not found"
    body = m.group(1)
    for panel, dom_id in (("renderStats", "s-equity"), ("renderHoldings", "holdings-body"),
                          ("renderWatchlist", "watchlist-body"),
                          ("renderAllocChart", "alloc-chart")):
        assert f"getElementById('{dom_id}')" in body, f"{panel} unguarded"


def test_normalizer_maps_the_published_shape(client):
    """Static equivalent of running the JS: every field the normalizer reads must
    exist in the real route payload."""
    d = client.get("/api/portfolio").json()["data"]
    bundle = _customer_bundle()
    m = re.search(r"function normalizePortfolioEquity\(d\)\{(.*?)\n\}", bundle, re.S)
    assert m
    for field in re.findall(r"d\.(\w+)", m.group(1)):
        if field == "equity_summary":
            continue                        # the owner branch, absent by design
        assert field in d, f"normalizer reads d.{field}, absent from the customer payload"


def test_day_change_null_is_rendered_as_unavailable_not_zero():
    bundle = _customer_bundle()
    m = re.search(r"function renderStats\(eq\)\{(.*?)\n\}", bundle, re.S)
    assert m
    body = m.group(1)
    assert "day_change_available" in body
    assert "eq.day_change||0" not in body.replace(" ", ""), (
        "a null day change would render as a flat 0")


def test_owner_contract_is_unchanged(client, monkeypatch):
    monkeypatch.setattr(server.auth_users, "authenticate_session",
                        lambda tok: OWNER, raising=True)
    d = client.get("/api/portfolio").json()["data"]
    assert "equity_summary" in d
    assert d["holdings"][0]["average_buy_price"] == pytest.approx(50.0)
    bundle = server._render_dashboard("owner")
    assert "renderPortfolioPanels" in bundle and "renderStats" in bundle
