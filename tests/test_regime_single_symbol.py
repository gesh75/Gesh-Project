"""detect_regimes single-symbol shape bug (found live 2026-07-02).

A 1-symbol request still yields (field, ticker) MultiIndex columns — from
plain yfinance AND from price_source's RH multi-symbol fallback. The old
single-symbol branch of _get_col returned raw[col].values as a (n,1) 2-D
array, which kept every ADX/BB operation array-shaped until float(adx) raised
"only 0-dimensional arrays can be converted to Python scalars".
Fully offline: price_source.yf_download is monkeypatched.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import regime


def _frame(n: int = 260, multiindex: bool = True) -> pd.DataFrame:
    idx = pd.date_range(end="2026-07-01", periods=n, freq="B")
    c = 700 + 20 * np.sin(np.arange(n) / 15) + np.linspace(0, 30, n)
    data = {"Open": c, "High": c + 3, "Low": c - 3, "Close": c, "Volume": np.full(n, 5e7)}
    df = pd.DataFrame(data, index=idx)
    if multiindex:
        df.columns = pd.MultiIndex.from_product([df.columns, ["SPY"]], names=["Price", "Ticker"])
    return df


def _assert_numeric_regime(result: dict) -> None:
    row = result["regimes"][0]
    assert row["symbol"] == "SPY"
    assert row["regime"] != "UNKNOWN"
    assert isinstance(row["adx"], float)
    assert isinstance(row["rsi"], float)


def test_single_symbol_multiindex_frame(monkeypatch):
    monkeypatch.setattr(regime.price_source, "yf_download", lambda *a, **k: _frame(multiindex=True))
    _assert_numeric_regime(regime.detect_regimes(["SPY"]))


def test_single_symbol_simple_columns_frame(monkeypatch):
    """RH single-symbol fallback shape — simple columns must keep working."""
    monkeypatch.setattr(
        regime.price_source, "yf_download", lambda *a, **k: _frame(multiindex=False)
    )
    _assert_numeric_regime(regime.detect_regimes(["SPY"]))


@pytest.mark.parametrize("available_symbols", [["SPY"], ["SPY", "QQQ"]])
def test_multi_symbol_partial_frame_missing_symbol_is_unknown(monkeypatch, available_symbols):
    """Partial provider results must not reuse another ticker's price data."""
    frames = {
        (field, symbol): _frame(multiindex=False)[field]
        for symbol in available_symbols
        for field in ("Open", "High", "Low", "Close", "Volume")
    }
    partial = pd.DataFrame(frames)
    partial.columns = pd.MultiIndex.from_tuples(partial.columns, names=["Price", "Ticker"])
    monkeypatch.setattr(regime.price_source, "yf_download", lambda *a, **k: partial)

    result = regime.detect_regimes([*available_symbols, "MISSING"])

    missing = next(row for row in result["regimes"] if row["symbol"] == "MISSING")
    assert missing["regime"] == "UNKNOWN"
    assert missing["adx"] is None
