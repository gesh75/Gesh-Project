"""Unit tests for server._cookie_secure — the session-cookie Secure-flag resolver.

Covers the P1 hardening (2026-07-16): the flag is derived per-request from the
proxied protocol so real HTTPS users get a Secure cookie WITHOUT locking the owner
out of http://localhost, while an explicit GESH_COOKIE_SECURE env value still wins
as a deliberate override. Regression guard for the "session cookie shipped in clear
to paying users because .env pinned GESH_COOKIE_SECURE=0" finding.
"""
import pytest

from server import _cookie_secure


class _FakeURL:
    def __init__(self, scheme: str, hostname: str) -> None:
        self.scheme = scheme
        self.hostname = hostname


class _FakeRequest:
    """Minimal stand-in — _cookie_secure only reads .headers.get() and .url.scheme.

    Header keys are stored lowercased to mirror Starlette's case-insensitive Headers,
    which is what the production Request exposes.
    """
    def __init__(self, headers: dict | None = None, scheme: str = "http",
                 hostname: str = "localhost") -> None:
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.url = _FakeURL(scheme, hostname)


@pytest.fixture(autouse=True)
def _no_ambient_override(monkeypatch):
    """Strip any GESH_COOKIE_SECURE leaking in from the dev .env / shell so the
    derivation branch is what each unmarked test actually exercises."""
    monkeypatch.delenv("GESH_COOKIE_SECURE", raising=False)
    yield


# --- derivation branch (no override) ---------------------------------------

def test_forwarded_proto_https_is_secure():
    req = _FakeRequest(headers={"X-Forwarded-Proto": "https"}, scheme="http")
    assert _cookie_secure(req) is True


def test_plain_http_localhost_is_not_secure():
    # The owner hitting http://localhost:9000 keeps a working (non-Secure) cookie.
    req = _FakeRequest(headers={}, scheme="http")
    assert _cookie_secure(req) is False


@pytest.mark.parametrize("hostname", ["example.com", "gesh.financial", ""])
def test_plain_http_non_loopback_is_secure(hostname):
    req = _FakeRequest(scheme="http", hostname=hostname)
    assert _cookie_secure(req) is True


@pytest.mark.parametrize("hostname", ["127.0.0.1", "::1", "LOCALHOST."])
def test_plain_http_loopback_is_not_secure(hostname):
    req = _FakeRequest(scheme="http", hostname=hostname)
    assert _cookie_secure(req) is False


def test_direct_https_scheme_is_secure():
    # No proxy header, but the connection itself is TLS.
    req = _FakeRequest(headers={}, scheme="https")
    assert _cookie_secure(req) is True


def test_forwarded_proto_list_takes_first_hop():
    # X-Forwarded-Proto can be a comma list ("client, proxy"); the client hop wins.
    req = _FakeRequest(headers={"X-Forwarded-Proto": "https, http"}, scheme="http")
    assert _cookie_secure(req) is True


def test_forwarded_proto_http_cannot_downgrade_https():
    req = _FakeRequest(headers={"X-Forwarded-Proto": "http"}, scheme="https")
    assert _cookie_secure(req) is True


def test_forwarded_proto_http_cannot_downgrade_public_http_host():
    req = _FakeRequest(headers={"X-Forwarded-Proto": "http"}, scheme="http",
                       hostname="gesh.financial")
    assert _cookie_secure(req) is True


# --- no request context -----------------------------------------------------

def test_no_request_defaults_secure():
    # Fail safe: a call with no request and no override must not downgrade.
    assert _cookie_secure() is True
    assert _cookie_secure(None) is True


# --- explicit env override wins over derivation -----------------------------

def test_env_override_zero_forces_insecure_even_on_https(monkeypatch):
    monkeypatch.setenv("GESH_COOKIE_SECURE", "0")
    req = _FakeRequest(headers={"X-Forwarded-Proto": "https"}, scheme="https")
    assert _cookie_secure(req) is False


@pytest.mark.parametrize("val", ["false", "No", "OFF"])
def test_env_override_falsey_words_force_insecure(monkeypatch, val):
    monkeypatch.setenv("GESH_COOKIE_SECURE", val)
    assert _cookie_secure(_FakeRequest(scheme="https")) is False


def test_env_override_one_forces_secure_on_plain_http(monkeypatch):
    monkeypatch.setenv("GESH_COOKIE_SECURE", "1")
    req = _FakeRequest(headers={}, scheme="http")  # would derive False
    assert _cookie_secure(req) is True


def test_empty_env_override_falls_through_to_derivation(monkeypatch):
    # An empty/whitespace value is not a deliberate override — derive instead.
    monkeypatch.setenv("GESH_COOKIE_SECURE", "  ")
    assert _cookie_secure(_FakeRequest(scheme="http")) is False
    assert _cookie_secure(_FakeRequest(headers={"X-Forwarded-Proto": "https"})) is True
