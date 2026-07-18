"""[CLAUDE Task3 2026-07-18] Codex remediation Task 3 — public-endpoint hardening.

Covers: (a) webhook body cap rejects oversized payloads with 413 BEFORE the
billing handler runs; (b) a magic link cannot silently replace a DIFFERENT
user's active session (409, token NOT consumed); (c) throttled logins carry a
Retry-After header.
"""
from __future__ import annotations

from unittest.mock import Mock

import pytest
from starlette.testclient import TestClient

import auth_users
import billing
import server


@pytest.fixture
def client():
    return TestClient(server.app, raise_server_exceptions=False)


def test_oversized_webhook_is_rejected_before_handler(client, monkeypatch):
    handler = Mock()
    monkeypatch.setattr(billing, "handle_webhook", handler)
    r = client.post("/api/billing/webhook",
                    content=b"x" * (server.WEBHOOK_MAX_BYTES + 1),
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 413
    handler.assert_not_called()


def test_oversized_webhook_rejected_by_declared_length(client, monkeypatch):
    handler = Mock()
    monkeypatch.setattr(billing, "handle_webhook", handler)
    r = client.post("/api/billing/webhook", content=b"{}",
                    headers={"Content-Length": str(server.WEBHOOK_MAX_BYTES + 10)})
    assert r.status_code == 413
    handler.assert_not_called()


def test_normal_webhook_still_reaches_handler(client, monkeypatch):
    handler = Mock(return_value={"ok": True})
    monkeypatch.setattr(billing, "handle_webhook", handler)
    r = client.post("/api/billing/webhook", json={"meta": {}},
                    headers={"X-Signature": "0" * 64})
    assert r.status_code == 200
    handler.assert_called_once()


def test_magic_link_does_not_replace_another_users_session(client, monkeypatch):
    monkeypatch.setenv("GESH_AUTH_ENABLED", "1")
    auth_users.init_auth_db()
    auth_users.create_user("alice@x.com", "pw-alice")
    auth_users.create_user("bob@x.com", "pw-bob")
    alice_session = auth_users.issue_token("alice@x.com", typ="session")
    bob_magic = auth_users.issue_token("bob@x.com", ttl=900, typ="magic")

    r = client.get(f"/auth/magic?token={bob_magic}",
                   cookies={auth_users.COOKIE_NAME: alice_session},
                   follow_redirects=False)
    assert r.status_code == 409
    # the magic token was NOT consumed — bob can still use his link
    assert auth_users.verify_token(bob_magic, expected_typ="magic") is not None
    # and no replacement cookie was issued on the conflict response
    assert auth_users.COOKIE_NAME not in r.cookies


def test_magic_link_still_works_without_conflicting_session(client, monkeypatch):
    monkeypatch.setenv("GESH_AUTH_ENABLED", "1")
    auth_users.init_auth_db()
    auth_users.create_user("carol@x.com", "pw-carol")
    magic = auth_users.issue_token("carol@x.com", ttl=900, typ="magic")
    r = client.get(f"/auth/magic?token={magic}", follow_redirects=False)
    assert r.status_code == 303
    assert auth_users.COOKIE_NAME in r.cookies


def test_login_throttle_carries_retry_after(client, monkeypatch):
    auth_users.init_auth_db()
    for _ in range(server.LOGIN_FAIL_LIMIT):
        client.post("/auth/login", json={"email": "who@x.com", "password": "bad"})
    r = client.post("/auth/login", json={"email": "who@x.com", "password": "bad"})
    assert r.status_code == 429
    assert r.headers.get("Retry-After") == str(server.LOGIN_FAIL_WINDOW)
