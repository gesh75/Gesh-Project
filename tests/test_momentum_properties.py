"""Property harness for the momentum math (coverage theme, slice 2).

RSI / EMA / MACD and the combined signal are pure array math (momentum_signal.py is
NOT a gesh_core shim), so these invariants are fully verifiable offline. Seeded-random
loops assert bounds, identities, and directional monotonicity that example tests miss.
"""
from __future__ import annotations

import numpy as np
import pytest

import momentum_signal as ms

_RNG = np.random.default_rng(20260714)


# ── _rsi ────────────────────────────────────────────────────────────────────

def test_rsi_is_bounded_0_100():
    for _ in range(400):
        n = int(_RNG.integers(16, 200))
        close = np.cumsum(_RNG.normal(0, 1, size=n)) + 100
        r = ms._rsi(close)
        assert 0.0 <= r <= 100.0


def test_rsi_extremes_on_monotonic_series():
    up = np.arange(1, 60, dtype=float)              # strictly rising → no losses
    down = np.arange(60, 1, -1, dtype=float)        # strictly falling → no gains
    assert ms._rsi(up) == 100.0
    assert ms._rsi(down) == 0.0


def test_rsi_short_series_is_neutral():
    assert ms._rsi(np.array([1.0, 2.0, 3.0])) == 50.0   # len < period+1


# ── _ema ────────────────────────────────────────────────────────────────────

def test_ema_is_bounded_by_input_range():
    for _ in range(300):
        n = int(_RNG.integers(5, 150))
        x = _RNG.normal(50, 10, size=n)
        e = ms._ema(x, span=int(_RNG.integers(2, 40)))
        assert e[0] == x[0]                          # seed is the first sample
        assert x.min() - 1e-9 <= e.min() and e.max() <= x.max() + 1e-9


def test_ema_constant_series_is_constant():
    x = np.full(50, 7.0)
    assert np.allclose(ms._ema(x, span=12), 7.0)


# ── _macd ───────────────────────────────────────────────────────────────────

def test_macd_hist_is_line_minus_signal():
    for _ in range(200):
        close = np.cumsum(_RNG.normal(0, 1, size=80)) + 100
        line, sig, hist = ms._macd(close)
        assert hist == pytest.approx(line - sig, abs=1e-9)


def test_macd_positive_on_rising_series():
    close = np.linspace(100, 200, 120)              # steady uptrend
    line, _sig, _hist = ms._macd(close)
    assert line > 0                                 # fast EMA leads slow EMA up


# ── compute (combined signal) ─────────────────────────────────────────────────

def test_compute_shape_and_score_identity():
    for _ in range(150):
        n = int(_RNG.integers(35, 200))
        close = np.cumsum(_RNG.normal(0, 1, size=n)) + 200
        volume = _RNG.uniform(1e6, 5e6, size=n)
        out = ms.compute("x", close=close, volume=volume)
        assert out["available"] is True
        assert out["signal"] in ("BUY", "SELL", "NEUTRAL")
        assert 0.0 <= out["rsi"] <= 100.0
        c = out["components"]
        assert all(c[k] in (-1, 0, 1) for k in ("rsi", "macd", "volume"))
        assert out["score"] == c["rsi"] + c["macd"] + c["volume"]   # score == vote sum
        assert -3 <= out["score"] <= 3


def test_compute_insufficient_data_is_unavailable():
    out = ms.compute("x", close=np.arange(10, dtype=float), volume=np.ones(10))
    assert out["available"] is False


def test_compute_strong_uptrend_with_volume_is_buy():
    # accelerating uptrend (decisive MACD) + a confirming volume spike on the last bar
    close = 100 + np.linspace(0, 1, 80) ** 2 * 120
    volume = np.concatenate([np.full(79, 1e6), [3e6]])
    out = ms.compute("x", close=close, volume=volume)
    assert out["signal"] == "BUY" and out["score"] >= 2
