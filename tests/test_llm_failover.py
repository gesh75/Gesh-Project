"""Guard tests for the provider-independent LLM failover path.

The contract these lock down: when Anthropic is unavailable, a fallback provider
returns an object that ``llm_contracts.extract_tool_input`` accepts unchanged, so
no downstream reader (renest_analysis, fair_value, gen_report2, db.log_analysis)
needs to know a different vendor answered.
"""

from __future__ import annotations

import json
import sys
import types

import anthropic
import httpx
import pytest

import llm_contracts
import llm_failover
import llm_gateway


@pytest.fixture(autouse=True)
def _reset(monkeypatch, tmp_path):
    llm_gateway.reset_gateway()
    monkeypatch.setattr(llm_failover, "FAILOVER_COUNT", 0, raising=False)
    monkeypatch.setattr(llm_failover, "LAST_FAILOVER", None, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.delenv("GESH_LLM_SKIP_ANTHROPIC", raising=False)
    yield
    llm_gateway.reset_gateway()


def _tool(schema: dict) -> dict:
    return llm_contracts.strict_tool("return_portfolio_analysis", "d", schema)


_SCHEMA = {
    "type": "object",
    "required": ["portfolio_score", "portfolio_verdict", "risk_score", "holdings_analysis"],
    "properties": {
        "portfolio_score": {"type": "integer"},
        "portfolio_verdict": {"type": "string"},
        "risk_score": {"type": "integer"},
        "holdings_analysis": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["symbol", "verdict"],
                "properties": {"symbol": {"type": "string"}, "verdict": {"type": "string"}},
            },
        },
    },
}


def _kwargs() -> dict:
    return {
        "tools": [_tool(_SCHEMA)],
        "tool_choice": {"type": "tool", "name": "return_portfolio_analysis"},
        "system": [{"type": "text", "text": "You are an analyst."}],
        "messages": [{"role": "user", "content": "Analyze this book."}],
    }


def _install_provider(monkeypatch, text: str, name: str = "stub") -> None:
    providers = dict(llm_failover._PROVIDERS)
    providers[name] = (lambda: True, lambda prompt: text)
    monkeypatch.setattr(llm_failover, "_PROVIDERS", providers)
    monkeypatch.setenv("GESH_LLM_FAILOVER", name)


def _kill_anthropic(monkeypatch) -> None:
    def dead(*a, **k):
        raise anthropic.APIConnectionError(request=None)

    monkeypatch.setattr(llm_gateway, "create_client", dead)


# ── JSON recovery ────────────────────────────────────────────────────────────


def test_extract_json_from_markdown_fence():
    text = 'Here you go:\n```json\n{"a": 1}\n```\nDone.'
    assert llm_failover.extract_json_object(text) == {"a": 1}


def test_extract_json_ignores_braces_inside_strings():
    text = 'prefix {"note": "close } brace", "n": 2} suffix'
    assert llm_failover.extract_json_object(text) == {"note": "close } brace", "n": 2}


def test_extract_json_handles_escaped_quotes():
    text = r'{"q": "he said \"hi\" }", "z": 3}'
    assert llm_failover.extract_json_object(text)["z"] == 3


def test_extract_json_raises_on_prose_only():
    with pytest.raises(ValueError):
        llm_failover.extract_json_object("I cannot help with that.")


def test_extract_json_skips_unrelated_object_when_required_keys_given():
    """A status blob or CLI envelope must not beat the real analysis after it."""
    text = '{"ok": true, "num_turns": 1}\n{"portfolio_score": 71, "portfolio_verdict": "HOLD"}'
    got = llm_failover.extract_json_object(
        text, required_any=["portfolio_score", "portfolio_verdict"]
    )
    assert got == {"portfolio_score": 71, "portfolio_verdict": "HOLD"}


def test_extracted_payload_rejects_cli_envelope():
    envelope = {"result": None, "text": None, "num_turns": 1, "session_id": "abc"}
    assert not llm_failover.extracted_payload_matches_schema(envelope, _SCHEMA)


def test_extracted_payload_accepts_partial_analysis():
    assert llm_failover.extracted_payload_matches_schema({"portfolio_score": 64}, _SCHEMA)


# ── Schema coercion ──────────────────────────────────────────────────────────


def test_coerce_fills_missing_required_keys_with_typed_blanks():
    out = llm_failover.coerce_to_schema({"portfolio_score": 70}, _SCHEMA)
    assert out["portfolio_score"] == 70
    assert out["portfolio_verdict"] == ""
    assert out["risk_score"] is None
    assert out["holdings_analysis"] == []


def test_coerce_omitted_numeric_is_none_not_zero():
    """A missing score must not render as 0 / 100 (gap F-11)."""
    out = llm_failover.coerce_to_schema({}, _SCHEMA)
    assert out["portfolio_score"] is None
    assert out["risk_score"] is None
    assert out["portfolio_verdict"] == ""


def test_coerce_recurses_into_array_items():
    out = llm_failover.coerce_to_schema({"holdings_analysis": [{"symbol": "NVDA"}]}, _SCHEMA)
    assert out["holdings_analysis"][0] == {"symbol": "NVDA", "verdict": ""}


def test_coerce_casts_numeric_strings_to_int():
    out = llm_failover.coerce_to_schema({"portfolio_score": "87.6"}, _SCHEMA)
    assert out["portfolio_score"] == 88


def test_coerce_invalid_numeric_is_none_not_zero():
    """Garbage is not a measured 0 — same F-11 class as an omitted key."""
    out = llm_failover.coerce_to_schema({"portfolio_score": "n/a", "risk_score": "nope"}, _SCHEMA)
    assert out["portfolio_score"] is None
    assert out["risk_score"] is None


# ── Duck-typed message ───────────────────────────────────────────────────────


def test_failover_message_satisfies_extract_tool_input():
    msg = llm_failover.FailoverMessage(
        provider="grok",
        model="grok-build",
        tool_name="return_portfolio_analysis",
        payload={"portfolio_score": 71},
    )
    got = llm_contracts.extract_tool_input(
        msg, expected_tool="return_portfolio_analysis", operation="t"
    )
    assert got == {"portfolio_score": 71}


def test_failover_message_reports_zero_anthropic_usage():
    """Fallback spend is not Anthropic spend — the ledger must not be inflated."""
    msg = llm_failover.FailoverMessage(
        provider="grok", model="grok-build", tool_name="t", payload={}
    )
    assert msg.usage.input_tokens == 0
    assert msg.usage.output_tokens == 0


# ── Gateway wiring ───────────────────────────────────────────────────────────


def test_attempt_rejects_cli_envelope_and_tries_next_provider(monkeypatch):
    """Empty-text Grok envelope must not coerce into a zeroed successful analysis."""
    providers = {
        "grok": (
            lambda: True,
            lambda _prompt: json.dumps({"result": None, "text": "", "num_turns": 1}),
        ),
        "gemini": (
            lambda: True,
            lambda _prompt: json.dumps({"portfolio_score": 61, "portfolio_verdict": "HOLD"}),
        ),
    }
    monkeypatch.setattr(llm_failover, "_PROVIDERS", providers)
    monkeypatch.setenv("GESH_LLM_FAILOVER", "grok,gemini")

    msg = llm_failover.attempt(operation="deep_analyze_portfolio", max_tokens=1000, **_kwargs())
    assert msg.provider == "gemini"
    assert msg.content[0].input["portfolio_score"] == 61
    assert msg.content[0].input["portfolio_verdict"] == "HOLD"


def test_attempt_rejects_unrelated_json_object(monkeypatch):
    _install_provider(monkeypatch, json.dumps({"ok": True, "status": "done"}))
    with pytest.raises(llm_failover.FailoverUnavailable, match="no JSON object found"):
        llm_failover.attempt(operation="deep_analyze_portfolio", max_tokens=1000, **_kwargs())


def _credit_exhausted() -> anthropic.BadRequestError:
    import httpx

    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(400, request=request)
    return anthropic.BadRequestError(
        "Your credit balance is too low to access the Anthropic API",
        response=response,
        body=None,
    )


def test_gateway_fails_over_when_anthropic_credits_are_exhausted(monkeypatch, tmp_path):
    """Subscription lapsed / credit 400 is the actual 2026-08-22 dashboard 500.

    BadRequestError is non-retryable, so it used to bubble out of _call_anthropic
    as a raw SDK error. It is an APIStatusError and MUST fail over, not 500.
    """

    class _Stream:
        def __enter__(self):
            raise _credit_exhausted()

        def __exit__(self, *a):
            return False

    class _Client:
        class messages:
            @staticmethod
            def create(**k):
                raise _credit_exhausted()

            @staticmethod
            def stream(**k):
                return _Stream()

    monkeypatch.setattr(llm_gateway, "create_client", lambda: _Client())
    _install_provider(monkeypatch, json.dumps({"portfolio_score": 64, "portfolio_verdict": "HOLD"}))
    gw = llm_gateway.Gateway(db_path=tmp_path / "usage.db")
    msg = gw.stream(
        operation="deep_analyze_portfolio", model="claude-sonnet-4-6", max_tokens=1000, **_kwargs()
    )
    out = llm_contracts.extract_tool_input(
        msg, expected_tool="return_portfolio_analysis", operation="deep_analyze_portfolio"
    )
    assert out["portfolio_score"] == 64
    assert llm_failover.FAILOVER_COUNT == 1


def test_second_call_skips_anthropic_after_credit_400(monkeypatch, tmp_path):
    """A billed-out key will not recover later in the same process."""
    hits = {"n": 0}

    class _Stream:
        def __enter__(self):
            hits["n"] += 1
            raise _credit_exhausted()

        def __exit__(self, *a):
            return False

    class _Client:
        class messages:
            @staticmethod
            def create(**k):
                hits["n"] += 1
                raise _credit_exhausted()

            @staticmethod
            def stream(**k):
                return _Stream()

    monkeypatch.setattr(llm_gateway, "create_client", lambda: _Client())
    _install_provider(monkeypatch, json.dumps({"portfolio_score": 64, "portfolio_verdict": "HOLD"}))
    gw = llm_gateway.Gateway(db_path=tmp_path / "usage.db")
    kwargs = _kwargs()
    gw.stream(
        operation="deep_analyze_portfolio", model="claude-sonnet-4-6", max_tokens=1000, **kwargs
    )
    assert hits["n"] == 1
    gw.stream(
        operation="deep_analyze_portfolio", model="claude-sonnet-4-6", max_tokens=1000, **kwargs
    )
    assert hits["n"] == 1
    assert llm_failover.FAILOVER_COUNT == 2


def test_skip_anthropic_env_goes_straight_to_failover(monkeypatch, tmp_path):
    monkeypatch.setenv("GESH_LLM_SKIP_ANTHROPIC", "1")

    def _boom():
        raise AssertionError("Anthropic must not be contacted")

    monkeypatch.setattr(llm_gateway, "create_client", _boom)
    _install_provider(monkeypatch, json.dumps({"portfolio_score": 70, "portfolio_verdict": "HOLD"}))
    gw = llm_gateway.Gateway(db_path=tmp_path / "usage.db")
    msg = gw.create(
        operation="deep_analyze_portfolio", model="claude-sonnet-4-6", max_tokens=1000, **_kwargs()
    )
    out = llm_contracts.extract_tool_input(
        msg, expected_tool="return_portfolio_analysis", operation="deep_analyze_portfolio"
    )
    assert out["portfolio_score"] == 70
    assert llm_failover.FAILOVER_COUNT == 1


def test_call_gemini_forwards_deep_analysis_token_budget(monkeypatch):
    seen: dict = {}

    def fake_generate(
        prompt, model="", max_tokens=1024, response_mime_type=None, timeout_s=None, **k
    ):
        seen["max_tokens"] = max_tokens
        seen["mime"] = response_mime_type
        seen["model"] = model
        seen["timeout_s"] = timeout_s
        return json.dumps({"portfolio_score": 71, "portfolio_verdict": "HOLD"})

    stub = type("Agent", (), {"_generate": staticmethod(fake_generate)})()
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test-not-a-real-key")
    monkeypatch.setenv("GESH_GEMINI_TIMEOUT_S", "180")
    monkeypatch.setitem(sys.modules, "gemini_agent", stub)
    out = llm_failover.call_gemini("analyze", max_tokens=24000)
    assert json.loads(out)["portfolio_score"] == 71
    assert seen["max_tokens"] == 24000
    assert seen["mime"] == "application/json"
    assert seen["timeout_s"] == 180.0


def test_grok_executable_finds_home_local_bin_when_not_on_path(monkeypatch, tmp_path):
    grok = tmp_path / "grok"
    grok.write_text("#!/bin/sh\n")
    grok.chmod(0o755)
    monkeypatch.setattr(llm_failover.shutil, "which", lambda _: None)
    monkeypatch.setattr(llm_failover, "_GROK_FALLBACK_BINS", (str(grok),))
    assert llm_failover._grok_executable() == str(grok)


def test_gateway_fails_over_when_anthropic_unreachable(monkeypatch, tmp_path):
    _kill_anthropic(monkeypatch)
    _install_provider(monkeypatch, json.dumps({"portfolio_score": 64, "portfolio_verdict": "HOLD"}))

    gw = llm_gateway.Gateway(db_path=tmp_path / "usage.db")
    msg = gw.stream(
        operation="deep_analyze_portfolio", model="claude-sonnet-4-6", max_tokens=1000, **_kwargs()
    )

    out = llm_contracts.extract_tool_input(
        msg, expected_tool="return_portfolio_analysis", operation="deep_analyze_portfolio"
    )
    assert out["portfolio_score"] == 64
    assert out["risk_score"] is None  # omitted numeric is blank, not a fake 0
    assert llm_failover.FAILOVER_COUNT == 1


def test_gateway_failover_is_disabled_by_empty_env(monkeypatch, tmp_path):
    _kill_anthropic(monkeypatch)
    _install_provider(monkeypatch, json.dumps({"portfolio_score": 1}))
    monkeypatch.setenv("GESH_LLM_FAILOVER", "")

    gw = llm_gateway.Gateway(db_path=tmp_path / "usage.db")
    with pytest.raises(llm_gateway.LLMUnavailableError):
        gw.stream(
            operation="deep_analyze_portfolio",
            model="claude-sonnet-4-6",
            max_tokens=1000,
            **_kwargs(),
        )


def test_original_error_is_reraised_when_all_providers_fail(monkeypatch, tmp_path):
    _kill_anthropic(monkeypatch)
    monkeypatch.setenv("GESH_LLM_FAILOVER", "nonexistent-provider")

    gw = llm_gateway.Gateway(db_path=tmp_path / "usage.db")
    with pytest.raises(llm_gateway.LLMUnavailableError):
        gw.stream(
            operation="deep_analyze_portfolio",
            model="claude-sonnet-4-6",
            max_tokens=1000,
            **_kwargs(),
        )


def test_refusal_is_not_laundered_through_a_fallback():
    """A model refusing must surface, never be re-asked to a second vendor."""
    assert llm_gateway.LLMRefusalError not in llm_gateway._FAILOVER_TRIGGERS


def test_truncation_is_not_masked_by_a_fallback():
    """Truncation is a schema-too-large bug; a silent fallback would hide it."""
    assert llm_gateway.LLMTruncatedError not in llm_gateway._FAILOVER_TRIGGERS


def test_budget_exhaustion_triggers_failover():
    """'Claude unavailable' includes 'the daily Anthropic budget is spent'."""
    assert llm_gateway.LLMBudgetExceededError in llm_gateway._FAILOVER_TRIGGERS


def _budget_exhausted_gateway(tmp_path, monkeypatch):
    """Gateway whose first Anthropic reservation exceeds a half-reservation budget.

    Admission happens in the ledger *before* the client is built, so a factory
    that raises proves Anthropic was never reached.
    """
    monkeypatch.setattr(llm_gateway, "backoff_seconds", lambda *a, **k: 0)
    reserved = llm_gateway.reserve_cost_microusd("claude-sonnet-4-6", 100)
    budget_usd = (reserved / 1_000_000) * 0.5

    def _must_not_call_anthropic():
        raise AssertionError("budget admission must fail before create_client()")

    return llm_gateway.Gateway(
        db_path=tmp_path / "budget_failover.db",
        client_factory=_must_not_call_anthropic,
        budget_usd=budget_usd,
        max_attempts=1,
    )


def test_budget_exhaustion_fails_over_instead_of_killing_the_chain(
    monkeypatch, tmp_path
):
    """Daily Anthropic budget spent must hand off, not take step 3/5 with it.

    Membership-only coverage (``test_budget_exhaustion_triggers_failover``)
    cannot catch a regression that lists the exception but never reaches
    ``llm_failover.attempt``.
    """
    calls: list = []
    _fallback_ok(monkeypatch, calls)
    gw = _budget_exhausted_gateway(tmp_path, monkeypatch)

    result = gw.create(
        operation="deep_analysis",
        model="claude-sonnet-4-6",
        max_tokens=100,
        **_kwargs(),
    )

    assert calls == ["deep_analysis"], "fallback provider was never reached"
    payload = llm_contracts.extract_tool_input(
        result, expected_tool="return_portfolio_analysis", operation="deep_analysis"
    )
    assert payload["portfolio_verdict"] == "HOLD"


def test_budget_exhaustion_raises_when_failover_disabled(monkeypatch, tmp_path):
    """Empty ``GESH_LLM_FAILOVER`` is the opt-out; budget spent must still surface."""
    monkeypatch.setenv("GESH_LLM_FAILOVER", "")
    calls: list = []

    def _attempt(**kwargs):
        calls.append(kwargs.get("operation"))
        raise AssertionError("disabled failover must not call attempt()")

    monkeypatch.setattr(llm_failover, "attempt", _attempt)
    gw = _budget_exhausted_gateway(tmp_path, monkeypatch)

    with pytest.raises(llm_gateway.LLMBudgetExceededError):
        gw.create(
            operation="deep_analysis",
            model="claude-sonnet-4-6",
            max_tokens=100,
            **_kwargs(),
        )
    assert calls == []


def test_budget_exhaustion_reraises_original_when_all_fallbacks_fail(
    monkeypatch, tmp_path
):
    """A dead fallback must not launder the cause into FailoverUnavailable."""
    monkeypatch.setattr(llm_failover, "enabled", lambda: True)

    def _boom(*, operation, max_tokens, **kwargs):
        raise llm_failover.FailoverUnavailable("all fallback providers failed")

    monkeypatch.setattr(llm_failover, "attempt", _boom)
    gw = _budget_exhausted_gateway(tmp_path, monkeypatch)

    with pytest.raises(llm_gateway.LLMBudgetExceededError):
        gw.create(
            operation="deep_analysis",
            model="claude-sonnet-4-6",
            max_tokens=100,
            **_kwargs(),
        )


def test_provider_order_defaults_to_gemini_then_xai_then_grok(monkeypatch):
    monkeypatch.delenv("GESH_LLM_FAILOVER", raising=False)
    assert llm_failover.provider_order() == ["gemini", "xai", "grok"]


def test_xai_available_requires_key(monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    assert llm_failover.xai_available() is False
    monkeypatch.setenv("XAI_API_KEY", "xai-test-not-a-real-key")
    assert llm_failover.xai_available() is True


def test_call_xai_reads_chat_completions_content(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-test-not-a-real-key")
    monkeypatch.delenv("GESH_XAI_MODEL", raising=False)
    seen: dict = {}

    class _Resp:
        status_code = 200
        text = "{}"

        def json(self):
            return {"choices": [{"message": {"content": '{"ok": true}'}}]}

    def fake_post(url, **kwargs):
        seen["url"] = url
        seen["headers"] = kwargs.get("headers")
        seen["json"] = kwargs.get("json")
        seen["timeout"] = kwargs.get("timeout")
        return _Resp()

    monkeypatch.setattr(llm_failover.httpx, "post", fake_post)
    out = llm_failover.call_xai("return json")
    assert out == '{"ok": true}'
    assert seen["url"] == "https://api.x.ai/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer xai-test-not-a-real-key"
    assert seen["json"]["model"] == "grok-4.6"
    assert seen["json"]["response_format"] == {"type": "json_object"}
    assert "max_tokens" not in seen["json"]
    assert seen["timeout"] == llm_failover._XAI_TIMEOUT_S


def test_call_xai_forwards_max_tokens(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-test-not-a-real-key")
    seen: dict = {}

    class _Resp:
        status_code = 200
        text = "{}"

        def json(self):
            return {"choices": [{"message": {"content": '{"ok": true}'}}]}

    def fake_post(url, **kwargs):
        seen["json"] = kwargs.get("json")
        return _Resp()

    monkeypatch.setattr(llm_failover.httpx, "post", fake_post)
    llm_failover.call_xai("return json", max_tokens=24000)
    assert seen["json"]["max_tokens"] == 24000


def test_xai_timeout_honours_env_at_call_time(monkeypatch):
    monkeypatch.setenv("GESH_XAI_TIMEOUT_S", "480")
    assert llm_failover.xai_timeout_s() == 480.0
    monkeypatch.delenv("GESH_XAI_TIMEOUT_S", raising=False)
    assert llm_failover.xai_timeout_s() == llm_failover._XAI_TIMEOUT_S


def test_grok_timeout_honours_env_at_call_time(monkeypatch):
    monkeypatch.setenv("GESH_GROK_TIMEOUT_S", "180")
    assert llm_failover.grok_timeout_s() == 180.0
    monkeypatch.delenv("GESH_GROK_TIMEOUT_S", raising=False)
    assert llm_failover.grok_timeout_s() == llm_failover._GROK_TIMEOUT_S


def test_attempt_forwards_max_tokens_to_the_provider(monkeypatch):
    seen: dict = {}

    def stub(prompt, operation="", max_tokens=None):
        seen["max_tokens"] = max_tokens
        seen["operation"] = operation
        return json.dumps(
            {
                "portfolio_score": 64,
                "portfolio_verdict": "HOLD",
                "risk_score": 40,
                "holdings_analysis": [],
            }
        )

    providers = dict(llm_failover._PROVIDERS)
    providers["stub"] = (lambda: True, stub)
    monkeypatch.setattr(llm_failover, "_PROVIDERS", providers)
    monkeypatch.setenv("GESH_LLM_FAILOVER", "stub")
    llm_failover.attempt(operation="deep_analyze_portfolio", max_tokens=24000, **_kwargs())
    assert seen["max_tokens"] == 24000
    assert seen["operation"] == "deep_analyze_portfolio"


def test_call_xai_news_sentiment_uses_fast_timeout(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-test-not-a-real-key")
    seen: dict = {}

    class _Resp:
        status_code = 200
        text = "{}"

        def json(self):
            return {"choices": [{"message": {"content": '{"ok": true}'}}]}

    def fake_post(url, **kwargs):
        seen["timeout"] = kwargs.get("timeout")
        return _Resp()

    monkeypatch.setattr(llm_failover.httpx, "post", fake_post)
    llm_failover.call_xai("score this", operation="news_sentiment")
    assert seen["timeout"] == llm_failover._XAI_FAST_TIMEOUT_S
    assert seen["timeout"] <= 30


def test_fast_ops_skip_the_grok_cli(monkeypatch):
    """News/SEC/fundamentals must not fall through to a 900s grok CLI loop."""
    called: list[str] = []

    def grok_call(prompt):
        called.append("grok")
        raise AssertionError("grok CLI must not run for news_sentiment")

    def xai_call(prompt):
        called.append("xai")
        return json.dumps(
            {
                "portfolio_score": 1,
                "portfolio_verdict": "HOLD",
                "risk_score": 1,
                "holdings_analysis": [],
            }
        )

    monkeypatch.setattr(
        llm_failover,
        "_PROVIDERS",
        {"grok": (lambda: True, grok_call), "xai": (lambda: True, xai_call)},
    )
    monkeypatch.setenv("GESH_LLM_FAILOVER", "grok,xai")
    llm_failover.attempt(operation="news_sentiment", max_tokens=200, **_kwargs())
    assert called == ["xai"]


def test_call_xai_raises_on_http_error_without_leaking_the_key(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-secret-key-must-not-leak")

    class _Resp:
        status_code = 401
        text = "unauthorized"

        def json(self):
            return {}

    monkeypatch.setattr(llm_failover.httpx, "post", lambda *a, **k: _Resp())
    with pytest.raises(llm_failover.FailoverUnavailable, match="xAI HTTP 401") as ei:
        llm_failover.call_xai("hi")
    assert "xai-secret-key-must-not-leak" not in str(ei.value)
    assert "unauthorized" not in str(ei.value)


def test_call_xai_http_error_does_not_log_raw_body(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-test-not-a-real-key")

    class _Resp:
        status_code = 400
        text = "PROMPT ECHO: Analyze this private book NVDA 12 shares"

        def json(self):
            return {"error": {"type": "invalid_request_error", "message": self.text}}

    monkeypatch.setattr(llm_failover.httpx, "post", lambda *a, **k: _Resp())
    with pytest.raises(llm_failover.FailoverUnavailable) as ei:
        llm_failover.call_xai("Analyze this private book")
    msg = str(ei.value)
    assert msg == "xAI HTTP 400 (invalid_request_error)"
    assert "PROMPT ECHO" not in msg
    assert "NVDA 12 shares" not in msg


def test_build_prompt_embeds_schema_and_forbids_tools():
    tool_name, prompt, schema = llm_failover.build_prompt(_kwargs())
    assert tool_name == "return_portfolio_analysis"
    assert schema["properties"]["portfolio_score"]["type"] == "integer"
    assert "You are an analyst." in prompt  # system prompt carried over
    assert "Analyze this book." in prompt  # user content carried over
    assert "RAW JSON only" in prompt
    assert "Do not use any tools" in prompt


def test_build_prompt_requires_a_tool_schema():
    kwargs = _kwargs()
    kwargs["tools"] = []
    with pytest.raises(llm_failover.FailoverUnavailable):
        llm_failover.build_prompt(kwargs)


# ── Grok CLI adapter hardening ───────────────────────────────────────────────


def test_grok_command_pins_a_single_turn_completion(monkeypatch):
    """The CLI inherits the user's global agent rules (OMEGA memory on this host).

    Left alone it spends turns opening a memory session before answering and dies
    with 'max turns reached'. The allowlist + rules override keep it a completion.
    """
    seen: dict[str, list[str]] = {}

    class _Proc:
        returncode = 0
        stdout = '{"text": "{\\"ok\\": 1}"}'
        stderr = ""

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        seen["check"] = kw.get("check")
        return _Proc()

    monkeypatch.setattr(llm_failover.subprocess, "run", fake_run)
    monkeypatch.setattr(llm_failover.shutil, "which", lambda _: "/usr/bin/grok")

    out = llm_failover.call_grok("hi")
    cmd = seen["cmd"]
    assert "--tools" in cmd and "--rules" in cmd
    assert cmd[cmd.index("--max-turns") + 1] == llm_failover._GROK_MAX_TURNS
    rules = cmd[cmd.index("--rules") + 1].lower()
    assert "do not call any tool" in rules
    assert "memory system" in rules
    assert seen["check"] is False  # non-zero exit is translated, not raised
    assert llm_failover.extract_json_object(out) == {"ok": 1}


def test_grok_reads_the_text_envelope_key(monkeypatch):
    """This CLI returns the answer under 'text'; 'result' is null."""

    class _Proc:
        returncode = 0
        stdout = '{"result": null, "text": "{\\"portfolio_score\\": 5}", "num_turns": 1}'
        stderr = ""

    monkeypatch.setattr(llm_failover.subprocess, "run", lambda cmd, **kw: _Proc())
    monkeypatch.setattr(llm_failover.shutil, "which", lambda _: "/usr/bin/grok")
    assert llm_failover.extract_json_object(llm_failover.call_grok("x")) == {"portfolio_score": 5}


def test_grok_retries_on_the_cli_default_when_model_id_is_retired(monkeypatch):
    """xAI rotates ids (grok-build -> grok-4.6). A stale pin must degrade, not fail."""
    calls: list[list[str]] = []

    class _Bad:
        returncode = 1
        stdout = ""
        stderr = 'Error: Couldn\'t set model: Invalid params: "unknown model id".'

    class _Good:
        returncode = 0
        stdout = '{"text": "{\\"ok\\": true}"}'
        stderr = ""

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return _Bad() if "--model" in cmd else _Good()

    monkeypatch.setattr(llm_failover.subprocess, "run", fake_run)
    monkeypatch.setattr(llm_failover.shutil, "which", lambda _: "/usr/bin/grok")

    assert llm_failover.extract_json_object(llm_failover.call_grok("x")) == {"ok": True}
    assert len(calls) == 2
    assert "--model" in calls[0] and "--model" not in calls[1]


def test_grok_rejects_envelope_with_no_text_answer(monkeypatch):
    """result is documented-null; empty text must fail, not become the payload."""

    class _Proc:
        returncode = 0
        stdout = '{"result": null, "text": "", "num_turns": 1, "session_id": "s"}'
        stderr = ""

    monkeypatch.setattr(llm_failover.subprocess, "run", lambda cmd, **kw: _Proc())
    monkeypatch.setattr(llm_failover.shutil, "which", lambda _: "/usr/bin/grok")
    with pytest.raises(llm_failover.FailoverUnavailable, match="envelope had no text"):
        llm_failover.call_grok("x")


def test_grok_returns_bare_analysis_json_when_not_an_envelope(monkeypatch):
    class _Proc:
        returncode = 0
        stdout = '{"portfolio_score": 5, "portfolio_verdict": "HOLD"}'
        stderr = ""

    monkeypatch.setattr(llm_failover.subprocess, "run", lambda cmd, **kw: _Proc())
    monkeypatch.setattr(llm_failover.shutil, "which", lambda _: "/usr/bin/grok")
    raw = llm_failover.call_grok("x")
    assert llm_failover.extract_json_object(raw) == {
        "portfolio_score": 5,
        "portfolio_verdict": "HOLD",
    }


def test_grok_surfaces_other_cli_failures(monkeypatch):
    class _Proc:
        returncode = 1
        stdout = ""
        stderr = "Error: not authenticated"

    monkeypatch.setattr(llm_failover.subprocess, "run", lambda cmd, **kw: _Proc())
    monkeypatch.setattr(llm_failover.shutil, "which", lambda _: "/usr/bin/grok")
    with pytest.raises(llm_failover.FailoverUnavailable, match="not authenticated"):
        llm_failover.call_grok("x")


# ── Absent-credentials failover (added 2026-08-21) ────────────────────────────
#
# The 18 tests above all run with ANTHROPIC_API_KEY set (see the _reset fixture) and
# simulate Anthropic being *down*. None of them exercised Anthropic being *absent* —
# which is the state the moment a subscription lapses, and the single most likely
# trigger for the whole failover feature. It did not work: create_client raised
# LLMConfigurationError, which was deliberately excluded from _FAILOVER_TRIGGERS, so
# the daily chain died at step 3/5 instead of handing off.
#
# These three tests pin the corrected behaviour AND the distinction it rests on.


def _fallback_ok(monkeypatch, calls: list):
    """Route llm_failover.attempt() to a stub that records that it ran."""

    def _attempt(*, operation, max_tokens, **kwargs):
        calls.append(operation)
        payload = llm_failover.coerce_to_schema(
            {
                "portfolio_score": 61,
                "portfolio_verdict": "HOLD",
                "risk_score": 44,
                "holdings_analysis": [{"symbol": "SPY", "verdict": "HOLD"}],
            },
            _SCHEMA,
        )
        return llm_failover.FailoverMessage(
            provider="grok",
            model="grok-4.6",
            tool_name="return_portfolio_analysis",
            payload=payload,
        )

    monkeypatch.setattr(llm_failover, "enabled", lambda: True)
    monkeypatch.setattr(llm_failover, "attempt", _attempt)


def test_absent_api_key_fails_over_instead_of_killing_the_chain(monkeypatch):
    """unset ANTHROPIC_API_KEY must hand off, not raise.

    Regression guard: before 2026-08-21 this raised LLMConfigurationError straight
    out of the gateway and took the daily chain with it.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    calls: list = []
    _fallback_ok(monkeypatch, calls)

    llm_gateway.reset_gateway()
    gw = llm_gateway.Gateway(db_path=None)
    result = gw._call(
        mode="create",
        operation="deep_analysis",
        model="claude-sonnet-4-6",
        max_tokens=4096,
        **_kwargs(),
    )

    assert calls == ["deep_analysis"], "fallback provider was never reached"
    payload = llm_contracts.extract_tool_input(
        result, expected_tool="return_portfolio_analysis", operation="deep_analysis"
    )
    assert payload["portfolio_verdict"] == "HOLD"


def test_unconfigured_is_a_failover_trigger_but_its_parent_is_not():
    """The distinction is the whole point — pin it so neither side drifts.

    Adding the parent LLMConfigurationError to the tuple would silently launder a
    genuinely malformed setting (e.g. GESH_LLM_TIMEOUT_SECONDS="abc") through a
    second vendor. Removing the subclass reopens the dead-chain bug above.
    """
    assert llm_gateway.LLMProviderUnconfiguredError in llm_gateway._FAILOVER_TRIGGERS
    assert llm_gateway.LLMConfigurationError not in llm_gateway._FAILOVER_TRIGGERS
    # ...and the subclass relationship keeps every existing handler working.
    assert issubclass(llm_gateway.LLMProviderUnconfiguredError, llm_gateway.LLMConfigurationError)


def test_malformed_setting_still_fails_loudly(monkeypatch):
    """A real misconfiguration must NOT fail over — it must surface."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("GESH_LLM_TIMEOUT_SECONDS", "not-a-number")
    calls: list = []
    _fallback_ok(monkeypatch, calls)

    with pytest.raises(llm_gateway.LLMConfigurationError):
        llm_gateway.create_client()
    assert calls == [], "a malformed setting must never reach a fallback provider"


def test_call_grok_requires_cli_on_path(monkeypatch):
    monkeypatch.setattr(llm_failover.shutil, "which", lambda _: None)
    monkeypatch.setattr(llm_failover, "_GROK_FALLBACK_BINS", ())
    with pytest.raises(llm_failover.FailoverUnavailable, match="not on PATH"):
        llm_failover.call_grok("x")


# ── Availability / Gemini / attempt fallbacks ────────────────────────────────


def test_enabled_is_false_for_empty_or_whitespace_env(monkeypatch):
    monkeypatch.setenv("GESH_LLM_FAILOVER", "")
    assert llm_failover.enabled() is False
    monkeypatch.setenv("GESH_LLM_FAILOVER", "  ,  ")
    assert llm_failover.enabled() is False
    assert llm_failover.provider_order() == []


def test_grok_available_requires_cli_plus_auth(monkeypatch, tmp_path):
    monkeypatch.setattr(llm_failover.shutil, "which", lambda _: None)
    monkeypatch.setattr(llm_failover, "_GROK_FALLBACK_BINS", ())
    monkeypatch.setattr(llm_failover.Path, "home", lambda *a: tmp_path)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    assert llm_failover.grok_available() is False

    monkeypatch.setattr(llm_failover.shutil, "which", lambda _: "/usr/bin/grok")
    assert llm_failover.grok_available() is False

    grok_dir = tmp_path / ".grok"
    grok_dir.mkdir()
    (grok_dir / "auth.json").write_text("{}", encoding="utf-8")
    assert llm_failover.grok_available() is True

    (grok_dir / "auth.json").unlink()
    monkeypatch.setenv("XAI_API_KEY", "xai-test-not-used")
    assert llm_failover.grok_available() is True


def test_gemini_available_follows_api_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert llm_failover.gemini_available() is False
    monkeypatch.setenv("GEMINI_API_KEY", "fake-not-used")
    assert llm_failover.gemini_available() is True


def test_call_gemini_requires_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(llm_failover.FailoverUnavailable, match="GEMINI_API_KEY"):
        llm_failover.call_gemini("x")


def test_call_gemini_returns_generated_text(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-not-used")
    fake = types.SimpleNamespace(_generate=lambda prompt, model="": '{"portfolio_score": 61}')
    monkeypatch.setitem(sys.modules, "gemini_agent", fake)
    assert llm_failover.call_gemini("analyze") == '{"portfolio_score": 61}'


def test_call_gemini_rejects_empty_text(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-not-used")
    fake = types.SimpleNamespace(_generate=lambda prompt, model="": "   ")
    monkeypatch.setitem(sys.modules, "gemini_agent", fake)
    with pytest.raises(llm_failover.FailoverUnavailable, match="empty"):
        llm_failover.call_gemini("analyze")


def test_attempt_skips_unconfigured_provider_and_uses_next(monkeypatch):
    """A missing Grok CLI must not take down Gemini — order is a preference, not a lock."""

    def _boom(_prompt: str) -> str:
        raise AssertionError("unconfigured provider must not be called")

    providers = {
        "grok": (lambda: False, _boom),
        "stub": (
            lambda: True,
            lambda _p: json.dumps({"portfolio_score": 55, "portfolio_verdict": "HOLD"}),
        ),
    }
    monkeypatch.setattr(llm_failover, "_PROVIDERS", providers)
    monkeypatch.setenv("GESH_LLM_FAILOVER", "grok,stub")

    msg = llm_failover.attempt(operation="deep_analyze_portfolio", max_tokens=10, **_kwargs())
    assert msg.provider == "stub"
    assert msg.content[0].input["portfolio_score"] == 55
    assert llm_failover.FAILOVER_COUNT == 1
    assert llm_failover.LAST_FAILOVER == {
        "operation": "deep_analyze_portfolio",
        "provider": "stub",
    }


def test_attempt_tries_next_provider_when_the_first_raises(monkeypatch):
    providers = {
        "dead": (
            lambda: True,
            lambda _p: (_ for _ in ()).throw(llm_failover.FailoverUnavailable("boom")),
        ),
        "stub": (lambda: True, lambda _p: json.dumps({"portfolio_score": 41})),
    }
    monkeypatch.setattr(llm_failover, "_PROVIDERS", providers)
    monkeypatch.setenv("GESH_LLM_FAILOVER", "dead,stub")

    msg = llm_failover.attempt(operation="screener", max_tokens=10, **_kwargs())
    assert msg.provider == "stub"
    assert msg.content[0].input["portfolio_score"] == 41


def test_gateway_create_fails_over_on_api_status_error(monkeypatch, tmp_path):
    """create() shares _call with stream(); a 503 must fail over, not only connection errors."""

    def dead(*_a, **_k):
        request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        response = httpx.Response(503, request=request)
        raise anthropic.APIStatusError("overloaded", response=response, body=None)

    monkeypatch.setattr(llm_gateway, "create_client", dead)
    _install_provider(monkeypatch, json.dumps({"portfolio_score": 33, "portfolio_verdict": "HOLD"}))

    gw = llm_gateway.Gateway(db_path=tmp_path / "usage.db")
    msg = gw.create(
        operation="deep_analyze_portfolio",
        model="claude-sonnet-4-6",
        max_tokens=1000,
        **_kwargs(),
    )
    out = llm_contracts.extract_tool_input(
        msg, expected_tool="return_portfolio_analysis", operation="deep_analyze_portfolio"
    )
    assert out["portfolio_score"] == 33
    assert llm_failover.FAILOVER_COUNT == 1


def test_api_timeout_is_a_failover_trigger():
    assert anthropic.APITimeoutError in llm_gateway._FAILOVER_TRIGGERS
    assert anthropic.APIStatusError in llm_gateway._FAILOVER_TRIGGERS


def test_coerce_fills_boolean_and_number_blanks_and_invalid_ints():
    schema = {
        "type": "object",
        "required": ["ok", "beta", "n"],
        "properties": {
            "ok": {"type": "boolean"},
            "beta": {"type": "number"},
            "n": {"type": "integer"},
        },
    }
    out = llm_failover.coerce_to_schema({}, schema)
    assert out == {"ok": False, "beta": None, "n": None}
    assert llm_failover.coerce_to_schema({"n": "not-a-number"}, schema)["n"] is None
    assert llm_failover.coerce_to_schema({"beta": "1.5"}, schema)["beta"] == 1.5


def test_extract_json_raises_on_empty_and_on_arrays():
    with pytest.raises(ValueError):
        llm_failover.extract_json_object("")
    with pytest.raises(ValueError):
        llm_failover.extract_json_object("[1, 2]")


def test_build_prompt_flattens_string_system_and_mixed_user_blocks():
    kwargs = _kwargs()
    kwargs["system"] = "plain system"
    kwargs["messages"] = [
        {"role": "user", "content": ["part-a", {"type": "text", "text": "part-b"}]},
        {"role": "assistant", "content": "ignore me"},
    ]
    _name, prompt, _schema = llm_failover.build_prompt(kwargs)
    assert "plain system" in prompt
    assert "part-a" in prompt
    assert "part-b" in prompt
    assert "ignore me" not in prompt


# ── Gemini adapter (P0-5, 2026-08-21 audit) ───────────────────────────────────
#
# llm_failover.call_gemini pinned gemini-2.5-pro, which 404s for new API users.
# Combined with the scheduled-PATH bug that hid Grok, the chain lost step 3/5
# on 2026-08-20. Default to a live Flash id and retry on 404 like call_grok
# already retries a retired model pin.


def test_call_gemini_defaults_to_flash_not_retired_pro(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gk-test")
    monkeypatch.delenv("GESH_GEMINI_FAILOVER_MODEL", raising=False)
    seen: list[str] = []

    class _Agent:
        @staticmethod
        def _generate(prompt, model=""):
            seen.append(model)
            return '{"ok": true}'

    monkeypatch.setitem(sys.modules, "gemini_agent", _Agent)
    assert llm_failover.call_gemini("hi") == '{"ok": true}'
    assert seen == ["gemini-2.5-flash"]


def test_call_gemini_model_is_env_overridable(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gk-test")
    monkeypatch.setenv("GESH_GEMINI_FAILOVER_MODEL", "gemini-2.0-flash-lite")
    seen: list[str] = []

    class _Agent:
        @staticmethod
        def _generate(prompt, model=""):
            seen.append(model)
            return "ok"

    monkeypatch.setitem(sys.modules, "gemini_agent", _Agent)
    llm_failover.call_gemini("hi")
    assert seen == ["gemini-2.0-flash-lite"]


def test_call_gemini_retries_fallback_when_pinned_model_404s(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gk-test")
    monkeypatch.setenv("GESH_GEMINI_FAILOVER_MODEL", "gemini-2.5-pro")
    monkeypatch.delenv("GESH_GEMINI_FAILOVER_FALLBACK", raising=False)
    seen: list[str] = []

    class _Agent:
        @staticmethod
        def _generate(prompt, model=""):
            seen.append(model)
            if model == "gemini-2.5-pro":
                raise RuntimeError("404 NOT_FOUND no longer available to new users")
            return '{"ok": true}'

    monkeypatch.setitem(sys.modules, "gemini_agent", _Agent)
    assert llm_failover.call_gemini("hi") == '{"ok": true}'
    assert seen == ["gemini-2.5-pro", "gemini-2.0-flash-lite"]
