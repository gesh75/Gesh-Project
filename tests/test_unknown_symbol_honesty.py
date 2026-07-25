"""Unknown data must stay unknown.

Audit BUG-05 / finding J (2026-07-22): analyzing the nonexistent ticker ZZZQQ
produced a page of confident-looking values —

    Analyst    Consensus: Hold          (yfinance defaults `recommendation`)
    Cycle      Direction: Falling
    Fundamentals  "yfinance · just now"  (company_name echoed back, all nulls)
    Earnings   "no earnings source (set FINNHUB_API_KEY for VPN-safe data)"

Every one of those endpoints returned HTTP 200. Two defects: providers that
answer with a SHAPED-BUT-EMPTY row were treated as coverage, and an internal
environment-variable name was rendered as customer copy.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

import server


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: False)
    server._ticker_get_cache.clear()
    return TestClient(server.app, raise_server_exceptions=False)


# ─── the shaped-but-empty predicates ─────────────────────────────────────────

def test_analyst_row_with_no_coverage_is_empty():
    """The exact yfinance shape for an unknown ticker."""
    assert server._analyst_row_is_empty({
        "current": None, "target_mean": None, "target_high": None,
        "target_low": None, "target_median": None, "num_analysts": 0,
        "recommendation": "Hold", "reco_cls": "reco-hold", "upside_pct": None,
    }) is True


def test_analyst_row_with_real_coverage_is_kept():
    assert server._analyst_row_is_empty(
        {"num_analysts": 34, "recommendation": "Buy", "target_mean": 210.0}) is False
    # a price target alone is coverage, even with no analyst count
    assert server._analyst_row_is_empty(
        {"num_analysts": 0, "recommendation": "Hold", "target_mean": 210.0}) is False


def test_fundamentals_row_that_only_echoes_the_ticker_is_empty():
    assert server._fundamentals_row_is_empty({
        "symbol": "ZZZQQ", "company_name": "ZZZQQ", "sector": "", "price": None,
        "ret_1m": None, "revenue_growth": None, "trailing_pe": None,
    }) is True


def test_fundamentals_row_with_any_real_metric_is_kept():
    assert server._fundamentals_row_is_empty(
        {"symbol": "AAPL", "company_name": "Apple Inc.", "price": 213.5}) is False
    assert server._fundamentals_row_is_empty(
        {"symbol": "X", "company_name": "X", "sector": "Technology"}) is False


# ─── the REAL adapter shape (staging 2026-07-25) ─────────────────────────────
#
# The simplified row above is not what the adapter actually emits. For a symbol
# it has never heard of, fundamental_agent._fetch_fundamentals_batch still fills
# in categorical placeholders and zero counters, and those non-null values made
# the structural emptiness check answer "this vendor knows something" — so the
# 404 guard never fired and a nonexistent ticker rendered as a successful card
# ("status": "ok", source "yfinance", a just-now as_of, Consensus: Hold).

def _default_only_row(sym: str = "ZZZQQ") -> dict:
    """Verbatim shape returned by the yfinance fallback for an unknown ticker."""
    return {
        "symbol": sym,
        "company_name": sym,
        "sector": "",
        "price": None,
        "ret_1m": None,
        "ret_3m": None,
        "revenue_growth": None,
        "earnings_growth": None,
        "gross_margin": None,
        "op_margin": None,
        "forward_pe": None,
        "trailing_pe": None,
        "pb_ratio": None,
        "debt_equity": None,
        "current_ratio": None,
        "free_cashflow_b": None,
        "inst_ownership": None,
        "insider_pct": None,
        "insider_signal": "NEUTRAL",
        "insider_buys": 0,
        "insider_sells": 0,
        "analyst_target": None,
        "analyst_upside": None,
        "num_analysts": 0,
        "recommendation": "hold",
        "short_float_pct": None,
        "beta": None,
        "market_cap_b": None,
    }


def test_fundamentals_default_only_row_is_empty():
    """Adapter placeholders are not vendor knowledge."""
    assert server._fundamentals_row_is_empty(_default_only_row()) is True


@pytest.mark.parametrize("field,value", [
    ("recommendation", None),
    ("recommendation", ""),
    ("recommendation", "hold"),
    ("recommendation", "HOLD"),
    ("recommendation", " Hold "),
    ("insider_signal", None),
    ("insider_signal", ""),
    ("insider_signal", "NEUTRAL"),
    ("insider_signal", "neutral"),
    ("insider_buys", 0),
    ("insider_sells", 0),
    ("num_analysts", 0),
])
def test_single_synthetic_default_is_not_evidence(field, value):
    """Each placeholder alone must not rescue an otherwise-empty row."""
    assert server._fundamentals_row_is_empty(
        {"symbol": "ZZZQQ", "company_name": "ZZZQQ", field: value}) is True


@pytest.mark.parametrize("field,value", [
    ("price", 213.5),
    ("sector", "Technology"),
    ("trailing_pe", 28.4),
    ("market_cap_b", 3100.0),
    # Zero is a REAL answer for these — the row must survive.
    ("debt_equity", 0),
    ("short_float_pct", 0.0),
    ("insider_pct", 0),
    ("revenue_growth", 0.0),
    # A non-default categorical is genuine coverage.
    ("recommendation", "buy"),
    ("insider_signal", "BULLISH"),
    ("num_analysts", 34),
    ("insider_buys", 3),
])
def test_substantive_metric_survives_even_beside_defaults(field, value):
    """A legitimate zero (or real categorical) is knowledge, not a placeholder."""
    row = _default_only_row()
    row[field] = value
    assert server._fundamentals_row_is_empty(row) is False, f"{field}={value!r} dropped"


def test_unknown_future_vendor_field_still_counts_as_knowledge():
    """The check stays structural: a field the allow-list never heard of is evidence."""
    row = _default_only_row()
    row["some_new_vendor_metric_2027"] = 42.0
    assert server._fundamentals_row_is_empty(row) is False


# ─── end to end ──────────────────────────────────────────────────────────────

def test_unknown_ticker_analyst_is_404_not_a_fabricated_hold(client, monkeypatch):
    import market_data
    monkeypatch.setattr(market_data, "rh_analyst", lambda s: None, raising=False)
    monkeypatch.setattr(market_data, "get_analyst_targets", lambda syms: {
        "ZZZQQ": {"current": None, "target_mean": None, "num_analysts": 0,
                  "recommendation": "Hold", "upside_pct": None}}, raising=False)
    r = client.get("/api/analyst/ZZZQQ")
    assert r.status_code == 404, f"fabricated coverage served: {r.text[:200]}"
    assert "Hold" not in r.text


def test_unknown_ticker_fundamentals_is_404_not_an_empty_card(client, monkeypatch):
    import fundamental_agent
    import market_data
    monkeypatch.setattr(market_data, "rh_fundamentals", lambda s: None, raising=False)
    monkeypatch.setattr(fundamental_agent, "_fetch_fundamentals_batch",
                        lambda syms: [{"symbol": "ZZZQQ", "company_name": "ZZZQQ",
                                       "sector": "", "price": None,
                                       "revenue_growth": None}], raising=False)
    r = client.get("/api/fundamentals/ZZZQQ")
    assert r.status_code == 404
    assert "just now" not in r.text


def test_unknown_ticker_fundamentals_default_only_row_is_404(client, monkeypatch):
    """The staging reproduction: RH has nothing, yfinance returns placeholders."""
    import fundamental_agent
    import market_data
    monkeypatch.setattr(market_data, "rh_fundamentals", lambda s: None, raising=False)
    monkeypatch.setattr(fundamental_agent, "_fetch_fundamentals_batch",
                        lambda syms: [_default_only_row()], raising=False)

    r = client.get("/api/fundamentals/ZZZQQ")

    assert r.status_code == 404, f"fabricated card served: {r.text[:300]}"
    body = r.text
    # No synthetic success, no fabricated provenance.
    assert '"status": "ok"' not in body and '"status":"ok"' not in body
    assert "hold" not in body.lower()
    assert "NEUTRAL" not in body
    assert "yfinance" not in body
    assert "as_of" not in body
    assert "just now" not in body


def test_rejected_fundamentals_row_is_not_cached(client, monkeypatch):
    """A refusal must not be stored, or the next caller is served the fabrication."""
    import fundamental_agent
    import market_data
    monkeypatch.setattr(market_data, "rh_fundamentals", lambda s: None, raising=False)
    monkeypatch.setattr(fundamental_agent, "_fetch_fundamentals_batch",
                        lambda syms: [_default_only_row()], raising=False)

    assert client.get("/api/fundamentals/ZZZQQ").status_code == 404
    assert ("fundamentals", "ZZZQQ") not in server._ticker_get_cache

    # And a repeat call still refuses rather than replaying a cached 200.
    assert client.get("/api/fundamentals/ZZZQQ").status_code == 404


def test_known_ticker_fundamentals_still_served_beside_defaults(client, monkeypatch):
    """A real row that also carries the placeholders must still be a 200."""
    import fundamental_agent
    import market_data
    row = _default_only_row("AAPL")
    row["company_name"] = "Apple Inc."
    row["price"] = 213.5
    row["sector"] = "Technology"
    monkeypatch.setattr(market_data, "rh_fundamentals", lambda s: None, raising=False)
    monkeypatch.setattr(fundamental_agent, "_fetch_fundamentals_batch",
                        lambda syms: [row], raising=False)

    r = client.get("/api/fundamentals/AAPL")

    assert r.status_code == 200
    data = r.json()["data"]
    assert data["price"] == 213.5
    assert data["sector"] == "Technology"
    # Response shape for a valid symbol is unchanged.
    assert r.json()["source"] == "yfinance"


def test_known_ticker_still_served(client, monkeypatch):
    """The guard must not start 404-ing real symbols."""
    import market_data
    monkeypatch.setattr(market_data, "rh_analyst",
                        lambda s: {"num_analysts": 41, "recommendation": "Buy",
                                   "target_mean": 220.0}, raising=False)
    r = client.get("/api/analyst/AAPL")
    assert r.status_code == 200
    assert r.json()["data"]["recommendation"] == "Buy"


def test_earnings_unavailable_copy_names_no_internal_config(client, monkeypatch):
    import earnings_viz
    monkeypatch.setattr(earnings_viz, "_from_finnhub", lambda s: None, raising=False)
    monkeypatch.setattr(earnings_viz, "_from_yfinance", lambda s: None, raising=False)
    r = client.get("/api/earnings/ZZZQQ")
    assert r.status_code == 200                    # a shaped "unavailable" is fine here
    body = r.json()["data"]
    assert body["available"] is False
    assert "API_KEY" not in r.text and "FINNHUB" not in r.text
    assert "diagnostic" not in body, "operator diagnostics must not ship to the client"
