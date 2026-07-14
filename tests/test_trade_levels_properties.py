"""Property harness for the trade-setup math (coverage theme, slice 3).

trade_levels.compute is pure price math (pivots + fractal swings → support/resistance →
a long setup). These invariants must hold for ANY OHLCV, so they run over hundreds of
seeded-random bars — the exact math a customer reads on the 🎯 Trade Setup card.
Prices kept in a normal range ($10–$500) so 2-dp rounding can't collapse a level.
"""
from __future__ import annotations

import numpy as np
import pytest

import trade_levels as tl

_RNG = np.random.default_rng(20260714)


def _ohlcv(n):
    close = np.cumsum(_RNG.normal(0, 1.2, size=n)) + float(_RNG.uniform(30, 400))
    close = np.clip(close, 10, 500)
    hi = close + np.abs(_RNG.normal(0, 0.9, size=n)) + 0.5
    lo = close - np.abs(_RNG.normal(0, 0.9, size=n)) - 0.5
    return close, hi, np.clip(lo, 1, None)


def test_support_below_price_resistance_above():
    for _ in range(300):
        c, h, lo = _ohlcv(int(_RNG.integers(40, 160)))
        out = tl.compute("x", close=c, high=h, low=lo)
        assert out["available"] is True
        px = out["price"]
        assert all(s < px for s in out["support"])
        assert all(r > px for r in out["resistance"])


def test_pivots_follow_classic_formula():
    for _ in range(200):
        c, h, lo = _ohlcv(int(_RNG.integers(40, 120)))
        out = tl.compute("x", close=c, high=h, low=lo)
        p = out["pivots"]["P"]
        assert p == pytest.approx((h[-1] + lo[-1] + c[-1]) / 3.0, abs=0.01)
        # R1 = 2P − L ≥ P ≥ 2P − H = S1
        assert out["pivots"]["R1"] >= p - 0.01 >= -1e9
        assert out["pivots"]["S1"] <= p + 0.01


def test_long_setup_is_coherent():
    for _ in range(300):
        c, h, lo = _ohlcv(int(_RNG.integers(40, 160)))
        s = tl.compute("x", close=c, high=h, low=lo)["setup"]
        assert s["stop"] < s["entry"] < s["target"]     # a valid long: stop below, target above
        assert s["risk_reward"] is not None and s["risk_reward"] > 0
        assert s["risk_pct"] > 0 and s["reward_pct"] > 0


def test_insufficient_data_is_unavailable():
    out = tl.compute("x", close=np.array([100.0, 101.0]),
                     high=np.array([101.0, 102.0]), low=np.array([99.0, 100.0]))
    assert out["available"] is False


def test_never_raises_on_garbage():
    # NaNs / empty / mismatched lengths must degrade to available:False, never throw.
    for bad in (np.array([]), np.array([np.nan] * 50), np.array([100.0])):
        out = tl.compute("x", close=bad, high=bad, low=bad)
        assert out["available"] is False
