from __future__ import annotations

import pytest

import llm_gateway


def test_resolve_model_alias_returns_current_canonical_id() -> None:
    assert llm_gateway.resolve_model("haiku", default="sonnet") == "claude-haiku-4-5"
    assert llm_gateway.resolve_model("claude-sonnet-4-6", default="haiku") == "claude-sonnet-4-6"


def test_resolve_model_unknown_value_fails_closed() -> None:
    with pytest.raises(llm_gateway.LLMConfigurationError, match="Unsupported Claude model"):
        llm_gateway.resolve_model("claude-haiku-4-5-20251001", default="haiku")


def test_create_client_applies_bounded_timeout_and_disables_hidden_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeAnthropic:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")
    monkeypatch.setattr(llm_gateway.anthropic, "Anthropic", FakeAnthropic)

    llm_gateway.create_client(timeout_seconds=42.0)

    assert captured == {"api_key": "test-key-not-used", "timeout": 42.0, "max_retries": 0}
