"""Live-quote binding must not treat bool/inf as a dollar entry.

``compute(current_price=True)`` used ``float(True)`` → $1.00 and rebound the
customer Trade Setup card around a fake penny print. This file stays off the
server/starlette stack so it can run with --noconftest.
"""

from __future__ import annotations

import numpy as np

import trade_levels as tl


def _ohlcv(n=80):
    rng = np.random.default_rng(7)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    high = close + np.abs(rng.normal(0, 0.8, n)) + 0.5
    low = close - np.abs(rng.normal(0, 0.8, n)) - 0.5
    return close, high, low


def test_bool_and_non_finite_current_price_fall_back_to_last_close():
    c, h, low = _ohlcv()
    good = tl.compute("TEST", close=c, high=h, low=low)["setup"]["entry"]
    for bad in (True, False, float("nan"), float("inf"), 0, -5, None, "x"):
        out = tl.compute("TEST", close=c, high=h, low=low, current_price=bad)
        assert out["setup"]["entry"] == good
        assert out["price"] == good


def test_real_current_price_still_binds_entry():
    c, h, low = _ohlcv()
    out = tl.compute("TEST", close=c, high=h, low=low, current_price=97.31)
    assert out["setup"]["entry"] == 97.31
    assert out["price"] == 97.31
