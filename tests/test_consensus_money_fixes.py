"""Consensus money-path fixes (audit PR5).

1. RSI must saturate to 100 (bullish) on an all-gains window — the old code
   forced it to 50 (neutral), muting the Momentum node in the strongest trends.
2. The Kelly node must receive SIMPLE returns (np.diff(c)/c[:-1]) to match
   gesh_core.kelly's pct_change() convention — log returns understate mu by
   ~sigma^2/2 and skewed the node bearish vs the dashboard Kelly.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import consensus


def test_momentum_rsi_saturates_bullish_on_all_gains():
    # 46 flat bars then 14 small consecutive gains: zero losses in the RSI-14
    # window, but MA20/MA50 within the ±0.5% neutral band. Only a correct
    # RSI=100 tips the node to BULLISH; the old RSI=50 fallback left it NEUTRAL.
    c = np.concatenate([np.full(46, 100.0), 100.0 + 0.05 * np.arange(1, 15)])
    vote, detail = consensus.node_momentum("TEST", close=c)
    assert "RSI=100" in detail
    assert vote == consensus.VOTE_BULLISH


def test_momentum_all_losses_is_bearish():
    c = np.concatenate([np.full(46, 100.0), 100.0 - 0.05 * np.arange(1, 15)])
    vote, detail = consensus.node_momentum("TEST", close=c)
    assert vote == consensus.VOTE_BEARISH


def test_kelly_node_receives_simple_returns(monkeypatch):
    n = 120
    c = np.linspace(100.0, 130.0, n)
    idx = pd.date_range("2026-01-01", periods=n)
    frame = pd.DataFrame(
        {"Open": c, "High": c + 1, "Low": c - 1, "Close": c, "Volume": np.full(n, 1e6)},
        index=idx,
    )
    monkeypatch.setattr(consensus.price_source, "yf_download", lambda *a, **k: frame)
    monkeypatch.setattr(consensus, "_regime_weights", lambda sym: dict(consensus._BASE_WEIGHTS))

    captured: dict = {}

    def fake_kelly(symbol, equity, total, cost, price, returns=None):
        captured["returns"] = returns
        return (0, "stub")

    monkeypatch.setattr(consensus, "node_kelly", fake_kelly)
    for name in ("node_momentum", "node_trend", "node_relative_strength",
                 "node_fundamentals", "node_analyst", "node_news",
                 "node_ecg", "node_volume"):
        monkeypatch.setattr(consensus, name, lambda *a, **k: (0, "stub"))

    consensus.run_consensus_holding("TEST", 0, 1, 0, 0)

    c91 = c[-91:]
    expected = np.diff(c91) / c91[:-1]  # SIMPLE returns
    assert captured["returns"] is not None
    assert np.allclose(captured["returns"], expected), (
        "Kelly node must get simple returns (gesh_core.kelly pct_change convention)"
    )


def test_ohlcv_cache_stays_row_aligned(monkeypatch):
    """A NaN in one price column must drop the whole ROW, not just that value —
    otherwise Close/High/Low drift out of time alignment and ADX is garbage."""
    n = 80
    c = np.linspace(100.0, 120.0, n)
    high = c + 1
    high[40] = np.nan  # single mid-series gap in High only
    idx = pd.date_range("2026-01-01", periods=n)
    frame = pd.DataFrame(
        {"Open": c, "High": high, "Low": c - 1, "Close": c, "Volume": np.full(n, 1e6)},
        index=idx,
    )
    monkeypatch.setattr(consensus.price_source, "yf_download", lambda *a, **k: frame)
    monkeypatch.setattr(consensus, "_regime_weights", lambda sym: dict(consensus._BASE_WEIGHTS))

    captured: dict = {}

    def fake_trend(symbol, close=None, high=None, low=None):
        captured.update(close=close, high=high, low=low)
        return (0, "stub")

    monkeypatch.setattr(consensus, "node_trend", fake_trend)
    for name in ("node_momentum", "node_relative_strength", "node_fundamentals",
                 "node_analyst", "node_news", "node_kelly", "node_ecg", "node_volume"):
        monkeypatch.setattr(consensus, name, lambda *a, **k: (0, "stub"))

    consensus.run_consensus_holding("TEST", 0, 1, 0, 0)

    assert len(captured["close"]) == len(captured["high"]) == len(captured["low"])
    # The NaN row must be gone from ALL arrays: every remaining high == close+1.
    assert np.allclose(captured["high"], captured["close"] + 1)


# ── M-05: Kelly must abstain when the portfolio total is unusable ────────────
def test_node_kelly_abstains_when_total_portfolio_missing_for_a_held_position(monkeypatch):
    import sys
    import types

    calls = []
    fake = types.SimpleNamespace(
        kelly_size_holding=lambda *a, **k: calls.append(a) or {"action": "TRIM", "optimal_pct": 1}
    )
    monkeypatch.setitem(sys.modules, "kelly", fake)
    for equity, total in ((2500.0, 0.0), (2500.0, 1.0), (2500.0, float("nan")), (1.0, -3.0)):
        vote, detail = consensus.node_kelly("MU", equity, total, 90.0, 95.0)
        assert vote is consensus.ABSTAIN, (equity, total, detail)
    assert calls == []  # never reached the sizing maths with a nonsense denominator


def test_node_kelly_still_runs_for_flat_watchlist_symbol_with_default_total(monkeypatch):
    import sys
    import types

    fake = types.SimpleNamespace(kelly_size_holding=lambda *a, **k: {"action": "ADD", "optimal_pct": 5})
    monkeypatch.setitem(sys.modules, "kelly", fake)
    vote, _ = consensus.node_kelly("NVDA", 0.0, 1.0, 0.0, 100.0)
    assert vote == consensus.VOTE_BULLISH


# ── M-12: ECG must not turn a missing equity point into a flat 0% session ────
def _ecg_with_history(monkeypatch, equities_newest_first):
    import sys
    import types

    rows = [{"equity": e} for e in equities_newest_first]
    monkeypatch.setitem(sys.modules, "db", types.SimpleNamespace(get_equity_history=lambda s, days=30: rows))
    return consensus.node_ecg("MU")


def test_ecg_loss_streak_survives_a_missing_equity_point(monkeypatch):
    # oldest->newest: 100, 99, 0(missing), 97, 96, 95. Dropping the gap leaves 4
    # straight losses (99, 97, 96, 95). The old code turned the gap into a 0.0%
    # session and reported a streak of only 2.
    vote, detail = _ecg_with_history(monkeypatch, [95.0, 96.0, 97.0, 0.0, 99.0, 100.0])
    assert "Streak=4" in detail, detail


def test_ecg_abstains_when_too_few_real_points_remain(monkeypatch):
    vote, detail = _ecg_with_history(monkeypatch, [0.0, None, 100.0, 101.0])
    assert vote is consensus.ABSTAIN
