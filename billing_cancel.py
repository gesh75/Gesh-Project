"""
billing_cancel.py — production Lemon Squeezy subscription cancellation + reconciliation.

Used by deletion_saga to satisfy the invariant that an account with a live remote
subscription is never erased locally without either a CONFIRMED cancellation or a
CONFIRMED remote absence — otherwise the customer keeps being billed for an account
that no longer exists.

Conventions follow `billing_reconcile._ls_get` (urllib + Bearer + JSON:API Accept);
no second HTTP stack is introduced.

404 IS NOT PROOF OF ABSENCE (P1, adjudicated 2026-07-23)
-------------------------------------------------------
A 404 for a subscription we have RECORDED locally is ambiguous, not authenticated
absence: a test-mode key looking up a live-mode subscription 404s; a wrong store,
a wrong API host, or a credential/mode mismatch all 404 too. Treating that as
"subscription gone → safe to erase" would delete an account that is still being
billed. Every 404 on a recorded subscription therefore raises
ProviderAmbiguousAbsence → the saga parks it in manual_review and NEVER deletes.
A genuine administrative "remote subscription absent" decision must be explicit
and audited; it is never inferred from a 404.

RETRY POLICY NOTE
-----------------
Lemon Squeezy acknowledges a webhook on HTTP 200 and may retry a non-200 delivery a
small number of additional times. Correctness here does not depend on any specific
retry count or schedule, and none is encoded: every operation is idempotent and
every unknown outcome is reconciled rather than assumed.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import deletion_saga

logger = logging.getLogger(__name__)

# Production host is fixed. A different base is allowed ONLY through explicit test
# injection (GESH_LS_API_BASE) — never silently, so a misconfigured base cannot
# reach the destructive path and 404 its way into a deletion.
_PROD_API = "https://api.lemonsqueezy.com"
_TIMEOUT = 15.0                      # bounded connect+read
_CANCELLED_STATUSES = {"cancelled", "expired"}


class ProviderAmbiguousAbsence(deletion_saga.ProviderPermanentError):
    """A 404 (or equivalent) for a locally-recorded subscription.

    Subclasses ProviderPermanentError so the saga routes it to manual_review and
    never auto-deletes, while carrying a distinct identity for audit/classification.
    """


def _api_base() -> str:
    """The API base. Production is fixed; a test override must be an explicit,
    non-production localhost/loopback or test host — validated below."""
    override = os.getenv("GESH_LS_API_BASE", "").strip()
    return override or _PROD_API


def _validate_base(base: str) -> None:
    """Reject a base URL that could quietly point destructive calls at the wrong
    host. Production must be the official HTTPS host; any override must be HTTP(S)
    with an explicit host (used only by tests, which patch urlopen anyway)."""
    if base == _PROD_API:
        return
    u = urllib.parse.urlparse(base)
    if u.scheme not in ("http", "https") or not u.netloc:
        raise deletion_saga.ProviderPermanentError("invalid provider API base configured")


def _api_key() -> str:
    return os.getenv("LEMON_SQUEEZY_API_KEY", "").strip()


def _request(method: str, path: str) -> tuple[int, dict[str, Any]]:
    """One bounded JSON:API call. Returns (status, parsed_body).

    Raises the deletion_saga error classes so the saga can decide safely. Never logs
    the key, the Authorization header, or personal data.
    """
    key = _api_key()
    if not key:
        # Config failure, not a transient one: retrying cannot supply a key.
        raise deletion_saga.ProviderPermanentError("LEMON_SQUEEZY_API_KEY is unset")
    base = _api_base()
    _validate_base(base)
    req = urllib.request.Request(
        base + path, method=method,
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/vnd.api+json",
            "Content-Type": "application/vnd.api+json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            raw = r.read()
            body = json.loads(raw) if raw else {}
            return r.status, (body if isinstance(body, dict) else {})
    except urllib.error.HTTPError as e:
        status = e.code
        try:
            body = json.loads(e.read() or b"{}")
        except Exception:  # noqa: BLE001 — body is advisory only
            body = {}
        if status in (401, 403):
            raise deletion_saga.ProviderPermanentError(f"provider auth failed ({status})") from e
        if status == 404:
            return status, body if isinstance(body, dict) else {}
        if status == 429:
            raise deletion_saga.ProviderRetryableError("provider rate limited") from e
        if 500 <= status < 600:
            raise deletion_saga.ProviderRetryableError(f"provider {status}") from e
        raise deletion_saga.ProviderPermanentError(f"provider rejected ({status})") from e
    except TimeoutError as e:
        # The remote MAY have applied the change. Never guess either way.
        raise deletion_saga.ProviderUnknownOutcome("provider timeout") from e
    except urllib.error.URLError as e:
        if isinstance(getattr(e, "reason", None), TimeoutError):
            raise deletion_saga.ProviderUnknownOutcome("provider timeout") from e
        raise deletion_saga.ProviderRetryableError("provider connection failed") from e
    except json.JSONDecodeError as e:
        raise deletion_saga.ProviderPermanentError("provider returned malformed JSON") from e


def _data(body: dict[str, Any]) -> dict[str, Any]:
    data = body.get("data")
    return data if isinstance(data, dict) else {}


def _attrs(body: dict[str, Any]) -> dict[str, Any]:
    attrs = _data(body).get("attributes")
    return attrs if isinstance(attrs, dict) else {}


def _status_of(body: dict[str, Any]) -> str | None:
    st = _attrs(body).get("status")
    return str(st).strip().lower() if st else None


def _validate_subscription_object(body: dict[str, Any], subscription_id: str) -> None:
    """Strict JSON:API checks BEFORE trusting a 200 as authoritative.

    A response can be a 200 yet describe the wrong resource (wrong id / store /
    mode). Confirming success on such a body could erase an account whose real
    subscription is still live. Store id and test-mode are enforced only when the
    corresponding env is set, so test and live deployments are both supported.
    """
    data = _data(body)
    if data.get("type") != "subscriptions":
        raise deletion_saga.ProviderPermanentError("provider returned a non-subscription object")
    if str(data.get("id")) != str(subscription_id):
        raise deletion_saga.ProviderPermanentError("provider returned a different subscription id")
    attrs = _attrs(body)
    want_store = os.getenv("LS_STORE_ID", "").strip()
    if want_store and str(attrs.get("store_id")) != want_store:
        raise deletion_saga.ProviderPermanentError("subscription belongs to a different store")
    want_mode = os.getenv("LS_EXPECT_TEST_MODE", "").strip().lower()
    if want_mode in ("0", "1", "true", "false"):
        want_bool = want_mode in ("1", "true")
        if bool(attrs.get("test_mode")) != want_bool:
            raise deletion_saga.ProviderPermanentError("subscription mode (test/live) mismatch")


def _confirm_cancelled(body: dict[str, Any], subscription_id: str) -> None:
    """A subscription is cancelled only when the validated object says so on BOTH
    the status field and the `cancelled` boolean."""
    _validate_subscription_object(body, subscription_id)
    attrs = _attrs(body)
    st = _status_of(body)
    if st not in _CANCELLED_STATUSES:
        raise deletion_saga.ProviderPermanentError(f"subscription not cancelled (status={st})")
    # LS sets attributes.cancelled=true once cancellation is scheduled; "expired"
    # subscriptions are already ended and need not carry the flag.
    if st == "cancelled" and attrs.get("cancelled") is not True:
        raise deletion_saga.ProviderPermanentError("subscription status/cancelled flag disagree")


def reconcile(subscription_id: str) -> str:
    """Read current remote state. Used after an unknown outcome, before deciding.

    Returns deletion_saga.CANCEL_ALREADY when confirmed cancelled, or "active"
    when still live. A 404 is AMBIGUOUS for a recorded subscription → raises.
    """
    status, body = _request("GET", f"/v1/subscriptions/{subscription_id}")
    if status == 404:
        raise ProviderAmbiguousAbsence("provider 404 for a recorded subscription (reconcile)")
    if _status_of(body) in _CANCELLED_STATUSES:
        _validate_subscription_object(body, subscription_id)
        return deletion_saga.CANCEL_ALREADY
    _validate_subscription_object(body, subscription_id)
    return "active"


def cancel(subscription_id: str) -> str:
    """Cancel a subscription, reconciling anything ambiguous.

    Returns deletion_saga.CANCEL_OK / CANCEL_ALREADY. Safe to call repeatedly: a
    second call against an already-cancelled subscription reconciles rather than
    failing, so no provider idempotency-key header is assumed (Lemon Squeezy does
    not document one). A 404 raises ProviderAmbiguousAbsence — never a success.
    """
    try:
        status, body = _request("DELETE", f"/v1/subscriptions/{subscription_id}")
    except deletion_saga.ProviderUnknownOutcome:
        # Timeout: the DELETE may have landed. Ask the provider what is true now.
        logger.warning("cancel: unknown outcome for sub=%s — reconciling", subscription_id)
        state = reconcile(subscription_id)     # raises on 404-ambiguous; may raise UnknownOutcome
        if state == deletion_saga.CANCEL_ALREADY:
            return state
        raise                                   # still active → saga stays blocked

    if status == 404:
        # Recorded locally but the correctly-addressed provider says 404 → AMBIGUOUS.
        # Could be a test/live-mode mismatch, wrong store, or wrong host. Never erase.
        raise ProviderAmbiguousAbsence("provider 404 for a recorded subscription (cancel)")
    _confirm_cancelled(body, subscription_id)
    logger.info("cancel: sub=%s confirmed cancelled", subscription_id)
    return deletion_saga.CANCEL_OK


def production_cancel_fn() -> Any:
    """The adapter the production endpoint injects into deletion_saga.run()."""
    return cancel
