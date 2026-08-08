"""
deletion_saga.py — cancel-then-erase orchestration for account deletion.

WHY THIS EXISTS
---------------
`auth_users.delete_user_data()` removed the `users` row outright. That freed the
unique `billing_sub_id` index, so the next Lemon Squeezy renewal event found no
account, fell through to `billing.provision()` → `create_magic_link_user()`, and
**recreated the account plus emailed a magic link to someone who had asked to be
erased**. The variant-less invoice path instead raised `RetryableError` → 503, so
the provider retried a webhook that could never succeed.

Either the erasure was undone or billing broke. This module makes the lifecycle
explicit, durable and resumable.

DESIGN
------
State machine (persisted in `deletion_sagas`; see auth_users._CORE_DDL):

    requested
      → cancellation_pending      (intent persisted BEFORE the provider call)
      → cancellation_confirmed    (provider says cancelled / gone)
      → tombstone_pending
      → tombstoned                (durable memory written; recreation now refused)
      → completed                 (sensitive rows purged)

    any → retryable_failure       (transient; resume_state says where to restart)
    any → manual_review           (ambiguous/permanent; never auto-deletes)

INVARIANTS
----------
* Cancellation is attempted BEFORE irreversible deletion.
* No DB transaction is held across a provider call: persist intent → commit →
  call provider → persist result. Each step is a short local transaction.
* Transitions are compare-and-set on `version`; a lost CAS means another worker
  advanced the saga, so concurrent duplicates converge instead of racing.
* An UNKNOWN provider outcome (timeout) is reconciled before any deletion — it is
  never treated as success.
* The tombstone is written BEFORE the rows are purged, so a crash in between
  still leaves recreation blocked (fail-safe ordering).
* `last_error` stores a CLASSIFICATION string only — never a provider payload.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

import auth_users

logger = logging.getLogger("gesh.deletion_saga")


class ProviderRetryableError(RuntimeError):
    """Transient provider failure — safe to retry the same step."""


class ProviderPermanentError(RuntimeError):
    """Provider refused in a way retrying cannot fix. Goes to manual review."""


class ProviderUnknownOutcome(RuntimeError):
    """Timeout: the remote may or may not have applied the cancellation.

    MUST be reconciled (re-read subscription state) before any local deletion —
    treating it as success risks erasing an account that is still being billed.
    """


# Result vocabulary a cancel_fn may return.
CANCEL_OK = "cancelled"
CANCEL_ALREADY = "already_cancelled"
CANCEL_NOT_FOUND = "not_found"
_TERMINAL_OK = (CANCEL_OK, CANCEL_ALREADY, CANCEL_NOT_FOUND)


def _cas(saga: dict[str, Any], state: str, **kw: Any) -> dict[str, Any]:
    """Compare-and-set, re-reading on a lost race so callers always see truth."""
    row = auth_users.advance_saga(saga["saga_id"], saga["version"], state, **kw)
    if row is None:
        current = auth_users.get_saga(saga["saga_id"])
        logger.info("saga %s CAS lost (another worker advanced it)", saga["saga_id"])
        return current or saga
    return row


def run(email: str, *, cancel_fn: Callable[[str], str] | None = None,
        purge_fn: Callable[[str], dict] | None = None) -> dict[str, Any]:
    """Execute (or resume) the deletion saga for `email`.

    `cancel_fn(sub_id) -> CANCEL_*` performs the provider cancellation and may raise
    ProviderRetryableError / ProviderPermanentError / ProviderUnknownOutcome.
    Injected rather than imported so tests never touch a real provider.

    Idempotent: a completed saga replays as a no-op success.
    """
    user_id = auth_users.resolve_user_id(email)
    purge = purge_fn or auth_users.delete_user_data

    if user_id is None:
        # Already erased (or never existed). Replay must look like success — this is
        # what lets the HTTP layer answer 200 to a duplicate request.
        return {"status": "ok", "state": auth_users.SAGA_COMPLETED,
                "already_completed": True, "counts": {}}

    sub_id, _sub_status = auth_users.billing_subscription_status(email)
    claim = auth_users.start_deletion_saga(user_id, email, sub_id)
    saga = claim["saga"]

    # ── 1. cancellation ───────────────────────────────────────────────────────
    if saga["state"] in (auth_users.SAGA_REQUESTED,
                         auth_users.SAGA_CANCELLATION_PENDING,
                         auth_users.SAGA_RETRYABLE_FAILURE):
        if sub_id and cancel_fn is None:
            # FAIL CLOSED (Gate 1 Q1/Q2). Previously a missing cancel_fn skipped
            # straight to cancellation_confirmed, so an account with a live
            # subscription could be erased with NO cancellation attempt — the user
            # keeps being billed for an account that no longer exists. An account
            # with a recorded subscription must not be erased without either a
            # confirmed cancellation or confirmed remote absence.
            _cas(saga, auth_users.SAGA_MANUAL_REVIEW,
                 resume_state=auth_users.SAGA_CANCELLATION_PENDING,
                 last_error="no_cancellation_adapter_configured")
            raise ProviderPermanentError(
                "account has a billing subscription but no cancellation adapter is configured")
        if sub_id and cancel_fn is not None:
            # Persist intent, COMMIT, and only then call out. A crash here resumes
            # from cancellation_pending rather than losing the request.
            saga = _cas(saga, auth_users.SAGA_CANCELLATION_PENDING,
                        resume_state=auth_users.SAGA_CANCELLATION_PENDING)
            try:
                outcome = cancel_fn(sub_id)
            except ProviderRetryableError:
                _cas(saga, auth_users.SAGA_RETRYABLE_FAILURE,
                     resume_state=auth_users.SAGA_CANCELLATION_PENDING,
                     last_error="provider_retryable")
                raise
            except ProviderUnknownOutcome:
                # Ambiguous: do NOT delete. Park for reconciliation.
                _cas(saga, auth_users.SAGA_RETRYABLE_FAILURE,
                     resume_state=auth_users.SAGA_CANCELLATION_PENDING,
                     last_error="provider_unknown_outcome")
                raise
            except ProviderPermanentError:
                _cas(saga, auth_users.SAGA_MANUAL_REVIEW,
                     resume_state=auth_users.SAGA_CANCELLATION_PENDING,
                     last_error="provider_permanent")
                raise
            if outcome not in _TERMINAL_OK:
                _cas(saga, auth_users.SAGA_MANUAL_REVIEW,
                     resume_state=auth_users.SAGA_CANCELLATION_PENDING,
                     last_error="provider_unexpected_result")
                raise ProviderPermanentError(f"unexpected cancel outcome: {outcome}")
        saga = _cas(saga, auth_users.SAGA_CANCELLATION_CONFIRMED,
                    resume_state=auth_users.SAGA_CANCELLATION_CONFIRMED)

    # ── 2. tombstone BEFORE purge (fail-safe ordering) ────────────────────────
    if saga["state"] in (auth_users.SAGA_CANCELLATION_CONFIRMED,
                         auth_users.SAGA_TOMBSTONE_PENDING):
        saga = _cas(saga, auth_users.SAGA_TOMBSTONE_PENDING,
                    resume_state=auth_users.SAGA_TOMBSTONE_PENDING)
        auth_users.write_tombstone(email, user_id, billing_sub_id=sub_id,
                                   saga_id=saga["saga_id"])
        saga = _cas(saga, auth_users.SAGA_TOMBSTONED,
                    resume_state=auth_users.SAGA_TOMBSTONED)

    # ── 3. purge + complete ───────────────────────────────────────────────────
    counts: dict[str, Any] = {}
    if saga["state"] == auth_users.SAGA_TOMBSTONED:
        counts = (purge(email) or {}).get("counts", {})
        saga = _cas(saga, auth_users.SAGA_COMPLETED,
                    resume_state=auth_users.SAGA_COMPLETED, completed=True)

    return {"status": "ok", "state": saga["state"], "saga_id": saga["saga_id"],
            "already_completed": not claim["created"], "counts": counts}


def resume_all(cancel_fn: Callable[[str], str] | None = None) -> list[dict[str, Any]]:
    """Crash recovery: drive every unfinished saga forward. Safe to run repeatedly."""
    out = []
    for saga in auth_users.resumable_sagas():
        user = auth_users.get_user_by_id(saga["user_id"])
        if not user:
            # Rows already gone: finish the bookkeeping rather than leaving it open.
            auth_users.advance_saga(saga["saga_id"], saga["version"],
                                    auth_users.SAGA_COMPLETED,
                                    resume_state=auth_users.SAGA_COMPLETED, completed=True)
            out.append({"saga_id": saga["saga_id"], "state": auth_users.SAGA_COMPLETED})
            continue
        try:
            out.append(run(user["email"], cancel_fn=cancel_fn))
        except Exception as exc:  # noqa: BLE001 — one stuck saga must not stop the sweep
            logger.warning("saga %s resume failed: %s", saga["saga_id"], type(exc).__name__)
            out.append({"saga_id": saga["saga_id"], "error": type(exc).__name__})
    return out
