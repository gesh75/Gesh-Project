"""The model book must equal its own components.

Audit Q-03 (2026-07-22): the customer-facing $100,000 model book showed
holdings $72,872.82 + cash $25,029.18 = $97,902.00 under a $100,000.00 headline,
and the resulting "+2.28% day change" was an artifact of that $2,098 gap.

Traced cause: the owner snapshot's broker-reported `total` ($35,750.87) exceeds
positions ($26,052.67) + cash ($8,948.15) = $35,000.82 by $750.05 — the broker
total covers sleeves the payload does not itemize. `_customer_scale_factor`
divided the model book by that reported total, so the headline landed on exactly
$100,000 by construction while the components landed $2,098 short
($750.05 × 2.7971).

Fixed on both sides: the headline is DERIVED from components, and the scale
factor uses the same derived total, so the identity holds for any snapshot.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

OWNER = "georgy75@gmail.com"


@pytest.fixture
def client(monkeypatch, tmp_path):
    import auth_users
    import server
    monkeypatch.setattr(auth_users, "AUTH_DB_PATH", tmp_path / "auth.db")
    auth_users.init_auth_db()
    auth_users.create_user(OWNER, "test-password")
    auth_users.create_user("cust@x.com", "test-password")
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: True)
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda p, c: "allow", raising=False)
    monkeypatch.setattr(server.auth_users, "is_owner", lambda e: e == OWNER, raising=False)
    monkeypatch.setattr(server, "_maybe_log_snapshot", lambda h: None, raising=True)
    return server, TestClient(server.app, raise_server_exceptions=False)


def _seed_payload(server, monkeypatch, positions_value, cash, reported_total):
    """A snapshot shaped exactly like the audited one: broker total > components."""
    monkeypatch.setattr(
        server, "build_data_payload",
        lambda wl: {
            "as_of": "2026-07-23T00:00:00+00:00",
            "holdings": [{"symbol": "OWN", "quantity": 100.0, "equity": positions_value,
                          "current_price": positions_value / 100, "average_buy_price": 1.0,
                          "percent_change": 0.0, "equity_change": 0.0, "type": "stock",
                          "portfolio_percentage": 100.0}],
            "equity_summary": {
                "portfolio_equity": positions_value, "cash": cash,
                "total": reported_total, "account_equity": reported_total,
                "adjusted_equity_previous_close": positions_value,
            },
            "watchlist": [],
        },
        raising=True)


# Real audited proportions (owner snapshot 2026-07-22) — used as a REALISTIC
# shape, never as an expected output value.
_POSITIONS, _CASH, _REPORTED = 26_052.67, 8_948.15, 35_750.87


def _as(server, monkeypatch, c, email):
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: email)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")


def test_owner_headline_equals_components(client, monkeypatch):
    server, c = client
    _seed_payload(server, monkeypatch, _POSITIONS, _CASH, _REPORTED)
    _as(server, monkeypatch, c, OWNER)

    eq = c.get("/api/portfolio").json()["data"]["equity_summary"]
    assert eq["total"] == pytest.approx(eq["portfolio_equity"] + eq["cash"], abs=0.05)
    assert eq["account_equity"] == pytest.approx(eq["total"])


def test_broker_reported_total_is_preserved_and_diffed(client, monkeypatch):
    """The un-modelled sleeve must be visible, not silently absorbed."""
    server, c = client
    _seed_payload(server, monkeypatch, _POSITIONS, _CASH, _REPORTED)
    _as(server, monkeypatch, c, OWNER)

    eq = c.get("/api/portfolio").json()["data"]["equity_summary"]
    assert eq["reported_total"] == pytest.approx(_REPORTED)
    assert eq["reported_total_delta"] == pytest.approx(_REPORTED - (_POSITIONS + _CASH), abs=0.05)
    assert eq["reconciles"] is False          # honestly flagged, not hidden


def test_customer_model_book_reconciles(client, monkeypatch):
    """The headline a customer sees must equal the components they see."""
    server, c = client
    _seed_payload(server, monkeypatch, _POSITIONS, _CASH, _REPORTED)
    _as(server, monkeypatch, c, "cust@x.com")
    monkeypatch.setattr(
        server, "_customer_scale_factor",
        lambda: server._MODEL_BOOK_USD / (_POSITIONS + _CASH), raising=True)

    body = c.get("/api/portfolio").json()
    assert body["view"] == "model_book"
    d = body["data"]
    holdings = sum(h["equity"] for h in d["holdings"])

    assert holdings + d["cash"] == pytest.approx(d["calculated_total_equity"], abs=0.05)
    assert d["calculated_total_equity"] == pytest.approx(server._MODEL_BOOK_USD, abs=0.05)
    assert d["reconciles"] is True


def test_scale_factor_uses_components_not_broker_total(client):
    """The specific line that produced the $2,098 gap."""
    server, _c = client
    es = {"portfolio_equity": _POSITIONS, "cash": _CASH, "total": _REPORTED}
    factor = server._scale_factor_from_summary(es)
    assert factor == pytest.approx(server._MODEL_BOOK_USD / (_POSITIONS + _CASH))
    assert factor != pytest.approx(server._MODEL_BOOK_USD / _REPORTED)
    # …and the model book it produces reconciles exactly
    assert (_POSITIONS + _CASH) * factor == pytest.approx(server._MODEL_BOOK_USD, abs=0.01)


def test_scale_factor_fails_closed_on_unusable_summary(client):
    server, _c = client
    assert server._scale_factor_from_summary({}) == 0.0
    assert server._scale_factor_from_summary({"portfolio_equity": 0, "cash": 0}) == 0.0
    # legacy snapshots that only carry `total` still work
    assert server._scale_factor_from_summary({"total": 50_000.0}) == pytest.approx(
        server._MODEL_BOOK_USD / 50_000.0)


# ─── the reconciliation branch matrix ────────────────────────────────────────

@pytest.mark.parametrize("positions,cash,reported,expect_reconciles,expect_state", [
    # broker total GREATER than components (the audited case: +$750.05)
    (26_052.67, 8_948.15, 35_750.87, False, "degraded"),
    # broker total LOWER than components
    (26_052.67, 8_948.15, 34_000.00, False, "degraded"),
    # exact reconciliation
    (26_052.67, 8_948.15, 35_000.82, True, "calculated"),
    # within rounding tolerance
    (26_052.67, 8_948.15, 35_000.85, True, "calculated"),
    # missing broker total
    (26_052.67, 8_948.15, None, True, "calculated"),
])
def test_reconciliation_matrix(client, monkeypatch, positions, cash, reported,
                               expect_reconciles, expect_state):
    server, c = client
    _seed_payload(server, monkeypatch, positions, cash, reported)
    if reported is None:
        base = server.build_data_payload
        monkeypatch.setattr(server, "build_data_payload",
                            lambda wl: {**base(wl), "equity_summary": {
                                "portfolio_equity": positions, "cash": cash,
                                "adjusted_equity_previous_close": positions}},
                            raising=True)
    _as(server, monkeypatch, c, OWNER)

    eq = c.get("/api/portfolio").json()["data"]["equity_summary"]
    # The calculated total ALWAYS wins, in every branch.
    assert eq["total"] == pytest.approx(positions + cash, abs=0.05)
    assert eq["calculated_total_equity"] == pytest.approx(positions + cash, abs=0.05)
    assert eq["reconciles"] is expect_reconciles
    assert eq["valuation_source_state"] == expect_state
    if reported is None:
        assert eq["reported_total"] is None
    else:
        # the broker figure is preserved internally, never used as the headline
        assert eq["reported_total"] == pytest.approx(reported)
        assert eq["reported_total_delta"] == pytest.approx(
            reported - (positions + cash), abs=0.05)


def test_variance_is_logged_without_dollar_figures(client, monkeypatch, caplog):
    """Log the variance — but an alert about a leak must not become one."""
    server, c = client
    _seed_payload(server, monkeypatch, _POSITIONS, _CASH, _REPORTED)
    _as(server, monkeypatch, c, OWNER)
    with caplog.at_level("WARNING"):
        c.get("/api/portfolio")
    variance = [r for r in caplog.records if "equity variance" in r.getMessage()]
    assert variance, "a non-reconciling broker total was not logged"
    msg = variance[0].getMessage()
    for secret in (f"{_REPORTED}", f"{_POSITIONS}", f"{_CASH}", "35,750"):
        assert secret not in msg


def test_missing_position_price_does_not_fabricate_equity(client, monkeypatch):
    """An unpriced holding must not silently contribute 0 to a headline that
    still claims to be the whole book."""
    server, c = client
    monkeypatch.setattr(
        server, "build_data_payload",
        lambda wl: {
            "as_of": "2026-07-23T00:00:00+00:00",
            "holdings": [
                {"symbol": "OK", "quantity": 10.0, "equity": 1000.0,
                 "current_price": 100.0, "type": "stock", "portfolio_percentage": 100.0},
                {"symbol": "NOPRICE", "quantity": 5.0, "equity": 0.0,
                 "current_price": None, "type": "stock", "portfolio_percentage": 0.0},
            ],
            "equity_summary": {"portfolio_equity": 1000.0, "cash": 500.0,
                               "total": 1500.0,
                               "adjusted_equity_previous_close": 1000.0},
            "watchlist": [],
        }, raising=True)
    _as(server, monkeypatch, c, "cust@x.com")
    monkeypatch.setattr(server, "_customer_scale_factor", lambda: 1.0, raising=True)

    d = c.get("/api/portfolio").json()["data"]
    # identity still holds over what IS priced …
    assert d["positions_market_value"] + d["cash"] == pytest.approx(
        d["calculated_total_equity"], abs=0.05)
    # … and the unpriced name is carried with a null price, not an invented one
    noprice = [h for h in d["holdings"] if h["symbol"] == "NOPRICE"][0]
    assert noprice["current_price"] is None


def test_day_change_unavailable_when_no_prior_close(client, monkeypatch):
    server, c = client
    _seed_payload(server, monkeypatch, _POSITIONS, _CASH, _REPORTED)
    monkeypatch.setattr(
        server, "build_data_payload",
        lambda wl: {
            "as_of": "2026-07-23T00:00:00+00:00", "holdings": [], "watchlist": [],
            "equity_summary": {"portfolio_equity": 0.0, "cash": _CASH, "total": _CASH},
        }, raising=True)
    _as(server, monkeypatch, c, OWNER)

    eq = c.get("/api/portfolio").json()["data"]["equity_summary"]
    assert eq["day_change_available"] is False
    assert eq["day_change"] is None           # NOT 0.0
