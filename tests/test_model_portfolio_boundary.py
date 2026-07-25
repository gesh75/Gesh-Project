"""The customer/owner portfolio boundary.

Four distinct portfolios exist (owner brokerage · internal execution book ·
published model portfolio · individual customer book). Before 2026-07-23 a
customer received #1 — the raw broker payload, `source: "robinhood_mcp"`,
per-quote `session` identifiers, buying power, average costs — passed through a
middleware that only rescaled the dollar VALUES. Transformed owner account data
is still owner account data (audit A-01).

`model_portfolio.publish()` is a build-up allowlist, so the property under test
is not "today's known bad fields are absent" but "only named fields are
present". A field added to the owner payload next month cannot leak.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import model_portfolio

CUSTOMER = "cust@x.com"
OWNER = "georgy75@gmail.com"

# A payload shaped like the REAL owner snapshot, including everything that must
# not escape: brokerage capacity, quote-session metadata, cost basis, execution
# state. Values are sentinels so a leak is unambiguous.
_OWNER_PAYLOAD = {
    "as_of": "2026-07-23T00:00:00+00:00",
    "source": "robinhood_mcp",
    "quote_provenance": {"NVDA": {"provider": "robinhood_mcp",
                                  "session": "mcp-snapshot-77771",
                                  "quote_as_of": "2026-07-22T18:48:12Z"}},
    "account_number": "RH-ACCT-99999",
    "holdings": [{
        "symbol": "NVDA", "quantity": 15.768482, "current_price": 213.85,
        "equity": 3372.011, "portfolio_percentage": 12.9, "type": "stock",
        "average_buy_price": 88.7711, "percent_change": 140.9,
        "equity_change": 1972.0111, "ecg_state": "SUSPENDED", "drawdown_pct": 22.2,
    }],
    "equity_summary": {
        "portfolio_equity": 26052.67, "cash": 8948.15, "total": 35750.87,
        "account_equity": 35750.87, "buying_power": 8948.15,
        "buying_power_leveraged": 21632.44, "margin_buying_power": 21632.44,
        "withdrawable_amount": 8948.15, "deployable": 8948.15,
        "settled_cash": 8948.15, "pending_deposits": 750.05,
        "adjusted_equity_previous_close": 34954.40,
    },
    "risk_gate": {"kill_switch": False, "orders_today": 3},
    "paper_session": {"fills": 2},
    "watchlist": [],
}

_SENTINELS = ("RH-ACCT-99999", "mcp-snapshot-77771", "robinhood_mcp",
              "21632.44", "88.7711", "SUSPENDED", "kill_switch", "750.05")


# ─── the serializer, in isolation ────────────────────────────────────────────

def test_publish_emits_only_allowlisted_top_level_fields():
    out = model_portfolio.publish(_OWNER_PAYLOAD)
    extra = set(out) - set(model_portfolio.PUBLISHED_TOP_LEVEL_FIELDS)
    assert not extra, f"unlisted field(s) published: {sorted(extra)}"


def test_publish_emits_only_allowlisted_holding_fields():
    out = model_portfolio.publish(_OWNER_PAYLOAD)
    for h in out["holdings"]:
        extra = set(h) - set(model_portfolio.PUBLISHED_HOLDING_FIELDS)
        assert not extra, f"unlisted holding field(s) published: {sorted(extra)}"


@pytest.mark.parametrize("sentinel", _SENTINELS)
def test_publish_drops_every_brokerage_and_execution_sentinel(sentinel):
    blob = json.dumps(model_portfolio.publish(_OWNER_PAYLOAD))
    assert sentinel not in blob, f"owner/brokerage value published: {sentinel}"


def test_publish_forbids_key_names_by_tripwire():
    """Belt-and-braces alongside the allowlist: no published key may LOOK like
    brokerage, session or execution state."""
    def walk(obj, path=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                lk = str(k).lower()
                for bad in model_portfolio.FORBIDDEN_KEY_SUBSTRINGS:
                    assert bad not in lk, f"forbidden key {path}.{k}"
                walk(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for x in obj:
                walk(x, path)
    walk(model_portfolio.publish(_OWNER_PAYLOAD))


def test_a_new_owner_field_cannot_leak():
    """The property that matters: the allowlist is build-up, not tear-down."""
    payload = {**_OWNER_PAYLOAD, "some_future_broker_field": "LEAK-ME-42"}
    payload["holdings"] = [{**_OWNER_PAYLOAD["holdings"][0],
                            "future_position_field": "LEAK-ME-43"}]
    blob = json.dumps(model_portfolio.publish(payload))
    assert "LEAK-ME-42" not in blob and "LEAK-ME-43" not in blob


def test_published_total_is_calculated_not_the_broker_headline():
    out = model_portfolio.publish(_OWNER_PAYLOAD)
    # CANONICAL equity (quantity × current_price), not the source `equity` field:
    # 15.768482 × 213.85 = 3372.09, vs a source equity of 3372.01. The 0.08 gap is
    # inside the price-display tolerance, so this still reconciles — but the number
    # published is the one derived from the quantity and price actually shown.
    canonical_position = round(15.768482 * 213.85, 2)
    assert out["calculated_total_equity"] == pytest.approx(canonical_position + 8948.15,
                                                           abs=0.02)
    assert out["calculated_total_equity"] != pytest.approx(35750.87)
    assert out["positions_market_value"] + out["cash"] == pytest.approx(
        out["calculated_total_equity"], abs=0.02)


def test_publication_metadata_is_present_and_labelled():
    out = model_portfolio.publish(_OWNER_PAYLOAD)
    assert out["model_portfolio_id"] == model_portfolio.MODEL_PORTFOLIO_ID
    assert out["source_label"] == model_portfolio.SOURCE_LABEL
    assert "not your account" in out["methodology"]
    assert out["published_at"] and out["data_as_of"]
    assert out["version"].startswith("v")


def test_version_is_stable_for_stable_composition_and_moves_when_it_changes():
    a = model_portfolio.publish(_OWNER_PAYLOAD)["version"]
    b = model_portfolio.publish(_OWNER_PAYLOAD)["version"]
    assert a == b
    changed = {**_OWNER_PAYLOAD,
               "holdings": [{**_OWNER_PAYLOAD["holdings"][0], "quantity": 20.0}]}
    assert model_portfolio.publish(changed)["version"] != a


def test_stale_flag_is_carried_through():
    stale = {**_OWNER_PAYLOAD, "stale_snapshot": True}
    assert model_portfolio.publish(stale)["stale"] is True
    assert model_portfolio.publish(_OWNER_PAYLOAD)["stale"] is False


# ─── end to end through the real route ───────────────────────────────────────

@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    import auth_users
    import server
    monkeypatch.setattr(auth_users, "AUTH_DB_PATH", tmp_path / "auth.db")
    auth_users.init_auth_db()
    for e in (CUSTOMER, OWNER):
        auth_users.create_user(e, "test-password")
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: True)
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda p, c: "allow", raising=False)
    monkeypatch.setattr(server.auth_users, "is_owner", lambda e: e == OWNER, raising=False)
    monkeypatch.setattr(server, "build_data_payload",
                        lambda wl: json.loads(json.dumps(_OWNER_PAYLOAD)), raising=True)
    monkeypatch.setattr(server, "_maybe_log_snapshot", lambda h: None, raising=True)
    monkeypatch.setattr(server, "_customer_scale_factor", lambda: 2.0, raising=True)
    return server, TestClient(server.app, raise_server_exceptions=False)


def _as(server, monkeypatch, c, email):
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: email)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")


@pytest.mark.parametrize("sentinel", _SENTINELS)
def test_customer_route_leaks_no_brokerage_data(client, monkeypatch, sentinel):
    server, c = client
    _as(server, monkeypatch, c, CUSTOMER)
    blob = c.get("/api/portfolio").text
    assert sentinel not in blob, f"reached a customer via /api/portfolio: {sentinel}"


def test_customer_route_returns_the_published_shape(client, monkeypatch):
    server, c = client
    _as(server, monkeypatch, c, CUSTOMER)
    d = c.get("/api/portfolio").json()["data"]
    assert "equity_summary" not in d and "quote_provenance" not in d
    assert d["source_label"] == model_portfolio.SOURCE_LABEL
    assert d["positions_market_value"] + d["cash"] == pytest.approx(
        d["calculated_total_equity"], abs=0.05)


def test_dollars_are_scaled_exactly_once(client, monkeypatch):
    """publish() must NOT scale — the masking middleware is the single scaler.
    Double application would show as a book that misses the model notional."""
    server, c = client
    _as(server, monkeypatch, c, CUSTOMER)
    d = c.get("/api/portfolio").json()["data"]
    basis_cash, basis_pos = 8948.15, round(15.768482 * 213.85, 2)
    factor = server._MODEL_BOOK_USD / (basis_pos + basis_cash)
    # exactly one application of exactly that factor
    assert d["cash"] == pytest.approx(basis_cash * factor, abs=0.05)
    assert d["holdings"][0]["equity"] == pytest.approx(basis_pos * factor, abs=0.05)
    assert sum(h["equity"] for h in d["holdings"]) + d["cash"] == pytest.approx(
        server._MODEL_BOOK_USD, abs=0.05)


def test_owner_still_receives_the_full_brokerage_payload(client, monkeypatch):
    """The boundary is one-directional — the operator must not lose their own data."""
    server, c = client
    _as(server, monkeypatch, c, OWNER)
    d = c.get("/api/portfolio").json()["data"]
    assert d["equity_summary"]["buying_power_leveraged"] == pytest.approx(21632.44)
    assert d["holdings"][0]["average_buy_price"] == pytest.approx(88.7711)
    assert d["source"] == "robinhood_mcp"


def test_unauthenticated_caller_is_treated_as_a_customer(client, monkeypatch):
    """Fails CLOSED: no valid session must never mean 'owner'."""
    server, c = client
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: None)
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda p, ck: "allow", raising=False)
    d = c.get("/api/portfolio").json()["data"]
    assert "equity_summary" not in d
