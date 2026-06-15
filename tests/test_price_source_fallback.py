"""Tests for price_source.py — the Yahoo→Robinhood OHLCV fallback adapter.

No network: yfinance is monkeypatched to simulate the 401/crumb storm, and the
Robinhood layer is stubbed at the `robin_stocks.robinhood` import boundary plus
a forced-ready session. Asserts the fallback returns a frame shaped exactly like
`yfinance.download()` so downstream callers (`raw["Close"].squeeze()`, multi-
symbol `frame["Close"]`) work unchanged.
"""
from __future__ import annotations

import sys
import types

import numpy as np
import pandas as pd
import pytest

import price_source


# ─── Fake robin_stocks.robinhood with deterministic historicals ───────────────

def _fake_rows(symbol: str, n: int = 60) -> list[dict]:
    base = {"NVDA": 120.0, "SPY": 540.0, "MU": 100.0}.get(symbol, 50.0)
    rows = []
    for i in range(n):
        px = base + i * 0.5
        rows.append({
            "begins_at": f"2026-0{1 + i % 3}-{1 + i % 27:02d}T00:00:00Z",
            "open_price": str(px),
            "high_price": str(px + 1),
            "low_price": str(px - 1),
            "close_price": str(px + 0.25),
            "volume": str(1_000_000 + i),
            "symbol": symbol,
        })
    return rows


@pytest.fixture
def fake_rh(monkeypatch):
    mod = types.ModuleType("robin_stocks.robinhood")
    mod.get_stock_historicals = lambda sym, interval="day", span="year", bounds="regular": _fake_rows(sym)
    pkg = types.ModuleType("robin_stocks")
    pkg.robinhood = mod
    monkeypatch.setitem(sys.modules, "robin_stocks", pkg)
    monkeypatch.setitem(sys.modules, "robin_stocks.robinhood", mod)
    # Force the session memo "ready" so we don't touch the real pickle.
    monkeypatch.setattr(price_source, "_RH_SESSION_READY", True)
    return mod


def _yahoo_dead(monkeypatch):
    """Make the inline `import yfinance` raise a 401 on download()."""
    fake_yf = types.ModuleType("yfinance")

    def _boom(*a, **k):
        raise RuntimeError("401 Client Error: Unauthorized — Invalid Crumb")

    fake_yf.download = _boom
    monkeypatch.setitem(sys.modules, "yfinance", fake_yf)


# ─── Period/interval translation ──────────────────────────────────────────────

@pytest.mark.parametrize("period,expected", [
    ("1d", "day"), ("5d", "week"), ("1mo", "month"), ("60d", "3month"),
    ("90d", "3month"), ("3mo", "3month"), ("1y", "year"), ("252d", "year"),
    ("2y", "5year"), ("max", "5year"),
])
def test_span_from_period(period, expected):
    assert price_source._span_from_period(period, None, None) == expected


def test_span_from_start_end():
    assert price_source._span_from_period(None, "2025-01-01", "2026-01-01") == "year"


def test_interval_map():
    assert price_source._interval_to_rh("1d") == "day"
    assert price_source._interval_to_rh("1h") == "hour"
    assert price_source._interval_to_rh("5m") == "5minute"
    assert price_source._interval_to_rh(None) == "day"


# ─── Single-symbol fallback shape (matches yf.download single) ─────────────────

def test_rh_fallback_single_symbol_shape(fake_rh):
    df = price_source.rh_fallback("NVDA", period="2y", interval="1d")
    assert df is not None
    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert isinstance(df.index, pd.DatetimeIndex)
    assert len(df) >= 5
    # The downstream idiom must work:
    closes = df["Close"].squeeze()
    assert closes.dtype == float


# ─── Multi-symbol fallback shape (MultiIndex like yf.download multi) ───────────

def test_rh_fallback_multi_symbol_shape(fake_rh):
    df = price_source.rh_fallback(["NVDA", "SPY"], period="1y")
    assert df is not None
    assert isinstance(df.columns, pd.MultiIndex)
    close = df["Close"]                       # the monte_carlo / vol_models idiom
    assert isinstance(close, pd.DataFrame)
    assert set(close.columns) == {"NVDA", "SPY"}
    assert (close["NVDA"].dropna() > 0).all()


# ─── No session → graceful None (sandbox behaviour preserved) ─────────────────

def test_rh_fallback_no_session_returns_none(monkeypatch):
    monkeypatch.setattr(price_source, "_RH_SESSION_READY", False)
    assert price_source.rh_fallback("NVDA", period="1y") is None


# ─── yf_download: Yahoo dies → RH serves the frame ────────────────────────────

def test_yf_download_falls_back_on_yahoo_401(monkeypatch, fake_rh):
    _yahoo_dead(monkeypatch)
    df = price_source.yf_download("NVDA", period="1y", interval="1d")
    assert df is not None
    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert len(df) >= 5


# ─── yf_download: Yahoo healthy → its frame is returned untouched ──────────────

def test_yf_download_prefers_yahoo_when_healthy(monkeypatch, fake_rh):
    good = pd.DataFrame(
        {"Open": np.arange(10.0), "High": np.arange(10.0), "Low": np.arange(10.0),
         "Close": np.arange(10.0), "Volume": np.arange(10.0)},
        index=pd.date_range("2026-01-01", periods=10),
    )
    fake_yf = types.ModuleType("yfinance")
    fake_yf.download = lambda *a, **k: good
    monkeypatch.setitem(sys.modules, "yfinance", fake_yf)

    called = {"rh": False}
    orig = price_source.rh_fallback
    monkeypatch.setattr(price_source, "rh_fallback",
                        lambda *a, **k: called.__setitem__("rh", True) or orig(*a, **k))

    df = price_source.yf_download("NVDA", period="1y")
    pd.testing.assert_frame_equal(df, good)
    assert called["rh"] is False          # fallback never invoked on a healthy frame
