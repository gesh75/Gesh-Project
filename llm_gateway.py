"""Canonical Claude model routing and Anthropic client construction.

Every Anthropic-backed analysis path constructs clients and resolves model IDs
through this module. Retry policy, spend telemetry, and call helpers land in
later hardening tasks; this module owns the closed allowlist and factory.
"""

from __future__ import annotations

import os
from typing import Final, Literal

import anthropic

ModelAlias = Literal["sonnet", "haiku"]
MODEL_IDS: Final[dict[ModelAlias, str]] = {
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5",
}
CANONICAL_MODELS: Final[frozenset[str]] = frozenset(MODEL_IDS.values())


class LLMConfigurationError(RuntimeError):
    """Raised when model routing or client configuration is invalid."""


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


def create_client(*, timeout_seconds: float | None = None) -> anthropic.Anthropic:
    """Build a bounded Anthropic client with SDK retries disabled.

    Gateway-owned retry accounting is authoritative. SDK retries would hide
    attempt counts from telemetry and spend reservations.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise LLMConfigurationError("Anthropic service is not configured")
    timeout = default_timeout_seconds() if timeout_seconds is None else float(timeout_seconds)
    if not 1.0 <= timeout <= 600.0:
        raise LLMConfigurationError("GESH_LLM_TIMEOUT_SECONDS must be between 1 and 600")
    return anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=0)
