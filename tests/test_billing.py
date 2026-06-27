"""
tests/test_billing.py — Lemon Squeezy payment webhook (gesh-project :9000).

Covers the contract that flips the launch `payment` gate: signature verification
(fail-closed), event parsing + tier mapping, idempotent dedup of replayed deliveries,
and the provision/revoke side effects on auth_users. No network: email is monkeypatched,
SQLite is a temp file, the webhook secret is fixed.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from typing import Generator

import pytest


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator:
    db = tmp_path / "auth.db"
    monkeypatch.setenv("GESH_AUTH_DB", str(db))
    monkeypatch.setenv("GESH_AUTH_SECRET", "test-secret-deterministic")
    monkeypatch.setenv("BILLING_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setenv("APP_BASE_URL", "https://gesh.test")
    monkeypatch.delenv("GESH_AUTH_ENABLED", raising=False)
    import auth_users as au
    monkeypatch.setattr(au, "AUTH_DB_PATH", db)
    au.init_auth_db()
    # never hit the network for email — record calls instead
    import email_sender
    sent: list = []
    monkeypatch.setattr(email_sender, "send_magic_link",
                        lambda to, link, tier=None: sent.append((to, link, tier)) or True)
    monkeypatch.setattr("billing.email_sender.send_magic_link",
                        email_sender.send_magic_link)
    yield


import auth_users as au
import billing


def _sign(raw: bytes, secret: str = "whsec_test") -> str:
    return hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def _payload(event: str, email: str = "buyer@example.com", *,
             status: str = "active", variant_name: str = "Trader plan",
             sub_id: str = "sub_1", customer_id: int = 99,
             updated_at: str = "2026-01-01T00:00:00.000000Z",
             variant_id=None) -> dict:
    attrs = {"user_email": email, "status": status, "customer_id": customer_id,
             "variant_name": variant_name, "product_name": "GESH", "updated_at": updated_at}
    if variant_id is not None:
        attrs["variant_id"] = variant_id
    return {"meta": {"event_name": event},
            "data": {"id": sub_id, "type": "subscriptions", "attributes": attrs}}


# ── signature verification (fail closed) ──────────────────────────────────────

def test_valid_signature_passes():
    raw = b'{"a":1}'
    assert billing.verify_signature(raw, _sign(raw)) is True


def test_bad_signature_fails():
    assert billing.verify_signature(b'{"a":1}', "deadbeef") is False


def test_missing_signature_or_secret_fails(monkeypatch):
    assert billing.verify_signature(b'{}', None) is False
    monkeypatch.setenv("BILLING_WEBHOOK_SECRET", "")
    assert billing.verify_signature(b'{}', _sign(b'{}')) is False


# ── event parsing + tier mapping ──────────────────────────────────────────────

def test_parse_event_extracts_fields_and_tier_by_name():
    ev = billing.parse_event(_payload("subscription_created", variant_name="Desk plan"))
    assert ev["event"] == "subscription_created"
    assert ev["email"] == "buyer@example.com"
    assert ev["tier"] == "desk"
    assert ev["sub_id"] == "sub_1"
    assert ev["customer_id"] == "99"


def test_tier_mapping_prefers_variant_id_env(monkeypatch):
    monkeypatch.setenv("LS_VARIANT_TRADER", "555")
    assert billing._tier_for("555", "anything", "GESH") == "trader"


# ── provision / revoke via handle_webhook ─────────────────────────────────────

def _post(event: str, **kw) -> dict:
    payload = _payload(event, **kw)
    raw = json.dumps(payload).encode()
    return billing.handle_webhook(raw, _sign(raw), payload)


def test_subscription_created_provisions_user():
    res = _post("subscription_created", variant_name="Trader plan")
    assert res["ok"] and res["action"] == "provision" and res["created"] is True
    u = au.get_user("buyer@example.com")
    assert u and u["is_active"] == 1 and u["tier"] == "trader"
    assert u["billing_status"] == "active" and u["billing_sub_id"] == "sub_1"


def test_duplicate_delivery_is_idempotent():
    payload = _payload("subscription_created")
    raw = json.dumps(payload).encode()
    first = billing.handle_webhook(raw, _sign(raw), payload)
    second = billing.handle_webhook(raw, _sign(raw), payload)
    assert first["action"] == "provision"
    assert second["action"] == "duplicate"


def test_subscription_expired_revokes_user():
    _post("subscription_created")
    res = _post("subscription_expired", status="expired", sub_id="sub_1")
    # NOTE: expired payload differs from created (status) → distinct dedup key
    assert res["action"] == "revoke"
    u = au.get_user("buyer@example.com")
    assert u["is_active"] == 0 and u["billing_status"] == "expired"


def test_cancelled_keeps_access_until_expiry():
    _post("subscription_created")
    res = _post("subscription_cancelled", status="cancelled")
    assert res["action"] == "mark"
    u = au.get_user("buyer@example.com")
    assert u["is_active"] == 1                      # still active until it expires
    assert u["billing_status"] == "cancelled"


def test_magic_token_is_single_use():
    """A magic-link token works exactly once; replay (leaked URL) is rejected."""
    au.create_magic_link_user("buyer@example.com", tier="trader")
    token = au.issue_token("buyer@example.com", ttl=900, typ="magic")
    assert au.consume_magic_token(token) == "buyer@example.com"   # first use
    assert au.consume_magic_token(token) is None                  # replay rejected


# ── H1: magic and session tokens are NOT interchangeable ──────────────────────

def test_magic_token_rejected_as_session():
    """A leaked magic URL token must NOT authenticate as a session cookie (H1)."""
    au.create_magic_link_user("buyer@example.com", tier="trader")
    magic = au.issue_token("buyer@example.com", ttl=900, typ="magic")
    assert au.authenticate_session(magic) is None        # wrong purpose → rejected
    assert au.verify_token(magic, expected_typ="session") is None


def test_session_token_rejected_as_magic():
    """A session cookie can't be spent as a one-time magic token (H1)."""
    au.create_magic_link_user("buyer@example.com", tier="trader")
    session = au.issue_token("buyer@example.com", typ="session")
    assert au.consume_magic_token(session) is None


def test_legacy_typeless_token_treated_as_session():
    """Back-compat: cookies issued before the typ claim (no typ) still authenticate."""
    import json as _json, time as _time, hashlib as _h, hmac as _hm
    payload = au._b64u(_json.dumps(
        {"sub": "buyer@example.com", "exp": int(_time.time()) + 900}).encode())
    sig = au._b64u(_hm.new(au._secret(), payload.encode(), _h.sha256).digest())
    legacy = f"{payload}.{sig}"
    au.create_magic_link_user("buyer@example.com", tier="trader")
    assert au.authenticate_session(legacy) == "buyer@example.com"


def test_consume_rejects_invalid_token():
    assert au.consume_magic_token("not-a-real-token") is None
    assert au.consume_magic_token(None) is None


def test_bad_signature_raises_permission_error():
    payload = _payload("subscription_created")
    raw = json.dumps(payload).encode()
    with pytest.raises(PermissionError):
        billing.handle_webhook(raw, "nope", payload)


def test_missing_email_raises_value_error():
    payload = _payload("subscription_created", email="")
    raw = json.dumps(payload).encode()
    with pytest.raises(ValueError):
        billing.handle_webhook(raw, _sign(raw), payload)


# ── H4: a failed payment must NOT revoke during dunning ───────────────────────

def test_payment_failed_keeps_access_marks_past_due():
    _post("subscription_created")
    res = _post("subscription_payment_failed", status="past_due", sub_id="sub_1")
    assert res["action"] == "mark" and res["status"] == "past_due"
    u = au.get_user("buyer@example.com")
    assert u["is_active"] == 1                      # still active while LS retries
    assert u["billing_status"] == "past_due"


def test_updated_cancelled_keeps_access():
    _post("subscription_created")
    res = _post("subscription_updated", status="cancelled")
    assert res["action"] == "mark"
    assert au.get_user("buyer@example.com")["is_active"] == 1


# ── H3: unknown / un-allowlisted product never grants access ──────────────────

def test_unknown_variant_is_not_provisioned():
    res = _post("order_created", variant_name="Sticker pack", status="paid")
    assert res["action"] == "ignored" and res["reason"] == "unknown_variant"
    assert au.get_user("buyer@example.com") is None


# ── H2: subscription identity integrity + stale-sub revoke guard ──────────────

def test_payment_event_does_not_corrupt_sub_id():
    """An invoice/payment event carries data.id=invoice; the real sub id is in
    attributes.subscription_id — billing_sub_id must track THAT, not the invoice id."""
    _post("subscription_created", sub_id="sub_1")           # establishes sub_1
    payload = {
        "meta": {"event_name": "subscription_payment_success"},
        "data": {"id": "invoice-8", "type": "subscription-invoices",
                 "attributes": {"user_email": "buyer@example.com", "status": "paid",
                                "subscription_id": "sub_1", "variant_name": "Trader plan",
                                "product_name": "GESH"}},
    }
    raw = json.dumps(payload).encode()
    billing.handle_webhook(raw, _sign(raw), payload)
    assert au.get_user("buyer@example.com")["billing_sub_id"] == "sub_1"   # not invoice-8


def test_stale_subscription_expiry_does_not_disable_newer_sub():
    """An old subscription's expired event must not revoke a newer active subscription."""
    _post("subscription_created", sub_id="sub_OLD")
    _post("subscription_created", sub_id="sub_NEW")          # user now on sub_NEW
    res = _post("subscription_expired", status="expired", sub_id="sub_OLD")
    assert res["action"] == "ignored" and res["reason"] == "stale_subscription"
    assert au.get_user("buyer@example.com")["is_active"] == 1


# ── H5: onboarding email is reliably retryable ────────────────────────────────

def test_failed_onboarding_email_raises_retryable_and_is_not_deduped(monkeypatch):
    """If the welcome email can't be sent, the webhook raises RetryableError (→ 503) so LS
    actually retries, and the delivery is NOT acked as done — the retry reprocesses (#1)."""
    monkeypatch.setattr("billing.email_sender.send_magic_link",
                        lambda *a, **k: False)               # email outage
    with pytest.raises(billing.RetryableError):
        _post("subscription_created")
    # retry (identical body) is NOT swallowed as duplicate — it reprocesses (still failing)
    with pytest.raises(billing.RetryableError):
        _post("subscription_created")
    # once email recovers, the same delivery completes
    monkeypatch.setattr("billing.email_sender.send_magic_link", lambda *a, **k: True)
    third = _post("subscription_created")
    assert third["action"] == "provision" and third["emailed"] is True


def test_request_link_only_emails_active_user(monkeypatch):
    monkeypatch.setattr("auth_users.issue_magic_token", au.issue_magic_token)
    au.create_magic_link_user("buyer@example.com", tier="trader")
    assert au.issue_magic_token("buyer@example.com") is not None
    au.set_active("buyer@example.com", False)
    assert au.issue_magic_token("buyer@example.com") is None      # inactive → no link
    assert au.issue_magic_token("nobody@example.com") is None     # unknown → no link


# ── #2 (round-3): out-of-order delivery must not overwrite newer state ─────────

def test_delayed_activation_for_old_sub_is_ignored():
    """A late subscription_updated(active) for an OLD subscription must not reactivate an
    account whose newer subscription already expired (monotonic ordering, #2)."""
    _post("subscription_created", sub_id="sub_new",
          updated_at="2026-03-10T00:00:00.000000Z")
    _post("subscription_expired", status="expired", sub_id="sub_new",
          updated_at="2026-03-20T00:00:00.000000Z")
    assert au.get_user("buyer@example.com")["is_active"] == 0
    # delayed activation for the older sub (earlier updated_at) arrives now
    res = _post("subscription_updated", status="active", sub_id="sub_old",
                updated_at="2026-03-01T00:00:00.000000Z")
    assert res["action"] == "ignored" and res["reason"] == "stale_event"
    assert au.get_user("buyer@example.com")["is_active"] == 0       # still revoked


# ── #3 (round-3): strict allowlist — name can't escalate past the id gate ──────

def test_strict_allowlist_rejects_name_match_when_ids_configured(monkeypatch):
    monkeypatch.setenv("LS_VARIANT_TRADER", "555")
    assert billing._tier_for("999", "Trader sticker", "GESH") is None   # unknown id, no fallback
    assert billing._tier_for("555", "anything", "GESH") == "trader"     # allowlisted id wins
    res = _post("order_created", variant_name="Trader sticker", variant_id=999, status="paid")
    assert res["action"] == "ignored" and res["reason"] == "unknown_variant"
    assert au.get_user("buyer@example.com") is None


# ── #4 (round-3): a crash after claim must not permanently swallow the event ───

def test_crash_after_claim_is_reclaimed_after_lease(monkeypatch):
    payload = _payload("subscription_created")
    raw = json.dumps(payload).encode()
    key = billing._dedup_key(raw)
    status, _token = billing._claim(key)
    assert status == "claimed"                       # worker claimed, then "crashed"
    # a fresh retry within the lease must NOT be acked as done (would stop LS retrying) —
    # it surfaces as in_progress → 503
    assert billing._claim(key)[0] == "in_progress"
    with pytest.raises(billing.RetryableError):
        billing.handle_webhook(raw, _sign(raw), payload)
    monkeypatch.setattr(billing, "_CLAIM_LEASE_SECONDS", 0)   # lease elapses
    res = billing.handle_webhook(raw, _sign(raw), payload)    # retry reclaims + processes
    assert res["action"] == "provision"
    assert au.get_user("buyer@example.com") is not None


def test_reclaim_is_owner_fenced(monkeypatch):
    """Two reclaimers can't both win, and a resurrected old worker can't complete/delete the
    newer claim (owner-token CAS, R4-3)."""
    payload = _payload("subscription_created")
    raw = json.dumps(payload).encode()
    key = billing._dedup_key(raw)
    s0, tok_old = billing._claim(key)
    assert s0 == "claimed"
    monkeypatch.setattr(billing, "_CLAIM_LEASE_SECONDS", 0)   # lease expired → reclaimable
    s1, tok_new = billing._claim(key)
    assert s1 == "reclaimed" and tok_new != tok_old
    monkeypatch.setattr(billing, "_CLAIM_LEASE_SECONDS", 3600)  # freeze the lease for the checks
    # the resurrected old worker tries to finish/release — both must be NO-OPs (it no longer
    # owns the row); the row stays pending under the new claimer
    billing._complete(key, tok_old)
    billing._unclaim(key, tok_old)
    assert billing._claim(key)[0] == "in_progress"           # still pending, owned by tok_new
    # the new owner can still complete it
    billing._complete(key, tok_new)
    assert billing._claim(key)[0] == "completed"


# ── #5 (round-3): an email change keeps ONE account (sub_id authoritative) ─────

def test_email_change_keeps_single_account():
    _post("subscription_created", email="old@example.com", sub_id="sub_x",
          updated_at="2026-04-01T00:00:00.000000Z")
    res = _post("subscription_updated", email="new@example.com", sub_id="sub_x",
                status="active", updated_at="2026-04-05T00:00:00.000000Z")
    assert res["email"] == "new@example.com"
    assert au.get_user("old@example.com") is None                 # moved, not duplicated
    u = au.get_user("new@example.com")
    assert u and u["is_active"] == 1 and u["billing_sub_id"] == "sub_x"


def test_stale_event_does_not_rename_before_rejection():
    """A delayed stale event must be rejected WITHOUT first mutating the email (R4-2):
    the account stays on the newer email, not silently renamed back."""
    _post("subscription_created", email="old@example.com", sub_id="sub_x",
          updated_at="2026-05-01T00:00:00.000000Z")
    _post("subscription_updated", email="new@example.com", sub_id="sub_x",
          status="active", updated_at="2026-05-10T00:00:00.000000Z")     # moved to new@
    # a delayed (older) event for the same sub under the OLD email arrives now
    res = _post("subscription_updated", email="old@example.com", sub_id="sub_x",
                status="active", updated_at="2026-05-05T00:00:00.000000Z")
    assert res["action"] == "ignored" and res["reason"] == "stale_event"
    assert au.get_user("new@example.com") is not None                    # not renamed back
    assert au.get_user("old@example.com") is None


# ── #6 (round-3): the /auth/request-link endpoint + its rate limit ────────────

def test_request_link_endpoint_and_rate_limit(monkeypatch):
    """Exercise the real HTTP route: it always 200s (no enumeration), only emails active
    users, and 429s after the per-IP burst limit."""
    from starlette.testclient import TestClient
    import server                                   # heavy import; route lives here
    sent: list = []
    monkeypatch.setattr(server.email_sender, "send_magic_link",
                        lambda to, link, tier=None: sent.append(to) or True)
    au.create_magic_link_user("buyer@example.com", tier="trader")
    client = TestClient(server.app)                 # no `with` → lifespan/startup not run
    # unknown address: still 200 (no account enumeration) but sends NO email
    assert client.post("/auth/request-link", json={"email": "nobody@example.com"}).status_code == 200
    # active user: emailed; limit is 5/window — 1 used above, 4 more ok, then 429
    codes = [client.post("/auth/request-link", json={"email": "buyer@example.com"}).status_code
             for _ in range(5)]
    assert codes[:4] == [200] * 4 and codes[4] == 429
    assert sent == ["buyer@example.com"] * 4        # only the active user, only within the limit
