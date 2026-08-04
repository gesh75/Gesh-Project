"""
Offline unit tests for Anthropic prompt caching in ai_analyzer.py.

These tests never touch the network (the autouse `_no_network` guard in
conftest.py hard-fails any real socket). A fake Anthropic client is injected
so we can inspect the exact request shape and feed a deterministic response.

We assert two independent things per analyzer:

  (a) the request marks a LARGE STATIC block with cache_control type
      'ephemeral' (the system prompt, and for tool-use calls the tool schema),
      so repeat calls hit the prompt cache and input-token cost drops; and

  (b) the analyzer still parses a correct result out of a mocked response,
      proving the change is request-shape only (no output behavior change).
"""

from __future__ import annotations

from typing import Any

import pytest

import ai_analyzer

# ─── Fakes ───────────────────────────────────────────────────────────────────


class _TextBlock:
    """Mimics a plain-text content block from a Messages response."""

    def __init__(self, text: str) -> None:
        self.text = text


class _ToolBlock:
    """Mimics a tool_use content block (carries parsed `.input`)."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.type = "tool_use"  # _extract_tool_input() now iterates for type=="tool_use"
        self.input = payload


class _FakeMessage:
    def __init__(self, content: list[Any]) -> None:
        self.content = content


class _FakeStreamContext:
    """Mimics the context manager anthropic.messages.stream() returns."""

    def __init__(self, response: _FakeMessage) -> None:
        self._response = response

    def __enter__(self) -> "_FakeStreamContext":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        return None

    def get_final_message(self) -> _FakeMessage:
        return self._response


class _FakeMessages:
    def __init__(self, response: _FakeMessage, sink: dict[str, Any]) -> None:
        self._response = response
        self._sink = sink

    def create(self, **kwargs: Any) -> _FakeMessage:
        # Capture the outgoing request for inspection.
        self._sink.clear()
        self._sink.update(kwargs)
        return self._response

    def stream(self, **kwargs: Any) -> _FakeStreamContext:
        # ai_analyzer._create_streaming() drives deep_analyze_portfolio through
        # client.messages.stream(...).get_final_message() (2026-07-27, dodges a
        # wall-clock timeout on long generations) — mirror create()'s capture.
        self._sink.clear()
        self._sink.update(kwargs)
        return _FakeStreamContext(self._response)


class _FakeClient:
    def __init__(self, response: _FakeMessage, sink: dict[str, Any]) -> None:
        self.messages = _FakeMessages(response, sink)


def _install_fake_client(
    monkeypatch: pytest.MonkeyPatch,
    response: _FakeMessage,
) -> dict[str, Any]:
    """Patch the Anthropic constructor; return the dict that records kwargs."""
    import llm_gateway

    sink: dict[str, Any] = {}
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")
    monkeypatch.setattr(
        llm_gateway.anthropic,
        "Anthropic",
        lambda *a, **k: _FakeClient(response, sink),
    )
    return sink


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _has_ephemeral_cache(blocks: Any) -> bool:
    """True if any content-block in `blocks` carries cache_control ephemeral."""
    if not isinstance(blocks, list):
        return False
    return any(
        isinstance(b, dict) and b.get("cache_control", {}).get("type") == "ephemeral"
        for b in blocks
    )


# ─── analyze_portfolio (plain text) ──────────────────────────────────────────


def test_analyze_portfolio_caches_system_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _FakeMessage([_TextBlock("RISK SCORE: 4/10")])
    sink = _install_fake_client(monkeypatch, response)

    result = ai_analyzer.analyze_portfolio({"equity": 1000})

    # (a) system prompt is a content-block list with an ephemeral cache marker.
    assert _has_ephemeral_cache(sink["system"]), sink["system"]
    # The cached block must carry the real static prompt text (stable prefix).
    assert sink["system"][0]["text"] == ai_analyzer.SYSTEM_PROMPT
    # Dynamic user content must NOT be cached.
    assert isinstance(sink["messages"][0]["content"], str)

    # (b) output still parsed correctly from the mocked response.
    assert result == "RISK SCORE: 4/10"


# ─── deep_analyze_portfolio (tool use / JSON) ────────────────────────────────


def test_deep_analyze_caches_system_and_tool_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    parsed = {"portfolio_score": 72, "portfolio_verdict": "balanced"}
    response = _FakeMessage([_ToolBlock(parsed)])
    sink = _install_fake_client(monkeypatch, response)

    result = ai_analyzer.deep_analyze_portfolio({"holdings": [{"symbol": "AAPL"}]})

    # (a) system prompt cached as an ephemeral content block...
    assert _has_ephemeral_cache(sink["system"]), sink["system"]
    assert sink["system"][0]["text"] == ai_analyzer.DEEP_SYSTEM_PROMPT
    # ...and the large static tool schema is also cache-tagged.
    assert sink["tools"][-1]["cache_control"]["type"] == "ephemeral"
    # Schema content is unchanged (no behavior change).
    assert sink["tools"][0]["input_schema"] is ai_analyzer.DEEP_ANALYSIS_SCHEMA

    # (b) parsed tool output is returned verbatim.
    assert result == parsed


# ─── analyze_screener (tool use / JSON) ──────────────────────────────────────


def test_analyze_screener_caches_system_and_tool_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    parsed = {"screen_summary": "ok", "market_bias": "neutral", "top_sectors": [], "picks": []}
    response = _FakeMessage([_ToolBlock(parsed)])
    sink = _install_fake_client(monkeypatch, response)

    result = ai_analyzer.analyze_screener([{"symbol": "NVDA"}])

    # (a) ephemeral cache on system prompt and tool schema.
    assert _has_ephemeral_cache(sink["system"]), sink["system"]
    assert sink["system"][0]["text"] == ai_analyzer.SCREENER_SYSTEM_PROMPT
    assert sink["tools"][-1]["cache_control"]["type"] == "ephemeral"
    assert sink["tools"][0]["input_schema"] is ai_analyzer.SCREENER_SCHEMA

    # (b) parsed result preserved.
    assert result == parsed


# ─── Sanity: model selection unchanged (pure caching, not routing) ───────────


def test_model_selection_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _FakeMessage([_TextBlock("ok")])
    sink = _install_fake_client(monkeypatch, response)

    ai_analyzer.analyze_portfolio({"equity": 1})

    assert sink["model"] == ai_analyzer.MODEL
