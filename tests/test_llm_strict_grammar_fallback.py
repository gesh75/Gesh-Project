"""Guard tests for the strict-tool grammar fallback (2026-08-07 chain outage).

Anthropic compiles a constrained-decoding grammar for every tool carrying
``strict: True`` and 400s when it is too large. ``DEEP_ANALYSIS_SCHEMA`` sits on
that provider-side boundary: the SAME unchanged schema was accepted on 08-05 and
08-06 and rejected on 08-04 and 08-07, killing step 3/5 of the daily chain and
leaving a stale report. No transport retry can clear it — the fallback is to
re-send the identical request without ``strict``.

These tests pin the three properties that matter:
  1. the 400 is recognised and retried exactly once, WITHOUT strict;
  2. the fallback never fires for unrelated 400s (silent contract loss);
  3. the degrade is counted so it cannot hide behind a green run.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx
import pytest

import llm_gateway

_REPO_ROOT = Path(__file__).resolve().parent.parent


# ── Fakes ─────────────────────────────────────────────────────────────────────


class _Usage:
    input_tokens = 10
    output_tokens = 5
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0


class _FakeMessage:
    def __init__(self) -> None:
        self.content = [SimpleNamespace(text="ok")]
        self.stop_reason = "end_turn"
        self.usage = _Usage()
        self._request_id = "req_test"
        self.id = "msg_test"


def _bad_request(message: str) -> anthropic.BadRequestError:
    """A real BadRequestError — isinstance checks must hold, not just the text."""
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(400, request=request, json={"error": {"message": message}})
    return anthropic.BadRequestError(message, response=response, body=None)


_GRAMMAR_400 = (
    "Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', "
    "'message': 'The compiled grammar is too large, which would cause performance "
    "issues. Simplify your tool schemas or reduce the number of strict tools.'}}"
)


class _RecordingMessages:
    """Raises the queued exception(s), then returns a message. Records tools sent."""

    def __init__(self, script: list[Any]) -> None:
        self._script = list(script)
        self.tools_seen: list[Any] = []
        self.calls = 0

    def _next(self, kwargs: dict[str, Any]) -> Any:
        self.calls += 1
        self.tools_seen.append(kwargs.get("tools"))
        item = self._script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def create(self, **kwargs: Any) -> Any:
        return self._next(kwargs)

    def stream(self, **kwargs: Any) -> Any:
        outer = self

        class _Ctx:
            def __enter__(self_inner) -> Any:
                return self_inner

            def __exit__(self_inner, *exc: Any) -> None:
                return None

            def get_final_message(self_inner) -> Any:
                return outer._next(kwargs)

        return _Ctx()


class _RecordingClient:
    def __init__(self, script: list[Any]) -> None:
        self.messages = _RecordingMessages(script)


_STRICT_TOOL = {
    "name": "return_portfolio_analysis",
    "description": "Return the complete deep portfolio analysis.",
    "strict": True,
    "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    "cache_control": {"type": "ephemeral"},
}


@pytest.fixture(autouse=True)
def _reset_counter() -> Any:
    llm_gateway.STRICT_DEGRADE_COUNT = 0
    yield
    llm_gateway.STRICT_DEGRADE_COUNT = 0


@pytest.fixture
def make_gateway(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")
    monkeypatch.setenv("LLM_DAILY_BUDGET_USD", "0")
    monkeypatch.setattr(llm_gateway, "backoff_seconds", lambda *a, **k: 0)

    def _build(client: _RecordingClient) -> llm_gateway.Gateway:
        return llm_gateway.Gateway(
            db_path=tmp_path / "llm_usage.db",
            budget_usd=0.0,
            max_attempts=3,
            client_factory=lambda: client,
        )

    return _build


# ── Detection ─────────────────────────────────────────────────────────────────


def test_detects_grammar_400_and_ignores_other_400s() -> None:
    assert llm_gateway._is_grammar_too_large(_bad_request(_GRAMMAR_400))
    assert not llm_gateway._is_grammar_too_large(_bad_request("max_tokens: must be >= 1"))
    # A transport error is not a grammar error, however similar the text.
    assert not llm_gateway._is_grammar_too_large(httpx.ReadTimeout("compiled grammar is too large"))


def test_without_strict_tools_returns_none_when_nothing_to_degrade() -> None:
    assert llm_gateway._without_strict_tools({"messages": []}) is None
    assert llm_gateway._without_strict_tools({"tools": [{"name": "t"}]}) is None


def test_without_strict_tools_does_not_mutate_caller_payload() -> None:
    kwargs = {"tools": [dict(_STRICT_TOOL)]}
    degraded = llm_gateway._without_strict_tools(kwargs)
    assert degraded is not None
    assert "strict" not in degraded["tools"][0]
    # The original must survive intact — a retry loop may re-send it.
    assert kwargs["tools"][0]["strict"] is True
    # Everything except `strict` is preserved, including cache_control.
    assert degraded["tools"][0]["cache_control"] == {"type": "ephemeral"}
    assert degraded["tools"][0]["input_schema"] == _STRICT_TOOL["input_schema"]


# ── Fallback behaviour ────────────────────────────────────────────────────────


@pytest.mark.parametrize("mode", ["stream", "create"])
def test_grammar_400_retries_once_without_strict(make_gateway, mode: str) -> None:
    client = _RecordingClient([_bad_request(_GRAMMAR_400), _FakeMessage()])
    gw = make_gateway(client)

    call = gw.stream if mode == "stream" else gw.create
    message = call(
        operation="deep_analyze_portfolio",
        model="claude-sonnet-4-6",
        max_tokens=1000,
        messages=[{"role": "user", "content": "hi"}],
        tools=[dict(_STRICT_TOOL)],
    )

    assert message is not None
    assert client.messages.calls == 2, "expected exactly one retry, not a retry storm"
    assert client.messages.tools_seen[0][0]["strict"] is True
    assert "strict" not in client.messages.tools_seen[1][0]
    assert llm_gateway.STRICT_DEGRADE_COUNT == 1


def test_degrade_happens_once_not_per_transport_attempt(make_gateway) -> None:
    """The fallback must not consume — or multiply across — transport retries."""
    client = _RecordingClient([_bad_request(_GRAMMAR_400), _FakeMessage()])
    gw = make_gateway(client)
    gw.stream(
        operation="deep_analyze_portfolio",
        model="claude-sonnet-4-6",
        max_tokens=1000,
        messages=[{"role": "user", "content": "hi"}],
        tools=[dict(_STRICT_TOOL)],
    )
    assert llm_gateway.STRICT_DEGRADE_COUNT == 1


def test_second_grammar_400_is_not_swallowed(make_gateway) -> None:
    """If the non-strict retry ALSO 400s, fail loudly — never loop or hide it."""
    client = _RecordingClient([_bad_request(_GRAMMAR_400), _bad_request(_GRAMMAR_400)])
    gw = make_gateway(client)
    with pytest.raises(anthropic.BadRequestError):
        gw.stream(
            operation="deep_analyze_portfolio",
            model="claude-sonnet-4-6",
            max_tokens=1000,
            messages=[{"role": "user", "content": "hi"}],
            tools=[dict(_STRICT_TOOL)],
        )
    assert client.messages.calls == 2


def test_unrelated_400_is_not_degraded(make_gateway) -> None:
    client = _RecordingClient([_bad_request("max_tokens: must be >= 1")])
    gw = make_gateway(client)
    with pytest.raises(anthropic.BadRequestError):
        gw.stream(
            operation="deep_analyze_portfolio",
            model="claude-sonnet-4-6",
            max_tokens=1000,
            messages=[{"role": "user", "content": "hi"}],
            tools=[dict(_STRICT_TOOL)],
        )
    assert client.messages.calls == 1
    assert llm_gateway.STRICT_DEGRADE_COUNT == 0


def _schema_shape(schema: dict[str, Any]) -> dict[str, int]:
    import llm_contracts

    hardened = llm_contracts._harden_schema(schema)
    counts = {"props": 0, "objects": 0, "unions": 0, "depth": 0}

    def walk(node: Any, depth: int = 0) -> None:
        counts["depth"] = max(counts["depth"], depth)
        if not isinstance(node, dict):
            return
        if isinstance(node.get("type"), list):
            counts["unions"] += 1
        if "properties" in node:
            counts["objects"] += 1
            for child in node["properties"].values():
                counts["props"] += 1
                walk(child, depth + 1)
        if "items" in node:
            walk(node["items"], depth + 1)

    walk(hardened)
    return counts


def _load_schema_literals() -> dict[str, Any]:
    """Read schema literals without importing ai_analyzer (which needs an API key)."""
    import ast

    ns: dict[str, Any] = {}
    for node in ast.parse((_REPO_ROOT / "ai_analyzer.py").read_text()).body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            try:
                exec(compile(ast.Module(body=[node], type_ignores=[]), "<s>", "exec"), ns)
            except Exception:  # noqa: BLE001 — non-schema assignments are irrelevant here
                pass
    return ns


def test_deep_analysis_schema_stays_within_its_measured_grammar_budget() -> None:
    """Keep the schema inside the envelope that was PROBED against the live API.

    Empirically established 2026-08-07 by sending candidate schemas to the real
    endpoint (see the outage writeup). Byte size is NOT the constraint — stripping
    all descriptions cut 531 B and still 400'd. What binds is structure: property
    count, object nodes, and especially nullable UNIONS.

    Measured boundary, holding everything else constant:
      48 props / 11 objs / 2 unions -> 400   (the shape that broke the chain)
      47 props / 10 objs / 0 unions -> OK, but 0 spare properties (a cliff edge)
      41 props /  7 objs / 0 unions -> OK, +4 spare props OK, +8 -> 400

    So the live ceiling sits near 45-48 properties and the current schema keeps
    roughly 4-7 properties of headroom. Adding a per-holding field spends that
    headroom — re-probe before raising these numbers, never just bump them.
    """
    counts = _schema_shape(_load_schema_literals()["DEEP_ANALYSIS_SCHEMA"])
    assert counts["props"] <= 41, f"grew to {counts['props']} properties (probed ceiling ~45)"
    assert counts["objects"] <= 7, f"grew to {counts['objects']} object nodes"
    assert counts["depth"] <= 5, f"nesting deepened to {counts['depth']}"


def test_deep_analysis_schema_has_no_nullable_unions() -> None:
    """Nullable unions were the single most expensive construct in the grammar.

    Dropping the two `["string","null"]` properties in portfolio_recommendations
    was necessary to get back under the ceiling. Express absence as an empty
    string instead — every reader already treats falsy as absent.
    """
    counts = _schema_shape(_load_schema_literals()["DEEP_ANALYSIS_SCHEMA"])
    assert counts["unions"] == 0, (
        f"{counts['unions']} nullable union(s) reintroduced — these are the most "
        "expensive construct in the compiled grammar; use '' for absent instead"
    )


def test_degrade_is_logged_at_warning(make_gateway, caplog) -> None:
    """A silent permanent degrade is the failure mode this guards against."""
    client = _RecordingClient([_bad_request(_GRAMMAR_400), _FakeMessage()])
    gw = make_gateway(client)
    with caplog.at_level("WARNING", logger="llm_gateway"):
        gw.stream(
            operation="deep_analyze_portfolio",
            model="claude-sonnet-4-6",
            max_tokens=1000,
            messages=[{"role": "user", "content": "hi"}],
            tools=[dict(_STRICT_TOOL)],
        )
    assert any("STRICT_DEGRADE" in r.message for r in caplog.records)
