"""Support/resistance + trade-setup levels (trade_levels.compute).

Deterministic on synthetic OHLCV — pivots, swing structure, and the long setup
(entry/stop/target/RR) are pure price math; no network.
"""

from __future__ import annotations

import numpy as np
import pytest
from starlette.testclient import TestClient

import trade_levels as tl


def _ohlcv(n=80):
    rng = np.random.default_rng(7)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    high = close + np.abs(rng.normal(0, 0.8, n)) + 0.5
    low = close - np.abs(rng.normal(0, 0.8, n)) - 0.5
    return close, high, low


def test_shape_and_setup_fields():
    c, h, low = _ohlcv()
    out = tl.compute("nvda", close=c, high=h, low=low)
    assert out["available"] is True and out["symbol"] == "NVDA"
    for k in ("support", "resistance", "pivots", "setup", "swing_high_60d", "swing_low_60d"):
        assert k in out
    for k in ("entry", "stop", "target", "risk_reward"):
        assert k in out["setup"]
    assert set(out["pivots"]) == {"P", "R1", "R2", "S1", "S2"}


def test_stop_below_entry_and_target_above():
    c, h, low = _ohlcv()
    s = tl.compute("TEST", close=c, high=h, low=low)["setup"]
    assert s["stop"] < s["entry"] < s["target"]      # a coherent long setup
    assert s["risk_reward"] is None or s["risk_reward"] > 0


def test_support_below_resistance_above_price():
    c, h, low = _ohlcv()
    out = tl.compute("TEST", close=c, high=h, low=low)
    price = out["price"]
    assert all(x < price for x in out["support"])
    assert all(x > price for x in out["resistance"])


def test_insufficient_data():
    assert tl.compute("TEST", close=np.array([1.0, 2]), high=np.array([2.0, 3]),
                      low=np.array([0.5, 1]))["available"] is False


def test_pivots_are_classic_formula():
    # single flat window → pivots derive from the last bar's H/L/C
    c = np.full(40, 100.0)
    h = np.full(40, 102.0)
    low = np.full(40, 98.0)
    out = tl.compute("TEST", close=c, high=h, low=low)
    p = (102 + 98 + 100) / 3
    assert out["pivots"]["P"] == round(p, 2)


# ── endpoint ──────────────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch):
    import server
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: False)
    return server, TestClient(server.app, raise_server_exceptions=False)


def test_endpoint(client, monkeypatch):
    server, c = client
    monkeypatch.setattr("trade_levels.compute",
                        lambda sym: {"available": True, "symbol": sym, "price": 100.0,
                                     "support": [95.0], "resistance": [110.0],
                                     "pivots": {"P": 100, "R1": 105, "R2": 110, "S1": 95, "S2": 90},
                                     "swing_high_60d": 112.0, "swing_low_60d": 88.0,
                                     "setup": {"entry": 100.0, "stop": 94.5, "target": 110.0,
                                               "risk_reward": 1.82, "risk_pct": 5.5, "reward_pct": 10.0},
                                     "note": "x"}, raising=False)
    r = c.get("/api/levels/NVDA")
    assert r.status_code == 200
    assert r.json()["data"]["setup"]["risk_reward"] == 1.82
