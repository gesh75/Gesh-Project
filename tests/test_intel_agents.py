"""Company Intelligence agents — cache skip + per-run caps."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

import news_agent as na
import sec_agent as sa


@pytest.fixture
def agent_db(tmp_path, monkeypatch):
    import agent_db

    path = tmp_path / "gesh_financial.db"
    monkeypatch.setattr(agent_db, "agent_db_path", lambda: path)
    return path


def test_sec_skips_already_analyzed_filing(agent_db, monkeypatch):
    url = "https://www.sec.gov/example-filing"
    conn = sqlite3.connect(agent_db)
    conn.execute(
        """CREATE TABLE sec_filings (
            id INTEGER PRIMARY KEY, symbol TEXT, form_type TEXT, filing_date TEXT,
            filing_url TEXT, analysis_json TEXT, action_signal TEXT, urgency TEXT,
            created_at TEXT DEFAULT (datetime('now')))"""
    )
    conn.execute(
        "INSERT INTO sec_filings (symbol, form_type, filing_date, filing_url, analysis_json, action_signal, urgency) VALUES (?,?,?,?,?,?,?)",
        ("NVDA", "8-K", "2026-07-01", url, "{}", "NEUTRAL", "LOW"),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(sa, "_get_cik", lambda s: "0001045810")
    monkeypatch.setattr(
        sa,
        "_fetch_recent_filings",
        lambda c, f, days_back=7: [
            {
                "title": "8-K",
                "url": url,
                "date": "2026-07-01",
                "form": f,
            }
        ],
    )
    monkeypatch.setattr(
        sa, "_analyze_filing", lambda *a, **k: pytest.fail("should not call Claude")
    )
    monkeypatch.setattr(
        sa, "_get_recent_from_db", lambda s, days=7: [{"symbol": "NVDA", "form_type": "8-K"}]
    )

    out = sa.watch_filings(["NVDA"], days_back=7)
    assert out["new_filings"] == 0
    assert out["skipped_cached"] >= 1
    assert len(out["results"]) >= 1


def test_news_reuses_fresh_db_sentiment(agent_db, monkeypatch):
    conn = sqlite3.connect(agent_db)
    conn.execute(
        """CREATE TABLE news_sentiment (
            id INTEGER PRIMARY KEY, symbol TEXT, overall_sentiment TEXT,
            sentiment_score INTEGER, impact_level TEXT, analysis_json TEXT,
            created_at TEXT)"""
    )
    ts = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO news_sentiment (symbol, overall_sentiment, sentiment_score, impact_level, analysis_json, created_at) VALUES (?,?,?,?,?,?)",
        ("AAPL", "POSITIVE", 42, "MEDIUM", json.dumps({"article_count": 3}), ts),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(na, "_fetch_all_news", lambda *a, **k: pytest.fail("should not fetch RSS"))
    monkeypatch.setattr(
        na, "analyze_news_sentiment", lambda *a, **k: pytest.fail("should not call Claude")
    )
    import regime

    monkeypatch.setattr(regime, "get_macro_context", lambda: {"macro_signal": "NEUTRAL"})

    out = na.scan_news(["AAPL"])
    assert out["skipped_cached"] == 1
    assert out["results"][0]["symbol"] == "AAPL"
    assert out["results"][0]["sentiment_score"] == 42
