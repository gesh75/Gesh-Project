"""Canonical holdings integrity — the quantity a customer writes is the quantity
EVERY read path returns.

Audit finding A (2026-07-22, round 2): POST /api/mybook with N shares, then GET
/api/account/export reported N × 2.7971 — a constant, unexplained scaling factor
between the read path and the export path.

ROOT CAUSE (traced, not assumed): `_customer_view_middleware` normalizes every
`/api/*` JSON response for a NON-owner session to a $100k "model book", scaling
each key in `_QTY_KEYS` (`shares`, `quantity`, …) by
``_MODEL_BOOK_USD / owner_real_total``. That masking exists to keep the OWNER's
wealth off a customer's screen. `/api/mybook` is mask-exempt (the customer's own
dollars must not be redacted back at them) but `/api/account/export` was NOT —
so the GDPR/CCPA portability export of the customer's OWN book came back
multiplied by the owner's scale factor. With the owner's real total at
$35,750.87 the factor is exactly 2.7971, matching the audit to 4 decimals.

The fix is NOT to divide the export by anything: it is to stop applying
owner-masking to a payload that contains only the caller's own data.

These tests deliberately use an arbitrary factor (3.0 / 7.5) rather than the
observed 2.7971 so they prove "no scaling at all", not "not that one scaling".
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

import model_portfolio

CUSTOMER = "cust@x.com"
OWNER = "georgy75@gmail.com"

# Deliberately NOT the audit's 2.7971 — any constant factor must fail these tests.
_TEST_SCALE_FACTOR = 3.0

_PRICES = {"NVDA": 100.0, "AAPL": 200.0, "TSLA": 400.0}


@pytest.fixture
def au(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import auth_users as _au
    monkeypatch.setattr(_au, "AUTH_DB_PATH", tmp_path / "auth.db")
    _au.init_auth_db()
    for email in (CUSTOMER, "other@x.com", OWNER):
        _au.create_user(email, "test-password")
    return _au


@pytest.fixture
def client(au, monkeypatch):
    import server
    monkeypatch.setattr(server.auth_users, "AUTH_DB_PATH", au.AUTH_DB_PATH)
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: True)
    monkeypatch.setattr(server, "_latest_close", lambda sym: _PRICES.get(sym), raising=True)
    monkeypatch.setattr(
        server, "_latest_close_meta",
        lambda sym: (_PRICES.get(sym), {"source": "yahoo", "substituted": False}),
        raising=True)
    # Pin the model-book factor: the real one is read from
    # live_portfolio_snapshot.json and would make these tests depend on the
    # owner's current balance.
    monkeypatch.setattr(server, "_customer_scale_factor",
                        lambda: _TEST_SCALE_FACTOR, raising=True)
    return server, TestClient(server.app, raise_server_exceptions=False)


def _as(server, monkeypatch, email):
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: email)
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda p, c: "allow", raising=False)
    monkeypatch.setattr(server.auth_users, "is_owner", lambda e: e == OWNER, raising=False)


def _login(server, monkeypatch, c, email=CUSTOMER):
    _as(server, monkeypatch, email)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")


def _export_positions(c) -> dict[str, dict]:
    r = c.get("/api/account/export")
    assert r.status_code == 200, r.text
    return {p["symbol"]: p for p in r.json().get("positions", [])}


def _book_positions(c) -> dict[str, dict]:
    r = c.get("/api/mybook")
    assert r.status_code == 200, r.text
    return {p["symbol"]: p for p in r.json()["data"]["positions"]}


# ─── the invariant ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("qty", [1, 2.7971, 0.5, 0.000001, 12345.6789, 7.8239])
def test_written_quantity_survives_read_and_export(client, monkeypatch, au, qty):
    """submitted == stored == /api/mybook == /api/account/export."""
    server, c = client
    _login(server, monkeypatch, c)

    r = c.post("/api/mybook", json={"symbol": "NVDA", "shares": qty})
    assert r.status_code == 200, r.text

    stored = {p["symbol"]: p for p in au.get_positions(CUSTOMER)}["NVDA"]["shares"]
    assert stored == pytest.approx(qty, rel=1e-9), "storage layer altered the quantity"

    book = _book_positions(c)["NVDA"]["quantity"]
    assert book == pytest.approx(qty, rel=1e-9), "/api/mybook altered the quantity"

    exported = _export_positions(c)["NVDA"]["shares"]
    assert exported == pytest.approx(qty, rel=1e-9), (
        f"/api/account/export returned {exported} for a submitted {qty} "
        f"(factor {exported / qty if qty else float('nan'):.6f})")


def test_export_carries_no_constant_scaling_factor(client, monkeypatch):
    """Regression guard for the class of defect, not one instance of it.

    If ANY path re-introduces a constant multiplier, the ratio export/submitted
    is identical across every position — which is exactly what this asserts
    cannot be true unless that ratio is 1.
    """
    server, c = client
    _login(server, monkeypatch, c)
    submitted = {"NVDA": 3.0, "AAPL": 11.25, "TSLA": 0.125}
    for sym, qty in submitted.items():
        assert c.post("/api/mybook", json={"symbol": sym, "shares": qty}).status_code == 200

    exported = _export_positions(c)
    ratios = {s: exported[s]["shares"] / q for s, q in submitted.items()}
    assert all(r == pytest.approx(1.0, rel=1e-9) for r in ratios.values()), (
        f"export is scaled: per-symbol ratios {ratios}")


def test_export_cost_basis_and_quantity_stay_consistent(client, monkeypatch):
    """avg_cost was never in the masking key set while `shares` was, so a scaled
    export silently changed cost basis PER SHARE — a book that reconciled on
    write no longer reconciled on export."""
    server, c = client
    _login(server, monkeypatch, c)
    c.post("/api/mybook", json={"symbol": "NVDA", "shares": 4.0, "avg_cost": 90.0})

    pos = _export_positions(c)["NVDA"]
    assert pos["shares"] == pytest.approx(4.0)
    assert pos["avg_cost"] == pytest.approx(90.0)
    # cost basis implied by the export must equal what the user entered
    assert pos["shares"] * pos["avg_cost"] == pytest.approx(360.0)


def test_export_watchlist_and_prefs_survive_masking(client, monkeypatch):
    """The export is a portability artifact: nothing in it may be transformed."""
    server, c = client
    _login(server, monkeypatch, c)
    c.post("/api/watchlist", json={"symbol": "NVDA"})
    c.post("/api/mybook", json={"symbol": "AAPL", "shares": 2})

    data = c.get("/api/account/export").json()
    assert "NVDA" in data["watchlist"]
    assert data["account"]["email"] == CUSTOMER


def test_export_is_still_tenant_scoped(client, monkeypatch, au):
    """Un-masking the export must not widen its scope."""
    server, c = client
    _login(server, monkeypatch, c)
    c.post("/api/mybook", json={"symbol": "NVDA", "shares": 5})

    _login(server, monkeypatch, c, "other@x.com")
    assert _export_positions(c) == {}


def test_dormant_model_scaling_preserves_holding_identity(client):
    """The dormant model serializer remains mathematically valid for later use."""
    server, _c = client
    raw_total = 1000.0
    payload = {
        "as_of": "2026-07-23T00:00:00+00:00",
        "holdings": [{"symbol": "OWN", "quantity": 10.0, "equity": raw_total,
                      "current_price": 100.0, "average_buy_price": 50.0,
                      "percent_change": 0.0, "equity_change": 0.0,
                      "type": "stock", "portfolio_percentage": 100.0}],
        "equity_summary": {"portfolio_equity": raw_total, "cash": 0.0,
                           "total": raw_total,
                           "adjusted_equity_previous_close": raw_total},
        "watchlist": [],
    }
    published = model_portfolio.publish(payload)
    basis = published["positions_market_value"] + published["cash"]
    factor = server._model_scale_factor(basis)
    d = server._scale_money(published, factor)

    assert basis == pytest.approx(raw_total)
    assert factor == pytest.approx(server._MODEL_BOOK_USD / raw_total)
    h = d["holdings"][0]
    assert sum(x["equity"] for x in d["holdings"]) + d["cash"] == pytest.approx(
        server._MODEL_BOOK_USD, abs=0.05)
    assert h["equity"] == pytest.approx(h["quantity"] * h["current_price"], rel=1e-6)
    assert h["quantity"] != pytest.approx(10.0), "quantity was not scaled at all"
    assert "average_buy_price" not in h
