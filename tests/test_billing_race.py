"""Delivery-burst race handling (2026-07-03 live-launch finding).

LS fires the initial event burst concurrently: order_created can arrive before
the order item is materialized (no first_order_item), and the variant-less
subscription_payment_success invoice can race ahead of the subscription_created
that writes billing_sub_id. Both used to be REFUSED (200 "unknown_variant") —
now they 503 (RetryableError) so LS redelivers against settled state. A variant
that IS present but un-allowlisted still refuses permanently (H3).
"""
from __future__ import annotations

import pytest

import auth_users
import billing


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_users, "AUTH_DB_PATH", tmp_path / "auth.db")
    auth_users.init_auth_db()
    monkeypatch.setenv("LS_VARIANT_OBSERVER", "1865999")
    monkeypatch.delenv("BILLING_CAPTURE_DIR", raising=False)
    monkeypatch.setattr(billing.email_sender, "send_magic_link", lambda *a, **k: True)


def _order_created(first_order_item):
    attrs = {"user_email": "buyer@example.com", "status": "paid",
             "updated_at": "2026-07-03T14:16:30Z", "customer_id": 1}
    if first_order_item is not None:
        attrs["first_order_item"] = first_order_item
    return {"meta": {"event_name": "order_created"},
            "data": {"type": "orders", "id": "1", "attributes": attrs}}


def test_order_created_without_order_item_is_retryable(sandbox):
    ev = billing.parse_event(_order_created(first_order_item=None))
    with pytest.raises(billing.RetryableError):
        billing._dispatch(ev)                       # → 503, LS retries


def test_payment_success_racing_ahead_of_subscription_is_retryable(sandbox):
    ev = billing.parse_event({
        "meta": {"event_name": "subscription_payment_success"},
        "data": {"type": "subscription-invoices", "id": "9", "attributes": {
            "user_email": "buyer@example.com", "status": "paid",
            "subscription_id": 555, "updated_at": "2026-07-03T14:17:05Z"}}})
    with pytest.raises(billing.RetryableError):
        billing._dispatch(ev)                       # no account bound to sub yet


def test_present_but_unknown_variant_still_refused_permanently(sandbox):
    ev = billing.parse_event(_order_created(
        {"variant_id": 666, "variant_name": "Evil", "product_name": "Escalate"}))
    out = billing._dispatch(ev)
    assert out == {"action": "ignored", "email": "buyer@example.com",
                   "reason": "unknown_variant"}     # H3 allowlist holds


def test_settled_order_payload_provisions(sandbox):
    """The REAL resent order_created shape (first_order_item materialized)."""
    ev = billing.parse_event(_order_created(
        {"variant_id": 1865999, "product_id": 1193511,
         "variant_name": "Default", "product_name": "GESH Observer"}))
    out = billing._dispatch(ev)
    assert out["action"] == "provision" and out["tier"] == "observer"
    assert auth_users.get_user("buyer@example.com")["is_active"]


def test_retry_after_subscription_lands_keeps_tier(sandbox):
    """The renewal race converges: once subscription_created has bound the sub
    id, the retried payment_success keeps the account tier."""
    billing.provision("buyer@example.com", "observer", "c1", "555")
    ev = billing.parse_event({
        "meta": {"event_name": "subscription_payment_success"},
        "data": {"type": "subscription-invoices", "id": "9", "attributes": {
            "user_email": "buyer@example.com", "status": "paid",
            "subscription_id": 555, "updated_at": "2026-07-03T14:18:00Z"}}})
    out = billing._dispatch(ev)
    assert out["action"] == "provision" and out["tier"] == "observer"
