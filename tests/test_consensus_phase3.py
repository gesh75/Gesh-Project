"""Consensus convergence Phase 3 — Rel.Strength wired to the prefetched arrays.

run_consensus_holding pre-fetches 252d once; Phase 3 feeds that sym close + a
once-cached SPY close to node_relative_strength so it stops self-fetching its own
[symbol, SPY] batch. This pins that the wiring passes the arrays through (offline:
all other nodes stubbed, price_source + fetch_spy_close patched).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import consensus


def _fake_252(symbol, *a, **k):
    n = 252
    return pd.DataFrame({
        "Close":  np.linspace(100, 130, n),
        "High":   np.linspace(101, 131, n),
        "Low":    np.linspace(99, 129, n),
        "Volume": np.linspace(1e6, 2e6, n),
    })


def test_rel_strength_receives_prefetched_arrays(monkeypatch):
    captured = {}

    def recorder(symbol, benchmark="SPY", sym_close=None, spy_close=None):
        captured["sym_close"] = sym_close
        captured["spy_close"] = spy_close
        return consensus.VOTE_NEUTRAL, "stub"

    # prefetch returns a clean 252d frame; SPY comes from the shared cache helper
    monkeypatch.setattr(consensus.price_source, "yf_download", _fake_252)
    import gesh_core.market_fetch as mf
    monkeypatch.setattr(mf, "fetch_spy_close", lambda days=40: np.linspace(400, 420, 63))
    monkeypatch.setattr(consensus, "node_relative_strength", recorder)
    # stub the remaining nodes so the run is fast + offline
    for n in ("node_momentum", "node_trend", "node_fundamentals", "node_analyst",
              "node_news", "node_kelly", "node_ecg", "node_volume"):
        monkeypatch.setattr(consensus, n, lambda *a, **k: (consensus.ABSTAIN, "stub"))

    consensus.run_consensus_holding("NVDA")

    assert captured["sym_close"] is not None and len(captured["sym_close"]) == 252
    assert captured["spy_close"] is not None and len(captured["spy_close"]) == 63


def test_rel_strength_falls_back_when_prefetch_empty(monkeypatch):
    captured = {}

    def recorder(symbol, benchmark="SPY", sym_close=None, spy_close=None):
        captured["sym_close"] = sym_close
        return consensus.VOTE_NEUTRAL, "stub"

    # prefetch raises -> cache close is None -> RS must get sym_close=None (self-fetch path)
    monkeypatch.setattr(consensus.price_source, "yf_download",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(consensus, "node_relative_strength", recorder)
    for n in ("node_momentum", "node_trend", "node_fundamentals", "node_analyst",
              "node_news", "node_kelly", "node_ecg", "node_volume"):
        monkeypatch.setattr(consensus, n, lambda *a, **k: (consensus.ABSTAIN, "stub"))

    consensus.run_consensus_holding("NVDA")
    assert captured["sym_close"] is None   # graceful self-fetch fallback
