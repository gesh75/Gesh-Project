"""Nightly LS↔DB reconciliation (post-launch safety net for missed webhooks).

Offline: the LS API is mocked; auth DB is sandboxed via GESH_AUTH_DB-equivalent
monkeypatching of auth_users' connection. Email sends are stubbed.
"""
from __future__ import annotations

import pytest

import auth_users
import billing
import billing_reconcile as br


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_users, "AUTH_DB_PATH", tmp_path / "auth.db")
    auth_users.init_auth_db()
    monkeypatch.setenv("LEMON_SQUEEZY_API_KEY", "test-key")
    monkeypatch.setenv("LS_VARIANT_OBSERVER", "1865999")
    monkeypatch.setattr(billing.email_sender, "send_magic_link", lambda *a, **k: True)
    return tmp_path


def _sub(sub_id, email, status, variant=1865999):
    return {"id": str(sub_id), "attributes": {
        "user_email": email, "status": status, "variant_id": variant,
        "variant_name": "Default", "product_name": "GESH Observer",
        "customer_id": 9999, "updated_at": "2026-07-03T12:00:00Z"}}


def test_missed_webhook_user_gets_provisioned(sandbox, monkeypatch):
    monkeypatch.setattr(br, "fetch_all_subscriptions",
                        lambda store: [_sub(1, "missed@example.com", "active")])
    out = br.reconcile("421028")
    assert out["provisioned"] == ["missed@example.com"]
    u = auth_users.get_user("missed@example.com")
    assert u and u["is_active"] and u["tier"] == "observer" and u["billing_sub_id"] == "1"


def test_expired_sub_revokes_only_matching_account(sandbox, monkeypatch):
    billing.provision("gone@example.com", "observer", "c1", "77")
    auth_users.create_magic_link_user("comped@example.com", tier="observer")  # NO sub id
    monkeypatch.setattr(br, "fetch_all_subscriptions",
                        lambda store: [_sub(77, "gone@example.com", "expired")])
    out = br.reconcile("421028")
    assert out["revoked"] == ["gone@example.com"]
    assert not auth_users.get_user("gone@example.com")["is_active"]
    assert auth_users.get_user("comped@example.com")["is_active"]  # untouched


def test_changed_billing_email_does_not_create_duplicate(sandbox, monkeypatch):
    billing.provision("original@example.com", "observer", "c1", "77")
    monkeypatch.setattr(br, "fetch_all_subscriptions",
                        lambda store: [_sub(77, "changed@example.com", "active")])

    out = br.reconcile("421028")

    assert out["healed"] == 0
    assert auth_users.get_user("changed@example.com") is None
    assert auth_users.get_user_by_sub_id("77")["email"] == "original@example.com"


def test_changed_billing_email_revokes_subscription_bound_account(sandbox, monkeypatch):
    billing.provision("original@example.com", "observer", "c1", "77")
    monkeypatch.setattr(br, "fetch_all_subscriptions",
                        lambda store: [_sub(77, "changed@example.com", "expired")])

    out = br.reconcile("421028")

    assert out["revoked"] == ["original@example.com"]
    assert not auth_users.get_user("original@example.com")["is_active"]
    assert auth_users.get_user("changed@example.com") is None


def test_in_sync_and_cancelled_users_untouched(sandbox, monkeypatch):
    billing.provision("ok@example.com", "observer", "c1", "5")
    monkeypatch.setattr(br, "fetch_all_subscriptions",
                        lambda store: [_sub(5, "ok@example.com", "cancelled")])
    out = br.reconcile("421028")
    assert out["healed"] == 0                       # cancelled keeps access — no churn
    assert auth_users.get_user("ok@example.com")["is_active"]


def test_unknown_variant_never_provisions(sandbox, monkeypatch):
    monkeypatch.setattr(br, "fetch_all_subscriptions",
                        lambda store: [_sub(9, "evil@example.com", "active", variant=666)])
    out = br.reconcile("421028")
    assert out["provisioned"] == []
    assert auth_users.get_user("evil@example.com") is None
    assert out["skipped"][0]["reason"] == "unknown variant"
