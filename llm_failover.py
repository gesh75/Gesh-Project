"""Provider-independent failover for the analysis LLM calls.

The chain's deep analysis, screener, sentiment and SEC calls all go through
``llm_gateway.Gateway``. When Anthropic is unreachable (outage, auth failure,
exhausted budget) that gateway raises and the daily chain dies at step 3/5.

This module supplies a *second* set of providers behind the same wire contract.
The critical design constraint: the object returned here must quack like an
``anthropic.types.Message`` carrying a ``tool_use`` block, because
``llm_contracts.extract_tool_input`` — and therefore every downstream reader
(renest_analysis, fair_value, gen_report2, db.log_analysis) — reads it that way.
Satisfying that shape means NOTHING downstream changes when a fallback runs.

Providers are tried in the order named by ``GESH_LLM_FAILOVER`` (default
``grok,gemini``):

  grok    — the Grok CLI in headless mode (``grok -p --output-format json``).
            Uses the interactive OAuth session in ~/.grok/auth.json, so it needs
            no XAI_API_KEY. This is the same "CLI as a courier" pattern the
            snapshot fetcher already uses for the Robinhood MCP.
  gemini  — google-genai via GEMINI_API_KEY (see gemini_agent.py).

Failover is deliberately LOUD: ``FAILOVER_COUNT`` / ``LAST_FAILOVER`` are module
state and every hop logs a warning. A silent provider swap would let a degraded
run masquerade as a clean one — the same hazard class as the strict-tool
degrade in llm_gateway.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Final

logger = logging.getLogger(__name__)

# Observability: mirrors llm_gateway.STRICT_DEGRADE_COUNT so a caller (run_ai2)
# can stamp the degrade into engine_results.json rather than hiding it.
FAILOVER_COUNT: int = 0
LAST_FAILOVER: dict[str, Any] | None = None

_DEFAULT_ORDER: Final[str] = "grok,gemini"
_GROK_MODEL: Final[str] = os.getenv("GESH_GROK_MODEL", "grok-4.6")
_GROK_TIMEOUT_S: Final[float] = float(os.getenv("GESH_GROK_TIMEOUT_S", "900"))
_GROK_MAX_TURNS: Final[str] = os.getenv("GESH_GROK_MAX_TURNS", "4")

# The Grok CLI loads the user's global agent rules, which on this host instruct it
# to open a memory MCP session before answering ("I'll load OMEGA context first…").
# That burns agentic turns on a call that is a pure completion, and intermittently
# exhausted --max-turns and failed the whole fallback. A minimal tool allowlist plus
# an explicit rules override pins it to a single-turn answer.
_GROK_RULES: Final[str] = (
    "Answer using ONLY the text of this prompt. Do not call any tool, MCP server, "
    "memory system, or agent. Do not load context. Emit the answer immediately in "
    "your first message."
)
_GROK_TOOL_ALLOWLIST: Final[str] = "read_file"


class FailoverUnavailable(RuntimeError):
    """No configured fallback provider could satisfy the request."""


# ── Duck-typed Anthropic Message ──────────────────────────────────────────────
# Only the attributes actually read downstream are implemented; anything else is
# an explicit None so a typo surfaces as an AttributeError rather than silently
# reading a default.


class _ToolUseBlock:
    __slots__ = ("id", "input", "name", "type")

    def __init__(self, name: str, payload: dict[str, Any]) -> None:
        self.type = "tool_use"
        self.name = name
        self.input = payload
        self.id = "failover_tool_use"


class _Usage:
    __slots__ = (
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "input_tokens",
        "output_tokens",
    )

    def __init__(self) -> None:
        # A fallback provider's spend is NOT Anthropic spend; reporting zero here
        # keeps the Anthropic ledger honest instead of inventing token counts.
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_read_input_tokens = 0
        self.cache_creation_input_tokens = 0


class FailoverMessage:
    """Anthropic-Message-shaped response produced by a non-Anthropic provider."""

    def __init__(self, *, provider: str, model: str, tool_name: str, payload: dict[str, Any]):
        self.provider = provider
        self.model = model
        self.content = [_ToolUseBlock(tool_name, payload)]
        self.stop_reason = "tool_use"
        self.usage = _Usage()
        self.id = f"failover_{provider}"

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<FailoverMessage provider={self.provider} model={self.model}>"


# ── JSON recovery ─────────────────────────────────────────────────────────────

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json_object(text: str) -> dict[str, Any]:
    """Pull the first complete JSON object out of a free-text model response.

    Fallback providers have no forced-tool grammar, so the payload arrives as
    prose-wrapped text. Brace matching (rather than a greedy regex) is what makes
    this safe for the nested holdings_analysis array.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("empty model response")

    candidates: list[str] = []
    fenced = _FENCE_RE.findall(text)
    candidates.extend(block.strip() for block in fenced if block.strip())
    candidates.append(text.strip())

    for candidate in candidates:
        start = candidate.find("{")
        if start < 0:
            continue
        depth = 0
        in_str = False
        esc = False
        for idx in range(start, len(candidate)):
            ch = candidate[idx]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    blob = candidate[start : idx + 1]
                    try:
                        parsed = json.loads(blob)
                    except json.JSONDecodeError:
                        break
                    if isinstance(parsed, dict):
                        return parsed
                    break
    raise ValueError("no JSON object found in model response")


# ── Anthropic kwargs -> plain prompt ─────────────────────────────────────────


def _flatten_text(value: Any) -> str:
    """Anthropic accepts str or a list of content blocks; normalize both."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for block in value:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "\n".join(p for p in parts if p)
    return ""


def build_prompt(kwargs: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """Return (tool_name, prompt_text, schema) from Anthropic-style kwargs."""
    tools = kwargs.get("tools") or []
    if not tools:
        raise FailoverUnavailable("failover requires a tool schema (none supplied)")
    tool = tools[0]
    tool_name = str(tool.get("name") or "return_result")
    schema = tool.get("input_schema") or {}

    system_text = _flatten_text(kwargs.get("system"))
    user_parts = [
        _flatten_text(m.get("content"))
        for m in (kwargs.get("messages") or [])
        if isinstance(m, dict) and m.get("role") == "user"
    ]
    user_text = "\n\n".join(p for p in user_parts if p)

    prompt = f"""{system_text}

You must answer by returning ONE JSON object that validates against this JSON Schema:

```json
{json.dumps(schema, separators=(",", ":"))}
```

OUTPUT RULES — these override any other formatting instinct:
- Emit RAW JSON only. No prose before or after, no markdown fences, no commentary.
- Every key listed in "required" must be present. Never emit null for a required key.
- Numeric score fields are integers 0-100. Use the full range; do not cluster at 50.
- Do not invent live prices — use the prices given in the input data below.
- Do not use any tools. Answer directly from the data in this prompt.

TASK
{user_text}
"""
    return tool_name, prompt, schema


# ── Schema-shaped repair ─────────────────────────────────────────────────────

_ZERO_BY_TYPE: Final[dict[str, Any]] = {
    "integer": 0,
    "number": 0,
    "string": "",
    "array": [],
    "boolean": False,
}


def coerce_to_schema(payload: Any, schema: dict[str, Any]) -> Any:
    """Fill required keys a fallback provider omitted, with type-correct blanks.

    Downstream renderers index required keys directly. A provider without forced
    grammar WILL occasionally drop one; a typed blank keeps the report honest
    (an empty string renders as empty) instead of raising mid-chain.
    """
    node_type = schema.get("type")
    if node_type == "object":
        if not isinstance(payload, dict):
            payload = {}
        props = schema.get("properties") or {}
        out = dict(payload)
        for key, sub in props.items():
            if key in out and out[key] is not None:
                out[key] = coerce_to_schema(out[key], sub)
            elif key in (schema.get("required") or []):
                out[key] = coerce_to_schema(None, sub)
        return out
    if node_type == "array":
        items = schema.get("items") or {}
        if not isinstance(payload, list):
            return []
        return [coerce_to_schema(item, items) for item in payload]
    if payload is None:
        return _ZERO_BY_TYPE.get(str(node_type), None)
    if node_type == "integer":
        try:
            return round(float(payload))
        except (TypeError, ValueError):
            return 0
    if node_type == "number":
        try:
            return float(payload)
        except (TypeError, ValueError):
            return 0.0
    if node_type == "string" and not isinstance(payload, str):
        return str(payload)
    return payload


# ── Providers ────────────────────────────────────────────────────────────────


def grok_available() -> bool:
    if shutil.which("grok") is None:
        return False
    # The CLI falls back to XAI_API_KEY when no OAuth session is cached.
    return Path.home().joinpath(".grok", "auth.json").exists() or bool(os.getenv("XAI_API_KEY"))


def call_grok(prompt: str, *, timeout_s: float | None = None) -> str:
    """Run the Grok CLI headless and return its final text answer.

    The prompt goes through --prompt-file: a deep-analysis prompt is tens of KB
    and would risk ARG_MAX as an argv string. --max-turns 1 keeps it a single
    completion rather than an agentic loop that might try to read the repo.
    """
    if shutil.which("grok") is None:
        raise FailoverUnavailable("grok CLI not on PATH")

    timeout_s = timeout_s or _GROK_TIMEOUT_S
    with tempfile.TemporaryDirectory(prefix="gesh-failover.") as workdir:
        prompt_path = Path(workdir) / "prompt.txt"
        prompt_path.write_text(prompt, encoding="utf-8")
        base = [
            "grok",
            "--prompt-file",
            str(prompt_path),
            "--verbatim",
            "--output-format",
            "json",
            "--max-turns",
            _GROK_MAX_TURNS,
            "--tools",
            _GROK_TOOL_ALLOWLIST,
            "--rules",
            _GROK_RULES,
            "--no-auto-update",
        ]

        def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,  # a non-zero exit is a provider failure we translate below
                cwd=workdir,  # never let it wander this repo
            )

        proc = _run(base + ["--model", _GROK_MODEL])
        # xAI rotates model ids (grok-build -> grok-4.6 broke this once already).
        # A pinned-but-retired id must degrade to the CLI's own default rather
        # than take down the fallback that exists precisely to survive outages.
        if proc.returncode != 0 and "unknown model id" in (proc.stderr or proc.stdout or "").lower():
            logger.warning(
                "LLM_FAILOVER: grok model %r rejected as unknown — retrying on the CLI default. "
                "Set GESH_GROK_MODEL to a current id from `grok models`.",
                _GROK_MODEL,
            )
            proc = _run(base)

    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-400:]
        raise FailoverUnavailable(f"grok CLI exited {proc.returncode}: {tail}")

    raw = proc.stdout or ""
    # --output-format json wraps the answer in an envelope; fall back to raw text
    # if the envelope shape ever changes rather than failing the whole chain.
    try:
        env = json.loads(raw)
        if isinstance(env, dict):
            for key in ("text", "result", "response", "content", "output"):
                val = env.get(key)
                if isinstance(val, str) and val.strip():
                    return val
    except json.JSONDecodeError:
        pass
    return raw


def gemini_available() -> bool:
    return bool(os.getenv("GEMINI_API_KEY"))


def call_gemini(prompt: str) -> str:
    if not gemini_available():
        raise FailoverUnavailable("GEMINI_API_KEY unset")
    try:
        import gemini_agent
    except Exception as exc:  # pragma: no cover - import guard
        raise FailoverUnavailable(f"gemini_agent unavailable: {exc}") from exc
    text = gemini_agent._generate(prompt, model="gemini-2.5-pro")
    if not isinstance(text, str) or not text.strip():
        raise FailoverUnavailable("gemini returned empty text")
    return text


_PROVIDERS: Final[dict[str, tuple[Any, Any]]] = {
    "grok": (grok_available, call_grok),
    "gemini": (gemini_available, call_gemini),
}


def provider_order() -> list[str]:
    raw = os.getenv("GESH_LLM_FAILOVER", _DEFAULT_ORDER)
    return [p.strip().lower() for p in raw.split(",") if p.strip()]


def enabled() -> bool:
    """Failover is opt-out; setting GESH_LLM_FAILOVER='' disables it."""
    return bool(provider_order())


def current_provenance() -> tuple[str, int]:
    """``(provider, failover_count)`` for engine_results + the analysis_runs model id.

    A failover run must never be recorded as a clean Anthropic run. Lookup
    errors stay fail-safe (``('anthropic', 0)``) so a broken observability
    import cannot invent a failover. When the count is non-zero but
    ``LAST_FAILOVER`` is missing or not a dict, the provider stays
    ``anthropic`` — matching the original run_ai2 inline contract.
    """
    try:
        count = int(FAILOVER_COUNT or 0)
    except (TypeError, ValueError):
        return "anthropic", 0
    provider = "anthropic"
    if count and isinstance(LAST_FAILOVER, dict):
        provider = LAST_FAILOVER.get("provider") or "unknown"
    return provider, count


def analysis_model_label(default_model: str) -> str:
    """DB / ledger model id: rewrite a failover so it cannot read as Claude."""
    provider, count = current_provenance()
    if count:
        return f"{provider} (FAILOVER, anthropic down)"
    return default_model


# ── Entry point used by llm_gateway ──────────────────────────────────────────


def attempt(*, operation: str, max_tokens: int, **kwargs: Any) -> FailoverMessage:
    """Try each configured provider; return the first Anthropic-shaped success."""
    global FAILOVER_COUNT, LAST_FAILOVER

    order = provider_order()
    if not order:
        raise FailoverUnavailable("failover disabled (GESH_LLM_FAILOVER empty)")

    tool_name, prompt, schema = build_prompt(kwargs)
    errors: list[str] = []

    for name in order:
        entry = _PROVIDERS.get(name)
        if entry is None:
            errors.append(f"{name}: unknown provider")
            continue
        is_available, call = entry
        try:
            if not is_available():
                errors.append(f"{name}: not configured")
                continue
            logger.warning(
                "LLM_FAILOVER: Anthropic unavailable for operation=%s — falling back to %s. "
                "Output is NOT grammar-constrained; schema coercion applies.",
                operation,
                name,
            )
            text = call(prompt)
            payload = extract_json_object(text)
            payload = coerce_to_schema(payload, schema)
            if not isinstance(payload, dict) or not payload:
                raise ValueError("coerced payload empty")

            FAILOVER_COUNT += 1
            LAST_FAILOVER = {"operation": operation, "provider": name}
            logger.warning("LLM_FAILOVER: operation=%s satisfied by %s", operation, name)
            return FailoverMessage(
                provider=name,
                model=_GROK_MODEL if name == "grok" else name,
                tool_name=tool_name,
                payload=payload,
            )
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
            logger.warning("LLM_FAILOVER: provider %s failed: %s", name, exc)

    raise FailoverUnavailable("; ".join(errors) or "no providers configured")
