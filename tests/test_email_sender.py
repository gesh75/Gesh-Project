"""Fail-closed contract for transactional email (billing + retention jobs).

send_email must never raise (a Resend blip cannot 500 a Lemon Squeezy webhook)
and must take the DEV NO-OP path when RESEND_API_KEY is unset or whitespace.
is_configured() is how cron jobs distinguish "no key" from "nothing to send".

The magic-link HTML-escape pin lives in test_bugfix_batch_2026_08.py.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import email_sender


@pytest.fixture(autouse=True)
def _clear_resend_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("EMAIL_FROM", raising=False)


def test_is_configured_false_when_key_missing():
    assert email_sender.is_configured() is False


def test_is_configured_false_when_key_is_whitespace(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("RESEND_API_KEY", "   ")
    assert email_sender.is_configured() is False


def test_is_configured_true_when_key_present(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test_not_a_real_key")
    assert email_sender.is_configured() is True


def test_send_email_no_key_is_noop_and_does_not_call_requests(
    monkeypatch: pytest.MonkeyPatch,
):
    called = {"n": 0}

    def _boom(*_a, **_k):
        called["n"] += 1
        raise AssertionError("requests must not be imported/called without a key")

    monkeypatch.setitem(__import__("sys").modules, "requests", SimpleNamespace(post=_boom))
    assert email_sender.send_email("user@example.com", "hi", "<p>x</p>") is False
    assert called["n"] == 0


def test_send_email_2xx_returns_true(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test_not_a_real_key")
    captured: dict = {}

    def _post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return SimpleNamespace(status_code=200, text="ok")

    monkeypatch.setitem(__import__("sys").modules, "requests", SimpleNamespace(post=_post))
    assert email_sender.send_email(
        "user@example.com",
        "Your GESH access",
        "<p>welcome</p>",
        headers={"List-Unsubscribe": "<mailto:x>"},
    ) is True
    assert captured["url"] == email_sender._RESEND_ENDPOINT
    assert captured["timeout"] == 15
    assert captured["headers"]["Authorization"] == "Bearer re_test_not_a_real_key"
    assert captured["json"]["to"] == ["user@example.com"]
    assert captured["json"]["headers"]["List-Unsubscribe"] == "<mailto:x>"


def test_send_email_4xx_returns_false(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test_not_a_real_key")
    monkeypatch.setitem(
        __import__("sys").modules,
        "requests",
        SimpleNamespace(post=lambda *a, **k: SimpleNamespace(status_code=422, text="bad")),
    )
    assert email_sender.send_email("user@example.com", "hi", "<p>x</p>") is False


def test_send_email_never_raises_on_transport_error(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test_not_a_real_key")

    def _boom(*_a, **_k):
        raise RuntimeError("resend down")

    monkeypatch.setitem(__import__("sys").modules, "requests", SimpleNamespace(post=_boom))
    assert email_sender.send_email("user@example.com", "hi", "<p>x</p>") is False


def test_send_email_whitespace_key_is_treated_as_unset(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("RESEND_API_KEY", "\t  ")
    assert email_sender.send_email("user@example.com", "hi", "<p>x</p>") is False
