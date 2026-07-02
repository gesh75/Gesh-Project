"""price_source NaN-batch guard (audit PR3).

A Yahoo-429 storm returns a FULL-LENGTH all-NaN frame. Row-count-only emptiness
checks read that as success: the Robinhood fallback never fired AND the NaN
frame was cached for the TTL, poisoning consensus/Kelly/Monte-Carlo for the
whole run. The guard must count usable (non-NaN Close) rows instead.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import price_source


@pytest.fixture(autouse=True)
def _clear_yf_cache():
    price_source._YF_CACHE.clear()
    yield
    price_source._YF_CACHE.clear()


def _nan_frame(n: int = 40) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=n)
    return pd.DataFrame(
        {f: np.full(n, np.nan) for f in ("Open", "High", "Low", "Close", "Volume")},
        index=idx,
    )


def _good_frame(n: int = 40, base: float = 100.0) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=n)
    c = np.linspace(base, base * 1.1, n)
    return pd.DataFrame(
        {"Open": c, "High": c + 1, "Low": c - 1, "Close": c, "Volume": np.full(n, 1e6)},
        index=idx,
    )


def test_all_nan_batch_triggers_fallback_and_is_not_cached(monkeypatch):
    import yfinance

    monkeypatch.setattr(yfinance, "download", lambda *a, **k: _nan_frame())
    called: dict = {}

    def fake_rh(symbols, **kw):
        called["hit"] = True
        return None  # RH also unavailable → degrade, but must have been TRIED

    monkeypatch.setattr(price_source, "rh_fallback", fake_rh)

    price_source.yf_download("NVDA", period="40d")
    assert called.get("hit") is True, "all-NaN Yahoo frame must trigger the RH fallback"
    assert price_source._YF_CACHE == {}, "an all-NaN frame must never be cached"


def test_good_frame_is_served_and_cached(monkeypatch):
    import yfinance

    monkeypatch.setattr(yfinance, "download", lambda *a, **k: _good_frame())
    monkeypatch.setattr(
        price_source, "rh_fallback",
        lambda *a, **k: pytest.fail("fallback must not fire on a healthy frame"),
    )
    out = price_source.yf_download("NVDA", period="40d")
    assert out is not None and len(out) == 40
    assert len(price_source._YF_CACHE) == 1


def test_usable_rows_counts_values_not_rows():
    assert price_source._usable_rows(None) == 0
    assert price_source._usable_rows(_nan_frame()) == 0
    assert price_source._usable_rows(_good_frame()) == 40
    # Multi-symbol shape: Close is a DataFrame — a row counts if ANY ticker has data.
    idx = pd.date_range("2026-01-01", periods=10)
    cols = pd.MultiIndex.from_product([["Close"], ["AAA", "BBB"]], names=["Price", "Ticker"])
    half = pd.DataFrame(
        np.column_stack([np.linspace(1, 2, 10), np.full(10, np.nan)]),
        index=idx, columns=cols,
    )
    assert price_source._usable_rows(half) == 10
