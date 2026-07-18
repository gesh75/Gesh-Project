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


# ── Codex #3: the AI decision output (analyze-ticker card) is marked, not just display ──

def test_analyze_ticker_marks_suspect_price(monkeypatch):
    import server
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: True)
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: "cust@x.com")
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda p, c: "allow", raising=False)
    # broken NFLX-style feed. Patch _latest_close_meta: that is the seam the handler
    # calls, so stubbing the thin _latest_close wrapper left the endpoint grounding on
    # the LIVE price and this assertion riding on whether real NFLX happened to sit
    # outside the 52-wk band stubbed below.
    monkeypatch.setattr(server, "_latest_close_meta",
                        lambda sym: (73.83, {"source": "yahoo", "substituted": False}))
    monkeypatch.setattr(server, "deep_analyze_ticker",
                        lambda sym, current_price=None, regime_context=None: {
                            "symbol": sym, "verdict": "HOLD", "scores": {"valuation": 40}, "fair_value": {}})
    import market_data
    monkeypatch.setattr(market_data, "rh_fundamentals",
                        lambda s: {"low_52": 500.0, "high_52": 1200.0}, raising=False)
    c = TestClient(server.app, raise_server_exceptions=False)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    r = c.post("/api/analyze-ticker", json={"symbol": "NFLX"})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data.get("price_suspect") is True and data.get("price_suspect_reason")


# ── Codex #3 follow-up: opt-in STRICT mode blocks decisions on suspect quotes ────
# Default (env unset) stays warn-only — byte-identical to the behavior above.

def test_strict_reject_disabled_by_default(monkeypatch):
    monkeypatch.delenv("GESH_REJECT_SUSPECT_PRICES", raising=False)
    assert ps.strict_reject_enabled() is False


@pytest.mark.parametrize("val,expected", [
    ("1", True), ("true", True), ("TRUE", True), ("yes", True), ("on", True),
    ("0", False), ("false", False), ("", False), ("nope", False),
])
def test_strict_reject_env_parsing(monkeypatch, val, expected):
    monkeypatch.setenv("GESH_REJECT_SUSPECT_PRICES", val)
    assert ps.strict_reject_enabled() is expected


def _suspect_client(monkeypatch, spend_counter):
    import market_data
    import server
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: True)
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: "cust@x.com")
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda p, c: "allow", raising=False)
    monkeypatch.setattr(server, "_latest_close_meta",       # broken feed; the seam the handler calls
                        lambda sym: (73.83, {"source": "yahoo", "substituted": False}))
    monkeypatch.setattr(market_data, "rh_fundamentals",
                        lambda s: {"low_52": 500.0, "high_52": 1200.0}, raising=False)

    def _spend(sym, current_price=None, regime_context=None):
        spend_counter["n"] += 1
        return {"symbol": sym, "verdict": "HOLD", "scores": {"valuation": 40}, "fair_value": {}}
    monkeypatch.setattr(server, "deep_analyze_ticker", _spend)
    c = TestClient(server.app, raise_server_exceptions=False)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    return c


def test_strict_mode_blocks_ai_card_on_suspect_price_before_spend(monkeypatch):
    """GESH_REJECT_SUSPECT_PRICES=1 → 422 and NO Claude call (the check moves ahead
    of the spend, so a broken feed costs nothing and produces no decision)."""
    monkeypatch.setenv("GESH_REJECT_SUSPECT_PRICES", "1")
    spend = {"n": 0}
    c = _suspect_client(monkeypatch, spend)
    r = c.post("/api/analyze-ticker", json={"symbol": "NFLX"})
    assert r.status_code == 422
    assert "unreliable" in (r.json().get("detail") or "").lower()
    assert spend["n"] == 0                    # blocked BEFORE the LLM call


def test_default_mode_still_warns_and_spends(monkeypatch):
    monkeypatch.delenv("GESH_REJECT_SUSPECT_PRICES", raising=False)
    spend = {"n": 0}
    c = _suspect_client(monkeypatch, spend)
    r = c.post("/api/analyze-ticker", json={"symbol": "NFLX"})
    assert r.status_code == 200
    assert r.json()["data"]["price_suspect"] is True
    assert spend["n"] == 1                    # warn-only default is unchanged


# ── the trade setup must not look tradeable on a suspect quote either (GESH-9) ──

def _levels_client(monkeypatch):
    import market_data
    import server
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: False)
    monkeypatch.setattr(server, "_latest_close_meta",       # broken feed; the seam the handler calls
                        lambda sym: (73.83, {"source": "yahoo", "substituted": False}))
    monkeypatch.setattr(market_data, "rh_fundamentals",
                        lambda s: {"low_52": 500.0, "high_52": 1200.0}, raising=False)
    monkeypatch.setattr("trade_levels.compute",
                        lambda sym, current_price=None: {
                            "available": True, "symbol": sym, "price": current_price,
                            "support": [70.0], "resistance": [80.0], "pivots": {},
                            "swing_high_60d": 80.0, "swing_low_60d": 70.0,
                            "setup": {"entry": current_price, "stop": 69.0, "target": 80.0,
                                      "risk_reward": 1.3, "risk_pct": 5.0, "reward_pct": 8.0,
                                      "actionable": True, "warning": None},
                            "note": "x"}, raising=False)
    return TestClient(server.app, raise_server_exceptions=False)


def test_levels_setup_flagged_non_actionable_on_suspect_quote(monkeypatch):
    monkeypatch.delenv("GESH_REJECT_SUSPECT_PRICES", raising=False)
    c = _levels_client(monkeypatch)
    r = c.get("/api/levels/NFLX")
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["price_suspect"] is True
    assert d["setup"]["actionable"] is False        # was True from the engine
    assert "suspect" in d["setup"]["warning"].lower()


def test_levels_strict_mode_rejects_suspect_quote(monkeypatch):
    monkeypatch.setenv("GESH_REJECT_SUSPECT_PRICES", "1")
    c = _levels_client(monkeypatch)
    r = c.get("/api/levels/NFLX")
    assert r.status_code == 422
