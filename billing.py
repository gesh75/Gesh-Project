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
_CLAIM_LEASE_SECONDS = 300         # pending claim older than this is presumed crashed (reclaimable)


class RetryableError(Exception):
    """A processing failure that should make the webhook return a non-2xx so Lemon Squeezy
    RETRIES the delivery (LS only retries non-2xx). Used for an onboarding email we could
    not send — releasing the dedup claim is pointless if the HTTP response is still 200."""

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
    """Map a Lemon Squeezy variant to an internal tier.

    STRICT ALLOWLIST when any LS_VARIANT_* id is configured: only an exact id match grants a
    tier, and an unconfigured/unknown id returns None (→ refused, H3). The name-substring
    fallback is used ONLY in the un-configured bootstrap state (no ids set at all) — never
    alongside ids, so a product merely *named* "Trader" can't escalate past the id gate."""
    env_map = {
        os.getenv("LS_VARIANT_OBSERVER", "").strip(): "observer",
        os.getenv("LS_VARIANT_TRADER", "").strip():   "trader",
        os.getenv("LS_VARIANT_DESK", "").strip():     "desk",
    }
    env_map.pop("", None)
    if env_map:                                   # ids configured → allowlist, no fallback
        if variant_id is not None and str(variant_id) in env_map:
            return env_map[str(variant_id)]
        return None
    blob = f"{variant_name} {product_name}".lower()   # bootstrap only (no ids configured yet)
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
        # Subscription's own last-modified time — drives monotonic ordering so an
        # out-of-order (delayed) delivery for an OLD state can't overwrite a newer one (H2).
        "event_at":    (attrs.get("updated_at") or "").strip(),
        "tier":        _tier_for(attrs.get("variant_id"),
                                 attrs.get("variant_name", ""),
                                 attrs.get("product_name", "")),
    }


# ── idempotency (dedup replayed deliveries) ──────────────────────────────────────

# An outbox-style state machine, NOT a fire-and-forget marker. A delivery is `pending` while
# being processed and only `completed` on success. A `pending` row older than the lease is
# presumed crashed (process died after claiming, before dispatching) and is RECLAIMABLE — so
# a crash can no longer permanently swallow an event by leaving it deduped for 30 days (#4).
_DEDUP_DDL = """
CREATE TABLE IF NOT EXISTS processed_webhook_events (
    dedup_key    TEXT PRIMARY KEY,
    status       TEXT NOT NULL DEFAULT 'completed',
    claimed_at   TEXT,
    completed_at TEXT,
    seen_at      TEXT NOT NULL
);
"""
# Migrate the original (dedup_key, seen_at) table to add the state columns.
_DEDUP_COLUMNS = {"status": "TEXT NOT NULL DEFAULT 'completed'",
                  "claimed_at": "TEXT", "completed_at": "TEXT"}


@contextmanager
def _conn():
    import sqlite3
    con = sqlite3.connect(auth_users.AUTH_DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        con.executescript(_DEDUP_DDL)
        have = {r["name"] for r in con.execute(
            "PRAGMA table_info(processed_webhook_events)").fetchall()}
        for col, decl in _DEDUP_COLUMNS.items():
            if col not in have:
                con.execute(f"ALTER TABLE processed_webhook_events ADD COLUMN {col} {decl}")
        yield con
        con.commit()
    finally:
        con.close()


def _dedup_key(raw_body: bytes) -> str:
    # LS payloads carry no stable delivery id, but a retried delivery is byte-identical,
    # so a hash of the raw body is a sound idempotency key.
    return hashlib.sha256(raw_body).hexdigest()


def _claim(key: str) -> str:
    """Atomically claim a dedup key as `pending` before dispatching. Returns:
        "claimed"     — fresh claim; this caller should process.
        "reclaimed"   — a stale `pending` (crashed mid-flight, past lease) re-taken; process.
        "in_progress" — another worker holds a fresh `pending` claim; treat as duplicate.
        "completed"   — already finished; duplicate.
    The INSERT OR IGNORE rowcount is the atomic gate (M1 TOCTOU). Prunes completed rows >30d
    (M3)."""
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    with _conn() as con:
        con.execute("DELETE FROM processed_webhook_events WHERE status='completed' AND seen_at < ?",
                    ((now - timedelta(days=30)).isoformat(),))
        cur = con.execute(
            "INSERT OR IGNORE INTO processed_webhook_events "
            "(dedup_key, status, claimed_at, seen_at) VALUES (?, 'pending', ?, ?)",
            (key, now_iso, now_iso),
        )
        if cur.rowcount == 1:
            return "claimed"
        row = con.execute(
            "SELECT status, claimed_at FROM processed_webhook_events WHERE dedup_key=?", (key,)
        ).fetchone()
        if row is None:                       # raced delete; treat as fresh next retry
            return "in_progress"
        if row["status"] == "completed":
            return "completed"
        # status == 'pending': reclaim only if the lease has expired (crashed worker).
        lease_cut = (now - timedelta(seconds=_CLAIM_LEASE_SECONDS)).isoformat()
        if (row["claimed_at"] or "") < lease_cut:
            con.execute("UPDATE processed_webhook_events SET claimed_at=? WHERE dedup_key=?",
                        (now_iso, key))
            return "reclaimed"
        return "in_progress"


def _complete(key: str) -> None:
    """Mark a claimed delivery finished — only now is it a permanent duplicate."""
    with _conn() as con:
        con.execute(
            "UPDATE processed_webhook_events SET status='completed', completed_at=? "
            "WHERE dedup_key=?",
            (datetime.now(timezone.utc).isoformat(), key),
        )


def _unclaim(key: str) -> None:
    """Release a claim so Lemon Squeezy's retry can reprocess (processing failed or a
    brand-new user could not be emailed — H5/#4)."""
    with _conn() as con:
        con.execute("DELETE FROM processed_webhook_events WHERE dedup_key=?", (key,))


# ── actions ──────────────────────────────────────────────────────────────────────

def _magic_link(email: str) -> str:
    base = os.getenv("APP_BASE_URL", "").rstrip("/")
    token = auth_users.issue_token(email, ttl=MAGIC_LINK_TTL_SECONDS, typ="magic")
    return f"{base}/auth/magic?token={token}"


def provision(email: str, tier: str | None, customer_id: str | None,
              sub_id: str | None, event_at: str = "") -> dict[str, Any]:
    """Create-or-reactivate a subscriber and email a magic link to anyone who is still
    owed one (welcome_emailed_at IS NULL). The NULL-driven flag makes onboarding
    delivery RELIABLE (H5): a failed send leaves the flag unset so the LS retry (or
    /auth/request-link) re-sends; revoke clears it so a churned user who re-subscribes is
    re-onboarded. Unknown products never reach here (tier is required, H3)."""
    created = auth_users.create_magic_link_user(email, tier=tier)
    auth_users.set_billing(email, tier=tier, customer_id=customer_id,
                           sub_id=sub_id, status="active", event_at=event_at or None)
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


def revoke(email: str, status: str, event_sub_id: str | None,
           event_at: str = "") -> dict[str, Any]:
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
    auth_users.set_billing(email, status=status, event_at=event_at or None)
    auth_users.set_welcome_emailed(email, None)
    logger.info("billing: revoked %s (status=%s)", email, status)
    return {"action": "revoke", "email": email, "status": status}


# ── orchestrator ─────────────────────────────────────────────────────────────────

def _resolve_account(ev: dict[str, Any]) -> tuple[str, dict | None]:
    """Resolve which account an event acts on, treating billing_sub_id as authoritative (#5).

    If the subscription already maps to an account under a DIFFERENT email, the customer
    changed their email — move that one account to the new email rather than creating a
    second (a signed update under a changed email otherwise made two active accounts).
    Returns (email_to_use, existing_user_row)."""
    email, sub_id = ev["email"], ev["sub_id"]
    by_sub = auth_users.get_user_by_sub_id(sub_id) if sub_id else None
    if by_sub and email and by_sub["email"] != email:
        if auth_users.rename_user_email(by_sub["email"], email):
            logger.info("billing: moved sub=%s %s -> %s (email change)",
                        sub_id, by_sub["email"], email)
            return email, auth_users.get_user(email)
        logger.warning("billing: email-change conflict sub=%s (%s -> %s already taken)",
                       sub_id, by_sub["email"], email)
        return by_sub["email"], by_sub        # can't merge; keep operating on the sub's row
    if by_sub:
        return by_sub["email"], by_sub
    return email, auth_users.get_user(email)


def _is_stale(ev: dict[str, Any], user: dict | None) -> bool:
    """True if this event is OLDER than the last billing event we applied to the account —
    an out-of-order (delayed) delivery that must not overwrite newer state (#2)."""
    ev_at = ev.get("event_at") or ""
    prev = (user or {}).get("billing_event_at") or ""
    return bool(ev_at and prev and ev_at < prev)


_STATE_CHANGING = (_ACTIVATE_EVENTS | _REVOKE_EVENTS
                   | set(_MARK_ONLY_STATUS) | {"subscription_updated"})


def _dispatch(ev: dict[str, Any]) -> dict[str, Any]:
    """Translate a parsed event into an auth_users action (no dedup concerns here)."""
    name = ev["event"]
    email, user = _resolve_account(ev)
    ev = {**ev, "email": email}                # operate on the resolved (possibly moved) email

    # Drop events that arrive out of order for any state-changing transition (#2).
    if name in _STATE_CHANGING and _is_stale(ev, user):
        logger.info("billing: ignored stale event %s for %s (event_at=%s <= applied=%s)",
                    name, email, ev.get("event_at"), (user or {}).get("billing_event_at"))
        return {"action": "ignored", "email": email, "reason": "stale_event"}

    def _provision_guarded() -> dict[str, Any]:
        # An activation with no recognised tier means an unknown/un-allowlisted product —
        # never grant access on it (H3). Require an allowlisted variant.
        if ev["tier"] is None:
            logger.warning("billing: refusing to provision %s on unknown variant (event=%s)",
                           email, name)
            return {"action": "ignored", "email": email, "reason": "unknown_variant"}
        return provision(email, ev["tier"], ev["customer_id"], ev["sub_id"], ev.get("event_at", ""))

    eat = ev.get("event_at", "")
    if name in _ACTIVATE_EVENTS:
        return _provision_guarded()
    if name in _REVOKE_EVENTS:
        return revoke(email, status="expired", event_sub_id=ev["sub_id"], event_at=eat)
    if name in _MARK_ONLY_STATUS:
        status = _MARK_ONLY_STATUS[name]
        auth_users.set_billing(email, status=status, event_at=eat or None)
        return {"action": "mark", "email": email, "status": status}
    if name == "subscription_updated":
        if ev["status"] in ("active", "on_trial", "paid"):
            return _provision_guarded()
        if ev["status"] == "expired":
            return revoke(email, status="expired", event_sub_id=ev["sub_id"], event_at=eat)
        # cancelled / past_due / unpaid / paused → keep access, just record status (H4)
        if ev["status"]:
            auth_users.set_billing(email, status=ev["status"], event_at=ev.get("event_at") or None)
            return {"action": "mark", "email": email, "status": ev["status"]}
        return {"action": "noop", "status": ev["status"]}
    return {"action": "ignored", "event": name}


def handle_webhook(raw_body: bytes, signature: str | None, payload: dict) -> dict[str, Any]:
    """Verify → claim (dedup outbox) → parse → dispatch → complete. Raises only what the
    caller can map to HTTP: PermissionError=401, ValueError=400, RetryableError=503.

    The dedup key is claimed as `pending` BEFORE dispatch (atomic, M1) and marked `completed`
    ONLY on success — a crash mid-flight leaves a leased `pending` row that a later retry
    reclaims, so an event is never permanently swallowed (#4). A failure, or an onboarding
    email we couldn't send, releases the claim AND raises RetryableError so the HTTP response
    is non-2xx and Lemon Squeezy actually retries (#1)."""
    if not verify_signature(raw_body, signature):
        raise PermissionError("invalid webhook signature")

    name = (payload.get("meta") or {}).get("event_name", "")
    email = ((payload.get("data") or {}).get("attributes") or {}).get("user_email")
    if not (email or "").strip():
        # Validate BEFORE claiming so a malformed delivery isn't permanently deduped.
        raise ValueError("webhook missing user_email")

    key = _dedup_key(raw_body)
    claim = _claim(key)
    if claim in ("completed", "in_progress"):
        return {"action": "duplicate", "ok": True, "event": name}

    try:
        result = _dispatch(parse_event(payload))
    except Exception:
        _unclaim(key)        # let LS retry a transient failure
        raise

    # A brand-new (or re-onboarding) user we failed to email is a dead end — release the
    # claim and force a non-2xx so the LS retry re-sends rather than acking into limbo (#1/H5).
    if (result.get("action") == "provision" and result.get("owed_email")
            and not result.get("emailed")):
        _unclaim(key)
        raise RetryableError(f"onboarding email not sent to {result.get('email')}")

    _complete(key)
    result["ok"] = True
    result["event"] = name
    return result
