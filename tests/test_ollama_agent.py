"""Ollama fail-soft — a local-LLM outage must never invent NEUTRAL / 0.

`ollama_news_sentiment` empty-headline and parse-error honesty already live
in test_bugfix_batch_2026_08.py. These are the remaining wrappers: status,
quick analysis, second opinion, FAST→DEFAULT hop, and `_generate` transport.
A regression that treated ConnectionError as score 0 would vote NEUTRAL
into consensus Node NEWS.
"""

from __future__ import annotations

import json
import urllib.error

import pytest

import ollama_agent as oa


class _Resp:
    def __init__(self, payload, status=200):
        self.status = status
        self._raw = json.dumps(payload).encode()

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


def test_is_ollama_available_exception_is_false(monkeypatch):
    def _boom(*_a, **_k):
        raise OSError("refused")

    monkeypatch.setattr(oa.urllib.request, "urlopen", _boom)
    assert oa.is_ollama_available() is False
    assert oa.is_ollama_available("llama3.2:3b") is False


def test_is_ollama_available_non_200_is_false(monkeypatch):
    monkeypatch.setattr(
        oa.urllib.request, "urlopen", lambda *_a, **_k: _Resp({"models": []}, status=503)
    )
    assert oa.is_ollama_available() is False


def test_is_ollama_available_model_prefix_and_miss(monkeypatch):
    payload = {"models": [{"name": "llama3.2:3b"}, {"name": "mistral:7b"}]}
    monkeypatch.setattr(oa.urllib.request, "urlopen", lambda *_a, **_k: _Resp(payload))
    assert oa.is_ollama_available() is True
    assert oa.is_ollama_available("llama3.2:1b") is True  # prefix match
    assert oa.is_ollama_available("phi3:mini") is False


def test_get_ollama_status_down_is_not_ready(monkeypatch):
    def _boom(*_a, **_k):
        raise OSError("down")

    monkeypatch.setattr(oa.urllib.request, "urlopen", _boom)
    st = oa.get_ollama_status()
    assert st["running"] is False
    assert st["ready_for_gesh"] is False
    assert st["models"] == []
    assert "step1" in st["setup_guide"]


def test_get_ollama_status_llama_is_ready(monkeypatch):
    payload = {
        "models": [
            {"name": "llama3.2:3b", "size": 2_000_000_000, "modified_at": "2026-09-01T00:00:00"},
        ]
    }
    monkeypatch.setattr(oa.urllib.request, "urlopen", lambda *_a, **_k: _Resp(payload))
    st = oa.get_ollama_status()
    assert st["running"] is True
    assert st["ready_for_gesh"] is True
    assert st["models"][0]["name"] == "llama3.2:3b"
    assert st["models"][0]["size_gb"] == 2.0
    assert st["models"][0]["modified"] == "2026-09-01"


def test_get_ollama_status_unknown_family_is_not_ready(monkeypatch):
    payload = {"models": [{"name": "custom-finetune:latest", "size": 1, "modified_at": ""}]}
    monkeypatch.setattr(oa.urllib.request, "urlopen", lambda *_a, **_k: _Resp(payload))
    st = oa.get_ollama_status()
    assert st["running"] is True
    assert st["ready_for_gesh"] is False


def test_quick_analysis_unavailable_has_no_commentary(monkeypatch):
    monkeypatch.setattr(oa, "is_ollama_available", lambda *_a, **_k: False)
    out = oa.ollama_quick_analysis("NVDA 10%")
    assert out["available"] is False
    assert "analysis" not in out
    assert "error" in out


def test_quick_analysis_generate_error_is_unavailable(monkeypatch):
    monkeypatch.setattr(oa, "is_ollama_available", lambda *_a, **_k: True)
    monkeypatch.setattr(
        oa, "_generate", lambda *_a, **_k: (_ for _ in ()).throw(ConnectionError("x"))
    )
    out = oa.ollama_quick_analysis("book")
    assert out["available"] is False
    assert "analysis" not in out


def test_quick_analysis_success_shape(monkeypatch):
    monkeypatch.setattr(oa, "is_ollama_available", lambda *_a, **_k: True)
    monkeypatch.setattr(oa, "_generate", lambda *_a, **_k: "line1\nline2\nline3")
    out = oa.ollama_quick_analysis("book")
    assert out["available"] is True
    assert out["analysis"] == "line1\nline2\nline3"
    assert out["type"] == "quick_commentary"


def test_second_opinion_unavailable_and_generate_fail(monkeypatch):
    monkeypatch.setattr(oa, "is_ollama_available", lambda *_a, **_k: False)
    out = oa.ollama_second_opinion("NVDA", "bullish", verdict="STRONG BUY")
    assert out["available"] is False
    assert "second_opinion" not in out

    monkeypatch.setattr(oa, "is_ollama_available", lambda *_a, **_k: True)
    monkeypatch.setattr(
        oa, "_generate", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("oops"))
    )
    out = oa.ollama_second_opinion("NVDA", "bullish")
    assert out["available"] is False
    assert "second_opinion" not in out


def test_second_opinion_success_keeps_symbol(monkeypatch):
    monkeypatch.setattr(oa, "is_ollama_available", lambda *_a, **_k: True)
    monkeypatch.setattr(oa, "_generate", lambda *_a, **_k: "valuation risk")
    out = oa.ollama_second_opinion("nvda", "thesis", verdict="BUY")
    assert out["available"] is True
    assert out["symbol"] == "nvda"
    assert out["second_opinion"] == "valuation risk"
    assert out["type"] == "contrarian_review"


def test_news_sentiment_fast_model_falls_back_to_default(monkeypatch):
    seen: list[str] = []

    def _avail(model=None):
        return model == oa.DEFAULT_MODEL

    def _gen(_prompt, model, **_k):
        seen.append(model)
        return '{"score": 40, "signal": "BULLISH", "reason": "beat"}'

    monkeypatch.setattr(oa, "is_ollama_available", _avail)
    monkeypatch.setattr(oa, "_generate", _gen)
    out = oa.ollama_news_sentiment("AAPL", ["Earnings beat"])
    assert out["available"] is True
    assert seen == [oa.DEFAULT_MODEL]
    assert out["model"] == oa.DEFAULT_MODEL
    assert out["score"] == 40
    assert out["signal"] == "BULLISH"


def test_news_sentiment_both_models_down_has_no_score(monkeypatch):
    monkeypatch.setattr(oa, "is_ollama_available", lambda *_a, **_k: False)
    out = oa.ollama_news_sentiment("AAPL", ["Earnings beat"])
    assert out["available"] is False
    assert "score" not in out


def test_news_sentiment_generate_error_has_no_score(monkeypatch):
    monkeypatch.setattr(oa, "is_ollama_available", lambda *_a, **_k: True)
    monkeypatch.setattr(
        oa, "_generate", lambda *_a, **_k: (_ for _ in ()).throw(ConnectionError("x"))
    )
    out = oa.ollama_news_sentiment("AAPL", ["Earnings beat"])
    assert out["available"] is False
    assert "score" not in out


def test_portfolio_summary_unavailable_does_not_format_dollars(monkeypatch):
    monkeypatch.setattr(oa, "is_ollama_available", lambda *_a, **_k: False)
    out = oa.ollama_portfolio_summary(
        [{"symbol": "NVDA", "equity": 1000, "portfolio_percentage": 50, "percent_change": 2}],
        {"portfolio_equity": 2000},
    )
    assert out["available"] is False
    assert "analysis" not in out


def test_generate_urlerror_is_connection_error(monkeypatch):
    def _boom(*_a, **_k):
        raise urllib.error.URLError("refused")

    monkeypatch.setattr(oa.urllib.request, "urlopen", _boom)
    with pytest.raises(ConnectionError, match="Ollama unreachable"):
        oa._generate("hi")


def test_generate_reads_response_text(monkeypatch):
    monkeypatch.setattr(
        oa.urllib.request,
        "urlopen",
        lambda *_a, **_k: _Resp({"response": "  hello  "}),
    )
    assert oa._generate("hi") == "hello"
