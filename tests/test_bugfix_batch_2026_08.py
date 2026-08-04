"""Regression tests for the 2026-08 bug-fix batch (deep-research pass).

Each test pins one concrete defect found in the audit so it cannot silently
regress. All tests are fully offline (no network / broker / LLM).

Fixes covered:
  1. Customer model context must not leak the owner's Robinhood watchlist.
  2. gemini_news_sentiment must not fabricate NEUTRAL/0 (empty + provider fail).
  3. ollama_news_sentiment must not fabricate NEUTRAL/0 (empty + parse fail).
  4. send_magic_link must HTML-escape the tier + link it embeds.
  5. /pricing must HTML-escape the operator-set checkout URL.
  6. _ticker_get_cached must hand callers a copy, never the shared cached dict.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

REPO = Path(__file__).resolve().parents[1]


# ── 1. Owner watchlist isolation ─────────────────────────────────────────────

def test_customer_model_context_omits_owner_watchlist():
    import model_portfolio

    owner_payload = {
        "holdings": [
            {"symbol": "MSFT", "quantity": 2, "current_price": 100.0,
             "equity": 200.0, "portfolio_percentage": 100.0, "type": "stock"},
        ],
        "equity_summary": {"cash": 50.0},
        # Symbols the operator only WATCHES (does not hold) — private.
        "watchlist": [{"symbol": "SECRETCO"}, {"symbol": "PRIVTKR"}],
    }

    ctx = model_portfolio.build_customer_model_context(owner_payload)

    assert ctx["watchlist"] == []
    # And the private tickers must not appear anywhere in the customer context.
    blob = repr(ctx)
    assert "SECRETCO" not in blob
    assert "PRIVTKR" not in blob


# ── 2. Gemini news sentiment honesty ─────────────────────────────────────────

def test_gemini_news_sentiment_empty_articles_is_unavailable():
    import gemini_agent

    result = gemini_agent.gemini_news_sentiment("AAPL", [])

    assert result["availability"] == "UNAVAILABLE"
    assert result["sentiment_score"] is None
    assert result["overall_sentiment"] is None
    assert result["article_count"] == 0


def test_gemini_news_sentiment_provider_failure_is_unavailable(monkeypatch):
    import gemini_agent

    def _boom(*args, **kwargs):
        raise RuntimeError("gemini down")

    monkeypatch.setattr(gemini_agent, "_generate", _boom)

    articles = [{"title": "X raises guidance", "source": "wire", "description": "up"}]
    result = gemini_agent.gemini_news_sentiment("AAPL", articles)

    # A provider error is not a flat/neutral reading.
    assert result["availability"] == "UNAVAILABLE"
    assert result["sentiment_score"] is None
    assert "gemini_error" in result["unavailable_reason"]


# ── 3. Ollama news sentiment honesty ─────────────────────────────────────────

def test_ollama_news_sentiment_empty_headlines_unavailable(monkeypatch):
    import ollama_agent

    # Pretend Ollama is up so we exercise the empty-headlines branch (not the
    # "not running" early return).
    monkeypatch.setattr(ollama_agent, "is_ollama_available", lambda *a, **k: True)

    result = ollama_agent.ollama_news_sentiment("AAPL", [])

    assert result["available"] is False
    assert "score" not in result  # never fabricate a numeric score


def test_ollama_news_sentiment_parse_error_unavailable(monkeypatch):
    import ollama_agent

    monkeypatch.setattr(ollama_agent, "is_ollama_available", lambda *a, **k: True)
    # Model returns non-JSON garbage → must degrade to unavailable, not NEUTRAL/0.
    monkeypatch.setattr(ollama_agent, "_generate", lambda *a, **k: "totally not json")

    result = ollama_agent.ollama_news_sentiment("AAPL", ["Big earnings beat"])

    assert result["available"] is False
    assert result["reason"] == "parse_error"
    assert "score" not in result


# ── 4. Magic-link email escaping ─────────────────────────────────────────────

def test_send_magic_link_escapes_html(monkeypatch):
    import email_sender

    captured = {}

    def _capture(to, subject, html, headers=None):
        captured["html"] = html
        return True

    monkeypatch.setattr(email_sender, "send_email", _capture)

    email_sender.send_magic_link(
        "user@example.com",
        link='https://x/login?t=abc"><script>alert(1)</script>',
        tier='desk"><img src=x onerror=alert(1)>',
    )

    html = captured["html"]
    # No raw tag openings may survive — every "<" the attacker supplied is escaped,
    # so the injected <script>/<img> become inert text, not live markup.
    assert "<script" not in html
    assert "<img" not in html
    assert "<script>alert(1)</script>" not in html
    # Escaped forms should be present instead.
    assert "&lt;script&gt;" in html
    assert "&lt;img" in html
    assert "&quot;" in html


# ── 5. Pricing page checkout-URL escaping ────────────────────────────────────

@pytest.fixture
def client(tmp_path, monkeypatch):
    import auth_users as _au

    monkeypatch.setattr(_au, "AUTH_DB_PATH", tmp_path / "auth.db")
    _au.init_auth_db()
    import server

    return TestClient(server.app)


def test_pricing_page_escapes_checkout_url(client, monkeypatch):
    # Operator-set env var with an attribute-breakout attempt.
    monkeypatch.setenv("LS_CHECKOUT_PRIMARY", 'https://evil/"><script>alert(1)</script>')
    monkeypatch.delenv("GESH_SIGNUPS_PAUSED", raising=False)

    resp = client.get("/pricing")
    assert resp.status_code == 200
    body = resp.text

    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body or "&quot;&gt;" in body


# ── 6. Ticker GET cache returns a copy ───────────────────────────────────────

def test_ticker_get_cache_returns_copy():
    import server

    server._ticker_get_store("regime", "ZZTESTSYM", {"status": "ok", "data": {"x": 1}})

    first = server._ticker_get_cached("regime", "ZZTESTSYM")
    assert first is not None
    # Mutate the returned object as a buggy handler/middleware might.
    first["data"]["x"] = 999

    second = server._ticker_get_cached("regime", "ZZTESTSYM")
    assert second["data"]["x"] == 1, "cache HIT must not be poisoned by a caller mutation"
