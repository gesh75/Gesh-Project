"""market_data performance-return slicing (audit task: single-fetch returns).

get_performance_returns used to fire 5 separate history() HTTP calls per symbol
(7d/1m/3m/1y/ytd). The fix fetches 1y ONCE and slices — these tests pin the
slicing math offline on a synthetic frame.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import market_data


def _hist(n: int = 400, end: str = "2026-07-01") -> pd.DataFrame:
    idx = pd.date_range(end=end, periods=n, freq="D")
    closes = pd.Series(np.linspace(100.0, 200.0, n), index=idx)
    return pd.DataFrame({"Close": closes})


def test_ret_from_hist_slices_calendar_windows():
    hist = _hist()
    closes = hist["Close"]
    last = float(closes.iloc[-1])
    for days in (7, 30, 90, 365):
        got = market_data._ret_from_hist(hist, days)
        window = closes[closes.index >= closes.index[-1] - pd.Timedelta(days=days)]
        expected = round((last - float(window.iloc[0])) / float(window.iloc[0]) * 100, 2)
        assert got == pytest.approx(expected), f"{days}d window"


def test_ret_from_hist_handles_empty_and_short():
    assert market_data._ret_from_hist(None, 7) is None
    assert market_data._ret_from_hist(pd.DataFrame({"Close": []}), 7) is None
    one = pd.DataFrame({"Close": [100.0]}, index=pd.date_range("2026-01-01", periods=1))
    assert market_data._ret_from_hist(one, 7) is None


def test_ytd_from_hist():
    hist = _hist()
    closes = hist["Close"]
    jan1 = pd.Timestamp("2026-01-01", tz=closes.index.tz)
    window = closes[closes.index >= jan1]
    expected = round(
        (float(closes.iloc[-1]) - float(window.iloc[0])) / float(window.iloc[0]) * 100, 2
    )
    assert market_data._ytd_from_hist(hist) == pytest.approx(expected)


def test_get_performance_returns_fetches_history_once_per_symbol(monkeypatch):
    calls: list = []

    class _FakeTicker:
        def __init__(self, sym):
            self.sym = sym

        def history(self, **kw):
            calls.append(kw)
            return _hist()

    monkeypatch.setattr(market_data.yf, "Ticker", _FakeTicker)
    out = market_data.get_performance_returns(["NVDA"])
    assert len(calls) == 1, "must fetch ONE 1y history and slice, not 5 calls"
    r = out["NVDA"]
    assert set(r) == {"7d", "1m", "3m", "1y", "ytd"}
    assert all(isinstance(v, float) for v in r.values())
