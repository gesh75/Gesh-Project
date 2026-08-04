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
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
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
    # Hermetic tier mapping: clear any ambient LS_VARIANT_* (e.g. a populated .env or shell
    # env) so name-based tests run in bootstrap mode as they assume; allowlist-mode tests set
    # these explicitly via monkeypatch. Without this, a real LS_VARIANT_OBSERVER leaks in and
    # flips _tier_for to strict-allowlist, refusing every name-only payload.
    for _v in ("LS_VARIANT_OBSERVER", "LS_VARIANT_TRADER", "LS_VARIANT_DESK"):
        monkeypatch.delenv(_v, raising=False)
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


def test_malformed_signature_fails_closed_not_500():
    """Non-hex / non-ASCII / wrong-length signatures must fail CLOSED (False → 401), not
    raise TypeError out of compare_digest (which would become a 500)."""
    raw = b'{"a":1}'
    assert billing.verify_signature(raw, "é") is False                 # non-ASCII
    assert billing.verify_signature(raw, "zz" * 32) is False            # 64 chars, non-hex
    assert billing.verify_signature(raw, "abc123") is False             # too short
    assert billing.verify_signature(raw, "a" * 65) is False             # too long
    # and the full webhook turns it into a 401-class PermissionError, never a 500
    payload = _payload("subscription_created")
    with pytest.raises(PermissionError):
        billing.handle_webhook(json.dumps(payload).encode(), "é", payload)


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


def test_completed_delivery_remains_duplicate_after_30_days():
    """Completed activation claims must never expire: an old signed payload has no freshness
    proof and could otherwise reactivate a churned account when replayed after pruning."""
    payload = _payload("subscription_created", sub_id="sub_old")
    raw = json.dumps(payload).encode()
    assert billing.handle_webhook(raw, _sign(raw), payload)["action"] == "provision"

    key = billing._dedup_key(raw)
    with billing._conn() as con:
        con.execute(
            "UPDATE processed_webhook_events SET seen_at=? WHERE dedup_key=?",
            ("2020-01-01T00:00:00+00:00", key),
        )

    assert billing.handle_webhook(raw, _sign(raw), payload)["action"] == "duplicate"


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


def test_legacy_email_subject_token_is_rejected_fail_closed():
    """Pre-v2 email-subject cookies must never authenticate a reused address."""
    import hashlib as _h
    import hmac as _hm
    import json as _json
    import time as _time
    payload = au._b64u(_json.dumps(
        {"sub": "buyer@example.com", "exp": int(_time.time()) + 900}).encode())
    sig = au._b64u(_hm.new(au._secret(), payload.encode(), _h.sha256).digest())
    legacy = f"{payload}.{sig}"
    au.create_magic_link_user("buyer@example.com", tier="trader")
    assert au.verify_token(legacy) is None
    assert au.authenticate_session(legacy) is None
    assert au.consume_magic_token(legacy) is None


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


# ── subscription email changes cannot rename the auth principal ───────────────

def test_email_change_does_not_rename_auth_account():
    _post("subscription_created", email="old@example.com", sub_id="sub_x",
          updated_at="2026-04-01T00:00:00.000000Z")
    res = _post("subscription_updated", email="new@example.com", sub_id="sub_x",
                status="active", updated_at="2026-04-05T00:00:00.000000Z")
    assert res["email"] == "old@example.com"
    assert au.get_user("new@example.com") is None
    u = au.get_user("old@example.com")
    assert u and u["is_active"] == 1 and u["billing_sub_id"] == "sub_x"


def test_subscription_email_change_cannot_enable_magic_link_takeover():
    """Regression: a payer cannot move a subscription-bound victim auth row to their email."""
    au.create_magic_link_user("victim@example.com", tier="observer")
    _post("subscription_created", email="victim@example.com", sub_id="sub_attacker",
          updated_at="2026-04-01T00:00:00.000000Z")

    _post("subscription_updated", email="attacker@example.com", sub_id="sub_attacker",
          status="active", updated_at="2026-04-05T00:00:00.000000Z")

    assert au.get_user("victim@example.com")["billing_sub_id"] == "sub_attacker"
    assert au.get_user("attacker@example.com") is None
    assert au.issue_magic_token("attacker@example.com") is None


def test_stale_event_does_not_mutate_email_before_rejection():
    """A delayed stale event is rejected without changing the subscription-bound email."""
    _post("subscription_created", email="old@example.com", sub_id="sub_x",
          updated_at="2026-05-01T00:00:00.000000Z")
    _post("subscription_updated", email="new@example.com", sub_id="sub_x",
          status="active", updated_at="2026-05-10T00:00:00.000000Z")
    # a delayed (older) event for the same sub under the OLD email arrives now
    res = _post("subscription_updated", email="old@example.com", sub_id="sub_x",
                status="active", updated_at="2026-05-05T00:00:00.000000Z")
    assert res["action"] == "ignored" and res["reason"] == "stale_event"
    assert au.get_user("old@example.com") is not None
    assert au.get_user("new@example.com") is None


# ── #6 (round-3): the /auth/request-link endpoint + its rate limit ────────────

def test_request_link_endpoint_and_rate_limit(monkeypatch):
    """Exercise the real HTTP route: it always 200s (no enumeration), only emails active
    users, and 429s after the per-IP burst limit."""
    from starlette.testclient import TestClient

    import server  # heavy import; route lives here
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


# ── L2: signature hex-case normalization ──────────────────────────────────────

def test_signature_accepts_uppercase_hex():
    """Defense-in-depth: a constant-time match must still succeed if the provider ever sends
    the HMAC as uppercase hex (LS sends lowercase today)."""
    raw = json.dumps(_payload("subscription_created")).encode()
    assert billing.verify_signature(raw, _sign(raw).upper()) is True
    assert billing.verify_signature(raw, "deadbeef") is False   # wrong sig still rejected


# ── M2: dead-letter alert fires when onboarding email can't be sent ───────────

def test_failed_email_triggers_dead_letter_alert(monkeypatch):
    monkeypatch.setattr("billing.email_sender.send_magic_link",
                        lambda to, link, tier=None: False)      # email provider down
    alerts: list = []
    monkeypatch.setattr(billing, "_alert_dead_letter",
                        lambda email, reason: alerts.append((email, reason)) or True)
    payload = _payload("subscription_created", email="stuck@example.com")
    raw = json.dumps(payload).encode()
    with pytest.raises(billing.RetryableError):
        billing.handle_webhook(raw, _sign(raw), payload)
    assert alerts and alerts[0][0] == "stuck@example.com"


def test_alert_dead_letter_never_raises(monkeypatch):
    """Alerting is strictly best-effort — a broken alert channel must not bubble up."""
    def _boom(*a, **k):
        raise RuntimeError("telegram down")
    monkeypatch.setattr("trading.alerts._send", _boom)
    # even if the send blows up, the helper swallows it and returns False
    assert billing._alert_dead_letter("x@example.com", reason="test") is False


# ── M1: async owed-onboarding sweep recovers stuck subscribers ────────────────

def test_resend_owed_onboarding_recovers_stuck_user(monkeypatch):
    # first delivery fails → user provisioned but still owed a link (welcome_emailed_at NULL)
    monkeypatch.setattr("billing.email_sender.send_magic_link",
                        lambda to, link, tier=None: False)
    monkeypatch.setattr(billing, "_alert_dead_letter", lambda email, reason: True)
    payload = _payload("subscription_created", email="late@example.com")
    raw = json.dumps(payload).encode()
    with pytest.raises(billing.RetryableError):
        billing.handle_webhook(raw, _sign(raw), payload)
    u = au.get_user("late@example.com")
    assert u["is_active"] == 1 and u["welcome_emailed_at"] is None

    # provider recovers → the sweep re-sends and stamps delivery, idempotently
    sent: list = []
    monkeypatch.setattr("billing.email_sender.send_magic_link",
                        lambda to, link, tier=None: sent.append(to) or True)
    summary = billing.resend_owed_onboarding()
    assert summary == {"owed": 1, "sent": 1, "failed": 0}
    assert sent == ["late@example.com"]
    assert au.get_user("late@example.com")["welcome_emailed_at"] is not None

    # second sweep is a no-op (no longer owed)
    assert billing.resend_owed_onboarding() == {"owed": 0, "sent": 0, "failed": 0}


def test_sweep_skips_inactive_and_untiered_users():
    """Only ACTIVE, tiered subscribers are owed onboarding — churned or password users aren't."""
    au.create_magic_link_user("active@example.com", tier="trader")  # owed (NULL welcome)
    au.create_magic_link_user("churned@example.com", tier="trader")
    au.set_active("churned@example.com", False)
    au.create_user("pwuser@example.com", "pw")                      # no tier → not a subscriber
    owed = {u["email"] for u in au.list_owed_onboarding()}
    assert owed == {"active@example.com"}


# ── multi-worker safety: one account per subscription id ──────────────────────

def test_billing_sub_id_is_unique_across_accounts():
    """The DB itself must reject two accounts sharing one subscription id (the race that
    produced duplicate active accounts under concurrent created/updated events)."""
    import sqlite3
    au.create_magic_link_user("a@example.com", tier="trader")
    au.set_billing("a@example.com", sub_id="sub_dup")
    au.create_magic_link_user("b@example.com", tier="trader")
    with pytest.raises(sqlite3.IntegrityError):
        au.set_billing("b@example.com", sub_id="sub_dup")     # second row, same sub → rejected
    # NULL billing_sub_id is exempt (partial index) — many pre-payment users can coexist
    au.create_magic_link_user("c@example.com")               # no tier, no sub_id
    au.create_magic_link_user("d@example.com")
    assert au.get_user("c@example.com") and au.get_user("d@example.com")


def test_concurrent_provision_does_not_leave_active_loser():
    """A duplicate-subscription race must roll back the losing account completely."""
    start = threading.Barrier(2)

    def provision(email: str):
        start.wait()
        return billing.provision(email, "trader", "cust_race", "sub_race")

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(provision, "race-a@example.com"),
            pool.submit(provision, "race-b@example.com"),
        ]

    results, errors = [], []
    for future in futures:
        try:
            results.append(future.result())
        except sqlite3.IntegrityError as exc:
            errors.append(exc)

    assert len(results) == 1
    assert len(errors) == 1
    users = [
        au.get_user("race-a@example.com"),
        au.get_user("race-b@example.com"),
    ]
    assert sum(user is not None for user in users) == 1
    winner = next(user for user in users if user is not None)
    assert winner["is_active"] == 1
    assert winner["tier"] == "trader"
    assert winner["billing_sub_id"] == "sub_race"


# ── at-rest hardening: gesh_auth.db is owner-only ─────────────────────────────

def test_auth_db_is_chmod_600():
    import stat
    au.init_auth_db()
    mode = stat.S_IMODE(au.AUTH_DB_PATH.stat().st_mode)
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"


# ── variant resolution on order/payment events (live-test fix 2026-06-30) ──────
# Real LS purchase showed order_created carries variant in first_order_item, and
# subscription_payment_success (an invoice) carries no variant at all — both were wrongly
# refused as "unknown variant". Orders must read first_order_item; renewals must keep the
# tier already on the account. H3 (no escalation on a genuinely-unknown product) still holds.

def _order_payload(email: str = "buyer@example.com", *, variant_id="555",
                   order_id: str = "ord_1", customer_id: int = 99,
                   updated_at: str = "2026-02-01T00:00:00.000000Z") -> dict:
    return {"meta": {"event_name": "order_created"},
            "data": {"id": order_id, "type": "orders",
                     "attributes": {"user_email": email, "status": "paid",
                                    "customer_id": customer_id,
                                    "first_order_item": {"variant_id": variant_id},
                                    "updated_at": updated_at}}}


def _payment_success_payload(email: str = "buyer@example.com", *, sub_id: str = "sub_1",
                             invoice_id: str = "inv_1",
                             updated_at: str = "2026-03-01T00:00:00.000000Z") -> dict:
    # subscription-invoice object: data.id is the INVOICE id, sub id is in attributes,
    # and there is NO variant_id anywhere.
    return {"meta": {"event_name": "subscription_payment_success"},
            "data": {"id": invoice_id, "type": "subscription-invoices",
                     "attributes": {"user_email": email, "status": "paid",
                                    "subscription_id": sub_id, "updated_at": updated_at}}}


def _post_raw(payload: dict) -> dict:
    raw = json.dumps(payload).encode()
    return billing.handle_webhook(raw, _sign(raw), payload)


def test_order_created_resolves_variant_from_first_order_item(monkeypatch):
    monkeypatch.setenv("LS_VARIANT_TRADER", "555")
    res = _post_raw(_order_payload(variant_id="555"))
    assert res["action"] == "provision" and res["tier"] == "trader"
    assert au.get_user("buyer@example.com")["is_active"] == 1


def test_payment_success_keeps_existing_tier_for_active_user():
    _post("subscription_created", variant_name="Trader plan", sub_id="sub_1")   # → tier trader
    res = _post_raw(_payment_success_payload(sub_id="sub_1"))                    # invoice, no variant
    assert res["action"] == "provision" and res["tier"] == "trader"             # kept, not refused
    u = au.get_user("buyer@example.com")
    assert u["is_active"] == 1 and u["tier"] == "trader" and u["billing_status"] == "active"


def test_unknown_variant_payment_success_does_not_keep_existing_tier(monkeypatch):
    """An explicit, unallowlisted variant must not use the variant-less renewal fallback."""
    monkeypatch.setenv("LS_VARIANT_TRADER", "555")
    _post("subscription_created", variant_id="555", sub_id="sub_1")
    payload = _payment_success_payload(sub_id="sub_1")
    payload["data"]["attributes"]["variant_id"] = "999"

    res = _post_raw(payload)

    assert res["action"] == "ignored" and res["reason"] == "unknown_variant"
    u = au.get_user("buyer@example.com")
    assert u["is_active"] == 1 and u["tier"] == "trader"


def test_unknown_variant_activation_does_not_keep_existing_tier(monkeypatch):
    """Stored-tier retention is exclusive to variant-less renewal invoices."""
    monkeypatch.setenv("LS_VARIANT_TRADER", "555")
    _post("subscription_created", variant_id="555", sub_id="sub_1")

    res = _post("subscription_updated", variant_id="999", sub_id="sub_1", status="active")

    assert res["action"] == "ignored" and res["reason"] == "unknown_variant"
    u = au.get_user("buyer@example.com")
    assert u["is_active"] == 1 and u["tier"] == "trader"


def test_foreign_renewal_does_not_replace_subscription():
    """Codex HIGH-1: two LEGIT LS subscriptions can share an email. A payment_success for a
    DIFFERENT sub_id (no variant) must NOT keep tier + overwrite billing_sub_id — otherwise the
    authoritative subscription is replaced and the real sub's later expiry is ignored as stale,
    leaving the user active forever. Tier retention requires an EXACT sub_id match."""
    _post("subscription_created", variant_name="Trader plan", sub_id="sub_legit")
    res = _post_raw(_payment_success_payload(sub_id="sub_other",
                                             updated_at="2026-05-01T00:00:00.000000Z"))
    assert res["action"] == "ignored" and res["reason"] == "unknown_variant"
    u = au.get_user("buyer@example.com")
    assert u["billing_sub_id"] == "sub_legit"        # authoritative sub UNCHANGED
    assert u["is_active"] == 1                        # legit sub still active


def test_payment_success_without_account_is_ignored_and_deduped():
    """A no-account invoice is a settled unknown product/orphan, not proof of a race.
    Acknowledge and complete its dedup claim so signed invoices cannot cause 503 churn."""
    payload = _payment_success_payload(email="stranger@example.com", sub_id="sub_x")
    res = _post_raw(payload)
    assert res["action"] == "ignored" and res["reason"] == "unknown_variant"
    assert _post_raw(payload)["action"] == "duplicate"
    assert au.get_user("stranger@example.com") is None   # still nothing granted


def test_payment_success_does_not_resurrect_a_revoked_account():
    """H6: revoke() clears is_active but NOT tier. A payment_success whose event_at is NEWER
    than the processed subscription_expired passes the _is_stale ordering guard — the
    existing-tier fallback must STILL refuse it (the account stays revoked), because the
    fallback is gated on is_active, not merely on a leftover tier. Without that gate this
    re-provisions a churned account."""
    # 1) provision an active trader (bootstrap name mapping → tier=trader)
    _post_raw(_payload("subscription_created", sub_id="sub_1",
                       updated_at="2026-03-01T00:00:00.000000Z"))
    # 2) expire it with a LATER timestamp → revoke: is_active=0, tier retained by revoke()
    _post_raw(_payload("subscription_expired", sub_id="sub_1", status="expired",
                       updated_at="2026-03-02T00:00:00.000000Z"))
    revoked = au.get_user("buyer@example.com")
    assert revoked["is_active"] == 0 and revoked["tier"] == "trader"   # tier NOT cleared
    # 3) payment_success with an even NEWER event_at (passes ordering) must NOT re-activate
    res = _post_raw(_payment_success_payload(sub_id="sub_1",
                    updated_at="2026-03-03T00:00:00.000000Z"))
    assert res["action"] == "ignored" and res["reason"] == "unknown_variant"
    assert au.get_user("buyer@example.com")["is_active"] == 0          # still revoked


def test_expired_without_timestamp_blocks_reordered_reactivation():
    """8th-pass (Fable adversarial audit). A timestamp-less subscription_expired must
    still ADVANCE the ordering watermark. Otherwise a REORDERED subscription_updated
    (status=active, carrying a variant so tier != None) — delivered after the expiry
    but stamped earlier — passes _is_stale and RESURRECTS a revoked account. The H6
    guard only covers the tier-None case; a variant-carrying reactivation slips past
    it, so revoke() must fence future reactivation by received-at time when the expiry
    event omits updated_at. HMAC-gated (no forgery), but LS can both omit updated_at
    and reorder delivery."""
    # 1) provision (old timestamp)
    _post("subscription_created", variant_name="Trader plan", sub_id="sub_1",
          updated_at="2020-01-01T00:00:00.000000Z")
    assert au.get_user("buyer@example.com")["is_active"] == 1
    # 2) expire WITHOUT updated_at → revoke; watermark must not remain at 2020-01-01
    _post("subscription_expired", status="expired", sub_id="sub_1", updated_at="")
    assert au.get_user("buyer@example.com")["is_active"] == 0
    # 3) reordered active update, stamped AFTER the created but BEFORE now → must be stale
    res = _post("subscription_updated", status="active", variant_name="Trader plan",
                sub_id="sub_1", updated_at="2020-06-01T00:00:00.000000Z")
    assert au.get_user("buyer@example.com")["is_active"] == 0, \
        "revoked account resurrected by a reordered stale reactivation"
    assert res["action"] != "provision"
