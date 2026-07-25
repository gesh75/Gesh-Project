"""Two financial invariants the earlier remediation left open.

INVARIANT 1 — holding-level accounting identity.
`_reconciliation_verdict()` built canonical positions from quantity × price and
asked portfolio_valuation whether they reconciled, but `publish()` computed
`positions_market_value` / `calculated_total_equity` by SUMMING THE SOURCE
`equity` FIELDS. The two numbers were never compared, so a payload whose per-row
equity disagreed with quantity × price was published as `reconciles: true`:

    quantity=10, current_price=100, equity=900, cash=100
    canonical total 1,100  vs  published total 1,000   -> "reconciles": true

INVARIANT 2 — customer model-book day change must include cash.
`/api/portfolio` computed the percentage as
`(current_positions - prior_positions) / prior_positions`. The DOLLAR amount is
right when cash is unchanged (cash cancels), but the customer's book total is
positions + cash, so the DENOMINATOR was wrong:

    prior positions 80,000 -> current 88,000, cash 20,000
    reported 10.0%   correct 8.0%   (8,000 / 100,000)

Expected values here are computed independently in the test, never by calling
the production helper under test.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import model_portfolio

OWNER = "georgy75@gmail.com"
CUSTOMER = "cust@x.com"


# ─────────────────────────────────────────────────────────────────────────────
# INVARIANT 1 — holding identity
# ─────────────────────────────────────────────────────────────────────────────

def _payload(holdings: list[dict], cash: float = 100.0, **eq_extra) -> dict:
    eq = {"portfolio_equity": sum((h.get("equity") or 0) for h in holdings),
          "cash": cash}
    eq.update(eq_extra)
    return {"as_of": "2026-07-25T00:00:00+00:00", "holdings": holdings,
            "equity_summary": eq, "watchlist": []}


def _h(symbol="NVDA", quantity=10, price=100.0, equity=None, **extra):
    row = {"symbol": symbol, "quantity": quantity, "current_price": price,
           "equity": equity, "portfolio_percentage": 100.0, "type": "stock"}
    row.update(extra)
    return row


# CASE A — substantial mismatch (the exact negative control)
def test_A_substantial_mismatch_is_not_published_as_reconciled():
    out = model_portfolio.publish(_payload([_h(quantity=10, price=100.0, equity=900.0)],
                                           cash=100.0))
    # independently computed: 10 × 100 = 1,000 canonical; + 100 cash = 1,100
    assert out["holdings"][0]["equity"] == pytest.approx(1000.0), "source equity was trusted"
    assert out["positions_market_value"] == pytest.approx(1000.0)
    assert out["calculated_total_equity"] == pytest.approx(1100.0)
    assert out["reconciles"] is False
    assert out["valuation_status"] == "degraded"
    assert out["degraded_reason"] == "holding_equity_mismatch"


def test_A_context_uses_the_same_canonical_values():
    ctx = model_portfolio.build_customer_model_context(
        _payload([_h(quantity=10, price=100.0, equity=900.0)], cash=100.0))
    assert ctx["holdings"][0]["equity"] == pytest.approx(1000.0)
    assert ctx["equity_summary"]["portfolio_equity"] == pytest.approx(1000.0)
    assert ctx["equity_summary"]["total"] == pytest.approx(1100.0)


def test_A_serializers_agree_holding_for_holding():
    """The response serializer and the analysis-context serializer must not
    compute holdings differently — that divergence is the defect class."""
    p = _payload([_h("NVDA", 3, 33.33, 111.0), _h("MU", 4.5, 9.99, 40.0)], cash=7.0)
    pub = {h["symbol"]: h["equity"] for h in model_portfolio.publish(p)["holdings"]}
    ctx = {h["symbol"]: h["equity"] for h in
           model_portfolio.build_customer_model_context(p)["holdings"]}
    assert pub == ctx


# CASE B — legitimate two-decimal price-display rounding must NOT degrade
def test_B_price_display_rounding_is_not_a_mismatch():
    """A broker equity computed from full precision differs from
    quantity × (price rounded to 2dp) by at most quantity × 0.005."""
    qty, shown_price = 15.768482, 213.85
    # broker used an unrounded price; the drift is inside the display tolerance
    source_equity = float(Decimal("15.768482") * Decimal("213.8537"))
    drift = abs(source_equity - qty * shown_price)
    assert drift <= qty * 0.005, "fixture is not a display-rounding case"

    out = model_portfolio.publish(_payload([_h(quantity=qty, price=shown_price,
                                               equity=source_equity)], cash=0.0))
    assert out["reconciles"] is True, "false degradation on legitimate rounding"
    assert out["valuation_status"] == "reconciled"
    # output stays canonical and internally consistent
    assert out["holdings"][0]["equity"] == pytest.approx(qty * shown_price, abs=0.01)
    assert out["positions_market_value"] + out["cash"] == pytest.approx(
        out["calculated_total_equity"], abs=0.02)


def test_B_drift_just_beyond_tolerance_does_degrade():
    """The tolerance must still be a boundary, not a blanket pass."""
    qty, price = 10.0, 100.0
    # max legitimate drift is 10 × 0.005 = 0.05 (+ the money tolerance); 5.00 is far past it
    out = model_portfolio.publish(_payload([_h(quantity=qty, price=price,
                                               equity=qty * price + 5.00)], cash=0.0))
    assert out["reconciles"] is False
    assert out["degraded_reason"] == "holding_equity_mismatch"


# CASE C — missing price
def test_C_missing_price_is_degraded_not_a_zero_book():
    out = model_portfolio.publish(_payload([_h(quantity=10, price=None, equity=None)],
                                           cash=100.0))
    assert out["reconciles"] is False
    assert out["valuation_status"] in ("degraded", "unavailable")
    assert out["degraded_reason"] == "missing_price"
    # an unpriced row must not silently contribute a fabricated 0 to a total
    # presented as the whole book
    assert out["calculated_total_equity"] == pytest.approx(100.0)


# CASE D — non-finite inputs fail closed
@pytest.mark.parametrize("field,value", [
    ("quantity", float("nan")), ("quantity", float("inf")),
    ("price", float("nan")), ("price", float("inf")),
    ("equity", float("nan")), ("equity", float("inf")),
])
def test_D_non_finite_fails_closed(field, value):
    kwargs = {"quantity": 10, "price": 100.0, "equity": 1000.0}
    kwargs[field] = value
    out = model_portfolio.publish(_payload([_h(**kwargs)], cash=100.0))
    assert out["reconciles"] is False
    assert out["valuation_status"] == "unavailable"


def test_D_non_finite_cash_fails_closed():
    out = model_portfolio.publish(_payload([_h()], cash=float("nan")))
    assert out["reconciles"] is False
    assert out["valuation_status"] == "unavailable"


def test_D_weights_recomputed_from_canonical_equity():
    """portfolio_percentage must follow the canonical equities, not the source
    row's own (possibly wrong) percentage."""
    out = model_portfolio.publish(_payload(
        [_h("A", 10, 100.0, 900.0, portfolio_percentage=99.0),
         _h("B", 10, 100.0, 1000.0, portfolio_percentage=1.0)], cash=0.0))
    pct = {h["symbol"]: h["portfolio_percentage"] for h in out["holdings"]}
    assert pct["A"] == pytest.approx(50.0, abs=0.1)
    assert pct["B"] == pytest.approx(50.0, abs=0.1)


# ─────────────────────────────────────────────────────────────────────────────
# INVARIANT 2 — cash-aware day change
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def client(tmp_path: Path, monkeypatch):
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
    return server, monkeypatch


def _mk(server, monkeypatch, *, positions: float, prior: float | None, cash: float,
        role: str = "customer", scale: float = 1.0):
    """One holding worth `positions`, priced so quantity × price == positions."""
    payload = {
        "as_of": "2026-07-25T00:00:00+00:00",
        "holdings": [{"symbol": "NVDA", "quantity": 1000.0,
                      "current_price": positions / 1000.0, "equity": positions,
                      "portfolio_percentage": 100.0, "type": "stock"}],
        "equity_summary": {"portfolio_equity": positions, "cash": cash,
                           **({"adjusted_equity_previous_close": prior}
                              if prior is not None else {})},
        "watchlist": [],
    }
    monkeypatch.setattr(server, "build_data_payload",
                        lambda wl: json.loads(json.dumps(payload)), raising=True)
    monkeypatch.setattr(server, "_customer_scale_factor", lambda: scale, raising=True)
    email = {"owner": OWNER, "customer": CUSTOMER}[role]
    monkeypatch.setattr(server.auth_users, "authenticate_session",
                        lambda tok: email, raising=True)
    c = TestClient(server.app, raise_server_exceptions=False)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    return c.get("/api/portfolio").json()["data"]


# CASE A — the exact negative control: cash in the denominator
def test_day_change_percent_uses_total_including_cash(client):
    server, monkeypatch = client
    d = _mk(server, monkeypatch, positions=88_000.0, prior=80_000.0, cash=20_000.0)
    # independently: prior total 100,000; current 108,000; +8,000 = 8.00%
    assert d["day_change"] == pytest.approx(8_000.0)
    assert d["day_change_pct"] == pytest.approx(8.0, abs=0.01)
    assert d["day_change_pct"] != pytest.approx(10.0, abs=0.01), "positions-only denominator"
    assert d["calculated_total_equity"] == pytest.approx(108_000.0)


def test_owner_summary_exposes_prior_close_total_and_positions_only_separately(client):
    server, monkeypatch = client
    d = _mk(server, monkeypatch, positions=88_000.0, prior=80_000.0,
            cash=20_000.0, role="owner")
    eq = d["equity_summary"]
    assert eq["prior_close_equity"] == pytest.approx(100_000.0)
    assert eq["day_change_pct"] == pytest.approx(8.0, abs=0.01)
    # a positions-only metric may exist, but must be unambiguously named
    assert eq["positions_day_change_pct"] == pytest.approx(10.0, abs=0.01)


# CASE B — no cash: identical to the positions-only result
def test_day_change_with_no_cash_matches_positions_only(client):
    server, monkeypatch = client
    d = _mk(server, monkeypatch, positions=88_000.0, prior=80_000.0, cash=0.0)
    assert d["day_change"] == pytest.approx(8_000.0)
    assert d["day_change_pct"] == pytest.approx(10.0, abs=0.01)


# CASE C — missing prior close
def test_missing_prior_close_is_unavailable_not_zero(client):
    server, monkeypatch = client
    d = _mk(server, monkeypatch, positions=88_000.0, prior=None, cash=20_000.0)
    assert d["day_change"] is None
    assert d["day_change_pct"] is None
    assert d["day_change_available"] is False


# CASE D — negative day stays consistent
def test_negative_day_is_internally_consistent(client):
    server, monkeypatch = client
    d = _mk(server, monkeypatch, positions=72_000.0, prior=80_000.0, cash=20_000.0)
    assert d["day_change"] == pytest.approx(-8_000.0)
    assert d["day_change_pct"] == pytest.approx(-8.0, abs=0.01)   # -8,000 / 100,000


# CASE E — scaling: amount scales once, percentage does not
def test_scaling_moves_the_amount_once_and_leaves_the_percentage(client):
    server, monkeypatch = client
    d = _mk(server, monkeypatch, positions=88_000.0, prior=80_000.0,
            cash=20_000.0, scale=2.0)
    assert d["day_change"] == pytest.approx(16_000.0)      # 8,000 × 2
    assert d["day_change_pct"] == pytest.approx(8.0, abs=0.01)   # unchanged
    assert d["calculated_total_equity"] == pytest.approx(216_000.0)


# CASE F — the published contract, self-consistent after scaling
@pytest.mark.parametrize("scale", [1.0, 2.0, 0.5])
def test_route_holding_identity_survives_scaling(client, scale):
    """quantity and equity scale together while price stays per-share, so
    equity ≈ quantity × current_price must hold in the delivered response."""
    server, monkeypatch = client
    d = _mk(server, monkeypatch, positions=88_000.0, prior=80_000.0,
            cash=20_000.0, scale=scale)
    for h in d["holdings"]:
        assert h["equity"] == pytest.approx(h["quantity"] * h["current_price"], rel=1e-6)
    assert sum(h["equity"] for h in d["holdings"]) + d["cash"] == pytest.approx(
        d["calculated_total_equity"], abs=0.05)


def test_day_change_percentage_matches_the_published_total(client):
    """day_change_pct ≈ day_change / (total - day_change) × 100."""
    server, monkeypatch = client
    d = _mk(server, monkeypatch, positions=88_000.0, prior=80_000.0, cash=20_000.0)
    implied = d["day_change"] / (d["calculated_total_equity"] - d["day_change"]) * 100
    assert implied == pytest.approx(d["day_change_pct"], abs=0.01)
