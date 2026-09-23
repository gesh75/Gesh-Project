"""market_data typed blanks — NaN/bool/zero-close never invent a return.

`_safe` feeds analyst targets, PE, upside, and ownership. A Yahoo bool or
NaN that became 1.0 / 0.0% would paint a name as priced when the feed
failed. `_ret_from_hist` / `_ytd_from_hist` are the D-06 path: a $0 or
all-NaN Close must stay None, not a fabricated 0.0% match vs SPY.

yfinance is stubbed before import so this file is pandas + pytest only.
"""

from __future__ import annotations

import sys
import types

import numpy as np
import pandas as pd
import pytest

sys.modules.setdefault("yfinance", types.SimpleNamespace(Ticker=lambda *a, **k: None))

import market_data as md  # noqa: E402, I001


# ─── _safe ───────────────────────────────────────────────────────────────────


def test_safe_none_is_default():
    assert md._safe(None) is None
    assert md._safe(None, 0) == 0


def test_safe_nan_and_inf_are_default_not_zero():
    assert md._safe(float("nan")) is None
    assert md._safe(float("inf"), 0) == 0
    assert md._safe(float("-inf")) is None
    assert md._safe("nan") is None
    assert md._safe("NaN", default=None) is None


def test_safe_bool_is_default_not_one():
    """True must not become a $1 last price or a 1-analyst consensus."""
    assert md._safe(True) is None
    assert md._safe(False, 0) == 0


def test_safe_finite_number_and_numeric_string():
    assert md._safe(12.5) == 12.5
    assert md._safe(0) == 0.0
    assert md._safe("12.5") == 12.5
    assert md._safe("-3") == -3.0


def test_safe_unparseable_returns_original_not_zero():
    assert md._safe("N/A") == "N/A"
    assert md._safe("hold") == "hold"


# ─── return slicing (D-06) ───────────────────────────────────────────────────


def test_ret_from_hist_zero_old_close_is_none():
    idx = pd.date_range("2026-01-01", periods=5, freq="D")
    hist = pd.DataFrame({"Close": [0.0, 0.0, 0.0, 0.0, 10.0]}, index=idx)
    assert md._ret_from_hist(hist, 7) is None


def test_ret_from_hist_all_nan_close_is_none():
    idx = pd.date_range("2026-01-01", periods=5, freq="D")
    hist = pd.DataFrame({"Close": [np.nan] * 5}, index=idx)
    assert md._ret_from_hist(hist, 7) is None


def test_ytd_from_hist_empty_and_zero_old_are_none():
    assert md._ytd_from_hist(None) is None
    assert md._ytd_from_hist(pd.DataFrame({"Close": []})) is None
    idx = pd.date_range("2026-01-01", periods=4, freq="D")
    zeros = pd.DataFrame({"Close": [0.0, 0.0, 0.0, 5.0]}, index=idx)
    assert md._ytd_from_hist(zeros) is None


# ─── analyst targets fail-soft ───────────────────────────────────────────────


def test_analyst_targets_ticker_exception_is_error_not_invented(monkeypatch):
    class _Boom:
        def __init__(self, _sym):
            raise RuntimeError("yahoo 401")

    monkeypatch.setattr(md.yf, "Ticker", _Boom)
    out = md.get_analyst_targets(["NVDA", "AAPL"])
    assert set(out) == {"NVDA", "AAPL"}
    assert "error" in out["NVDA"] and "target_mean" not in out["NVDA"]
    assert "error" in out["AAPL"]


def test_analyst_targets_nan_price_has_no_upside(monkeypatch):
    class _Ticker:
        info = {
            "currentPrice": float("nan"),
            "targetMeanPrice": 200.0,
            "numberOfAnalystOpinions": 8,
            "recommendationKey": "buy",
        }

        def __init__(self, _sym):
            pass

    monkeypatch.setattr(md.yf, "Ticker", _Ticker)
    row = md.get_analyst_targets(["NVDA"])["NVDA"]
    assert row["current"] is None
    assert row["target_mean"] == 200.0
    assert row["upside_pct"] is None


def test_analyst_targets_bool_price_has_no_upside(monkeypatch):
    class _Ticker:
        info = {
            "currentPrice": True,
            "targetMeanPrice": True,
            "numberOfAnalystOpinions": False,
            "recommendationKey": "hold",
        }

        def __init__(self, _sym):
            pass

    monkeypatch.setattr(md.yf, "Ticker", _Ticker)
    row = md.get_analyst_targets(["ZZZZ"])["ZZZZ"]
    assert row["current"] is None
    assert row["target_mean"] is None
    assert row["upside_pct"] is None
    assert row["num_analysts"] == 0
    assert row["recommendation"] == "Hold"


def test_analyst_targets_zero_last_falls_through_then_stays_unpriced(monkeypatch):
    """`currentPrice or regularMarketPrice` treats $0 as missing (F-05 class).
    A $0 print must not become the mark used for upside."""

    class _ZeroOnly:
        info = {
            "currentPrice": 0.0,
            "targetMeanPrice": 50.0,
            "recommendationKey": "buy",
        }

        def __init__(self, _sym):
            pass

    monkeypatch.setattr(md.yf, "Ticker", _ZeroOnly)
    row = md.get_analyst_targets(["PENNY"])["PENNY"]
    assert row["current"] is None
    assert row["upside_pct"] is None

    class _ZeroThenRegular:
        info = {
            "currentPrice": 0.0,
            "regularMarketPrice": 12.5,
            "targetMeanPrice": 15.0,
            "recommendationKey": "buy",
        }

        def __init__(self, _sym):
            pass

    monkeypatch.setattr(md.yf, "Ticker", _ZeroThenRegular)
    row = md.get_analyst_targets(["PENNY"])["PENNY"]
    assert row["current"] == 12.5
    assert row["upside_pct"] == pytest.approx(20.0)


def test_performance_returns_exception_is_per_symbol(monkeypatch):
    class _Ticker:
        def __init__(self, sym):
            self.sym = sym

        def history(self, **_kw):
            if self.sym == "BAD":
                raise RuntimeError("crumb")
            idx = pd.date_range("2026-01-01", periods=20, freq="D")
            return pd.DataFrame({"Close": list(range(100, 120))}, index=idx)

    monkeypatch.setattr(md.yf, "Ticker", _Ticker)
    out = md.get_performance_returns(["OK", "BAD"])
    assert "error" in out["BAD"]
    assert out["OK"]["7d"] is not None
    assert "error" not in out["OK"]
