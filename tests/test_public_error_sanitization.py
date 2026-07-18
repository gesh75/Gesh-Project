"""Public endpoints never expose paths, credentials, emails, or upstream bodies."""

from __future__ import annotations

from starlette.testclient import TestClient

import billing
import server


def test_broker_offline_error_has_fixed_public_message() -> None:
    error = server.RobinhoodOfflineError(
        "snapshot /Users/private/book.json rejected for account 123"
    )
    response = server._server_error(error)
    assert response.status_code == 503
    assert response.detail == "Broker data temporarily unavailable"


def test_missing_anthropic_configuration_has_fixed_public_message() -> None:
    response = server._upstream_unavailable_error(
        EnvironmentError("ANTHROPIC_API_KEY=sk-secret missing in /Users/private/.env")
    )
    assert response is not None
    assert response.status_code == 503
    assert response.detail == "AI service temporarily unavailable"


def test_webhook_retryable_error_does_not_expose_customer_or_path(monkeypatch) -> None:
    def fail(*_args, **_kwargs):
        raise billing.RetryableError(
            "onboarding email not sent to customer@example.com via /Users/private/.env"
        )

    monkeypatch.setattr(billing, "handle_webhook", fail)
    client = TestClient(server.app, raise_server_exceptions=False)
    response = client.post(
        "/api/billing/webhook",
        json={"meta": {}},
        headers={"X-Signature": "0" * 64},
    )
    assert response.status_code == 503
    assert response.json() == {
        "ok": False,
        "detail": "Webhook processing temporarily unavailable",
        "retry": True,
    }


def test_webhook_value_error_does_not_expose_payload_details(monkeypatch) -> None:
    def fail(*_args, **_kwargs):
        raise ValueError("user_email customer@example.com was malformed")

    monkeypatch.setattr(billing, "handle_webhook", fail)
    client = TestClient(server.app, raise_server_exceptions=False)
    response = client.post(
        "/api/billing/webhook",
        json={"meta": {}},
        headers={"X-Signature": "0" * 64},
    )
    assert response.status_code == 400
    assert response.json() == {"ok": False, "detail": "Invalid webhook payload"}
