"""Cross-source price-sanity flag (catches whole-series feed errors like NFLX $73.83).

Pure helper is fully deterministic; the endpoint test mocks market_data + the close
cache so no network. Fail-open is the key contract — a missing/normal input never
flags, so this can never take a card down or hide a real quote.
"""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

import price_sanity as ps

# ── pure helper ───────────────────────────────────────────────────────────────

def test_nflx_style_below_low_is_suspect():
    suspect, reason = ps.is_price_suspect(73.83, low_52=500.0, high_52=1200.0)
    assert suspect is True and "below" in reason


def test_far_above_high_is_suspect():
    suspect, _ = ps.is_price_suspect(3000.0, low_52=500.0, high_52=1200.0)
    assert suspect is True


def test_normal_price_in_band_is_ok():
    # INTC ~$103 inside $75–$142 → not suspect (and a fresh 52-wk low is fine too)
    assert ps.is_price_suspect(103.12, 75.0, 142.35)[0] is False
    assert ps.is_price_suspect(74.0, 75.0, 142.35)[0] is False   # just under low = OK


@pytest.mark.parametrize("price,lo,hi", [
    (None, 100, 200), (0, 100, 200), (-5, 100, 200),
    (50, None, None), (50, 0, 0), (50, "x", "y"), (float("nan"), 100, 200),
])
def test_missing_or_bad_inputs_fail_open(price, lo, hi):
    assert ps.is_price_suspect(price, lo, hi)[0] is False


# ── endpoint wiring ───────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch):
    import server
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: False)
    return server, TestClient(server.app, raise_server_exceptions=False)


def test_fundamentals_endpoint_sets_suspect_flag(client, monkeypatch):
    server, c = client
    import market_data
    monkeypatch.setattr(market_data, "rh_fundamentals",
                        lambda s: {"pe_ratio": 40, "low_52": 500.0, "high_52": 1200.0})
    monkeypatch.setattr(server, "_cached_close", lambda s: 73.83)     # broken feed price
    r = c.get("/api/fundamentals/NFLX")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data.get("price_suspect") is True and data.get("price_suspect_reason")


def test_fundamentals_endpoint_no_flag_when_price_normal(client, monkeypatch):
    server, c = client
    import market_data
    monkeypatch.setattr(market_data, "rh_fundamentals",
                        lambda s: {"pe_ratio": 20, "low_52": 90.0, "high_52": 142.0})
    monkeypatch.setattr(server, "_cached_close", lambda s: 103.12)
    r = c.get("/api/fundamentals/INTC")
    assert r.json()["data"].get("price_suspect") is None
