"""Long-term macro overlay — the VIX / Fear&Greed / FINRA-margin-debt 1-3y bias.

Covers the three layers that shipped without tests:
  * node_long_term_macro() → consensus vote from the combined score
  * GET /api/long-term-macro[/cached] shape (no network — the fetch is stubbed)
  * the node is WIRED into run_consensus_holding (appears in votes) AND registered
    in _BASE_WEIGHTS (so the confidence denominator stays correct)

Offline: the sentiment fetch (yfinance VIX / F&G API / FINRA) is monkeypatched, so
no network is touched.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

import long_term_macro as ltm

_SENT = {
    "vix": {"score": 1, "detail": "VIX 15.0 low", "level": 15.0},
    "fear_greed": {"score": 1, "detail": "Greed 68", "level": 68.0},
    "margin_debt": {"score": 0, "detail": "flat", "level": 750000.0},
    "combined_score": 0.55,
    "bias": "BULLISH",
}


# ── node vote logic ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("score,expected", [
    (0.55, 1), (0.31, 1),          # > 0.3  → BULLISH tilt
    (-0.55, -1), (-0.31, -1),      # < -0.3 → BEARISH tilt
    (0.30, 0), (0.0, 0), (-0.30, 0),  # band → NEUTRAL
])
def test_node_vote_thresholds(monkeypatch, score, expected):
    monkeypatch.setattr(ltm, "get_long_term_macro_sentiment",
                        lambda: {**_SENT, "combined_score": score})
    vote, detail = ltm.node_long_term_macro("AAPL")
    assert vote == expected
    assert isinstance(detail, str) and detail          # non-empty reasoning


def test_node_detail_mentions_each_component(monkeypatch):
    monkeypatch.setattr(ltm, "get_long_term_macro_sentiment", lambda: _SENT)
    _, detail = ltm.node_long_term_macro()
    assert "VIX" in detail and "FG" in detail and "MD" in detail


# ── API endpoints ─────────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch):
    import server
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: False)
    # both endpoints lazily `from long_term_macro import get_long_term_macro_sentiment`
    monkeypatch.setattr(ltm, "get_long_term_macro_sentiment", lambda: dict(_SENT))
    return TestClient(server.app, raise_server_exceptions=False)


def test_endpoint_live_shape(client):
    r = client.get("/api/long-term-macro")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["data"]["bias"] == "BULLISH"
    assert body["data"]["vix"]["level"] == 15.0


def test_endpoint_cached_shape(client):
    r = client.get("/api/long-term-macro/cached")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["cached"] is True
    assert body["data"]["combined_score"] == 0.55


def test_endpoint_nan_sanitized_not_500(client, monkeypatch):
    """A NaN level (insufficient data) must serialize to null, not crash the route."""
    monkeypatch.setattr(ltm, "get_long_term_macro_sentiment",
                        lambda: {**_SENT, "vix": {"score": 0, "detail": "n/a",
                                                  "level": float("nan")}})
    r = client.get("/api/long-term-macro")
    assert r.status_code == 200
    assert r.json()["data"]["vix"]["level"] is None


# ── consensus wiring ──────────────────────────────────────────────────────────

def test_node_registered_in_base_weights():
    """In _BASE_WEIGHTS so len(NODE_WEIGHTS) counts it — otherwise an active
    LT_Macro would push confidence (= active/len(weights)) over 100%."""
    import consensus
    assert "LT_Macro" in consensus._BASE_WEIGHTS


def test_lt_macro_appears_in_consensus_votes(monkeypatch):
    """The node must actually be assembled into run_consensus_holding's votes —
    the whole point of the overlay. Other nodes ABSTAIN offline; LT_Macro votes."""
    import consensus
    monkeypatch.setattr(ltm, "get_long_term_macro_sentiment",
                        lambda: {**_SENT, "combined_score": 0.55})
    # keep the price-based nodes fast + offline → they ABSTAIN, LT_Macro still votes
    monkeypatch.setattr(consensus.price_source, "yf_download",
                        lambda *a, **k: None, raising=False)
    out = consensus.run_consensus_holding("AAPL", 1000.0, 10000.0, 90.0, 100.0)
    assert "LT_Macro" in out["votes"]
    assert out["votes"]["LT_Macro"] == 1          # bullish combined score → +1
    assert "VIX" in out["details"]["LT_Macro"]
