"""
saga_recovery.py — production-reachable recovery for deletion sagas and post-tombstone
billing remediations.

resume_all() alone was dead code. This module is the production entry point: it is
invoked from a repository-native scheduled maintenance runner (a launchd job calling
`python -m saga_recovery`, mirroring `billing.py sweep-onboarding`), and is also a
plain callable for tests.

Two work streams, both idempotent and crash-safe:

  1. Deletion sagas parked in a resumable state (retryable_failure, mid-flight).
  2. Billing remediations — a still-live subscription for an ERASED identity that a
     webhook recorded but did not itself cancel (provider calls never run inside a
     webhook transaction).

Guarantees required of this runner:
  * bounded batch size (never an unbounded scan)
  * bounded backoff with a persisted next-attempt time
  * concurrency-safe claiming (CAS on version), expired claims recovered
  * manual_review is NEVER auto-retried
  * no network call inside a database transaction
  * one failing item does not stop the batch
  * no PII in logs
  * safe to invoke repeatedly
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import auth_users
import deletion_saga

logger = logging.getLogger("gesh.saga_recovery")

_DEFAULT_BATCH = 50
_BACKOFF_BASE_SECONDS = 30
_BACKOFF_CAP_SECONDS = 3600


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _backoff(attempts: int) -> str:
    delay = min(_BACKOFF_BASE_SECONDS * (2 ** max(0, attempts - 1)), _BACKOFF_CAP_SECONDS)
    return (_now() + timedelta(seconds=delay)).isoformat()


def _run_remediations(cancel_fn: Callable[[str], str], batch: int,
                      reconcile_fn: Callable[[str], str] | None = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for rem in auth_users.pending_remediations()[:batch]:
        rid, ver, sub_id = rem["remediation_id"], rem["version"], rem["sub_id"]
        prior_attempt = int(rem["attempts"] or 0) > 0
        if not sub_id:
            # Nothing left to cancel (already scrubbed) — finalize.
            auth_users.advance_remediation(rid, ver, auth_users.REMEDIATION_COMPLETED,
                                           completed=True)
            continue
        # CLAIM before the provider call (CAS on version), pushing next_attempt_at
        # out by a lease. Two workers cannot both cancel the same subscription: the
        # loser's CAS returns None and it skips. On a crash after claiming, the lease
        # expires and the row becomes due again.
        claimed = auth_users.advance_remediation(
            rid, ver, auth_users.REMEDIATION_PENDING,
            next_attempt_at=_backoff(rem["attempts"] + 1))
        if claimed is None:
            out.append({"remediation_id": rid, "result": "claimed_elsewhere"})
            continue
        ver = claimed["version"]
        # RECONCILE-BEFORE-REPEAT (2026-07-24). `attempts > 0` means a PRIOR worker
        # already ran a cancellation attempt whose remote outcome may be unknown (its
        # DELETE could have been applied while the response was lost, then its claim
        # expired). Repeating DELETE blind is unsafe: Lemon Squeezy does NOT document
        # DELETE idempotency, so a second DELETE against an already-cancelled
        # subscription has no defined outcome. Read remote state first and only
        # re-issue DELETE when the subscription is genuinely still active.
        if prior_attempt and reconcile_fn is not None:
            try:
                remote = reconcile_fn(sub_id)
            except deletion_saga.ProviderRetryableError:
                auth_users.advance_remediation(rid, ver, auth_users.REMEDIATION_PENDING,
                                               last_error="reconcile_retryable",
                                               next_attempt_at=_backoff(rem["attempts"] + 1))
                out.append({"remediation_id": rid, "result": "retry_reconcile"})
                continue
            except deletion_saga.ProviderUnknownOutcome:
                auth_users.advance_remediation(rid, ver, auth_users.REMEDIATION_PENDING,
                                               last_error="reconcile_unknown_outcome",
                                               next_attempt_at=_backoff(rem["attempts"] + 1))
                out.append({"remediation_id": rid, "result": "retry_reconcile_unknown"})
                continue
            except deletion_saga.ProviderPermanentError:
                # Includes ProviderAmbiguousAbsence (404 on a recorded subscription)
                # and malformed/unexpected objects — never inferred as cancelled.
                auth_users.advance_remediation(rid, ver, auth_users.REMEDIATION_MANUAL_REVIEW,
                                               last_error="reconcile_permanent")
                out.append({"remediation_id": rid, "result": "manual_review"})
                continue
            if remote == deletion_saga.CANCEL_ALREADY:
                # Already cancelled remotely — finish WITHOUT a second DELETE.
                auth_users.advance_remediation(rid, ver, auth_users.REMEDIATION_COMPLETED,
                                               completed=True, scrub_sub=True)
                out.append({"remediation_id": rid, "result": deletion_saga.CANCEL_ALREADY})
                continue
            # remote == "active" → fall through and DELETE under this fenced claim.
        try:
            outcome = cancel_fn(sub_id)                       # OUTSIDE any transaction
        except deletion_saga.ProviderRetryableError:
            auth_users.advance_remediation(rid, ver, auth_users.REMEDIATION_PENDING,
                                           last_error="provider_retryable",
                                           next_attempt_at=_backoff(rem["attempts"] + 1))
            out.append({"remediation_id": rid, "result": "retry"})
            continue
        except deletion_saga.ProviderUnknownOutcome:
            auth_users.advance_remediation(rid, ver, auth_users.REMEDIATION_PENDING,
                                           last_error="provider_unknown_outcome",
                                           next_attempt_at=_backoff(rem["attempts"] + 1))
            out.append({"remediation_id": rid, "result": "retry_unknown"})
            continue
        except deletion_saga.ProviderPermanentError:
            auth_users.advance_remediation(rid, ver, auth_users.REMEDIATION_MANUAL_REVIEW,
                                           last_error="provider_permanent")
            out.append({"remediation_id": rid, "result": "manual_review"})
            continue
        except Exception as exc:  # noqa: BLE001 — never let one item stop the batch
            logger.warning("remediation %s failed: %s", rid, type(exc).__name__)
            auth_users.advance_remediation(rid, ver, auth_users.REMEDIATION_PENDING,
                                           last_error="unexpected",
                                           next_attempt_at=_backoff(rem["attempts"] + 1))
            out.append({"remediation_id": rid, "result": "error"})
            continue
        # Cancelled / already-cancelled / not-found are all terminal successes.
        # Scrub the raw provider id now that it is no longer operationally needed.
        auth_users.advance_remediation(rid, ver, auth_users.REMEDIATION_COMPLETED,
                                       completed=True, scrub_sub=True)
        out.append({"remediation_id": rid, "result": outcome})
    return out


def run_recovery(cancel_fn: Callable[[str], str] | None = None,
                 batch: int = _DEFAULT_BATCH,
                 reconcile_fn: Callable[[str], str] | None = None) -> dict[str, Any]:
    """Drive both work streams forward once. Safe to call on a schedule.

    `cancel_fn` is injected so tests never touch the network; production supplies
    `billing_cancel.cancel`.
    """
    if cancel_fn is None or reconcile_fn is None:
        import billing_cancel
        cancel_fn = cancel_fn or billing_cancel.cancel
        # Production reconciliation source for reconcile-before-repeat.
        reconcile_fn = reconcile_fn or billing_cancel.reconcile

    sagas = deletion_saga.resume_all(cancel_fn=cancel_fn)
    remediations = _run_remediations(cancel_fn, batch, reconcile_fn=reconcile_fn)
    logger.info("recovery: sagas=%d remediations=%d", len(sagas), len(remediations))
    return {"sagas": sagas, "remediations": remediations}


if __name__ == "__main__":  # pragma: no cover — production maintenance entry point
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    summary = run_recovery()
    print(f"recovery: sagas={len(summary['sagas'])} remediations={len(summary['remediations'])}")
