"""billing.py — Lemon Squeezy payment integration for the :9000 product.

Closes the launch_status.json `payment` gate. Lemon Squeezy is a MERCHANT OF RECORD, so
it owns global sales-tax/VAT compliance and invoicing — we only react to its webhooks.

Flow (see docs/PAYMENT_INTEGRATION_PLAN.md):
    hosted LS checkout (no card data touches us) → LS POSTs a signed webhook here →
    we verify the HMAC signature, dedup the delivery, and translate the event into an
    auth_users action: provision (create + magic-link) on activation, revoke on churn.

Pure-ish + testable: signature verification and event parsing take raw inputs; the only
side effects (auth_users, email) are module-level functions that tests monkeypatch.

Env:
    BILLING_WEBHOOK_SECRET   — Lemon Squeezy webhook signing secret (Settings → Webhooks)
    LS_VARIANT_OBSERVER / LS_VARIANT_TRADER / LS_VARIANT_DESK  — variant ids → tier
    APP_BASE_URL             — public base, used to build the magic-link
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

import auth_users
import email_sender

logger = logging.getLogger(__name__)

MAGIC_LINK_TTL_SECONDS = 15 * 60   # short-lived onboarding link

# Lemon Squeezy event → semantic action.
_ACTIVATE_EVENTS = {
    "subscription_created", "subscription_resumed", "subscription_unpaused",
    "subscription_payment_success", "order_created",
}
# Only a truly ENDED subscription revokes access. subscription_payment_failed does NOT —
# Lemon Squeezy keeps the subscription active and dunning/retrying while past_due; revoking
# on the first failed charge wrongly locked out paying users mid-retry (H4). The real
# lockout happens when LS gives up and emits subscription_expired.
_REVOKE_EVENTS = {"subscription_expired"}
# Access continues; we only record billing_status. subscription_cancelled = "won't renew"
# but access runs to period end; payment_failed = past_due during dunning; paused = paused.
_MARK_ONLY_STATUS = {
    "subscription_cancelled":      "cancelled",
    "subscription_paused":         "paused",
    "subscription_payment_failed": "past_due",
}


# ── signature verification ───────────────────────────────────────────────────────

def _webhook_secret() -> bytes:
    return os.getenv("BILLING_WEBHOOK_SECRET", "").encode()


def verify_signature(raw_body: bytes, signature: str | None) -> bool:
    """Verify the Lemon Squeezy `X-Signature` header (hex HMAC-SHA256 of the raw body).

    Constant-time compare. Returns False on any missing secret/header — fail CLOSED so a
    misconfigured server never provisions on unverified input."""
    secret = _webhook_secret()
    if not secret or not signature:
        return False
    expected = hmac.new(secret, raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.strip())


# ── tier mapping ─────────────────────────────────────────────────────────────────

def _tier_for(variant_id: Any, variant_name: str, product_name: str) -> str | None:
    """Map a Lemon Squeezy variant to an internal tier. Prefers explicit variant-id env
    config; falls back to name matching so it still works before ids are wired."""
    env_map = {
        os.getenv("LS_VARIANT_OBSERVER", "").strip(): "observer",
        os.getenv("LS_VARIANT_TRADER", "").strip():   "trader",
        os.getenv("LS_VARIANT_DESK", "").strip():     "desk",
    }
    env_map.pop("", None)
    if variant_id is not None and str(variant_id) in env_map:
        return env_map[str(variant_id)]
    blob = f"{variant_name} {product_name}".lower()
    for key, tier in (("desk", "desk"), ("trader", "trader"), ("observer", "observer")):
        if key in blob:
            return tier
    return None


# ── event parsing ────────────────────────────────────────────────────────────────

def _subscription_id(event: str, data: dict, attrs: dict) -> str | None:
    """Resolve the TRUE subscription id for an event (H2).

    `data.id` is only the subscription id for `subscription_*` lifecycle events. For
    `subscription_payment_*` the data object is a subscription-INVOICE (data.id = invoice
    id) and the real sub id lives in attributes.subscription_id. For `order_created` data.id
    is an ORDER id and there is no subscription. Using data.id blindly corrupted
    billing_sub_id and let a stale subscription's event act on a newer one."""
    if event.startswith("subscription_payment_") or event == "order_created":
        sid = attrs.get("subscription_id")
        return str(sid) if sid is not None else None
    if event.startswith("subscription_"):
        return str(data.get("id")) if data.get("id") is not None else None
    sid = attrs.get("subscription_id")
    return str(sid) if sid is not None else None


def parse_event(payload: dict) -> dict[str, Any]:
    """Flatten a Lemon Squeezy webhook payload into the fields we act on."""
    meta = payload.get("meta") or {}
    data = payload.get("data") or {}
    attrs = data.get("attributes") or {}
    cust = attrs.get("customer_id")
    return {
        "event":       meta.get("event_name", ""),
        "email":       (attrs.get("user_email") or "").strip().lower(),
        "status":      attrs.get("status", ""),
        "customer_id": str(cust) if cust is not None else None,
        "sub_id":      _subscription_id(meta.get("event_name", ""), data, attrs),
        "tier":        _tier_for(attrs.get("variant_id"),
                                 attrs.get("variant_name", ""),
                                 attrs.get("product_name", "")),
    }


# ── idempotency (dedup replayed deliveries) ──────────────────────────────────────

_DEDUP_DDL = """
CREATE TABLE IF NOT EXISTS processed_webhook_events (
    dedup_key TEXT PRIMARY KEY,
    seen_at   TEXT NOT NULL
);
"""


@contextmanager
def _conn():
    import sqlite3
    con = sqlite3.connect(auth_users.AUTH_DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        con.executescript(_DEDUP_DDL)
        yield con
        con.commit()
    finally:
        con.close()


def _dedup_key(raw_body: bytes) -> str:
    # LS payloads carry no stable delivery id, but a retried delivery is byte-identical,
    # so a hash of the raw body is a sound idempotency key.
    return hashlib.sha256(raw_body).hexdigest()


def _claim(key: str) -> bool:
    """Atomically claim a dedup key BEFORE dispatching. Returns True if this caller won the
    claim (first to see it), False if it was already claimed. The INSERT OR IGNORE rowcount
    is the gate — closing the check-then-act TOCTOU window where two concurrent identical
    deliveries both passed a separate SELECT and both provisioned (M1). Also prunes old
    keys so the table can't grow unbounded (M3)."""
    with _conn() as con:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        con.execute("DELETE FROM processed_webhook_events WHERE seen_at < ?", (cutoff,))
        cur = con.execute(
            "INSERT OR IGNORE INTO processed_webhook_events (dedup_key, seen_at) VALUES (?,?)",
            (key, datetime.now(timezone.utc).isoformat()),
        )
        return cur.rowcount == 1


def _unclaim(key: str) -> None:
    """Release a claim so Lemon Squeezy's retry can reprocess (used when processing failed
    or a brand-new user could not be emailed — H5)."""
    with _conn() as con:
        con.execute("DELETE FROM processed_webhook_events WHERE dedup_key=?", (key,))


# ── actions ──────────────────────────────────────────────────────────────────────

def _magic_link(email: str) -> str:
    base = os.getenv("APP_BASE_URL", "").rstrip("/")
    token = auth_users.issue_token(email, ttl=MAGIC_LINK_TTL_SECONDS, typ="magic")
    return f"{base}/auth/magic?token={token}"


def provision(email: str, tier: str | None, customer_id: str | None,
              sub_id: str | None) -> dict[str, Any]:
    """Create-or-reactivate a subscriber and email a magic link to anyone who is still
    owed one (welcome_emailed_at IS NULL). The NULL-driven flag makes onboarding
    delivery RELIABLE (H5): a failed send leaves the flag unset so the LS retry (or
    /auth/request-link) re-sends; revoke clears it so a churned user who re-subscribes is
    re-onboarded. Unknown products never reach here (tier is required, H3)."""
    created = auth_users.create_magic_link_user(email, tier=tier)
    auth_users.set_billing(email, tier=tier, customer_id=customer_id,
                           sub_id=sub_id, status="active")
    owed = (auth_users.get_user(email) or {}).get("welcome_emailed_at") is None
    emailed = False
    if owed:
        emailed = email_sender.send_magic_link(email, _magic_link(email), tier=tier)
        if emailed:
            auth_users.set_welcome_emailed(email, datetime.now(timezone.utc).isoformat())
    logger.info("billing: provisioned %s tier=%s (new=%s owed=%s emailed=%s)",
                email, tier, created, owed, emailed)
    return {"action": "provision", "email": email, "tier": tier,
            "created": created, "owed_email": owed, "emailed": emailed}


def revoke(email: str, status: str, event_sub_id: str | None) -> dict[str, Any]:
    """Disable a subscriber — takes effect on their next request (authenticate_session
    re-checks is_active each call). Guarded against STALE-subscription events (H2): if the
    event names a subscription that is NOT the user's current one, it is ignored, so an old
    expired subscription can't disable a newer active one. Clears welcome_emailed_at so a
    later re-subscribe re-onboards (H5)."""
    u = auth_users.get_user(email)
    stored_sub = (u or {}).get("billing_sub_id")
    if event_sub_id and stored_sub and event_sub_id != stored_sub:
        logger.info("billing: ignored stale revoke for %s (event sub=%s current=%s)",
                    email, event_sub_id, stored_sub)
        return {"action": "ignored", "email": email, "reason": "stale_subscription"}
    auth_users.set_active(email, False)
    auth_users.set_billing(email, status=status)
    auth_users.set_welcome_emailed(email, None)
    logger.info("billing: revoked %s (status=%s)", email, status)
    return {"action": "revoke", "email": email, "status": status}


# ── orchestrator ─────────────────────────────────────────────────────────────────

def _dispatch(ev: dict[str, Any]) -> dict[str, Any]:
    """Translate a parsed event into an auth_users action (no dedup concerns here)."""
    name, email = ev["event"], ev["email"]

    def _provision_guarded() -> dict[str, Any]:
        # An activation with no recognised tier means an unknown/un-allowlisted product —
        # never grant access on it (H3). Require an allowlisted variant.
        if ev["tier"] is None:
            logger.warning("billing: refusing to provision %s on unknown variant (event=%s)",
                           email, name)
            return {"action": "ignored", "email": email, "reason": "unknown_variant"}
        return provision(email, ev["tier"], ev["customer_id"], ev["sub_id"])

    if name in _ACTIVATE_EVENTS:
        return _provision_guarded()
    if name in _REVOKE_EVENTS:
        return revoke(email, status="expired", event_sub_id=ev["sub_id"])
    if name in _MARK_ONLY_STATUS:
        status = _MARK_ONLY_STATUS[name]
        auth_users.set_billing(email, status=status)
        return {"action": "mark", "email": email, "status": status}
    if name == "subscription_updated":
        if ev["status"] in ("active", "on_trial", "paid"):
            return _provision_guarded()
        if ev["status"] == "expired":
            return revoke(email, status="expired", event_sub_id=ev["sub_id"])
        # cancelled / past_due / unpaid / paused → keep access, just record status (H4)
        if ev["status"]:
            auth_users.set_billing(email, status=ev["status"])
            return {"action": "mark", "email": email, "status": ev["status"]}
        return {"action": "noop", "status": ev["status"]}
    return {"action": "ignored", "event": name}


def handle_webhook(raw_body: bytes, signature: str | None, payload: dict) -> dict[str, Any]:
    """Verify → claim (dedup) → parse → dispatch. Returns a small status dict; raises
    nothing the caller can't turn into an HTTP code (PermissionError = 401, ValueError =
    400). The dedup key is CLAIMED atomically before dispatch so concurrent identical
    deliveries can't both act (M1); on a processing failure — or a brand-new user we could
    not email — the claim is released so the LS retry can reprocess (H5)."""
    if not verify_signature(raw_body, signature):
        raise PermissionError("invalid webhook signature")

    name = (payload.get("meta") or {}).get("event_name", "")
    email = ((payload.get("data") or {}).get("attributes") or {}).get("user_email")
    if not (email or "").strip():
        # Validate BEFORE claiming so a malformed delivery isn't permanently deduped.
        raise ValueError("webhook missing user_email")

    key = _dedup_key(raw_body)
    if not _claim(key):
        return {"action": "duplicate", "ok": True, "event": name}

    try:
        result = _dispatch(parse_event(payload))
    except Exception:
        _unclaim(key)        # let LS retry a transient failure
        raise

    # A brand-new (or re-onboarding) user we failed to email is a dead end — release the
    # claim so the retry re-sends rather than acking them into limbo (H5).
    if (result.get("action") == "provision" and result.get("owed_email")
            and not result.get("emailed")):
        _unclaim(key)

    result["ok"] = True
    result["event"] = name
    return result
