"""Recent-News card fix — /api/news/{symbol} cached-article fallback.

The owner-only "Recent News" card (dashboard analyzeTicker) is fed by
GET /api/news/{symbol}. Its live feeds are dead on the owner box (Alpaca is
key-gated, the RSS feeds are blocked on the VPN), so the card was permanently
empty even though the news agent's cached news_sentiment rows retain the
articles Claude scored (analysis_json["articles"]).

Fix under test:
  * news_agent.get_cached_articles(symbol) — read-only, no-LLM extraction of
    the stored articles, allow-listed to the 4 public headline fields.
  * analyze_news_sentiment retains the fetched articles on its failure paths
    so cache rows written during an outage still feed the card later.
  * /api/news/{symbol} serves the cached articles (source="cache") when every
    live feed comes back empty — and stays fail-soft + allow-listed.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from starlette.testclient import TestClient

import news_agent
import server

_ALLOWED_KEYS = {"title", "url", "source", "published"}


def _sqlite_ts(days_ago: float = 0.0) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


@pytest.fixture
def agent_db(tmp_path, monkeypatch):
    """Point the agent DB at a throwaway file with a seeded news_sentiment table."""
    db_path = tmp_path / "agents.db"
    monkeypatch.setenv("GESH_DB_PATH", str(db_path))
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE news_sentiment (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            overall_sentiment TEXT,
            sentiment_score INTEGER,
            impact_level TEXT,
            analysis_json TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    conn.close()
    return db_path


def _insert_row(db_path, symbol: str, articles: list[dict], days_ago: float = 0.0) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO news_sentiment (symbol, overall_sentiment, sentiment_score,"
        " impact_level, analysis_json, created_at) VALUES (?,?,?,?,?,?)",
        (symbol, "POSITIVE", 40, "MEDIUM",
         json.dumps({"article_count": len(articles), "articles": articles}),
         _sqlite_ts(days_ago)),
    )
    conn.commit()
    conn.close()


def _article(title: str = "MSTR adds more bitcoin") -> dict:
    return {"title": title, "url": "https://example.com/a",
            "source": "Yahoo Finance", "published": "2026-07-16",
            "description": "long body text that must never ship"}


# ─── news_agent.get_cached_articles ──────────────────────────────────────────

def test_cached_articles_are_allow_listed(agent_db):
    _insert_row(agent_db, "MSTR", [_article()])
    rows = news_agent.get_cached_articles("MSTR")
    assert rows == [{"title": "MSTR adds more bitcoin", "url": "https://example.com/a",
                     "source": "Yahoo Finance", "published": "2026-07-16"}]
    for r in rows:
        assert set(r) <= _ALLOWED_KEYS


def test_cached_articles_empty_without_rows(agent_db):
    assert news_agent.get_cached_articles("MSTR") == []


def test_cached_articles_empty_when_db_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("GESH_DB_PATH", str(tmp_path / "nope.db"))
    assert news_agent.get_cached_articles("MSTR") == []


def test_cached_articles_respect_age_window(agent_db):
    _insert_row(agent_db, "MSTR", [_article()], days_ago=10)
    assert news_agent.get_cached_articles("MSTR", days=7) == []


def test_cached_articles_dedupe_and_limit(agent_db):
    _insert_row(agent_db, "MSTR", [_article("older headline")], days_ago=1)
    _insert_row(agent_db, "MSTR", [_article("fresh headline"), _article("older headline")])
    rows = news_agent.get_cached_articles("MSTR", limit=2)
    assert [r["title"] for r in rows] == ["fresh headline", "older headline"]
    assert news_agent.get_cached_articles("MSTR", limit=1) == rows[:1]


def test_cached_articles_skip_untitled_and_other_symbols(agent_db):
    _insert_row(agent_db, "MSTR", [_article(""), {"url": "https://x/y"}])
    _insert_row(agent_db, "SPY", [_article("SPY only")])
    assert news_agent.get_cached_articles("MSTR") == []


def test_cached_articles_never_raise(monkeypatch):
    def _boom():
        raise RuntimeError("db down")
    monkeypatch.setattr("agent_db.agent_db_path", _boom)
    assert news_agent.get_cached_articles("MSTR") == []


# ─── analyze_news_sentiment retains articles on failure paths ────────────────

def test_error_path_retains_allow_listed_articles(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    class _Boom:
        def __init__(self, api_key):
            raise RuntimeError("api down")
    monkeypatch.setattr(news_agent.anthropic, "Anthropic", _Boom)

    result = news_agent.analyze_news_sentiment("MSTR", [_article()])
    assert result["articles"] == [{"title": "MSTR adds more bitcoin",
                                   "url": "https://example.com/a",
                                   "source": "Yahoo Finance",
                                   "published": "2026-07-16"}]


def test_no_key_path_retains_allow_listed_articles(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = news_agent.analyze_news_sentiment("MSTR", [_article()])
    assert result["articles"] and set(result["articles"][0]) <= _ALLOWED_KEYS


# ─── /api/news/{symbol} fallback wiring ──────────────────────────────────────

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: True)
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: "cust@x.com")
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda p, c: "allow", raising=False)
    monkeypatch.setattr(server.auth_users, "is_owner", lambda e: False, raising=False)
    # These tests use MSTR, a REAL symbol — give it market identity so empty news
    # is a legitimate "no recent news" (status ok), not GATE-2 symbol_not_found.
    monkeypatch.setattr(server, "_cached_close", lambda s: 100.0, raising=True)
    c = TestClient(server.app, raise_server_exceptions=False)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    return c


def _stub_live_feeds(monkeypatch, items):
    monkeypatch.setattr(news_agent, "_fetch_alpaca_news", lambda s: items, raising=False)
    monkeypatch.setattr(news_agent, "_fetch_all_news", lambda s: [], raising=False)


def test_api_news_serves_cached_articles_when_live_feeds_empty(client, monkeypatch):
    _stub_live_feeds(monkeypatch, [])
    monkeypatch.setattr(news_agent, "get_cached_articles",
                        lambda sym, days=7, limit=8: [_article("Cached headline")],
                        raising=False)
    r = client.get("/api/news/MSTR")
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "cache"
    assert [it["title"] for it in body["data"]] == ["Cached headline"]
    for it in body["data"]:                       # trim allow-list still applies
        assert set(it) <= _ALLOWED_KEYS
    assert "description" not in r.text


def test_api_news_live_feed_wins_over_cache(client, monkeypatch):
    _stub_live_feeds(monkeypatch, [{"title": "Live headline", "url": "https://x/a",
                                    "source": "benzinga", "published": "p"}])

    def _never(sym, days=7, limit=8):
        raise AssertionError("cache must not be read when a live feed served")
    monkeypatch.setattr(news_agent, "get_cached_articles", _never, raising=False)
    r = client.get("/api/news/MSTR")
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "alpaca"
    assert body["data"][0]["title"] == "Live headline"


def test_api_news_cache_fallback_is_fail_soft(client, monkeypatch):
    _stub_live_feeds(monkeypatch, [])

    def _boom(sym, days=7, limit=8):
        raise RuntimeError("db down")
    monkeypatch.setattr(news_agent, "get_cached_articles", _boom, raising=False)
    r = client.get("/api/news/MSTR")
    assert r.status_code == 200 and r.json()["data"] == []
    assert "source" not in r.json()
