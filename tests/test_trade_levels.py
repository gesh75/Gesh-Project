"""Support/resistance + trade-setup levels (trade_levels.compute).

Deterministic on synthetic OHLCV — pivots, swing structure, and the long setup
(entry/stop/target/RR) are pure price math; no network.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from starlette.testclient import TestClient

import trade_levels as tl


def _ohlcv(n=80):
    rng = np.random.default_rng(7)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    high = close + np.abs(rng.normal(0, 0.8, n)) + 0.5
    low = close - np.abs(rng.normal(0, 0.8, n)) - 0.5
    return close, high, low


def test_shape_and_setup_fields():
    c, h, low = _ohlcv()
    out = tl.compute("nvda", close=c, high=h, low=low)
    assert out["available"] is True and out["symbol"] == "NVDA"
    for k in ("support", "resistance", "pivots", "setup", "swing_high_60d", "swing_low_60d"):
        assert k in out
    for k in ("entry", "stop", "target", "risk_reward"):
        assert k in out["setup"]
    assert set(out["pivots"]) == {"P", "R1", "R2", "S1", "S2"}


def test_stop_below_entry_and_target_above():
    c, h, low = _ohlcv()
    s = tl.compute("TEST", close=c, high=h, low=low)["setup"]
    assert s["stop"] < s["entry"] < s["target"]      # a coherent long setup
    assert s["risk_reward"] is None or s["risk_reward"] > 0


def test_support_below_resistance_above_price():
    c, h, low = _ohlcv()
    out = tl.compute("TEST", close=c, high=h, low=low)
    price = out["price"]
    assert all(x < price for x in out["support"])
    assert all(x > price for x in out["resistance"])


def test_insufficient_data():
    assert tl.compute("TEST", close=np.array([1.0, 2]), high=np.array([2.0, 3]),
                      low=np.array([0.5, 1]))["available"] is False


# ── GESH-1: single price source-of-truth (entry binds to the live quote) ────────

def _uptrend(n=60):
    """Steady uptrend ending at its peak → the only resistances are a few cents
    above the last close, so an entry pinned just under them yields a tiny reward."""
    close = np.linspace(90.0, 100.0, n)
    return close, close + 0.2, close - 0.2


def test_current_price_binds_entry_single_source_of_truth():
    c, h, low = _ohlcv()
    out = tl.compute("TEST", close=c, high=h, low=low, current_price=97.31)
    assert out["available"] is True
    assert out["setup"]["entry"] == 97.31           # entry follows the live quote…
    assert out["price"] == 97.31                     # …and every price surface agrees
    assert all(x < 97.31 for x in out["support"])    # S/R re-filtered around live entry
    assert all(x > 97.31 for x in out["resistance"])


def test_current_price_ignored_when_invalid():
    c, h, low = _ohlcv()
    good = tl.compute("TEST", close=c, high=h, low=low)["setup"]["entry"]
    for bad in (0, -5, float("nan"), None, "x"):
        out = tl.compute("TEST", close=c, high=h, low=low, current_price=bad)
        assert out["setup"]["entry"] == good         # falls back to last close


# ── GESH-2: degenerate R:R is flagged non-actionable, not shown as tradeable ────

def test_degenerate_low_rr_setup_flagged_not_actionable():
    c, h, low = _uptrend()
    # live quote pinned ~4c below the nearest resistance → ~$0.04 reward vs real risk
    out = tl.compute("TEST", close=c, high=h, low=low, current_price=100.16)
    st = out["setup"]
    assert out["available"] is True                  # levels still returned for context
    assert st["entry"] == 100.16
    assert st["risk_reward"] < 1.0
    assert st["actionable"] is False                 # never rendered as tradeable
    assert st["warning"]                             # human-readable reason present


def test_normal_setup_is_actionable():
    c, h, low = _ohlcv()
    st = tl.compute("TEST", close=c, high=h, low=low)["setup"]
    assert isinstance(st["actionable"], bool)
    if st["actionable"]:                             # actionable ⇒ clears both floors
        assert st["risk_reward"] >= tl._MIN_RR
        assert st["reward_pct"] >= tl._MIN_REWARD_PCT
        assert st["warning"] is None


# ── Codex #1: OHLC bars stay row-aligned when the download has a lone NaN ────────

def test_ohlc_joint_alignment_on_download(monkeypatch):
    n = 40
    idx = pd.RangeIndex(n)
    close = pd.Series(np.linspace(90.0, 100.0, n), index=idx)
    high = pd.Series(np.linspace(90.5, 100.5, n), index=idx)
    low = pd.Series(np.linspace(89.5, 99.5, n), index=idx)
    high.iloc[5] = np.nan          # a lone NaN in ONE column only
    frame = pd.DataFrame({"Close": close, "High": high, "Low": low})

    class _PS:
        @staticmethod
        def yf_download(*a, **k):
            return frame
    monkeypatch.setitem(__import__("sys").modules, "price_source", _PS)
    out = tl.compute("TEST")
    # joint dropna removes ONLY row 5 from all three → still coherent + aligned
    assert out["available"] is True
    assert out["setup"]["stop"] < out["setup"]["entry"] < out["setup"]["target"]


def test_pivots_are_classic_formula():
    # single flat window → pivots derive from the last bar's H/L/C
    c = np.full(40, 100.0)
    h = np.full(40, 102.0)
    low = np.full(40, 98.0)
    out = tl.compute("TEST", close=c, high=h, low=low)
    p = (102 + 98 + 100) / 3
    assert out["pivots"]["P"] == round(p, 2)


# ── endpoint ──────────────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch):
    import server
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: False)
    return server, TestClient(server.app, raise_server_exceptions=False)


def test_endpoint(client, monkeypatch):
    server, c = client
    # the endpoint now fetches the canonical quote and passes current_price=… in
    monkeypatch.setattr(server, "_latest_close", lambda s: 100.0, raising=True)
    monkeypatch.setattr("trade_levels.compute",
                        lambda sym, current_price=None: {"available": True, "symbol": sym, "price": 100.0,
                                     "support": [95.0], "resistance": [110.0],
                                     "pivots": {"P": 100, "R1": 105, "R2": 110, "S1": 95, "S2": 90},
                                     "swing_high_60d": 112.0, "swing_low_60d": 88.0,
                                     "setup": {"entry": 100.0, "stop": 94.5, "target": 110.0,
                                               "risk_reward": 1.82, "risk_pct": 5.5, "reward_pct": 10.0,
                                               "actionable": True, "warning": None},
                                     "note": "x"}, raising=False)
    r = c.get("/api/levels/NVDA")
    assert r.status_code == 200
    assert r.json()["data"]["setup"]["risk_reward"] == 1.82


def test_levels_endpoint_entry_binds_to_latest_close(client, monkeypatch):
    """GESH-1 integration: the /api/levels ENTRY must equal the canonical quote
    ($394.76), NOT trade_levels' own 6mo last bar ($396.18)."""
    server, c = client
    # the endpoint resolves the quote through _latest_close_meta (Codex #6 added the
    # provenance tuple); patch that seam, not the thin _latest_close wrapper.
    monkeypatch.setattr(server, "_latest_close_meta",
                        lambda s: (394.76, {"source": "yahoo", "substituted": False}),
                        raising=True)

    class _PS:
        @staticmethod
        def yf_download(*a, **k):
            n = 40
            close = np.linspace(380.0, 396.18, n)   # 6mo last bar = 396.18
            return pd.DataFrame({"Close": close, "High": close + 1, "Low": close - 1})
    monkeypatch.setitem(__import__("sys").modules, "price_source", _PS)
    r = c.get("/api/levels/NVDA")
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["available"] is True
    assert d["price"] == 394.76 and d["setup"]["entry"] == 394.76   # canonical quote, not 396.18
