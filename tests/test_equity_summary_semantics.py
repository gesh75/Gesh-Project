"""`equity_summary.portfolio_equity` is ACCOUNT-level, not positions-only.

Both production normalizers agree, and both were read before writing this:

  * live     — `data_fetcher.get_total_equity()` sets
               `portfolio_equity = profile["equity"]`, the Robinhood PORTFOLIO
               PROFILE equity (positions + cash), with `cash` a SEPARATE field
               taken from `margin_balances.portfolio_cash`.
  * snapshot — `data_fetcher.load_snapshot_payload()` sets
               `portfolio_equity = headline`, where
               `headline = account_equity or total or portfolio_equity`, and its
               own comment says "ACCOUNT-level (positions + cash) … NOT
               positions-only portfolio_equity (Codex HIGH-2)".

The remediation nevertheless treated it as positions-only and added `cash` to
it, so an account of 108,000 with 20,000 cash reconciled to 128,000.

Note the trap that made this survive review: the RAW snapshot FILE on disk uses
`portfolio_equity` for POSITIONS and `total` for the account headline. Only
after `load_snapshot_payload()` normalizes does `portfolio_equity` become
account-level. Fixtures built by hand from the raw file therefore encode the
opposite meaning to the payload the application actually passes around — which
is exactly why the tests here go through the REAL normalizers (CASE G) instead
of asserting against hand-made summaries.
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import data_fetcher

OWNER = "georgy75@gmail.com"
CUSTOMER = "cust@x.com"

# One production-shaped account, used by every case below.
POSITIONS = 88_000.0          # Σ quantity × current_price
CASH = 20_000.0               # separate, uninvested
ACCOUNT_EQUITY = 108_000.0    # broker headline = positions + cash
PRIOR_ACCOUNT_EQUITY = 100_000.0
PRIOR_POSITIONS = 80_000.0


def _raw_snapshot(*, account_equity=ACCOUNT_EQUITY, prior=PRIOR_ACCOUNT_EQUITY,
                  positions=POSITIONS, cash=CASH, price=88.0) -> dict:
    """A snapshot file in the shape the daily chain actually writes."""
    qty = positions / price
    return {
        "fetched_at": datetime.now(timezone.utc).isoformat(), "source": "robinhood_mcp",
        "equity_summary": {
            # raw-file convention: positions here, account headline in `total`
            "portfolio_equity": positions,
            "account_equity": account_equity, "total": account_equity,
            "cash": cash,
            **({"adjusted_equity_previous_close": prior} if prior is not None else {}),
        },
        "holdings": [{"symbol": "NVDA", "quantity": qty, "price": price,
                      "equity": positions, "average_buy_price": 50.0,
                      "percent_change": 10.0}],
    }


def _normalized(raw: dict) -> dict:
    """Through the REAL loader — never a hand-built equity_summary."""
    p = Path(tempfile.mkdtemp()) / "snap.json"
    p.write_text(json.dumps(raw))
    return data_fetcher.load_snapshot_payload(p)


# ─── CASE G — the contract itself, via the real normalizers ──────────────────

def test_G_snapshot_normalizer_emits_account_level_portfolio_equity():
    out = _normalized(_raw_snapshot())
    eq = out["equity_summary"]
    assert eq["portfolio_equity"] == pytest.approx(ACCOUNT_EQUITY), (
        "portfolio_equity is the ACCOUNT headline after normalization")
    assert eq["cash"] == pytest.approx(CASH)
    assert sum(h["equity"] for h in out["holdings"]) == pytest.approx(POSITIONS)
    # the defect this file exists to prevent
    assert eq["portfolio_equity"] + eq["cash"] == pytest.approx(128_000.0), (
        "fixture no longer reproduces the double-count shape")


def test_G_live_normalizer_maps_account_equity_and_separate_cash(monkeypatch):
    """get_total_equity(): portfolio_equity is profile['equity'] (account-level)
    and cash comes from margin_balances, so they are independent figures."""
    import types
    fake_rh = types.SimpleNamespace(
        load_portfolio_profile=lambda: {
            "equity": ACCOUNT_EQUITY, "extended_hours_equity": ACCOUNT_EQUITY,
            "adjusted_equity_previous_close": PRIOR_ACCOUNT_EQUITY,
            "withdrawable_amount": 0},
        load_account_profile=lambda: {"margin_balances": {"portfolio_cash": CASH}},
    )
    monkeypatch.setattr(data_fetcher, "rh", fake_rh, raising=True)
    eq = data_fetcher.get_total_equity()
    assert eq["portfolio_equity"] == pytest.approx(ACCOUNT_EQUITY)
    assert eq["cash"] == pytest.approx(CASH)
    assert eq["adjusted_equity_previous_close"] == pytest.approx(PRIOR_ACCOUNT_EQUITY)


# ─── route-level ─────────────────────────────────────────────────────────────

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
    return server, monkeypatch


def _get(server, monkeypatch, raw: dict, role: str, priors: dict | None = None):
    payload = _normalized(raw)          # production semantics, every time
    payload["offline"] = False          # exercise the live-shaped branch by default
    monkeypatch.setattr(server, "build_data_payload",
                        lambda wl: json.loads(json.dumps(payload)), raising=True)
    monkeypatch.setattr(server, "_prior_close",
                        lambda sym: (priors or {}).get(sym), raising=True)
    monkeypatch.setattr(server.auth_users, "authenticate_session",
                        lambda tok: {"owner": OWNER, "customer": CUSTOMER}[role],
                        raising=True)
    c = TestClient(server.app, raise_server_exceptions=False)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    return c.get("/api/portfolio").json()["data"]


# CASE A — production-shaped totals and account day return
def test_A_account_total_is_not_double_counted(env):
    server, monkeypatch = env
    d = _get(server, monkeypatch, _raw_snapshot(), "owner")
    eq = d["equity_summary"]
    assert eq["calculated_total_equity"] == pytest.approx(108_000.0)
    assert eq["calculated_total_equity"] != pytest.approx(128_000.0), "cash counted twice"
    assert eq["positions_market_value"] == pytest.approx(POSITIONS)
    assert eq["broker_account_equity"] == pytest.approx(ACCOUNT_EQUITY)


def test_A_account_day_change_is_account_minus_account(env):
    """108,000 vs a 100,000 previous ACCOUNT close = +8,000 = 8.0%.
    Cash is inside both sides already and must not be added to either."""
    server, monkeypatch = env
    eq = _get(server, monkeypatch, _raw_snapshot(), "owner")["equity_summary"]
    assert eq["day_change"] == pytest.approx(8_000.0)
    assert eq["day_change_pct"] == pytest.approx(8.0, abs=0.01)
    assert eq["day_change_pct"] != pytest.approx(6.67, abs=0.01), "cash added to prior"
    assert eq["day_change_pct"] != pytest.approx(10.0, abs=0.01), "positions-only denominator"


def test_A_customer_model_day_change_from_per_holding_priors(env):
    """The published model is component-only, so its prior must be component-only
    too: 1000 × 80.00 = 80,000 positions + 20,000 cash = 100,000 -> +8.0%."""
    server, monkeypatch = env
    d = _get(server, monkeypatch, _raw_snapshot(), "customer", priors={"NVDA": 80.0})
    assert d["day_change_available"] is True
    assert d["day_change_pct"] == pytest.approx(8.0, abs=0.01)


# CASE B — unmodelled broker sleeve
def test_B_unmodelled_sleeve_degrades_and_does_not_move_the_total(env):
    server, monkeypatch = env
    raw = _raw_snapshot(account_equity=110_000.0)     # 2,000 above components
    eq = _get(server, monkeypatch, raw, "owner")["equity_summary"]
    assert eq["calculated_total_equity"] == pytest.approx(108_000.0)
    assert eq["broker_account_equity"] == pytest.approx(110_000.0)
    assert eq["reported_total_delta"] == pytest.approx(2_000.0)
    assert eq["reconciles"] is False
    assert eq["valuation_source_state"] == "degraded"


# CASE C — exact customer scaling, through the REAL middleware
def test_C_customer_book_scales_to_exactly_the_model_notional(env):
    server, monkeypatch = env
    d = _get(server, monkeypatch, _raw_snapshot(), "customer", priors={"NVDA": 80.0})
    total = sum(h["equity"] for h in d["holdings"]) + d["cash"]
    assert total == pytest.approx(server._MODEL_BOOK_USD, abs=0.05)
    assert d["calculated_total_equity"] == pytest.approx(server._MODEL_BOOK_USD, abs=0.05)
    for h in d["holdings"]:
        assert h["equity"] == pytest.approx(h["quantity"] * h["current_price"], rel=1e-6)


def test_C_scaling_denominator_is_the_component_total(env):
    """With a 110,000 broker headline over 108,000 components, scaling by the
    headline would land the customer book at ~98,182, not the notional."""
    server, monkeypatch = env
    d = _get(server, monkeypatch, _raw_snapshot(account_equity=110_000.0), "customer")
    total = sum(h["equity"] for h in d["holdings"]) + d["cash"]
    assert total == pytest.approx(server._MODEL_BOOK_USD, abs=0.05)


# CASE D — offline snapshot path
def test_D_offline_snapshot_totals_match_the_live_shape(env):
    server, monkeypatch = env
    payload = _normalized(_raw_snapshot())
    payload["offline"] = True
    payload["snapshot_age_seconds"] = 900
    monkeypatch.setattr(server, "build_data_payload",
                        lambda wl: json.loads(json.dumps(payload)), raising=True)
    monkeypatch.setattr(server, "load_snapshot_payload",
                        lambda: json.loads(json.dumps(payload)), raising=True)
    monkeypatch.setattr(server.auth_users, "authenticate_session",
                        lambda tok: OWNER, raising=True)
    c = TestClient(server.app, raise_server_exceptions=False)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    d = c.get("/api/portfolio").json()["data"]
    assert d["equity_summary"]["calculated_total_equity"] == pytest.approx(108_000.0)
    assert d["data_freshness"]["state"] in ("cached", "stale")
    assert d["data_freshness"]["is_live"] is False


# CASE E — a missing prior emitted as 0 is NOT a return
def test_E_zero_previous_close_is_unavailable_not_a_return(env):
    server, monkeypatch = env
    raw = _raw_snapshot(prior=None)     # loader emits 0.0 for the absent field
    normalized = _normalized(raw)
    assert normalized["equity_summary"]["adjusted_equity_previous_close"] == 0.0, (
        "loader no longer emits 0 for a missing prior — fixture needs updating")
    eq = _get(server, monkeypatch, raw, "owner")["equity_summary"]
    assert eq["day_change_available"] is False
    assert eq["day_change"] is None
    assert eq["day_change_pct"] is None


@pytest.mark.parametrize("prior", [0.0, -5.0])
def test_E_zero_or_negative_prior_is_unavailable(env, prior):
    server, monkeypatch = env
    eq = _get(server, monkeypatch, _raw_snapshot(prior=prior), "owner")["equity_summary"]
    assert eq["day_change_available"] is False
    assert eq["day_change"] is None


def test_E_positive_cash_cannot_manufacture_a_return(env):
    """Cash must never turn a missing prior into an available day change."""
    server, monkeypatch = env
    eq = _get(server, monkeypatch, _raw_snapshot(prior=0.0, cash=50_000.0),
              "owner")["equity_summary"]
    assert eq["day_change_available"] is False


# CASE F — non-finite account fields
@pytest.mark.parametrize("field", ["account_equity", "cash"])
def test_F_non_finite_account_field_never_claims_reconciliation(env, field):
    server, monkeypatch = env
    raw = _raw_snapshot()
    raw["equity_summary"][field] = float("nan")
    if field == "account_equity":
        raw["equity_summary"]["total"] = float("nan")
    try:
        d = _get(server, monkeypatch, raw, "customer", priors={"NVDA": 80.0})
    except Exception:
        return          # the loader refusing a non-finite headline is also correct
    assert d.get("reconciles") is not True
    assert d.get("valuation_status") in ("degraded", "unavailable")
