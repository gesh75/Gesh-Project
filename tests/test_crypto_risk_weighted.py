"""GFD-026: crypto_risk_summary must equity-weight BTC correlation."""

from __future__ import annotations

import numpy as np
import pytest

import crypto_correlation as cc


def test_equity_weight_prefers_large_position(monkeypatch):
    # Tiny high-corr name must not dominate a large low-corr book.
    monkeypatch.setattr(
        cc,
        "get_crypto_correlations",
        lambda symbols, lookback_days=90: {
            "BTC-USD": {"TINY": 0.95, "HUGE": 0.10},
            "ETH-USD": {"TINY": 0.90, "HUGE": 0.05},
        },
    )
    holdings = [
        {"symbol": "TINY", "equity": 100.0},
        {"symbol": "HUGE", "equity": 9_900.0},
    ]
    summary = cc.crypto_risk_summary(holdings)
    expected = np.average([0.95, 0.10], weights=[100.0, 9_900.0])
    assert summary["avg_btc_corr"] == pytest.approx(round(float(expected), 3))
    assert summary["weighting"] == "equity"
    assert summary["unweighted_avg_btc_corr"] == pytest.approx(0.525, abs=0.01)
    assert abs(summary["avg_btc_corr"] - summary["unweighted_avg_btc_corr"]) > 0.2


def test_equal_weight_fallback_when_no_equity(monkeypatch):
    monkeypatch.setattr(
        cc,
        "get_crypto_correlations",
        lambda symbols, lookback_days=90: {
            "BTC-USD": {"A": 0.2, "B": 0.8},
            "ETH-USD": {},
        },
    )
    summary = cc.crypto_risk_summary([{"symbol": "A"}, {"symbol": "B"}])
    assert summary["avg_btc_corr"] == pytest.approx(0.5)
    assert summary["weighting"] == "equal-fallback"
