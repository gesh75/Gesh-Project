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


def test_unknown_ticker_falls_back_to_spy():
    assert sf.get_sector_etf("NVDA") == "XLK"
    assert sf.get_sector_etf("nvda") == "XLK"
    assert sf.get_sector_etf("ZZZZ") == "SPY"


def test_is_sector_hot_threshold_is_inclusive(monkeypatch):
    monkeypatch.setattr(sf, "_get_volume_ratio", lambda etf: (1_200_000.0, 1_000_000.0, 1.2))
    hot = sf.is_sector_hot("NVDA")
    assert hot["hot"] is True
    assert hot["symbol"] == "NVDA"
    assert hot["etf"] == "XLK"
    assert "HOT" in hot["prompt_context"]

    monkeypatch.setattr(sf, "_get_volume_ratio", lambda etf: (1_199_000.0, 1_000_000.0, 1.199))
    cold = sf.is_sector_hot("NVDA")
    assert cold["hot"] is False
    assert "COLD" in cold["prompt_context"]


def test_zero_or_missing_ratio_is_never_hot(monkeypatch):
    """ratio==0 is 'no data', not a 0× cold reading that could flip hot if threshold is 0."""
    monkeypatch.setattr(sf, "_get_volume_ratio", lambda etf: (0.0, 0.0, 0.0))
    r = sf.is_sector_hot("NVDA", threshold=0.0)
    assert r["hot"] is False
    assert r["volume_ratio"] == 0.0
    assert "unavailable" in r["prompt_context"]


def test_unknown_symbol_uses_spy_etf(monkeypatch):
    seen: list[str] = []

    def fake_ratio(etf: str):
        seen.append(etf)
        return (2_000_000.0, 1_000_000.0, 2.0)

    monkeypatch.setattr(sf, "_get_volume_ratio", fake_ratio)
    r = sf.is_sector_hot("not-a-ticker")
    assert seen == ["SPY"]
    assert r["symbol"] == "NOT-A-TICKER"
    assert r["etf"] == "SPY"
    assert r["hot"] is True


def test_get_volume_ratio_fetch_error_is_zero_not_invented(monkeypatch):
    def _boom(*_a, **_k):
        raise RuntimeError("yahoo 401")

    monkeypatch.setattr(sf.price_source, "locked_yf_download", _boom)
    assert sf._get_volume_ratio("XLK") == (0.0, 0.0, 0.0)


def test_get_volume_ratio_short_or_empty_frame_is_zero(monkeypatch):
    idx = pd.date_range("2026-06-01", periods=3, freq="B")
    short = pd.DataFrame({"Volume": [1.0, 2.0, 3.0]}, index=idx)
    monkeypatch.setattr(sf.price_source, "locked_yf_download", lambda *a, **k: short)
    assert sf._get_volume_ratio("XLK") == (0.0, 0.0, 0.0)

    monkeypatch.setattr(sf.price_source, "locked_yf_download", lambda *a, **k: None)
    assert sf._get_volume_ratio("XLK") == (0.0, 0.0, 0.0)


def test_get_volume_ratio_zero_average_is_ratio_zero(monkeypatch):
    """A zero 20d average must not divide; ratio stays 0 so the name cannot read HOT."""
    idx = pd.date_range("2026-06-01", periods=10, freq="B")
    vol = [0.0] * 9 + [1_500_000.0]
    frame = pd.DataFrame(
        {"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 1.0, "Volume": vol},
        index=idx,
    )
    monkeypatch.setattr(sf.price_source, "locked_yf_download", lambda *a, **k: frame)
    today, avg, ratio = sf._get_volume_ratio("XLK")
    assert today == pytest.approx(1_500_000.0)
    assert avg == 0.0
    assert ratio == 0.0


def test_batch_sector_check_dedups_etf_fetches(monkeypatch):
    calls: list[str] = []

    def fake_ratio(etf: str):
        calls.append(etf)
        return (1_000_000.0, 800_000.0, 1.5)

    monkeypatch.setattr(sf, "_get_volume_ratio", fake_ratio)
    # NVDA + AAPL share XLK; ZZZZ falls back to SPY
    report = sf.batch_sector_check(["NVDA", "AAPL", "zzzz"])
    assert sorted(calls) == ["SPY", "XLK"]
    assert {r["symbol"] for r in report["hot"]} == {"NVDA", "AAPL", "ZZZZ"}
    assert report["cold"] == []


def test_batch_unavailable_ratio_lands_in_cold(monkeypatch):
    monkeypatch.setattr(sf, "_get_volume_ratio", lambda etf: (0.0, 0.0, 0.0))
    report = sf.batch_sector_check(["NVDA", "JPM"])
    assert report["hot"] == []
    assert {r["symbol"] for r in report["cold"]} == {"NVDA", "JPM"}
    assert all(r["hot"] is False for r in report["cold"])
    assert "no data" in report["summary"]