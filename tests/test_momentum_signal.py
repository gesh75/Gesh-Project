"""Momentum buy/sell signal — RSI + MACD + Volume combined.

`momentum_signal.compute` votes three momentum indicators into BUY/SELL/NEUTRAL.
Deterministic on synthetic OHLCV (no network); the endpoint is exercised with the
computation stubbed.
"""

from __future__ import annotations

import numpy as np
import pytest
from starlette.testclient import TestClient

import momentum_signal as ms


def _uptrend(n=120):
    # accelerating rise → MACD line stays above its signal (decisively bullish)
    close = 100 + (np.linspace(0, 1, n) ** 2) * 80 + np.random.default_rng(1).normal(0, 0.2, n)
    vol = np.full(n, 1_000_000.0)
    vol[-1] = 1_800_000.0                      # elevated volume on the latest bar
    return close, vol


def _downtrend(n=120):
    close = 180 - (np.linspace(0, 1, n) ** 2) * 80 + np.random.default_rng(2).normal(0, 0.2, n)
    vol = np.full(n, 1_000_000.0)
    vol[-1] = 1_800_000.0
    return close, vol


def test_strong_uptrend_is_buy():
    c, v = _uptrend()
    out = ms.compute("TEST", close=c, volume=v)
    assert out["available"] is True
    assert out["signal"] == "BUY"
    assert out["components"]["macd"] == 1
    assert out["rsi"] > 55
    assert "→ BUY" in out["rationale"]


def test_strong_downtrend_is_sell():
    c, v = _downtrend()
    out = ms.compute("TEST", close=c, volume=v)
    assert out["signal"] == "SELL"
    assert out["components"]["macd"] == -1
    assert out["rsi"] < 45


def test_flat_is_neutral():
    c = np.full(120, 100.0) + np.random.default_rng(3).normal(0, 0.1, 120)
    v = np.full(120, 1_000_000.0)
    out = ms.compute("TEST", close=c, volume=v)
    assert out["signal"] == "NEUTRAL"


def test_insufficient_data():
    out = ms.compute("TEST", close=np.array([1.0, 2, 3]), volume=np.array([1.0, 1, 1]))
    assert out["available"] is False


def test_shape_has_all_fields():
    c, v = _uptrend()
    out = ms.compute("nvda", close=c, volume=v)
    for k in ("signal", "score", "rsi", "macd", "macd_signal", "macd_hist",
              "volume_ratio", "components", "rationale"):
        assert k in out
    assert out["symbol"] == "NVDA"
    assert set(out["components"]) == {"rsi", "macd", "volume"}


def test_macd_helper_sign():
    # a clean uptrend → MACD line above its signal (positive histogram)
    line, sig, hist = ms._macd(np.linspace(100, 200, 80))
    assert line > sig and hist > 0


# ── endpoint ──────────────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch):
    import server
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: False)
    return server, TestClient(server.app, raise_server_exceptions=False)


def test_endpoint_returns_signal(client, monkeypatch):
    server, c = client
    monkeypatch.setattr("momentum_signal.compute",
                        lambda sym: {"available": True, "symbol": sym, "signal": "BUY",
                                     "score": 3, "rsi": 61.0, "macd": 1.2, "macd_signal": 0.8,
                                     "macd_hist": 0.4, "volume_ratio": 1.5,
                                     "components": {"rsi": 1, "macd": 1, "volume": 1},
                                     "rationale": "… → BUY"}, raising=False)
    r = c.get("/api/momentum/NVDA")
    assert r.status_code == 200
    assert r.json()["data"]["signal"] == "BUY"


def test_endpoint_bad_symbol_4xx(client):
    server, c = client
    assert c.get("/api/momentum/@@@").status_code in (400, 404)
