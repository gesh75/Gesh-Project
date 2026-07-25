"""Dormant publication schema and legitimate owner rendering contracts.

The owner-domain `/api/portfolio` route is no longer a customer publication
surface. Customers receive an exact 403 before the owner loader runs. The
allowlisted `model_portfolio.publish()` schema remains tested directly so its
valuation contract does not rot while the separate Model Book publication
domain is intentionally deferred.

The production portfolio renderer remains an owner feature and is checked
through the owner bundle.
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


# ─── the dormant publication schema ─────────────────────────────────────────


def test_dormant_publication_matches_the_published_schema():
    d = model_portfolio.publish(_PAYLOAD)
    assert set(d) <= set(model_portfolio.PUBLISHED_TOP_LEVEL_FIELDS), (
        f"unlisted field(s): {sorted(set(d) - set(model_portfolio.PUBLISHED_TOP_LEVEL_FIELDS))}")
    for required in ("model_portfolio_id", "version", "published_at", "holdings",
                     "cash", "calculated_total_equity", "source_label", "stale"):
        assert required in d, f"missing contract field {required}"
    assert "equity_summary" not in d
    for h in d["holdings"]:
        assert set(h) == set(model_portfolio.PUBLISHED_HOLDING_FIELDS)
        assert "average_buy_price" not in h and "percent_change" not in h


def test_dormant_publication_nullable_fields_are_typed_as_documented():
    d = model_portfolio.publish(_PAYLOAD)
    assert isinstance(d["calculated_total_equity"], (int, float))
    assert isinstance(d["day_change_available"], bool)
    for k in ("day_change", "day_change_pct", "data_as_of"):
        assert d[k] is None or isinstance(d[k], (int, float, str))


def test_customer_route_rejects_the_legacy_publication_contract(client, monkeypatch):
    monkeypatch.setattr(
        server,
        "build_data_payload",
        lambda _watchlist: pytest.fail("customer request reached owner portfolio loader"),
        raising=True,
    )

    response = client.get("/api/portfolio")

    assert response.status_code == 403
    assert response.json() == {
        "detail": "Owner data is unavailable to customer accounts.",
        "code": "model_book_unavailable",
    }
    assert "data" not in response.json()
    assert "robinhood_mcp" not in response.text


# ─── the owner rendering path consumes compatible portfolio shapes ───────────


def _owner_bundle() -> str:
    return server._render_dashboard("owner")


def test_rendering_path_does_not_dereference_equity_summary_blindly():
    bundle = _owner_bundle()
    assert "renderStats(d.equity_summary)" not in bundle, (
        "portfolio renderer still dereferences an optional summary blindly")
    assert "normalizePortfolioEquity" in bundle


def test_owner_only_panels_are_guarded_before_render():
    """Owner renderers remain defensive if a panel is absent."""
    bundle = _owner_bundle()
    m = re.search(r"function renderPortfolioPanels\(d\)\{(.*?)\n\}", bundle, re.S)
    assert m, "renderPortfolioPanels not found"
    body = m.group(1)
    for panel, dom_id in (("renderStats", "s-equity"), ("renderHoldings", "holdings-body"),
                          ("renderWatchlist", "watchlist-body"),
                          ("renderAllocChart", "alloc-chart")):
        assert f"getElementById('{dom_id}')" in body, f"{panel} unguarded"


def test_owner_normalizer_maps_the_dormant_published_shape():
    """Every non-owner field read by the normalizer exists in the dormant schema."""
    d = model_portfolio.publish(_PAYLOAD)
    bundle = _owner_bundle()
    m = re.search(r"function normalizePortfolioEquity\(d\)\{(.*?)\n\}", bundle, re.S)
    assert m
    for field in re.findall(r"d\.(\w+)", m.group(1)):
        if field == "equity_summary":
            continue                        # the owner branch, absent by design
        assert field in d, f"normalizer reads d.{field}, absent from the customer payload"


def test_day_change_null_is_rendered_as_unavailable_not_zero():
    bundle = _owner_bundle()
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
