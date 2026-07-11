"""
Offline unit tests for consensus.run_consensus_holding().

run_consensus_holding() normally:
  1. does a top-level yf.download() to build a price cache, then
  2. calls 10 per-node functions (each of which hits yfinance / OpenBB / the DB).

To make it deterministic + fully offline we:
  * stub `consensus.yf.download` to raise, so the cache is empty and `returns`
    is None (the documented graceful-degradation path), and
  * monkeypatch each `node_*` function in the consensus module namespace to
    return a fixed (vote, detail) tuple.

We then assert the weighted vote tally and the documented verdict thresholds:
    STRONG BUY >= 0.55,  BUY >= 0.25,  HOLD in (-0.25, 0.25),
    SELL <= -0.25,  STRONG SELL <= -0.55,  INSUFFICIENT DATA when active < 4.

Node weights (from consensus.NODE_WEIGHTS), Kelly + FFT excluded from the vote:
    Rel.Strength 2.0, Trend 2.0, Volume 1.5, Momentum 1.5,
    Fundamentals 0.75, ECG 0.5, Analyst 0.5, News 0.25   (sum = 9.0)
"""

from __future__ import annotations

import pytest

import consensus

pytestmark = pytest.mark.unit


# Short NODE_WEIGHTS label -> the module-level function that produces its vote.
# ECG is included: it carries weight 0.5 in the tally and, left to the real
# implementation, would abstain (no DB history) and silently shift the
# denominator — so we pin it like every other directional node.
_LABEL_TO_FUNC = {
    "Momentum": "node_momentum",
    "Trend": "node_trend",
    "Rel.Strength": "node_relative_strength",
    "Fundamentals": "node_fundamentals",
    "Analyst": "node_analyst",
    "News": "node_news",
    "Volume": "node_volume",
    "ECG": "node_ecg",
}


def _bull(*_a, **_k):
    return (consensus.VOTE_BULLISH, "mock-bull")


def _bear(*_a, **_k):
    return (consensus.VOTE_BEARISH, "mock-bear")


def _neutral(*_a, **_k):
    return (consensus.VOTE_NEUTRAL, "mock-neutral")


def _abstain(*_a, **_k):
    return (consensus.ABSTAIN, "mock-abstain")


@pytest.fixture(autouse=True)
def _offline_consensus(monkeypatch):
    """Block the top-level price fetch and the FFT node; nodes are set per-test."""
    def _no_download(*_a, **_k):
        raise RuntimeError("offline: yf.download blocked in tests")

    # Block BOTH fetch paths: the cache build uses price_source.yf_download, and
    # leaving it live would also trip the Phase-3 SPY prefetch. Empty cache → the
    # documented graceful-degradation path (c=None, returns=None).
    monkeypatch.setattr(consensus.price_source, "yf_download", _no_download)
    monkeypatch.setattr(consensus.yf, "download", _no_download)
    # Kelly + FFT are excluded from the vote, but keep them harmless + offline.
    monkeypatch.setattr(consensus, "node_kelly", lambda *a, **k: (consensus.VOTE_NEUTRAL, "kelly"))
    import fft_cycles
    monkeypatch.setattr(fft_cycles, "node_fft_cycle", lambda *a, **k: (0, "fft"))
    # Macro is the 9th weighted node (0.75) but it self-fetches FRED/yfinance —
    # leaving it live makes these tally tests network-dependent and flaky (it was
    # the cause of the long-standing strong_buy/vote_tally failures). Pin it to
    # ABSTAIN so the deterministic 8-node model (sum 9.0) the tests assert holds.
    import macro_connector
    monkeypatch.setattr(macro_connector, "node_macro", lambda *a, **k: (consensus.ABSTAIN, "macro-stub"))
    # LT_Macro (0.5 weight) also self-fetches (VIX/F&G/FINRA), so pin it ABSTAIN
    # too — same reason as Macro. The directional-tally tests stay deterministic;
    # its own wiring is covered by tests/test_long_term_macro.py.
    import long_term_macro
    monkeypatch.setattr(long_term_macro, "node_long_term_macro",
                        lambda *a, **k: (consensus.ABSTAIN, "ltm-stub"))
    # Phase 4: NODE_WEIGHTS is now regime-conditional (calls detect_regimes). Pin
    # it to "no regime" so these tally tests use the balanced BASE weights (sum 9.0)
    # deterministically, and clear the 30-min regime cache between tests.
    import regime
    monkeypatch.setattr(regime, "detect_regimes", lambda syms: {"regimes": []})
    consensus._REGIME_LABEL_CACHE.clear()
    consensus._REGIME_CACHE_STAMP.clear()


def _set_votes(monkeypatch, votes: dict):
    """Set each directional node to a fixed result function.

    `votes` maps the SHORT node label used in NODE_WEIGHTS to a vote function.
    Any node not listed defaults to neutral, so the denominator is fully
    controlled (all 8 weighted nodes always active unless explicitly abstained).
    """
    for label, func_name in _LABEL_TO_FUNC.items():
        fn = votes.get(label, _neutral)
        monkeypatch.setattr(consensus, func_name, fn)


def _run():
    return consensus.run_consensus_holding(
        "TEST", equity=5_000, total_portfolio=100_000, avg_cost=250, current_price=300
    )


# ─── verdict thresholds ───────────────────────────────────────────────────────

def test_strong_buy_all_bullish(monkeypatch):
    _set_votes(monkeypatch, {label: _bull for label in _LABEL_TO_FUNC})
    res = _run()
    assert res["raw_score"] == pytest.approx(1.0)
    assert res["verdict"] == "STRONG BUY"
    assert res["verdict_color"] == "green"
    assert res["active_nodes"] == 8     # the 8 controlled nodes voted (Macro + LT_Macro pinned ABSTAIN)
    # confidence = round(active / len(NODE_WEIGHTS) * 100, 0). NODE_WEIGHTS has 10
    # entries (the 8 controlled nodes + Macro + LT_Macro), so round(8/10*100) = 80.0.
    assert res["confidence"] == pytest.approx(80.0)


def test_buy_band(monkeypatch):
    """Bull the two 2.0-weight nodes only -> 4.0/9.0 = 0.444 -> BUY."""
    _set_votes(monkeypatch, {"Rel.Strength": _bull, "Trend": _bull})
    res = _run()
    assert res["raw_score"] == pytest.approx(0.444, abs=1e-3)
    assert res["verdict"] == "BUY"


def test_hold_band(monkeypatch):
    """One 2.0 bull vs one 2.0 bear cancels -> 0.0 -> HOLD."""
    _set_votes(monkeypatch, {"Rel.Strength": _bull, "Trend": _bear})
    res = _run()
    assert res["raw_score"] == pytest.approx(0.0, abs=1e-9)
    assert res["verdict"] == "HOLD"
    assert res["verdict_color"] == "cyan"


def test_sell_band(monkeypatch):
    """Bear the two 2.0-weight nodes -> -0.444 -> SELL."""
    _set_votes(monkeypatch, {"Rel.Strength": _bear, "Trend": _bear})
    res = _run()
    assert res["raw_score"] == pytest.approx(-0.444, abs=1e-3)
    assert res["verdict"] == "SELL"
    assert res["verdict_color"] == "red"


def test_strong_sell_all_bearish(monkeypatch):
    _set_votes(monkeypatch, {label: _bear for label in _LABEL_TO_FUNC})
    res = _run()
    assert res["raw_score"] == pytest.approx(-1.0)
    assert res["verdict"] == "STRONG SELL"
    assert res["verdict_color"] == "red"


def test_insufficient_data_when_few_active(monkeypatch):
    """Only 3 nodes vote (< 4) -> INSUFFICIENT DATA regardless of score."""
    votes = {"Rel.Strength": _bull, "Trend": _bull, "Volume": _bull}
    # Force every other weighted node to ABSTAIN so active_nodes == 3.
    for label in ["Momentum", "Fundamentals", "Analyst", "News", "ECG"]:
        votes[label] = _abstain
    _set_votes(monkeypatch, votes)
    res = _run()
    assert res["active_nodes"] == 3
    assert res["verdict"] == "INSUFFICIENT DATA"
    assert res["verdict_color"] == "muted"


# ─── tally bookkeeping ────────────────────────────────────────────────────────

def test_vote_tally_counts(monkeypatch):
    """bullish/bearish/neutral/abstain counters match the injected votes."""
    votes = {
        "Rel.Strength": _bull,
        "Trend": _bull,
        "Volume": _bear,
        "Momentum": _neutral,
        "Fundamentals": _neutral,
        "ECG": _neutral,
        "Analyst": _abstain,
        "News": _abstain,
    }
    _set_votes(monkeypatch, votes)
    res = _run()
    assert res["bullish"] == 2
    assert res["bearish"] == 1
    assert res["neutral"] == 3          # Momentum, Fundamentals, ECG
    assert res["abstains"] == 4         # Analyst, News + Macro + LT_Macro (pinned ABSTAIN)
    assert res["active_nodes"] == 6     # 8 controlled nodes - 2 abstains (Macro + LT_Macro excluded)
    # Kelly + FFT are reported but excluded from the directional tally.
    assert "Kelly" in res["votes"]
    assert "FFT_Cycle" in res["votes"]
    assert res["total_nodes"] == 12     # 8 directional + Kelly + FFT + Macro + LT_Macro


def test_kelly_and_fft_excluded_from_score(monkeypatch):
    """Even a bullish Kelly/FFT must not move raw_score (they are sizing-only)."""
    monkeypatch.setattr(consensus, "node_kelly", lambda *a, **k: (consensus.VOTE_BULLISH, "k"))
    import fft_cycles
    monkeypatch.setattr(fft_cycles, "node_fft_cycle", lambda *a, **k: (1, "fft"))
    # All directional nodes neutral -> score must be exactly 0.0.
    _set_votes(monkeypatch, {})
    res = _run()
    assert res["raw_score"] == pytest.approx(0.0, abs=1e-9)
    assert res["verdict"] == "HOLD"
