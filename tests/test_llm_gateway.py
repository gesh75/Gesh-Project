from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
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


def test_max_attempts_above_three_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GESH_LLM_MAX_ATTEMPTS", "4")
    with pytest.raises(llm_gateway.LLMConfigurationError, match="GESH_LLM_MAX_ATTEMPTS"):
        llm_gateway.max_attempts_from_env()


class _Usage:
    def __init__(
        self,
        input_tokens: int = 10,
        output_tokens: int = 5,
        cache_read_input_tokens: int = 0,
        cache_creation_input_tokens: int = 0,
    ) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_input_tokens = cache_read_input_tokens
        self.cache_creation_input_tokens = cache_creation_input_tokens


class _FakeMessage:
    def __init__(
        self,
        *,
        stop_reason: str | None = "end_turn",
        usage: _Usage | None = None,
        request_id: str | None = "req_test",
    ) -> None:
        self.content = [SimpleNamespace(text="ok")]
        self.stop_reason = stop_reason
        self.usage = usage or _Usage()
        self._request_id = request_id
        self.id = "msg_test"


class _FakeStreamContext:
    def __init__(self, fn) -> None:
        self._fn = fn

    def __enter__(self) -> "_FakeStreamContext":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def get_final_message(self) -> Any:
        return self._fn()


class _AlwaysTimeoutMessages:
    def stream(self, **kwargs: Any) -> _FakeStreamContext:
        def _boom() -> Any:
            raise httpx.ReadTimeout("read timed out")

        return _FakeStreamContext(_boom)

    def create(self, **kwargs: Any) -> Any:
        raise httpx.ReadTimeout("read timed out")


class _AlwaysTimeoutClient:
    def __init__(self) -> None:
        self.messages = _AlwaysTimeoutMessages()


class _SequenceMessages:
    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls = 0

    def create(self, **kwargs: Any) -> Any:
        self.calls += 1
        item = self._responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def stream(self, **kwargs: Any) -> _FakeStreamContext:
        self.calls += 1
        item = self._responses.pop(0)

        def _get() -> Any:
            if isinstance(item, BaseException):
                raise item
            return item

        return _FakeStreamContext(_get)


class _SequenceClient:
    def __init__(self, responses: list[Any]) -> None:
        self.messages = _SequenceMessages(responses)


@pytest.fixture
def gateway(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")
    monkeypatch.setenv("LLM_DAILY_BUDGET_USD", "0")
    monkeypatch.setattr(llm_gateway, "backoff_seconds", lambda *a, **k: 0)
    db = tmp_path / "llm_usage.db"
    return llm_gateway.Gateway(db_path=db, budget_usd=0.0, max_attempts=3)


def test_stream_retries_three_times_and_keeps_unknown_cost_reservations(
    gateway: llm_gateway.Gateway,
) -> None:
    gw = llm_gateway.Gateway(
        db_path=gateway.db_path,
        client_factory=lambda: _AlwaysTimeoutClient(),
        budget_usd=0.0,
        max_attempts=3,
    )
    with pytest.raises(
        llm_gateway.LLMUnavailableError, match="AI provider temporarily unavailable"
    ):
        gw.stream(
            operation="deep_analyze_portfolio",
            model="claude-sonnet-4-6",
            max_tokens=100,
            messages=[{"role": "user", "content": "x"}],
        )
    summary = gw.daily_usage_summary(days=1)
    assert summary["attempt_count"] == 3
    assert summary["unknown_charge_reservations"] == 3


def test_budget_exhaustion_denies_further_attempt(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(llm_gateway, "backoff_seconds", lambda *a, **k: 0)
    # Tiny budget: one Sonnet reservation (50k in + 100 out) exceeds $0.001.
    reserved = llm_gateway.reserve_cost_microusd("claude-sonnet-4-6", 100)
    budget_usd = (reserved / 1_000_000) * 0.5  # half of one reservation
    msg = _FakeMessage(usage=_Usage(1, 1))
    client = _SequenceClient([msg])
    gw = llm_gateway.Gateway(
        db_path=tmp_path / "budget.db",
        client_factory=lambda: client,
        budget_usd=budget_usd,
        max_attempts=1,
    )
    with pytest.raises(llm_gateway.LLMBudgetExceededError):
        gw.create(
            operation="analyze_portfolio",
            model="claude-sonnet-4-6",
            max_tokens=100,
            messages=[{"role": "user", "content": "x"}],
        )
    summary = gw.daily_usage_summary(days=1)
    assert summary["attempt_count"] == 0


def test_refusal_stop_reason_raises(gateway: llm_gateway.Gateway) -> None:
    msg = _FakeMessage(stop_reason="refusal", usage=_Usage(2, 1))
    gw = llm_gateway.Gateway(
        db_path=gateway.db_path,
        client_factory=lambda: _SequenceClient([msg]),
        budget_usd=0.0,
        max_attempts=1,
    )
    with pytest.raises(
        llm_gateway.LLMRefusalError,
        match="AI research could not be completed for this request",
    ):
        gw.create(
            operation="deep_analyze_ticker",
            model="claude-haiku-4-5",
            max_tokens=50,
            messages=[{"role": "user", "content": "x"}],
        )
    summary = gw.daily_usage_summary(days=1)
    assert summary["refusals"] == 1


def test_max_tokens_stop_reason_raises(gateway: llm_gateway.Gateway) -> None:
    msg = _FakeMessage(stop_reason="max_tokens", usage=_Usage(3, 50))
    gw = llm_gateway.Gateway(
        db_path=gateway.db_path,
        client_factory=lambda: _SequenceClient([msg]),
        budget_usd=0.0,
        max_attempts=1,
    )
    with pytest.raises(
        llm_gateway.LLMTruncatedError,
        match="AI response exceeded its configured output limit",
    ):
        gw.create(
            operation="analyze_screener",
            model="claude-haiku-4-5",
            max_tokens=50,
            messages=[{"role": "user", "content": "x"}],
        )
    summary = gw.daily_usage_summary(days=1)
    assert summary["truncations"] == 1


def test_success_settles_tokens(gateway: llm_gateway.Gateway) -> None:
    msg = _FakeMessage(usage=_Usage(input_tokens=100, output_tokens=20, cache_read_input_tokens=5))
    gw = llm_gateway.Gateway(
        db_path=gateway.db_path,
        client_factory=lambda: _SequenceClient([msg]),
        budget_usd=0.0,
        max_attempts=1,
    )
    out = gw.create(
        operation="news_sentiment",
        model="claude-haiku-4-5",
        max_tokens=50,
        messages=[{"role": "user", "content": "x"}],
    )
    assert out is msg
    summary = gw.daily_usage_summary(days=1)
    assert summary["succeeded"] == 1
    assert summary["attempt_count"] == 1
    assert summary["cache_read_tokens"] == 5
    assert summary["total_estimated_spend_usd"] > 0
    expected = llm_gateway.estimate_cost_microusd("claude-haiku-4-5", 100, 20)
    assert summary["total_estimated_spend_microusd"] == expected
