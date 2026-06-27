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
from datetime import datetime, timezone
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
_REVOKE_EVENTS = {
    "subscription_expired", "subscription_payment_failed",
}
# subscription_cancelled = "won't renew" but access continues until period end → we keep
# the user active and only mark billing_status; subscription_expired does the real revoke.
_MARK_ONLY_EVENTS = {"subscription_cancelled", "subscription_paused"}


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

def parse_event(payload: dict) -> dict[str, Any]:
    """Flatten a Lemon Squeezy webhook payload into the fields we act on."""
    meta = payload.get("meta") or {}
    data = payload.get("data") or {}
    attrs = data.get("attributes") or {}
    return {
        "event":       meta.get("event_name", ""),
        "email":       (attrs.get("user_email") or "").strip().lower(),
        "status":      attrs.get("status", ""),
        "customer_id": str(attrs.get("customer_id")) if attrs.get("customer_id") is not None else None,
        "sub_id":      str(data.get("id")) if data.get("id") is not None else None,
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


def _already_processed(key: str) -> bool:
    with _conn() as con:
        return con.execute(
            "SELECT 1 FROM processed_webhook_events WHERE dedup_key=?", (key,)
        ).fetchone() is not None


def _mark_processed(key: str) -> None:
    with _conn() as con:
        con.execute(
            "INSERT OR IGNORE INTO processed_webhook_events (dedup_key, seen_at) VALUES (?,?)",
            (key, datetime.now(timezone.utc).isoformat()),
        )


# ── actions ──────────────────────────────────────────────────────────────────────

def _magic_link(email: str) -> str:
    base = os.getenv("APP_BASE_URL", "").rstrip("/")
    token = auth_users.issue_token(email, ttl=MAGIC_LINK_TTL_SECONDS)
    return f"{base}/auth/magic?token={token}"


def provision(email: str, tier: str | None, customer_id: str | None,
              sub_id: str | None) -> dict[str, Any]:
    """Create-or-reactivate a subscriber and email the magic link to brand-new users."""
    created = auth_users.create_magic_link_user(email, tier=tier)
    auth_users.set_billing(email, tier=tier, customer_id=customer_id,
                           sub_id=sub_id, status="active")
    emailed = False
    if created:
        emailed = email_sender.send_magic_link(email, _magic_link(email), tier=tier)
    logger.info("billing: provisioned %s tier=%s (new=%s emailed=%s)",
                email, tier, created, emailed)
    return {"action": "provision", "email": email, "tier": tier,
            "created": created, "emailed": emailed}


def revoke(email: str, status: str = "cancelled") -> dict[str, Any]:
    """Disable a subscriber — takes effect on their very next request (rule:
    authenticate_session re-checks is_active each call)."""
    auth_users.set_active(email, False)
    auth_users.set_billing(email, status=status)
    logger.info("billing: revoked %s (status=%s)", email, status)
    return {"action": "revoke", "email": email, "status": status}


# ── orchestrator ─────────────────────────────────────────────────────────────────

def handle_webhook(raw_body: bytes, signature: str | None, payload: dict) -> dict[str, Any]:
    """Verify → dedup → parse → dispatch. Returns a small status dict; raises nothing the
    caller can't turn into an HTTP code (PermissionError = 401, ValueError = 400)."""
    if not verify_signature(raw_body, signature):
        raise PermissionError("invalid webhook signature")

    key = _dedup_key(raw_body)
    if _already_processed(key):
        return {"action": "duplicate", "ok": True}

    ev = parse_event(payload)
    name, email = ev["event"], ev["email"]
    if not email:
        raise ValueError("webhook missing user_email")

    if name in _ACTIVATE_EVENTS:
        result = provision(email, ev["tier"], ev["customer_id"], ev["sub_id"])
    elif name in _REVOKE_EVENTS:
        result = revoke(email, status="expired" if name == "subscription_expired" else "past_due")
    elif name == "subscription_updated":
        # status drives it: active → ensure provisioned; terminal → revoke
        if ev["status"] in ("active", "on_trial", "paid"):
            result = provision(email, ev["tier"], ev["customer_id"], ev["sub_id"])
        elif ev["status"] in ("expired", "unpaid", "cancelled"):
            result = revoke(email, status=ev["status"])
        else:
            result = {"action": "noop", "status": ev["status"]}
    elif name in _MARK_ONLY_EVENTS:
        auth_users.set_billing(email, status="cancelled")
        result = {"action": "mark", "email": email, "status": "cancelled"}
    else:
        result = {"action": "ignored", "event": name}

    _mark_processed(key)
    result["ok"] = True
    result["event"] = name
    return result
