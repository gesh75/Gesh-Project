"""Consensus convergence Phase 5 — node_news live Reddit + cached-DB fallback.

Network-free: sentiment_connector.node_sentiment is mocked. Pins that node_news
prefers a live Reddit vote and falls back to the cached DB when Reddit ABSTAINs or
the connector errors.
"""
from __future__ import annotations

import sys
import types

import consensus


def _stub_sentiment(monkeypatch, result):
    mod = types.ModuleType("sentiment_connector")
    mod.node_sentiment = lambda symbol: result
    monkeypatch.setitem(sys.modules, "sentiment_connector", mod)


def test_news_prefers_live_reddit(monkeypatch):
    _stub_sentiment(monkeypatch, (consensus.VOTE_BULLISH, "Reddit=BULLISH Score=+0.4"))
    vote, detail = consensus.node_news("NVDA")
    assert vote == consensus.VOTE_BULLISH
    assert "Reddit" in detail


def test_news_falls_back_to_db_when_reddit_abstains(monkeypatch):
    # live Reddit ABSTAINs -> should hit the DB fallback path (no row -> ABSTAIN msg)
    _stub_sentiment(monkeypatch, (consensus.ABSTAIN, "Reddit unavailable"))
    monkeypatch.setattr(consensus, "DB_PATH", "/nonexistent/he.db")  # forces empty/err
    vote, detail = consensus.node_news("ZZZZ")
    assert vote is consensus.ABSTAIN
    assert "Reddit" not in detail or "cached" in detail.lower() or "no live" in detail.lower()


def test_news_falls_back_when_connector_errors(monkeypatch):
    mod = types.ModuleType("sentiment_connector")
    def boom(symbol):
        raise RuntimeError("connector down")
    mod.node_sentiment = boom
    monkeypatch.setitem(sys.modules, "sentiment_connector", mod)
    # connector raises -> caught -> DB fallback -> ABSTAIN (no row) — never raises out
    vote, _ = consensus.node_news("ZZZZ")
    assert vote is consensus.ABSTAIN
