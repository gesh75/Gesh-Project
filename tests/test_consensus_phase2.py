"""Consensus convergence Phase 2 — node signature migration (product branch).

Pins that node_relative_strength accepts optional pre-fetched arrays (the last
OHLCV node that still self-fetched) and computes RS network-free via the
engine-parity array path. The default (no-array) call path is unchanged, so
existing verdicts are unaffected — this only adds an injection path for Phase 3.
"""
from __future__ import annotations

import numpy as np

import consensus


def test_rel_strength_accepts_arrays():
    p = consensus.node_relative_strength.__code__.co_varnames
    assert "sym_close" in p and "spy_close" in p


def test_rel_strength_array_path_bullish():
    # symbol +~10% vs SPY +~2% over the window -> RS well above +3 -> BULLISH
    sym = np.linspace(100, 110, 25)
    spy = np.linspace(100, 102, 25)
    vote, detail = consensus.node_relative_strength("NVDA", sym_close=sym, spy_close=spy)
    assert vote == consensus.VOTE_BULLISH
    assert "RS=" in detail


def test_rel_strength_array_path_bearish():
    sym = np.linspace(100, 98, 25)    # -2%
    spy = np.linspace(100, 108, 25)   # +8%  -> RS ~ -10 -> BEARISH
    vote, _ = consensus.node_relative_strength("NVDA", sym_close=sym, spy_close=spy)
    assert vote == consensus.VOTE_BEARISH


def test_rel_strength_array_path_neutral():
    sym = np.linspace(100, 105, 25)   # +5%
    spy = np.linspace(100, 104, 25)   # +4%  -> RS ~ +1 (within ±3) -> NEUTRAL
    vote, _ = consensus.node_relative_strength("NVDA", sym_close=sym, spy_close=spy)
    assert vote == consensus.VOTE_NEUTRAL


def test_rel_strength_thin_arrays_abstain():
    vote, detail = consensus.node_relative_strength(
        "NVDA", sym_close=np.array([1.0, 2.0]), spy_close=np.array([1.0, 2.0]))
    assert vote is consensus.ABSTAIN


def test_all_ohlcv_nodes_accept_arrays():
    # momentum/trend/volume already had array params; relative_strength now too.
    assert "close" in consensus.node_momentum.__code__.co_varnames
    assert "close" in consensus.node_trend.__code__.co_varnames
    assert "volume" in consensus.node_volume.__code__.co_varnames
    assert "sym_close" in consensus.node_relative_strength.__code__.co_varnames
