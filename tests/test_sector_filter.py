"""Sector pre-filter must tolerate yfinance MultiIndex Volume columns."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import sector_filter as sf


def test_get_volume_ratio_squeezes_multiindex_volume(monkeypatch):
    idx = pd.date_range("2026-06-01", periods=25, freq="B")
    vol = np.linspace(1_000_000, 2_000_000, 25)
    cols = pd.MultiIndex.from_tuples(
        [("Open", "XLK"), ("High", "XLK"), ("Low", "XLK"), ("Close", "XLK"), ("Volume", "XLK")],
        names=["Price", "Ticker"],
    )
    frame = pd.DataFrame(
        {
            ("Open", "XLK"): 100.0,
            ("High", "XLK"): 101.0,
            ("Low", "XLK"): 99.0,
            ("Close", "XLK"): 100.5,
            ("Volume", "XLK"): vol,
        },
        index=idx,
    )
    frame.columns = cols

    monkeypatch.setattr(sf.price_source, "locked_yf_download", lambda *a, **k: frame)
    today, avg, ratio = sf._get_volume_ratio("XLK")
    assert today == pytest.approx(float(vol[-1]))
    assert avg > 0
    assert ratio > 0


def test_batch_sector_check_splits_hot_and_cold(monkeypatch):
    def fake_ratio(etf: str):
        return (1_000_000.0, 800_000.0, 1.5 if etf == "XLK" else 0.9)

    monkeypatch.setattr(sf, "_get_volume_ratio", fake_ratio)
    report = sf.batch_sector_check(["NVDA", "AAPL", "JPM"])
    assert len(report["hot"]) == 2
    assert len(report["cold"]) == 1
    assert report["hot"][0]["volume_ratio"] >= sf.SPIKE_THRESHOLD