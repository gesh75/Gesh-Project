"""Canonical Claude model routing, client construction, and spend-aware Gateway.

Every Anthropic-backed analysis path constructs clients and resolves model IDs
through this module. The Gateway owns retries, durable spend reservations, and
provider telemetry — callers must not wrap create/stream with their own retry
loops or ad-hoc usage JSONL.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Final, Literal

import anthropic
import httpx

import llm_failover

ModelAlias = Literal["sonnet", "haiku"]
MODEL_IDS: Final[dict[ModelAlias, str]] = {
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5",
}
CANONICAL_MODELS: Final[frozenset[str]] = frozenset(MODEL_IDS.values())

# (input $, output $) per million tokens — used for reservations and settlement.
_PRICE_PER_MTOK: Final[dict[str, tuple[float, float]]] = {
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}
_DEFAULT_PRICE: Final[tuple[float, float]] = (3.0, 15.0)
# Worst-case input assumption for admission reservations (documented ceiling).
_RESERVE_INPUT_TOKENS: Final[int] = 50_000
_DEFAULT_USAGE_DB: Final[Path] = Path(__file__).with_name("llm_usage.db")
_MAX_ATTEMPTS_CAP: Final[int] = 3

logger = logging.getLogger(__name__)


# ── Exception types ───────────────────────────────────────────────────────────


class LLMConfigurationError(RuntimeError):
    """Raised when model routing or client configuration is invalid."""


class LLMProviderUnconfiguredError(LLMConfigurationError):
    """The primary provider has no credentials at all — not a malformed setting.

    Split out from LLMConfigurationError (2026-08-21) because the two cases warrant
    OPPOSITE handling and were previously conflated:

      * a malformed value (GESH_LLM_TIMEOUT_SECONDS="abc") is an operator error and
        must fail LOUDLY — failing over would hide a bug;
      * an ABSENT ANTHROPIC_API_KEY is the expected steady state whenever the
        subscription lapses, and must fail OVER to Grok/Gemini.

    Only this subclass is in _FAILOVER_TRIGGERS. Do NOT add the parent class — that
    would silently launder real misconfiguration through a second vendor.

    Subclasses LLMConfigurationError so every existing `except LLMConfigurationError`
    handler keeps working unchanged.
    """


class LLMUnavailableError(RuntimeError):
    """Provider temporarily unreachable after exhausting retries."""


class LLMRefusalError(RuntimeError):
    """Model refused to complete the request (stop_reason=refusal)."""


class LLMTruncatedError(RuntimeError):
    """Model hit max_tokens before a complete response."""


class LLMBudgetExceededError(RuntimeError):
    """Daily provider spend ceiling would be exceeded by a new reservation."""


# Safe customer-facing copy (also used as exception messages where noted).
_MSG_REFUSAL = "AI research could not be completed for this request"
_MSG_UNAVAILABLE = "AI provider temporarily unavailable"
_MSG_TRUNCATED = "AI response exceeded its configured output limit"


# ── Model / client helpers (unchanged public surface) ─────────────────────────


def resolve_model(value: str | None, *, default: ModelAlias) -> str:
    """Map an alias or exact canonical ID to a supported Claude model ID.

    Unknown values (including dated legacy identifiers) fail closed — never
    silently fall back to a different tier.
    """
    selected = (value or default).strip().lower()
    if selected in MODEL_IDS:
        return MODEL_IDS[selected]  # type: ignore[index]
    if selected in CANONICAL_MODELS:
        return selected
    raise LLMConfigurationError(
        f"Unsupported Claude model {selected!r}; use one of "
        f"{', '.join(sorted((*MODEL_IDS, *CANONICAL_MODELS)))}"
    )


def default_timeout_seconds() -> float:
    """Read GESH_LLM_TIMEOUT_SECONDS (default 240); validate the closed range."""
    raw = os.getenv("GESH_LLM_TIMEOUT_SECONDS", "240")
    try:
        timeout = float(raw)
    except (TypeError, ValueError) as exc:
        raise LLMConfigurationError("GESH_LLM_TIMEOUT_SECONDS must be a number") from exc
    if not 1.0 <= timeout <= 600.0:
        raise LLMConfigurationError("GESH_LLM_TIMEOUT_SECONDS must be between 1 and 600")
    return timeout


def max_attempts_from_env() -> int:
    """Read GESH_LLM_MAX_ATTEMPTS (default 3); values above 3 fail closed."""
    raw = os.getenv("GESH_LLM_MAX_ATTEMPTS", "3")
    try:
        n = int(raw)
    except (TypeError, ValueError) as exc:
        raise LLMConfigurationError("GESH_LLM_MAX_ATTEMPTS must be an integer") from exc
    if n < 1:
        raise LLMConfigurationError("GESH_LLM_MAX_ATTEMPTS must be >= 1")
    if n > _MAX_ATTEMPTS_CAP:
        raise LLMConfigurationError(
            f"GESH_LLM_MAX_ATTEMPTS must be <= {_MAX_ATTEMPTS_CAP} (got {n})"
        )
    return n


def budget_usd_from_env() -> float:
    """Read LLM_DAILY_BUDGET_USD; <=0 means unlimited (local/test convenience)."""
    raw = os.getenv("LLM_DAILY_BUDGET_USD", "0")
    try:
        return float(raw or 0)
    except (TypeError, ValueError) as exc:
        raise LLMConfigurationError("LLM_DAILY_BUDGET_USD must be a number") from exc


def usage_db_path_from_env() -> Path:
    raw = os.getenv("LLM_USAGE_DB")
    if raw:
        return Path(raw)
    return _DEFAULT_USAGE_DB


def create_client(*, timeout_seconds: float | None = None) -> anthropic.Anthropic:
    """Build a bounded Anthropic client with SDK retries disabled.

    Gateway-owned retry accounting is authoritative. SDK retries would hide
    attempt counts from telemetry and spend reservations.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        # Unconfigured != misconfigured. This one fails OVER (see the class docstring).
        raise LLMProviderUnconfiguredError("Anthropic service is not configured")
    timeout = default_timeout_seconds() if timeout_seconds is None else float(timeout_seconds)
    if not 1.0 <= timeout <= 600.0:
        raise LLMConfigurationError("GESH_LLM_TIMEOUT_SECONDS must be between 1 and 600")
    return anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=0)


def backoff_seconds(attempt_no: int, retry_after: float | None = None) -> float:
    """Sleep duration before the next attempt. Monkeypatchable in tests."""
    if retry_after is not None:
        try:
            return max(0.0, float(retry_after))
        except (TypeError, ValueError):
            pass
    # 1s, 2s, 4s … capped
    return float(min(2 ** max(0, attempt_no - 1), 8))


def _prices_for(model: str) -> tuple[float, float]:
    return _PRICE_PER_MTOK.get(model, _DEFAULT_PRICE)


def estimate_cost_microusd(model: str, input_tokens: int, output_tokens: int) -> int:
    """Convert token counts to integer microdollars (1e-6 USD)."""
    p_in, p_out = _prices_for(model)
    # dollars → microdollars
    usd = (input_tokens * p_in + output_tokens * p_out) / 1_000_000
    return int(round(usd * 1_000_000))


def reserve_cost_microusd(model: str, max_tokens: int) -> int:
    """Documented max reservation: 50k input + max_tokens output at list prices."""
    return estimate_cost_microusd(model, _RESERVE_INPUT_TOKENS, int(max_tokens))


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(
        exc,
        (
            anthropic.APITimeoutError,
            anthropic.APIConnectionError,
            anthropic.RateLimitError,
            httpx.TimeoutException,
            httpx.ConnectError,
            httpx.ReadTimeout,
            httpx.WriteTimeout,
            httpx.ConnectTimeout,
            httpx.PoolTimeout,
        ),
    ):
        return True
    # Some SDK versions wrap transport errors.
    cause = getattr(exc, "__cause__", None)
    if cause is not None and cause is not exc and _is_retryable(cause):
        return True
    return False


# ── Strict-tool grammar degradation ───────────────────────────────────────────
#
# Anthropic compiles a constrained-decoding grammar for every tool carrying
# `strict: True`, and rejects the request with a 400 when that grammar exceeds an
# internal size ceiling. The ceiling is a PROVIDER-SIDE limit we do not control:
# `DEEP_ANALYSIS_SCHEMA` (48 properties / 11 nested objects / depth 5, four
# unbounded object arrays) sits close enough to it that the same unchanged schema
# was accepted on 2026-08-05 and 2026-08-06 and rejected on 2026-08-04 and
# 2026-08-07 — a hard, deterministic-per-request failure that no retry can clear.
#
# Dropping `strict` costs only server-side output CONSTRAINT, not the contract:
# `input_schema` is still sent, so the model still targets the same shape, and
# `llm_contracts.extract_tool_input` + the field validators still reject anything
# malformed downstream. Degrading therefore preserves the response contract while
# turning a fatal 400 into a warning — but it must never be silent, so every
# degrade logs at WARNING and is counted (rule: a degraded state must say so).

_GRAMMAR_ERROR_MARKERS: Final[tuple[str, ...]] = (
    "compiled grammar is too large",
    "reduce the number of strict tools",
)

#: Count of strict->non-strict degradations this process; surfaced by callers
#: (and asserted by tests) so a silent, permanent degrade cannot go unnoticed.
STRICT_DEGRADE_COUNT: int = 0


def _is_grammar_too_large(exc: BaseException) -> bool:
    """True for the provider 400 that rejects an over-large strict-tool grammar."""
    if not isinstance(exc, anthropic.BadRequestError):
        return False
    text = str(exc).lower()
    return any(marker in text for marker in _GRAMMAR_ERROR_MARKERS)


def _without_strict_tools(kwargs: dict[str, Any]) -> dict[str, Any] | None:
    """Copy of ``kwargs`` with ``strict`` removed from every tool.

    Returns ``None`` when there is nothing to degrade, so the caller re-raises
    the original error instead of silently retrying an identical request.
    """
    tools = kwargs.get("tools")
    if not isinstance(tools, list):
        return None
    degraded_tools: list[Any] = []
    changed = False
    for tool in tools:
        if isinstance(tool, dict) and "strict" in tool:
            relaxed = {k: v for k, v in tool.items() if k != "strict"}
            degraded_tools.append(relaxed)
            changed = True
        else:
            degraded_tools.append(tool)
    if not changed:
        return None
    return {**kwargs, "tools": degraded_tools}


def _retry_after_seconds(exc: BaseException) -> float | None:
    headers = getattr(exc, "response", None)
    if headers is not None:
        hdrs = getattr(headers, "headers", None) or {}
        raw = hdrs.get("retry-after") if hasattr(hdrs, "get") else None
        if raw is not None:
            try:
                return float(raw)
            except (TypeError, ValueError):
                return None
    return None


def _message_request_id(message: Any) -> str | None:
    rid = getattr(message, "_request_id", None)
    if rid:
        return str(rid)
    mid = getattr(message, "id", None)
    if mid:
        return str(mid)
    return None


def _usage_tokens(message: Any) -> tuple[int, int, int, int]:
    usage = getattr(message, "usage", None)
    if usage is None:
        return 0, 0, 0, 0
    in_tok = int(getattr(usage, "input_tokens", 0) or 0)
    out_tok = int(getattr(usage, "output_tokens", 0) or 0)
    cache_read = int(
        getattr(usage, "cache_read_input_tokens", None)
        or getattr(usage, "cache_read_tokens", 0)
        or 0
    )
    cache_create = int(
        getattr(usage, "cache_creation_input_tokens", None)
        or getattr(usage, "cache_creation_tokens", 0)
        or 0
    )
    return in_tok, out_tok, cache_read, cache_create


# ── Durable spend ledger ──────────────────────────────────────────────────────


class _Ledger:
    """SQLite ledger for reservations and provider telemetry.

    Never stores prompts, messages, tools, holdings, symbols, emails, or
    exception text — only operation/model/status/tokens/microusd/error_kind.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS llm_calls (
                    call_id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    operation TEXT NOT NULL,
                    model TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN (
                            'reserved',
                            'succeeded',
                            'failed_unknown_cost',
                            'rejected',
                            'truncated'
                        )
                    ),
                    attempt_no INTEGER NOT NULL,
                    request_id TEXT,
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    cache_read_tokens INTEGER,
                    cache_creation_tokens INTEGER,
                    reserved_microusd INTEGER NOT NULL,
                    settled_microusd INTEGER,
                    error_kind TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_llm_calls_started_at ON llm_calls(started_at)"
            )
            conn.commit()

    def _today_committed_microusd(self, conn: sqlite3.Connection) -> int:
        """Sum today's reserved (pending/unknown) + settled (terminal) spend."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        row = conn.execute(
            """
            SELECT COALESCE(SUM(
                CASE
                    WHEN status IN ('reserved', 'failed_unknown_cost')
                        THEN reserved_microusd
                    WHEN status IN ('succeeded', 'rejected', 'truncated')
                        THEN COALESCE(settled_microusd, reserved_microusd)
                    ELSE 0
                END
            ), 0) AS committed
            FROM llm_calls
            WHERE started_at >= ?
            """,
            (today,),
        ).fetchone()
        return int(row["committed"] if row else 0)

    def reserve(
        self,
        *,
        operation: str,
        model: str,
        attempt_no: int,
        reserved_microusd: int,
        budget_microusd: int | None,
    ) -> str:
        """Atomically admit a call under the daily budget; return call_id."""
        call_id = uuid.uuid4().hex
        started = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if budget_microusd is not None and budget_microusd > 0:
                    committed = self._today_committed_microusd(conn)
                    if committed + reserved_microusd > budget_microusd:
                        conn.execute("ROLLBACK")
                        raise LLMBudgetExceededError(_MSG_UNAVAILABLE)
                conn.execute(
                    """
                    INSERT INTO llm_calls (
                        call_id, started_at, operation, model, status,
                        attempt_no, reserved_microusd
                    ) VALUES (?, ?, ?, ?, 'reserved', ?, ?)
                    """,
                    (call_id, started, operation, model, attempt_no, reserved_microusd),
                )
                conn.commit()
            except LLMBudgetExceededError:
                raise
            except Exception:
                conn.rollback()
                raise
        return call_id

    def mark_unknown_cost(self, call_id: str, *, error_kind: str) -> None:
        completed = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE llm_calls
                SET status = 'failed_unknown_cost',
                    completed_at = ?,
                    error_kind = ?
                WHERE call_id = ?
                """,
                (completed, error_kind, call_id),
            )
            conn.commit()

    def mark_rejected(
        self,
        call_id: str,
        *,
        error_kind: str,
        settled_microusd: int | None = None,
        request_id: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cache_read_tokens: int | None = None,
        cache_creation_tokens: int | None = None,
    ) -> None:
        completed = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE llm_calls
                SET status = 'rejected',
                    completed_at = ?,
                    error_kind = ?,
                    settled_microusd = COALESCE(?, reserved_microusd),
                    request_id = COALESCE(?, request_id),
                    input_tokens = COALESCE(?, input_tokens),
                    output_tokens = COALESCE(?, output_tokens),
                    cache_read_tokens = COALESCE(?, cache_read_tokens),
                    cache_creation_tokens = COALESCE(?, cache_creation_tokens)
                WHERE call_id = ?
                """,
                (
                    completed,
                    error_kind,
                    settled_microusd,
                    request_id,
                    input_tokens,
                    output_tokens,
                    cache_read_tokens,
                    cache_creation_tokens,
                    call_id,
                ),
            )
            conn.commit()

    def mark_truncated(
        self,
        call_id: str,
        *,
        message: Any,
        settled_microusd: int,
    ) -> None:
        completed = datetime.now(timezone.utc).isoformat()
        in_tok, out_tok, cache_read, cache_create = _usage_tokens(message)
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE llm_calls
                SET status = 'truncated',
                    completed_at = ?,
                    request_id = ?,
                    input_tokens = ?,
                    output_tokens = ?,
                    cache_read_tokens = ?,
                    cache_creation_tokens = ?,
                    settled_microusd = ?,
                    error_kind = 'LLMTruncatedError'
                WHERE call_id = ?
                """,
                (
                    completed,
                    _message_request_id(message),
                    in_tok,
                    out_tok,
                    cache_read,
                    cache_create,
                    settled_microusd,
                    call_id,
                ),
            )
            conn.commit()

    def settle_message(self, call_id: str, message: Any, *, model: str) -> Any:
        completed = datetime.now(timezone.utc).isoformat()
        in_tok, out_tok, cache_read, cache_create = _usage_tokens(message)
        settled = estimate_cost_microusd(model, in_tok, out_tok)
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE llm_calls
                SET status = 'succeeded',
                    completed_at = ?,
                    request_id = ?,
                    input_tokens = ?,
                    output_tokens = ?,
                    cache_read_tokens = ?,
                    cache_creation_tokens = ?,
                    settled_microusd = ?,
                    error_kind = NULL
                WHERE call_id = ?
                """,
                (
                    completed,
                    _message_request_id(message),
                    in_tok,
                    out_tok,
                    cache_read,
                    cache_create,
                    settled,
                    call_id,
                ),
            )
            conn.commit()
        return message

    def daily_usage_summary(self, days: int = 1) -> dict[str, Any]:
        days = max(1, int(days))
        cutoff = datetime.now(timezone.utc).timestamp() - (days * 86400)
        cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM llm_calls WHERE started_at >= ? ORDER BY started_at",
                (cutoff_iso,),
            ).fetchall()

        attempt_count = len(rows)
        by_status: dict[str, int] = {}
        by_operation: dict[str, int] = {}
        by_model: dict[str, int] = {}
        total_micro = 0
        cache_read_total = 0
        unknown = 0
        refusals = 0
        truncations = 0
        succeeded = 0
        failed = 0

        for r in rows:
            status = r["status"]
            by_status[status] = by_status.get(status, 0) + 1
            by_operation[r["operation"]] = by_operation.get(r["operation"], 0) + 1
            by_model[r["model"]] = by_model.get(r["model"], 0) + 1
            cache_read_total += int(r["cache_read_tokens"] or 0)
            if status in ("reserved", "failed_unknown_cost"):
                total_micro += int(r["reserved_microusd"] or 0)
                if status == "failed_unknown_cost":
                    unknown += 1
                    failed += 1
            else:
                total_micro += int(
                    r["settled_microusd"]
                    if r["settled_microusd"] is not None
                    else r["reserved_microusd"] or 0
                )
            if status == "succeeded":
                succeeded += 1
            elif status == "rejected":
                failed += 1
                if r["error_kind"] == "LLMRefusalError":
                    refusals += 1
            elif status == "truncated":
                failed += 1
                truncations += 1

        return {
            "days": days,
            "attempt_count": attempt_count,
            "calls": attempt_count,
            "succeeded": succeeded,
            "failed": failed,
            "unknown_charge_reservations": unknown,
            "refusals": refusals,
            "truncations": truncations,
            "cache_read_tokens": cache_read_total,
            "total_estimated_spend_usd": round(total_micro / 1_000_000, 6),
            "total_estimated_spend_microusd": total_micro,
            "by_status": by_status,
            "by_operation": by_operation,
            "by_model": by_model,
        }


# ── Gateway ───────────────────────────────────────────────────────────────────


# Provider-level failures that justify handing the call to another vendor.
# Deliberately excludes LLMRefusalError (a refusal must not be laundered through a second
# vendor) and LLMTruncatedError (a schema-too-large bug a silent fallback would hide).
#
# LLMProviderUnconfiguredError IS included; its parent LLMConfigurationError is NOT.
# An absent API key is the expected state once the subscription lapses and must fail over;
# a malformed setting is an operator error and must still fail loudly. Adding the parent
# class here would collapse that distinction — don't.
_FAILOVER_TRIGGERS: Final[tuple[type[BaseException], ...]] = (
    LLMUnavailableError,
    LLMBudgetExceededError,
    LLMProviderUnconfiguredError,
    anthropic.APIStatusError,
    anthropic.APIConnectionError,
    anthropic.APITimeoutError,
)


class Gateway:
    """Single entry point for Anthropic create/stream with spend accounting."""

    def __init__(
        self,
        *,
        db_path: Path | None = None,
        client_factory: Callable[[], Any] | None = None,
        budget_usd: float | None = None,
        max_attempts: int | None = None,
    ) -> None:
        self.db_path = Path(db_path) if db_path is not None else usage_db_path_from_env()
        # Resolve create_client at call time so monkeypatches on
        # llm_gateway.create_client remain effective for the default gateway.
        self._client_factory = client_factory or (lambda: create_client())
        if budget_usd is None:
            self._budget_usd = budget_usd_from_env()
        else:
            self._budget_usd = float(budget_usd)
        if max_attempts is None:
            self._max_attempts = max_attempts_from_env()
        else:
            n = int(max_attempts)
            if n < 1 or n > _MAX_ATTEMPTS_CAP:
                raise LLMConfigurationError(
                    f"max_attempts must be between 1 and {_MAX_ATTEMPTS_CAP}"
                )
            self._max_attempts = n
        self._ledger = _Ledger(self.db_path)

    def _budget_microusd(self) -> int | None:
        if self._budget_usd is None or self._budget_usd <= 0:
            return None
        return int(round(self._budget_usd * 1_000_000))

    def create(self, *, operation: str, model: str, max_tokens: int, **kwargs: Any) -> Any:
        return self._call(
            mode="create", operation=operation, model=model, max_tokens=max_tokens, **kwargs
        )

    def stream(self, *, operation: str, model: str, max_tokens: int, **kwargs: Any) -> Any:
        return self._call(
            mode="stream", operation=operation, model=model, max_tokens=max_tokens, **kwargs
        )

    def daily_usage_summary(self, days: int = 1) -> dict[str, Any]:
        return self._ledger.daily_usage_summary(days=days)

    def _dispatch(self, *, mode: str, model: str, max_tokens: int, **kwargs: Any) -> Any:
        client = self._client_factory()
        if mode == "stream":
            with client.messages.stream(model=model, max_tokens=max_tokens, **kwargs) as stream:
                return stream.get_final_message()
        return client.messages.create(model=model, max_tokens=max_tokens, **kwargs)

    def _invoke_once(self, *, mode: str, model: str, max_tokens: int, **kwargs: Any) -> Any:
        """One provider call, with a single strict-tool grammar fallback.

        Lives here rather than in ``_call``'s retry loop on purpose: the fallback
        must not consume a transport-retry attempt, and reusing the caller's
        ``call_id`` keeps ledger settlement attributable to one logical call.
        """
        global STRICT_DEGRADE_COUNT
        try:
            return self._dispatch(mode=mode, model=model, max_tokens=max_tokens, **kwargs)
        except Exception as exc:
            if not _is_grammar_too_large(exc):
                raise
            degraded = _without_strict_tools(kwargs)
            if degraded is None:
                raise
            STRICT_DEGRADE_COUNT += 1
            logger.warning(
                "STRICT_DEGRADE: provider rejected the compiled strict-tool grammar "
                "(model=%s); retrying once WITHOUT strict. Output is no longer "
                "grammar-constrained — llm_contracts validators remain the contract. "
                "Shrink the tool schema to restore strict mode. (%s)",
                model,
                exc,
            )
            return self._dispatch(mode=mode, model=model, max_tokens=max_tokens, **degraded)

    def _call(
        self, *, mode: str, operation: str, model: str, max_tokens: int, **kwargs: Any
    ) -> Any:
        """Anthropic first; on a provider-level failure, hand off to a fallback.

        Failover covers the cases where Claude is genuinely *unavailable* —
        retries exhausted, auth/connection failure, daily budget spent. It does
        NOT cover refusal or truncation: a refusal must not be laundered through
        a second model, and a truncation is a schema-too-large bug that a silent
        fallback would hide (see the max_tokens note in CLAUDE.md).
        """
        try:
            return self._call_anthropic(
                mode=mode, operation=operation, model=model, max_tokens=max_tokens, **kwargs
            )
        except _FAILOVER_TRIGGERS as exc:
            if not llm_failover.enabled():
                raise
            try:
                return llm_failover.attempt(
                    operation=operation, max_tokens=max_tokens, **kwargs
                )
            except llm_failover.FailoverUnavailable as fexc:
                logger.error(
                    "LLM_FAILOVER: all fallback providers failed for operation=%s (%s)",
                    operation,
                    fexc,
                )
                raise exc from fexc

    def _call_anthropic(
        self, *, mode: str, operation: str, model: str, max_tokens: int, **kwargs: Any
    ) -> Any:
        # Never persist prompt/message/tool bodies — kwargs stay in-memory only.
        reserved = reserve_cost_microusd(model, max_tokens)
        budget_micro = self._budget_microusd()
        last_exc: BaseException | None = None

        for attempt_no in range(1, self._max_attempts + 1):
            call_id = self._ledger.reserve(
                operation=operation,
                model=model,
                attempt_no=attempt_no,
                reserved_microusd=reserved,
                budget_microusd=budget_micro,
            )
            try:
                message = self._invoke_once(mode=mode, model=model, max_tokens=max_tokens, **kwargs)
            except Exception as exc:
                kind = type(exc).__name__
                if _is_retryable(exc):
                    self._ledger.mark_unknown_cost(call_id, error_kind=kind)
                    last_exc = exc
                    if attempt_no == self._max_attempts:
                        raise LLMUnavailableError(_MSG_UNAVAILABLE) from exc
                    time.sleep(backoff_seconds(attempt_no, _retry_after_seconds(exc)))
                    continue
                # Non-retryable: keep conservative unknown-cost reservation.
                self._ledger.mark_unknown_cost(call_id, error_kind=kind)
                raise

            stop = getattr(message, "stop_reason", None)
            if stop == "refusal":
                in_tok, out_tok, cache_read, cache_create = _usage_tokens(message)
                settled = estimate_cost_microusd(model, in_tok, out_tok)
                self._ledger.mark_rejected(
                    call_id,
                    error_kind="LLMRefusalError",
                    settled_microusd=settled,
                    request_id=_message_request_id(message),
                    input_tokens=in_tok,
                    output_tokens=out_tok,
                    cache_read_tokens=cache_read,
                    cache_creation_tokens=cache_create,
                )
                raise LLMRefusalError(_MSG_REFUSAL)
            if stop == "max_tokens":
                in_tok, out_tok, _, _ = _usage_tokens(message)
                settled = estimate_cost_microusd(model, in_tok, out_tok)
                self._ledger.mark_truncated(call_id, message=message, settled_microusd=settled)
                raise LLMTruncatedError(_MSG_TRUNCATED)
            return self._ledger.settle_message(call_id, message, model=model)

        # Unreachable, but keeps type checkers happy.
        raise LLMUnavailableError(_MSG_UNAVAILABLE) from last_exc


# ── Module-level default gateway ──────────────────────────────────────────────

_default_gateway: Gateway | None = None


def get_gateway() -> Gateway:
    """Return the process-wide default Gateway (lazy-constructed)."""
    global _default_gateway
    if _default_gateway is None:
        _default_gateway = Gateway()
    return _default_gateway


def reset_gateway() -> None:
    """Drop the cached default gateway (test seam)."""
    global _default_gateway
    _default_gateway = None


def daily_usage_summary(days: int = 1) -> dict[str, Any]:
    """Owner telemetry helper — delegates to the default gateway."""
    return get_gateway().daily_usage_summary(days=days)
