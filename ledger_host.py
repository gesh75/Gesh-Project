"""Host integration helper for Decision Ledger Phase 4B (non-fatal).

This module owns the host-specific provenance mapping and error handling
for the production daily-analysis chain. It delegates all persistence logic
to the gesh-core adapter.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

# gesh-core v0.6.0+ is required for this integration.
from gesh_core.ledger.adapters.daily_analysis import record_daily_analysis_decision
from gesh_core.ledger.integration import (
    LedgerConflictError,
    LedgerError,
    LedgerValidationError,
    LedgerWriteError,
)

__all__ = ["record_daily_analysis_ledger_nonfatal"]


def _derive_quote_provider(engine_results: Mapping[str, Any]) -> str | None:
    """Derive a single quote provider from the engine results payload.

    Rules:
    1. If exactly one distinct non-blank provider exists across symbols, use it.
    2. If multiple distinct providers exist, return None.
    3. Fall back to snapshot.source, then run_manifest.input_source, then None.
    """
    snapshot = engine_results.get("snapshot") or {}
    manifest = engine_results.get("run_manifest") or {}

    providers: set[str] = set()
    provenance = snapshot.get("quote_provenance") or {}
    for symbol_data in provenance.values():
        if isinstance(symbol_data, Mapping):
            provider = symbol_data.get("provider")
            if provider:
                providers.add(str(provider).strip())

    if len(providers) == 1:
        return next(iter(providers))
    if len(providers) > 1:
        return None

    # Fallback chain
    source = snapshot.get("source")
    if source:
        return str(source).strip()
    input_source = manifest.get("input_source")
    if input_source:
        return str(input_source).strip()
    return None


def record_daily_analysis_ledger_nonfatal(
    *,
    engine_results: Mapping[str, Any],
    analysis_run_id: int | None,
    ai_model: str,
    db_path: Any,
    environ: Mapping[str, str] | None = None,
    log: Any = print,
) -> Any | None:
    """Record the daily analysis decision into the Decision Ledger (non-fatal).

    Returns the adapter result on success, or None on skip/failure.
    Never raises to the caller. No raw payload or secrets are logged.
    """
    env = environ if environ is not None else os.environ

    # Tenant resolution (non-fatal skip if missing/blank)
    tenant_id = str(env.get("OWNER_TENANT_ID", "")).strip()
    if not tenant_id:
        log("[ai] Ledger skipped (non-fatal): OWNER_TENANT_ID is not configured")
        return None

    quote_provider = _derive_quote_provider(engine_results)

    try:
        recording = record_daily_analysis_decision(
            tenant_id=tenant_id,
            source="daily_analysis_chain",
            engine_results=engine_results,
            analysis_run_id=analysis_run_id,
            ai_provider="anthropic",
            ai_model=ai_model,
            quote_provider=quote_provider,
            db_path=db_path,
        )

        # Compact success logging — counts only, no holdings, no raw recs, no secrets
        skipped_summary = ""
        if getattr(recording, "skipped", None):
            reasons: dict[str, int] = {}
            for item in recording.skipped:
                key = f"{getattr(item, 'decision_type', '?')}:{getattr(item, 'reason', '?')}"
                reasons[key] = reasons.get(key, 0) + 1
            skipped_summary = ", ".join(f"{k}={v}" for k, v in reasons.items())

        rec_ids = getattr(getattr(recording, "recording", None), "recommendation_ids", []) or []
        inserted = getattr(getattr(recording, "recording", None), "inserted_ids", []) or []
        reused = getattr(getattr(recording, "recording", None), "reused_ids", []) or []
        portfolio_id = getattr(getattr(recording, "recording", None), "portfolio_snapshot_id", None)

        log(
            f"[ai] Ledger saved: "
            f"portfolio={portfolio_id}, "
            f"recs={len(rec_ids)}, "
            f"inserted={len(inserted)}, "
            f"reused={len(reused)}, "
            f"skipped={len(getattr(recording, 'skipped', []) or [])}"
            + (f" ({skipped_summary})" if skipped_summary else "")
        )
        return recording

    except (
        LedgerValidationError,
        LedgerConflictError,
        LedgerWriteError,
        LedgerError,
        RuntimeError,
    ) as exc:
        log(f"[ai] Ledger save failed (non-fatal): {exc.__class__.__name__}: {exc}")
        return None
    except Exception as exc:
        # Catch unexpected integration errors (import, DB connection, etc.)
        log(f"[ai] Ledger save failed (non-fatal): {exc.__class__.__name__}: {exc}")
        return None
