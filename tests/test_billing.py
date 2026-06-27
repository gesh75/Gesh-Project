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
             sub_id: str = "sub_1", customer_id: int = 99) -> dict:
    return {
        "meta": {"event_name": event},
        "data": {"id": sub_id, "type": "subscriptions",
                 "attributes": {"user_email": email, "status": status,
                                "customer_id": customer_id,
                                "variant_name": variant_name, "product_name": "GESH"}},
    }


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
    token = au.issue_token("buyer@example.com", ttl=900)
    assert au.consume_magic_token(token) == "buyer@example.com"   # first use
    assert au.consume_magic_token(token) is None                  # replay rejected


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
