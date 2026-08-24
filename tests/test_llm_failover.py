"""Guard tests for the provider-independent LLM failover path.

The contract these lock down: when Anthropic is unavailable, a fallback provider
returns an object that ``llm_contracts.extract_tool_input`` accepts unchanged, so
no downstream reader (renest_analysis, fair_value, gen_report2, db.log_analysis)
needs to know a different vendor answered.
"""

from __future__ import annotations

import json
import sys

import anthropic
import pytest

import llm_contracts
import llm_failover
import llm_gateway


@pytest.fixture(autouse=True)
def _reset(monkeypatch, tmp_path):
    monkeypatch.setattr(llm_failover, "FAILOVER_COUNT", 0, raising=False)
    monkeypatch.setattr(llm_failover, "LAST_FAILOVER", None, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
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


def test_provider_order_defaults_to_xai_then_grok_then_gemini(monkeypatch):
    monkeypatch.delenv("GESH_LLM_FAILOVER", raising=False)
    assert llm_failover.provider_order() == ["xai", "grok", "gemini"]


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
    assert seen["timeout"] == llm_failover._XAI_TIMEOUT_S


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
