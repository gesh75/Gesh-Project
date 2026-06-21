"""Consensus convergence Phase 7 — result exposes regime + node_weights.

The dashboard already renders the 11 nodes (incl. Macro) + a regime panel; Phase 7
surfaces the detected regime label and the regime-adjusted weights in the consensus
result so the UI can show *why* the weights are what they are. Offline (nodes +
detect_regimes mocked).
"""
from __future__ import annotations

import sys
import types

import consensus
import regime
import macro_connector
import fft_cycles


def _offline(monkeypatch, regime_label):
    def no_dl(*a, **k): raise RuntimeError("offline")
    monkeypatch.setattr(consensus.price_source, "yf_download", no_dl)
    monkeypatch.setattr(consensus.yf, "download", no_dl)
    monkeypatch.setattr(consensus, "node_kelly", lambda *a, **k: (consensus.VOTE_NEUTRAL, "k"))
    monkeypatch.setattr(fft_cycles, "node_fft_cycle", lambda *a, **k: (0, "f"))
    monkeypatch.setattr(macro_connector, "node_macro", lambda *a, **k: (consensus.ABSTAIN, "m"))
    # node_news imports sentiment_connector live — stub it
    mod = types.ModuleType("sentiment_connector")
    mod.node_sentiment = lambda s: (consensus.ABSTAIN, "stub")
    monkeypatch.setitem(sys.modules, "sentiment_connector", mod)
    monkeypatch.setattr(regime, "detect_regimes",
                        lambda syms: {"regimes": [{"symbol": syms[0], "regime": regime_label}]} if regime_label else {"regimes": []})
    consensus._REGIME_LABEL_CACHE.clear()
    consensus._REGIME_CACHE_STAMP.clear()


def _run():
    return consensus.run_consensus_holding("TEST", 1000, 100000, 100, 120)


def test_result_exposes_regime_and_weights(monkeypatch):
    _offline(monkeypatch, "TRENDING")
    res = _run()
    assert res["regime"] == "TRENDING"
    assert isinstance(res["node_weights"], dict)
    # TRENDING profile: Momentum boosted 2.5, Trend cut to 1.0
    assert res["node_weights"]["Momentum"] == 2.5
    assert res["node_weights"]["Trend"] == 1.0
    assert "Macro" in res["node_weights"]


def test_result_regime_default_when_none(monkeypatch):
    _offline(monkeypatch, None)
    res = _run()
    assert res["regime"] == "DEFAULT"
    assert res["node_weights"]["Trend"] == 2.0   # base
    assert res["node_weights"]["Momentum"] == 1.5
