"""One canonical decision-quality block for degraded per-ticker decisions (Codex #6).

Price-suspect flags, strict-mode rejections, and freshness stamps grew up scattered
across the AI research card, the screener rows, and the daily chain under different
key names (``price_suspect`` / ``price_suspect_reason`` / ``generated_at`` /
``degraded`` / ``snapshot_source``), so the dashboard, the logs, and the audit
history each read a slightly different shape for the same condition.

``build()`` emits ONE structured block that every decision surface attaches, so a
degraded decision looks identical to a human (dashboard), a log parser, and an
auditor. It is additive — callers keep their existing back-compat keys and add
``decision_quality`` alongside them.

The block matches the shape Codex asked for::

    {"price_quality": "ok" | "suspect",
     "decision_degraded": bool,
     "data_sources": ["price_source", ...],
     "reasons": ["52-wk range cross-check failed …"],
     "as_of": "2026-07-15T…+00:00" | None}
"""
from __future__ import annotations

import logging

logger = logging.getLogger("gesh.decision_quality")


def build(*, price_suspect: bool = False, reason: str = "",
          data_sources=None, degraded: bool = False, as_of=None) -> dict:
    """Assemble the canonical decision-quality block.

    ``price_suspect`` + ``reason`` come from ``price_sanity.is_price_suspect`` (the
    cross-source 52-wk check). ``degraded`` lets a caller mark a decision degraded for
    a reason other than the price (stale snapshot, missing feed). A decision is
    ``decision_degraded`` if EITHER holds. ``data_sources`` names the feeds that fed
    the decision (honest: only what actually ran). Never raises.
    """
    reasons: list[str] = []
    if price_suspect and reason:
        reasons.append(reason)
    return {
        "price_quality": "suspect" if price_suspect else "ok",
        "decision_degraded": bool(price_suspect or degraded),
        "data_sources": list(data_sources or []),
        "reasons": reasons,
        "as_of": as_of,
    }


def attach(target: dict | None, block: dict, *, context: str = "") -> dict | None:
    """Attach ``block`` to ``target['decision_quality']`` (when target is a dict) and
    emit one structured log line if degraded. Returns ``target`` for chaining. A no-op
    when ``target`` isn't a dict, so a malformed engine payload never breaks the route."""
    if isinstance(target, dict):
        target["decision_quality"] = block
    log_if_degraded(context, block)
    return target


def log_if_degraded(context: str, block: dict) -> None:
    """Emit ONE structured WARNING when a decision is degraded, so logs carry the same
    signal the dashboard/audit block does. No-op on a clean decision."""
    if block and block.get("decision_degraded"):
        logger.warning(
            "degraded_decision context=%s price_quality=%s sources=%s reasons=%s",
            context or "-",
            block.get("price_quality"),
            ",".join(block.get("data_sources") or []) or "-",
            "; ".join(block.get("reasons") or []) or "-",
        )
