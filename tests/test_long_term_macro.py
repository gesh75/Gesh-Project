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


def test_endpoint_cached_cold_computes(client):
    """A cold serve-cache computes fresh and honestly reports cached=False."""
    r = client.get("/api/long-term-macro/cached")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["cached"] is False
    assert body["data"]["combined_score"] == 0.55


def test_endpoint_cached_serves_from_cache_after_live(client, monkeypatch):
    """After a live call warms the serve cache, /cached returns cached=True and
    does NOT re-enter the module (genuine cache, not a relabelled live call)."""
    assert client.get("/api/long-term-macro").status_code == 200   # warms cache
    calls = {"n": 0}

    def _boom():
        calls["n"] += 1
        raise AssertionError("module must not be called on a warm cache hit")
    monkeypatch.setattr(ltm, "get_long_term_macro_sentiment", _boom)
    r = client.get("/api/long-term-macro/cached")
    assert r.status_code == 200
    body = r.json()
    assert body["cached"] is True and calls["n"] == 0
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


# ── engine_block() — the engine_results.json contract for the agentic-rh bridge ──

def test_engine_block_shape_and_fields():
    """engine_block normalizes the raw sentiment into the flat block the bridge reads:
    fear_greed.label, margin_debt.elevated, vix.level, combined_score, bias."""
    blk = ltm.engine_block({**_SENT,
                            "fear_greed": {"score": -1, "detail": "Extreme Greed 82", "level": 82.0},
                            "margin_debt": {"score": -1, "detail": "rising fast", "level": 9.0e11}})
    assert blk["fear_greed"]["level"] == 82.0
    assert blk["fear_greed"]["label"] == "Extreme Greed"     # derived from the 0-100 level
    assert blk["margin_debt"]["elevated"] is True            # negative margin score → elevated
    assert blk["vix"]["level"] == 15.0
    assert blk["combined_score"] == 0.55 and blk["bias"] == "BULLISH"


@pytest.mark.parametrize("level,label", [
    (10.0, "Extreme Fear"), (24.9, "Extreme Fear"), (30.0, "Fear"),
    (50.0, "Neutral"), (68.0, "Greed"), (82.0, "Extreme Greed"), (None, None),
])
def test_engine_block_fear_greed_labels(level, label):
    blk = ltm.engine_block({**_SENT, "fear_greed": {"score": 0, "detail": "", "level": level}})
    assert blk["fear_greed"]["label"] == label


def test_engine_block_margin_elevated_only_when_score_negative():
    assert ltm.engine_block({**_SENT, "margin_debt": {"score": -1, "level": 1}})["margin_debt"]["elevated"] is True
    assert ltm.engine_block({**_SENT, "margin_debt": {"score": 0, "level": 1}})["margin_debt"]["elevated"] is False
    assert ltm.engine_block({**_SENT, "margin_debt": {"score": None, "level": 1}})["margin_debt"]["elevated"] is None
    # A genuine FINRA outage (status != "ok") is UNKNOWN, not a definite "not elevated":
    # score=0 + status="unavailable" must degrade to None so the leverage brake never
    # reads an outage as a confirmed all-clear.
    assert ltm.engine_block(
        {**_SENT, "margin_debt": {"score": 0, "status": "unavailable", "level": None}}
    )["margin_debt"]["elevated"] is None
    assert ltm.engine_block(
        {**_SENT, "margin_debt": {"score": 0, "status": "ok", "level": 1}}
    )["margin_debt"]["elevated"] is False   # explicit ok status still computes from score


def test_engine_block_never_raises_on_garbage():
    for bad in ({}, {"vix": None, "fear_greed": [], "margin_debt": "x"}, {"combined_score": None}):
        blk = ltm.engine_block(bad)
        assert blk["bias"] == "NEUTRAL"
        assert blk["fear_greed"]["label"] is None
        assert set(blk) >= {"vix", "fear_greed", "margin_debt", "combined_score", "bias"}


# ── Fear & Greed fetcher: CNN graphdata API (rebuilt to bypass 418) ────────────

class _Resp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload or {}
    def json(self):
        return self._payload


def test_fear_greed_uses_graphdata_with_browser_headers(monkeypatch, tmp_path):
    """The one live source is CNN's graphdata API — hit with full browser headers
    (it 418s bare agents). Returns a {date, fear_greed} frame from the series."""
    monkeypatch.setattr(ltm, "F_G_CACHE", tmp_path / "fg.json")   # cold cache
    seen = {}
    def _get(url, headers=None, timeout=None):
        seen["url"] = url
        seen["headers"] = headers or {}
        return _Resp(200, {"fear_and_greed_historical": {"data": [
            {"x": 1_700_000_000_000, "y": 55.0}, {"x": 1_700_086_400_000, "y": 62.0}]}})
    monkeypatch.setattr(ltm.requests, "get", _get)
    df = ltm.fetch_fear_greed()
    assert df is not None and list(df.columns) == ["date", "fear_greed"]
    assert df["fear_greed"].iloc[-1] == 62.0
    assert "dataviz.cnn.io" in seen["url"]
    assert "User-Agent" in seen["headers"] and "Chrome" in seen["headers"]["User-Agent"]


def test_fear_greed_418_returns_none_not_stale(monkeypatch, tmp_path):
    """A bot-block / failure returns None (honest no-data), never a stale frame."""
    monkeypatch.setattr(ltm, "F_G_CACHE", tmp_path / "fg.json")
    monkeypatch.setattr(ltm.requests, "get", lambda *a, **k: _Resp(418))
    assert ltm.fetch_fear_greed() is None
