"""Momentum buy/sell signal — RSI + MACD + Volume combined.

`momentum_signal.compute` votes three momentum indicators into BUY/SELL/NEUTRAL.
Deterministic on synthetic OHLCV (no network); the endpoint is exercised with the
computation stubbed.
"""

from __future__ import annotations

import sys

import numpy as np
import pytest
from starlette.testclient import TestClient

import momentum_signal as ms


def _uptrend(n=120):
    # accelerating rise → MACD line stays above its signal (decisively bullish)
    close = 100 + (np.linspace(0, 1, n) ** 2) * 80 + np.random.default_rng(1).normal(0, 0.2, n)
    vol = np.full(n, 1_000_000.0)
    vol[-1] = 1_800_000.0  # elevated volume on the latest bar
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
    for k in (
        "signal",
        "score",
        "rsi",
        "macd",
        "macd_signal",
        "macd_hist",
        "volume_ratio",
        "components",
        "rationale",
    ):
        assert k in out
    assert out["symbol"] == "NVDA"
    assert set(out["components"]) == {"rsi", "macd", "volume"}


def test_macd_helper_sign():
    # a clean uptrend → MACD line above its signal (positive histogram)
    line, sig, hist = ms._macd(np.linspace(100, 200, 80))
    assert line > sig and hist > 0


def test_rsi_short_series_is_neutral_50():
    assert ms._rsi(np.array([100.0, 101.0, 102.0])) == 50.0


def test_rsi_all_gains_is_100_and_all_losses_is_0():
    gains = np.linspace(100.0, 130.0, 20)
    losses = np.linspace(130.0, 100.0, 20)
    assert ms._rsi(gains) == 100.0
    assert ms._rsi(losses) == 0.0


def test_flat_book_casts_no_rsi_or_volume_vote():
    """RSI 55/45 and volume 1.1× are exclusive/inclusive cutovers; a flat
    book sits at RSI ~50 and 1.0× volume, so both votes must stay 0."""
    n = 80
    close = np.full(n, 100.0)
    vol = np.full(n, 1_000_000.0)
    out = ms.compute("TEST", close=close, volume=vol)
    assert out["components"]["rsi"] == 0
    assert out["components"]["volume"] == 0


def _last_bar_for_volume_ratio(base: np.ndarray, ratio: float) -> float:
    """Solve last so last / mean(last 20 including last) equals `ratio`.

    The live helper averages the final 20 prints, so bumping only `vol[-1]`
    to 1.1× the prior bar is *below* the 1.1 confirmation cutover.
    """
    prior = float(np.sum(base[-20:-1]))
    # last / ((prior + last) / 20) = ratio  →  last = ratio * prior / (20 - ratio)
    return ratio * prior / (20.0 - ratio)


def test_volume_vote_requires_1_1x_inclusive():
    n = 80
    close = np.linspace(100.0, 120.0, n)  # recent_dir = +1
    vol = np.full(n, 1_000_000.0)
    vol[-1] = _last_bar_for_volume_ratio(vol, 1.094)
    under = ms.compute("TEST", close=close, volume=vol)
    assert under["volume_ratio"] < 1.1
    assert under["components"]["volume"] == 0

    vol[-1] = _last_bar_for_volume_ratio(vol, 1.1)
    at = ms.compute("TEST", close=close, volume=vol)
    assert at["volume_ratio"] == pytest.approx(1.1, abs=0.01)
    assert at["components"]["volume"] == 1


def test_zero_average_volume_does_not_divide_and_does_not_vote():
    n = 80
    close = np.linspace(100.0, 80.0, n)
    vol = np.zeros(n)
    out = ms.compute("TEST", close=close, volume=vol)
    assert out["available"] is True
    assert out["volume_ratio"] == 1.0
    assert out["components"]["volume"] == 0


def test_compute_exception_is_unavailable_not_neutral(monkeypatch):
    def _boom(*_a, **_k):
        raise ValueError("indicator failed")

    monkeypatch.setattr(ms, "_rsi", _boom)
    out = ms.compute("TEST", close=np.linspace(1.0, 10.0, 40), volume=np.ones(40))
    assert out == {"available": False, "reason": "ValueError"}
    assert "signal" not in out


def test_missing_price_feed_is_unavailable(monkeypatch):
    class _PS:
        @staticmethod
        def yf_download(*_a, **_k):
            return None

    monkeypatch.setitem(sys.modules, "price_source", _PS())
    out = ms.compute("NVDA")
    assert out["available"] is False
    assert out["reason"] == "insufficient price data"


# ── endpoint ──────────────────────────────────────────────────────────────────


@pytest.fixture
def client(monkeypatch):
    import server

    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: False)
    return server, TestClient(server.app, raise_server_exceptions=False)


def test_endpoint_returns_signal(client, monkeypatch):
    server, c = client
    monkeypatch.setattr(
        "momentum_signal.compute",
        lambda sym: {
            "available": True,
            "symbol": sym,
            "signal": "BUY",
            "score": 3,
            "rsi": 61.0,
            "macd": 1.2,
            "macd_signal": 0.8,
            "macd_hist": 0.4,
            "volume_ratio": 1.5,
            "components": {"rsi": 1, "macd": 1, "volume": 1},
            "rationale": "… → BUY",
        },
        raising=False,
    )
    r = c.get("/api/momentum/NVDA")
    assert r.status_code == 200
    assert r.json()["data"]["signal"] == "BUY"


def test_endpoint_bad_symbol_4xx(client):
    server, c = client
    assert c.get("/api/momentum/@@@").status_code in (400, 404)
