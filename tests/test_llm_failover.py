"""Guard tests for the provider-independent LLM failover path.

The contract these lock down: when Anthropic is unavailable, a fallback provider
returns an object that ``llm_contracts.extract_tool_input`` accepts unchanged, so
no downstream reader (renest_analysis, fair_value, gen_report2, db.log_analysis)
needs to know a different vendor answered.
"""

from __future__ import annotations

import json

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
    assert out["risk_score"] == 0
    assert out["holdings_analysis"] == []


def test_coerce_recurses_into_array_items():
    out = llm_failover.coerce_to_schema(
        {"holdings_analysis": [{"symbol": "NVDA"}]}, _SCHEMA
    )
    assert out["holdings_analysis"][0] == {"symbol": "NVDA", "verdict": ""}


def test_coerce_casts_numeric_strings_to_int():
    out = llm_failover.coerce_to_schema({"portfolio_score": "87.6"}, _SCHEMA)
    assert out["portfolio_score"] == 88


# ── Duck-typed message ───────────────────────────────────────────────────────


def test_failover_message_satisfies_extract_tool_input():
    msg = llm_failover.FailoverMessage(
        provider="grok", model="grok-build",
        tool_name="return_portfolio_analysis", payload={"portfolio_score": 71},
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


def test_gateway_fails_over_when_anthropic_unreachable(monkeypatch, tmp_path):
    _kill_anthropic(monkeypatch)
    _install_provider(monkeypatch, json.dumps({"portfolio_score": 64, "portfolio_verdict": "HOLD"}))

    gw = llm_gateway.Gateway(db_path=tmp_path / "usage.db")
    msg = gw.stream(operation="deep_analyze_portfolio", model="claude-sonnet-4-6",
                    max_tokens=1000, **_kwargs())

    out = llm_contracts.extract_tool_input(
        msg, expected_tool="return_portfolio_analysis", operation="deep_analyze_portfolio"
    )
    assert out["portfolio_score"] == 64
    assert out["risk_score"] == 0          # coerced, not missing
    assert llm_failover.FAILOVER_COUNT == 1


def test_gateway_failover_is_disabled_by_empty_env(monkeypatch, tmp_path):
    _kill_anthropic(monkeypatch)
    _install_provider(monkeypatch, json.dumps({"portfolio_score": 1}))
    monkeypatch.setenv("GESH_LLM_FAILOVER", "")

    gw = llm_gateway.Gateway(db_path=tmp_path / "usage.db")
    with pytest.raises(llm_gateway.LLMUnavailableError):
        gw.stream(operation="deep_analyze_portfolio", model="claude-sonnet-4-6",
                  max_tokens=1000, **_kwargs())


def test_original_error_is_reraised_when_all_providers_fail(monkeypatch, tmp_path):
    _kill_anthropic(monkeypatch)
    monkeypatch.setenv("GESH_LLM_FAILOVER", "nonexistent-provider")

    gw = llm_gateway.Gateway(db_path=tmp_path / "usage.db")
    with pytest.raises(llm_gateway.LLMUnavailableError):
        gw.stream(operation="deep_analyze_portfolio", model="claude-sonnet-4-6",
                  max_tokens=1000, **_kwargs())


def test_refusal_is_not_laundered_through_a_fallback():
    """A model refusing must surface, never be re-asked to a second vendor."""
    assert llm_gateway.LLMRefusalError not in llm_gateway._FAILOVER_TRIGGERS


def test_truncation_is_not_masked_by_a_fallback():
    """Truncation is a schema-too-large bug; a silent fallback would hide it."""
    assert llm_gateway.LLMTruncatedError not in llm_gateway._FAILOVER_TRIGGERS


def test_budget_exhaustion_triggers_failover():
    """'Claude unavailable' includes 'the daily Anthropic budget is spent'."""
    assert llm_gateway.LLMBudgetExceededError in llm_gateway._FAILOVER_TRIGGERS


def test_provider_order_defaults_to_grok_then_gemini(monkeypatch):
    monkeypatch.delenv("GESH_LLM_FAILOVER", raising=False)
    assert llm_failover.provider_order() == ["grok", "gemini"]


def test_build_prompt_embeds_schema_and_forbids_tools():
    tool_name, prompt, schema = llm_failover.build_prompt(_kwargs())
    assert tool_name == "return_portfolio_analysis"
    assert schema["properties"]["portfolio_score"]["type"] == "integer"
    assert "You are an analyst." in prompt      # system prompt carried over
    assert "Analyze this book." in prompt       # user content carried over
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
    assert seen["check"] is False        # non-zero exit is translated, not raised
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
