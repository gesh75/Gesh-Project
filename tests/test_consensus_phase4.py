"""Consensus convergence Phase 4 — regime-conditional NODE_WEIGHTS (product branch).

Pins the engine-parity regime → weight profiles and the 30-min cache. detect_regimes
is mocked, so these are network-free and deterministic.
"""
from __future__ import annotations

import pytest

import consensus
import regime


@pytest.fixture(autouse=True)
def _clear_cache():
    consensus._REGIME_LABEL_CACHE.clear()
    consensus._REGIME_CACHE_STAMP.clear()
    yield
    consensus._REGIME_LABEL_CACHE.clear()
    consensus._REGIME_CACHE_STAMP.clear()


def _mock_regime(monkeypatch, label):
    monkeypatch.setattr(
        regime, "detect_regimes",
        lambda syms: {"regimes": [{"symbol": syms[0], "regime": label}]})


def test_base_weights_when_no_regime(monkeypatch):
    monkeypatch.setattr(regime, "detect_regimes", lambda syms: {"regimes": []})
    w = consensus._regime_weights("NVDA")
    assert w == consensus._BASE_WEIGHTS
    assert w["Trend"] == 2.0 and w["Momentum"] == 1.5


def test_breakout_ready_boosts_trend(monkeypatch):
    _mock_regime(monkeypatch, "BREAKOUT-READY")
    w = consensus._regime_weights("HOOD")
    assert w["Trend"] == 2.5 and w["Momentum"] == 1.0


@pytest.mark.parametrize("label", ["TRENDING", "MOMENTUM-HIGH"])
def test_trending_boosts_momentum(monkeypatch, label):
    _mock_regime(monkeypatch, label)
    w = consensus._regime_weights("NVDA")
    assert w["Momentum"] == 2.5 and w["Trend"] == 1.0


@pytest.mark.parametrize("label", ["RANGING", "MOMENTUM-LOW"])
def test_ranging_boosts_fundamentals(monkeypatch, label):
    _mock_regime(monkeypatch, label)
    w = consensus._regime_weights("DDOG")
    assert w["Fundamentals"] == 1.25 and w["Momentum"] == 1.0


def test_detection_failure_falls_back_to_base(monkeypatch):
    def boom(syms):
        raise RuntimeError("regime engine down")
    monkeypatch.setattr(regime, "detect_regimes", boom)
    assert consensus._regime_weights("NVDA") == consensus._BASE_WEIGHTS


def test_regime_label_cached_30min(monkeypatch):
    calls = {"n": 0}
    def counting(syms):
        calls["n"] += 1
        return {"regimes": [{"symbol": syms[0], "regime": "TRENDING"}]}
    monkeypatch.setattr(regime, "detect_regimes", counting)
    consensus._regime_weights("NVDA")
    consensus._regime_weights("NVDA")   # second call served from cache
    assert calls["n"] == 1


def test_base_weights_unmutated(monkeypatch):
    # _regime_weights must not mutate the shared _BASE_WEIGHTS dict.
    _mock_regime(monkeypatch, "BREAKOUT-READY")
    consensus._regime_weights("HOOD")
    assert consensus._BASE_WEIGHTS["Trend"] == 2.0   # still base
