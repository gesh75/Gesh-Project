"""Gemini failover token floor (PR #320 leftover).

``gemini_agent._generate`` defaults to 1024 output tokens — enough for news
scoring, not a holdings card. ``call_gemini`` floors at 4096 and defaults a
missing budget to 8192 so a failover cannot silently truncate JSON.
"""

from __future__ import annotations

import json
import sys

import pytest

import llm_failover


@pytest.fixture
def gemini_sink(monkeypatch):
    seen: dict = {}

    def fake_generate(
        prompt, model="", max_tokens=1024, response_mime_type=None, timeout_s=None, **k
    ):
        seen["max_tokens"] = max_tokens
        seen["mime"] = response_mime_type
        seen["model"] = model
        return json.dumps({"portfolio_score": 71, "portfolio_verdict": "HOLD"})

    stub = type("Agent", (), {"_generate": staticmethod(fake_generate)})()
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test-not-a-real-key")
    monkeypatch.setitem(sys.modules, "gemini_agent", stub)
    return seen


def test_call_gemini_legacy_ticker_budget_is_floored_at_4096(gemini_sink):
    llm_failover.call_gemini("analyze", max_tokens=2000)
    assert gemini_sink["max_tokens"] == 4096
    assert gemini_sink["mime"] == "application/json"


def test_call_gemini_missing_or_non_positive_budget_defaults_to_8192(gemini_sink):
    llm_failover.call_gemini("analyze")
    assert gemini_sink["max_tokens"] == 8192
    llm_failover.call_gemini("analyze", max_tokens=0)
    assert gemini_sink["max_tokens"] == 8192
    llm_failover.call_gemini("analyze", max_tokens=-5)
    assert gemini_sink["max_tokens"] == 8192


def test_call_gemini_deep_analysis_budget_is_not_clamped_down(gemini_sink):
    llm_failover.call_gemini("analyze", max_tokens=24000)
    assert gemini_sink["max_tokens"] == 24000


def test_gemini_out_tokens_rejects_bool_nan_inf_and_overflow():
    """JSON true / inf used to crash ``int()`` or become a 1-token budget."""
    assert llm_failover._gemini_out_tokens(True) == 8192
    assert llm_failover._gemini_out_tokens(False) == 8192
    assert llm_failover._gemini_out_tokens(float("nan")) == 8192
    assert llm_failover._gemini_out_tokens(float("inf")) == 8192
    assert llm_failover._gemini_out_tokens(float("-inf")) == 8192
    assert llm_failover._gemini_out_tokens(10**10000) == 8192
    assert llm_failover._gemini_out_tokens("n/a") == 8192


def test_call_gemini_poisoned_budget_defaults_to_8192(gemini_sink):
    llm_failover.call_gemini("analyze", max_tokens=True)
    assert gemini_sink["max_tokens"] == 8192
    llm_failover.call_gemini("analyze", max_tokens=float("inf"))
    assert gemini_sink["max_tokens"] == 8192
