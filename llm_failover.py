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
``xai,grok,gemini``):

  xai     — SpaceXAI HTTP Chat Completions (``https://api.x.ai/v1/chat/completions``)
            with ``XAI_API_KEY``. Model ``grok-4.6``. This is the production path
            for pay.geshfinance.com — no CLI, no launchd PATH hole.
  grok    — the Grok CLI in headless mode. Uses ~/.grok/auth.json (grok.com
            login, no extra API bill) when XAI_API_KEY is unset.
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

import httpx

logger = logging.getLogger(__name__)

# Observability: mirrors llm_gateway.STRICT_DEGRADE_COUNT so a caller (run_ai2)
# can stamp the degrade into engine_results.json rather than hiding it.
FAILOVER_COUNT: int = 0
LAST_FAILOVER: dict[str, Any] | None = None

_DEFAULT_ORDER: Final[str] = "xai,grok,gemini"
_GROK_MODEL: Final[str] = os.getenv("GESH_GROK_MODEL", "grok-4.6")
_GROK_TIMEOUT_S: Final[float] = float(os.getenv("GESH_GROK_TIMEOUT_S", "900"))
_GROK_MAX_TURNS: Final[str] = os.getenv("GESH_GROK_MAX_TURNS", "4")
_XAI_URL: Final[str] = "https://api.x.ai/v1/chat/completions"
_XAI_MODEL: Final[str] = os.getenv("GESH_XAI_MODEL", "grok-4.6")
_XAI_TIMEOUT_S: Final[float] = float(os.getenv("GESH_XAI_TIMEOUT_S", "900"))
# Interactive intel (news/SEC/fundamentals) must not inherit the 15-minute deep
# analysis budget. One hung grok-4.6 call per ticker froze Company Intelligence.
_XAI_FAST_TIMEOUT_S: Final[float] = float(os.getenv("GESH_XAI_FAST_TIMEOUT_S", "20"))
_FAST_OPS: Final[frozenset[str]] = frozenset({"news_sentiment", "sec_filing", "fundamental_scan"})
# gemini-2.5-pro 404s for new API users ("no longer available"). Flash is the
# live default; the id is env-overridable and a 404 degrades to the lite model
# the same way a retired Grok pin degrades to the CLI default.
_GEMINI_FAILOVER_DEFAULT: Final[str] = "gemini-2.5-flash"
_GEMINI_FAILOVER_FALLBACK_DEFAULT: Final[str] = "gemini-2.0-flash-lite"

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
        # Omitted scores must not become 0 — a missing portfolio_score rendered
        # as "0 / 100" is a fabricated measurement (gap F-11). Strings/arrays
        # stay typed blanks so required-key readers do not KeyError.
        if node_type in ("integer", "number"):
            return None
        return _ZERO_BY_TYPE.get(str(node_type), None)
    if node_type == "integer":
        try:
            return round(float(payload))
        except (TypeError, ValueError):
            return None
    if node_type == "number":
        try:
            return float(payload)
        except (TypeError, ValueError):
            return None
    if node_type == "string" and not isinstance(payload, str):
        return str(payload)
    return payload


# ── Providers ────────────────────────────────────────────────────────────────

# launchd's default PATH is /usr/bin:/bin:/usr/sbin:/sbin. The Grok CLI lives in
# ~/.local/bin. shutil.which("grok") is therefore False in every :9000 process
# started by com.gesh.main — the same hole that hid Grok from the scheduled
# chain (scripts/daily_analysis_et.sh). Search well-known locations too.
_GROK_FALLBACK_BINS: Final[tuple[str, ...]] = (
    os.path.expanduser("~/.local/bin/grok"),
    "/opt/homebrew/bin/grok",
    "/usr/local/bin/grok",
)


def _grok_executable() -> str | None:
    found = shutil.which("grok")
    if found:
        return found
    for raw in _GROK_FALLBACK_BINS:
        if os.path.isfile(raw) and os.access(raw, os.X_OK):
            return raw
    return None


def grok_available() -> bool:
    if _grok_executable() is None:
        return False
    # The CLI falls back to XAI_API_KEY when no OAuth session is cached.
    return Path.home().joinpath(".grok", "auth.json").exists() or bool(os.getenv("XAI_API_KEY"))


def call_grok(prompt: str, *, timeout_s: float | None = None) -> str:
    """Run the Grok CLI headless and return its final text answer.

    The prompt goes through --prompt-file: a deep-analysis prompt is tens of KB
    and would risk ARG_MAX as an argv string. --max-turns 1 keeps it a single
    completion rather than an agentic loop that might try to read the repo.
    """
    grok_bin = _grok_executable()
    if grok_bin is None:
        raise FailoverUnavailable("grok CLI not on PATH")

    timeout_s = timeout_s or _GROK_TIMEOUT_S
    with tempfile.TemporaryDirectory(prefix="gesh-failover.") as workdir:
        prompt_path = Path(workdir) / "prompt.txt"
        prompt_path.write_text(prompt, encoding="utf-8")
        base = [
            grok_bin,
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
        if (
            proc.returncode != 0
            and "unknown model id" in (proc.stderr or proc.stdout or "").lower()
        ):
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


def xai_available() -> bool:
    return bool(os.getenv("XAI_API_KEY"))


def _xai_timeout_s(operation: str = "") -> float:
    if operation in _FAST_OPS:
        return _XAI_FAST_TIMEOUT_S
    return _XAI_TIMEOUT_S


def _xai_http_error_summary(response: Any) -> str:
    """Status + optional error type/code. Never the raw body (may echo prompts)."""
    kind = ""
    try:
        body = response.json()
    except Exception:
        body = None
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            kind = str(err.get("type") or err.get("code") or "")[:80]
    suffix = f" ({kind})" if kind else ""
    return f"xAI HTTP {response.status_code}{suffix}"


def call_xai(prompt: str, *, operation: str = "") -> str:
    """SpaceXAI Chat Completions. Key from env only — never logged."""
    key = os.getenv("XAI_API_KEY") or ""
    if not key:
        raise FailoverUnavailable("XAI_API_KEY unset")
    model = os.getenv("GESH_XAI_MODEL") or _XAI_MODEL
    try:
        response = httpx.post(
            _XAI_URL,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
            },
            timeout=_xai_timeout_s(operation),
        )
    except Exception as exc:
        raise FailoverUnavailable(f"xAI request failed: {type(exc).__name__}") from exc
    if response.status_code >= 400:
        raise FailoverUnavailable(_xai_http_error_summary(response))
    try:
        data = response.json()
    except Exception as exc:
        raise FailoverUnavailable("xAI returned non-JSON") from exc
    choices = data.get("choices") if isinstance(data, dict) else None
    if isinstance(choices, list) and choices:
        msg = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, str) and content.strip():
            return content
    raise FailoverUnavailable("xAI returned empty content")


def gemini_available() -> bool:
    return bool(os.getenv("GEMINI_API_KEY"))


def _gemini_failover_model() -> str:
    return os.getenv("GESH_GEMINI_FAILOVER_MODEL") or _GEMINI_FAILOVER_DEFAULT


def _gemini_failover_fallback() -> str:
    return os.getenv("GESH_GEMINI_FAILOVER_FALLBACK") or _GEMINI_FAILOVER_FALLBACK_DEFAULT


def _is_missing_gemini_model(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "404" in text or "not_found" in text or "no longer available" in text


def call_gemini(prompt: str) -> str:
    if not gemini_available():
        raise FailoverUnavailable("GEMINI_API_KEY unset")
    try:
        import gemini_agent
    except Exception as exc:  # pragma: no cover - import guard
        raise FailoverUnavailable(f"gemini_agent unavailable: {exc}") from exc

    models = [_gemini_failover_model()]
    fallback = _gemini_failover_fallback()
    if fallback not in models:
        models.append(fallback)

    last_exc: BaseException | None = None
    for model in models:
        try:
            text = gemini_agent._generate(prompt, model=model)
        except Exception as exc:
            last_exc = exc
            if (
                _is_missing_gemini_model(exc)
                or "429" in str(exc)
                or "RESOURCE_EXHAUSTED" in str(exc)
            ):
                logger.warning(
                    "LLM_FAILOVER: gemini model %r failed (%s) — retrying fallback. "
                    "Set GESH_GEMINI_FAILOVER_MODEL to a current id.",
                    model,
                    exc,
                )
                continue
            raise FailoverUnavailable(f"gemini failed: {exc}") from exc
        if isinstance(text, str) and text.strip():
            return text
        last_exc = FailoverUnavailable("gemini returned empty text")
    raise FailoverUnavailable(f"gemini failed: {last_exc}")


_PROVIDERS: Final[dict[str, tuple[Any, Any]]] = {
    "xai": (xai_available, call_xai),
    "grok": (grok_available, call_grok),
    "gemini": (gemini_available, call_gemini),
}


def provider_order() -> list[str]:
    raw = os.getenv("GESH_LLM_FAILOVER", _DEFAULT_ORDER)
    return [p.strip().lower() for p in raw.split(",") if p.strip()]


def enabled() -> bool:
    """Failover is opt-out; setting GESH_LLM_FAILOVER='' disables it."""
    return bool(provider_order())


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
            if operation in _FAST_OPS and name == "grok":
                errors.append("grok: skipped (slow CLI) for fast operation")
                continue
            logger.warning(
                "LLM_FAILOVER: Anthropic unavailable for operation=%s — falling back to %s. "
                "Output is NOT grammar-constrained; schema coercion applies.",
                operation,
                name,
            )
            text = call(prompt, operation=operation) if call is call_xai else call(prompt)
            payload = extract_json_object(text)
            payload = coerce_to_schema(payload, schema)
            if not isinstance(payload, dict) or not payload:
                raise ValueError("coerced payload empty")

            FAILOVER_COUNT += 1
            LAST_FAILOVER = {"operation": operation, "provider": name}
            logger.warning("LLM_FAILOVER: operation=%s satisfied by %s", operation, name)
            model = _XAI_MODEL if name == "xai" else (_GROK_MODEL if name == "grok" else name)
            return FailoverMessage(
                provider=name,
                model=model,
                tool_name=tool_name,
                payload=payload,
            )
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
            logger.warning("LLM_FAILOVER: provider %s failed: %s", name, exc)

    raise FailoverUnavailable("; ".join(errors) or "no providers configured")
