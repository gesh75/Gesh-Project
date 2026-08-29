"""First execution tests for consolidation-zone money math.

The scanner pipeline (sector_filter → consolidation_zone → engines) sizes
entries from this module. Until now the only tests mocked `scan_consolidations`
or the download and asserted API wiring — a regression in entry/stop/target
or R/R filtering would still look green.

All offline: OHLCV frames are synthetic; `locked_yf_download` is stubbed.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import consolidation_zone as cz


def _ohlc(closes, highs=None, lows=None) -> pd.DataFrame:
    closes = np.asarray(closes, dtype=float)
    highs = np.asarray(highs if highs is not None else closes + 0.4, dtype=float)
    lows = np.asarray(lows if lows is not None else closes - 0.4, dtype=float)
    n = len(closes)
    return pd.DataFrame(
        {
            "Open": closes,
            "High": highs,
            "Low": lows,
            "Close": closes,
            "Volume": np.full(n, 1_000_000.0),
        },
        index=pd.date_range("2026-08-01", periods=n, freq="5min"),
    )


def _tight(n: int = 15, close: float = 100.0) -> pd.DataFrame:
    return _ohlc([close] * n, highs=[close + 0.4] * n, lows=[close - 0.4] * n)


# ── zone range / finder ──────────────────────────────────────────────────────


def test_zone_range_pct_zero_low_is_not_div_by_zero():
    """A $0 print must not raise — the finder treats it as a non-zone."""
    assert cz._zone_range_pct(np.array([0.0, 0.0, 1.0])) == 999.0


def test_zone_range_pct_flat_window_is_zero():
    assert cz._zone_range_pct(np.full(10, 100.0)) == 0.0


def test_zone_range_pct_just_inside_and_outside_threshold():
    inside = np.array([100.0] * 9 + [101.9])  # 1.9% < 2.0%
    outside = np.array([100.0] * 9 + [102.1])  # 2.1% > 2.0%
    assert cz._zone_range_pct(inside) <= cz.RANGE_PCT_THRESHOLD
    assert cz._zone_range_pct(outside) > cz.RANGE_PCT_THRESHOLD


def test_find_zones_detects_a_tight_stretch():
    frame = _tight(15)
    zones = cz._find_zones(
        frame["Close"].to_numpy(),
        frame["High"].to_numpy(),
        frame["Low"].to_numpy(),
    )
    assert len(zones) == 1
    z = zones[0]
    assert z["candles"] >= cz.MIN_CANDLES
    assert z["zone_low"] == pytest.approx(99.6)
    assert z["zone_high"] == pytest.approx(100.4)
    assert z["midpoint"] == pytest.approx(100.0)


def test_find_zones_rejects_a_trending_stretch():
    closes = np.array([100.0 * (1.05**i) for i in range(15)])
    zones = cz._find_zones(closes, closes + 0.4, closes - 0.4)
    assert zones == []


# ── detect_consolidation trade levels ────────────────────────────────────────


def test_detect_consolidation_2_to_1_fallback_levels(monkeypatch):
    """Single zone → entry is midpoint, stop is 0.5% below zone low, target is 2R."""
    monkeypatch.setattr(cz.price_source, "locked_yf_download", lambda *a, **k: _tight(15))
    r = cz.detect_consolidation("AAPL")
    assert r["status"] == "found"
    assert r["symbol"] == "AAPL"
    assert r["entry"] == pytest.approx(100.0)
    assert r["stop"] == pytest.approx(round(99.6 * (1 - cz.STOP_BUFFER_PCT / 100), 4))
    risk = r["entry"] - r["stop"]
    assert risk > 0
    assert r["target_source"] == "2:1_rr"
    assert r["target"] == pytest.approx(round(r["entry"] + cz.MIN_RR * risk, 4))
    assert r["rr_ratio"] == pytest.approx(2.0)
    assert "AAPL CONSOLIDATION ZONE" in r["prompt_context"]
    assert r["interval"] == "5m"


def test_detect_consolidation_uses_previous_zone_high_as_target(monkeypatch):
    """Black Bot rule: when an earlier zone sits above the current midpoint, use it."""
    closes = [100.0] * 12 + [110.0] * 3 + [90.0] * 12
    highs = [100.4] * 12 + [110.5] * 3 + [90.4] * 12
    lows = [99.6] * 12 + [109.5] * 3 + [89.6] * 12
    monkeypatch.setattr(
        cz.price_source, "locked_yf_download", lambda *a, **k: _ohlc(closes, highs, lows)
    )
    r = cz.detect_consolidation("NVDA")
    assert r["status"] == "found"
    assert r["target_source"] == "prev_zone"
    assert r["target"] == pytest.approx(100.4)
    assert r["entry"] == pytest.approx(90.0)
    assert r["target"] > r["entry"]
    assert r["rr_ratio"] > 2.0


def test_detect_consolidation_insufficient_bars_is_no_zone(monkeypatch):
    monkeypatch.setattr(cz.price_source, "locked_yf_download", lambda *a, **k: _tight(4))
    r = cz.detect_consolidation("SPY")
    assert r["status"] == "no_zone"
    assert "Insufficient data" in r["reason"]
    assert "entry" not in r


def test_detect_consolidation_none_frame_is_no_zone(monkeypatch):
    monkeypatch.setattr(cz.price_source, "locked_yf_download", lambda *a, **k: None)
    r = cz.detect_consolidation("SPY")
    assert r["status"] == "no_zone"
    assert "0 candles" in r["reason"]


def test_detect_consolidation_download_error_is_typed(monkeypatch):
    def _boom(*_a, **_k):
        raise RuntimeError("yahoo 401 crumb")

    monkeypatch.setattr(cz.price_source, "locked_yf_download", _boom)
    r = cz.detect_consolidation("HOOD")
    assert r == {"status": "error", "symbol": "HOOD", "reason": "yahoo 401 crumb"}


def test_detect_consolidation_nan_bars_are_dropped(monkeypatch):
    frame = _tight(12)
    frame.loc[frame.index[0], "Close"] = np.nan
    monkeypatch.setattr(cz.price_source, "locked_yf_download", lambda *a, **k: frame)
    r = cz.detect_consolidation("CAT")
    # 11 clean bars still meet MIN_CANDLES after the NaN is stripped.
    assert r["status"] == "found"
    assert r["zone_candles"] >= cz.MIN_CANDLES


def test_detect_consolidation_degenerate_inverted_hl_is_no_zone(monkeypatch):
    """Highs below lows flip midpoint under the stop — refuse the setup."""
    n = 12
    monkeypatch.setattr(
        cz.price_source,
        "locked_yf_download",
        lambda *a, **k: _ohlc([100.0] * n, highs=[50.0] * n, lows=[100.0] * n),
    )
    r = cz.detect_consolidation("BAD")
    assert r["status"] == "no_zone"
    assert "Degenerate zone" in r["reason"]


# ── scan_consolidations filter / sort / errors ───────────────────────────────


def test_scan_consolidations_min_rr_boundary_and_errors(monkeypatch):
    def _dl(symbol, **_k):
        if symbol == "ERR":
            raise RuntimeError("feed down")
        if symbol == "WIDE":
            closes = [100.0 * (1.05**i) for i in range(15)]
            return _ohlc(closes)
        return _tight(15)

    monkeypatch.setattr(cz.price_source, "locked_yf_download", _dl)

    kept = cz.scan_consolidations(["AAPL", "WIDE", "ERR"], min_rr=2.0)
    assert [r["symbol"] for r in kept["found"]] == ["AAPL"]
    assert kept["no_zone"] == ["WIDE"]
    assert kept["errors"] == [("ERR", "feed down")]
    assert "1 setup(s) of 3 scanned" in kept["summary"]

    dropped = cz.scan_consolidations(["AAPL"], min_rr=2.01)
    assert dropped["found"] == []
    assert dropped["no_zone"] == ["AAPL"]
    assert dropped["errors"] == []


def test_scan_consolidations_sorts_by_rr_descending(monkeypatch):
    def _dl(symbol, **_k):
        if symbol == "LOW":
            return _tight(15, close=100.0)
        # Two-zone book: current mid 90, prev high 100.4 → R/R >> 2
        closes = [100.0] * 12 + [110.0] * 3 + [90.0] * 12
        highs = [100.4] * 12 + [110.5] * 3 + [90.4] * 12
        lows = [99.6] * 12 + [109.5] * 3 + [89.6] * 12
        return _ohlc(closes, highs, lows)

    monkeypatch.setattr(cz.price_source, "locked_yf_download", _dl)
    report = cz.scan_consolidations(["LOW", "HIGH"], min_rr=2.0)
    assert [r["symbol"] for r in report["found"]] == ["HIGH", "LOW"]
    assert report["found"][0]["rr_ratio"] > report["found"][1]["rr_ratio"]
