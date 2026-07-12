"""GPT-5.6 audit item 8 — login hardening.

Two defects on the pre-fix branch:

  * ``verify_credentials()`` short-circuits for unknown / inactive users WITHOUT
    running the 200k-round PBKDF2. Response time then leaks whether an account
    exists (username enumeration). Fix: always run one PBKDF2 against a fixed
    dummy hash so the timing profile is identical to a real wrong-password miss.

  * ``/auth/login`` had no brute-force throttle and ran the blocking PBKDF2 on the
    event loop. Fix: a per-IP + per-(IP, email) FAILURE throttle (successful login
    clears it) and ``asyncio.to_thread`` for the hash so 200k rounds never block
    the loop.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

import auth_users as au
import server

client = TestClient(server.app)


def test_unknown_user_runs_dummy_hash(monkeypatch):
    """Constant-time: an unknown email must still trigger a PBKDF2 verify so the
    timing side-channel can't distinguish 'no such user' from 'wrong password'."""
    calls: list[str] = []
    real = au.verify_password

    def spy(pw: str, stored: str) -> bool:
        calls.append(stored)
        return real(pw, stored)

    monkeypatch.setattr(au, "verify_password", spy)
    assert au.verify_credentials("ghost@nowhere.example", "whatever") is False
    assert calls, "verify_password (PBKDF2) must run even for an unknown user"


def test_inactive_user_runs_dummy_hash(monkeypatch):
    au.create_user("dormant@x.com", "pw")
    au.set_active("dormant@x.com", False)
    calls: list[str] = []
    real = au.verify_password

    def spy(pw: str, stored: str) -> bool:
        calls.append(stored)
        return real(pw, stored)

    monkeypatch.setattr(au, "verify_password", spy)
    assert au.verify_credentials("dormant@x.com", "pw") is False
    assert calls, "an inactive account must still run a hash (no timing leak)"


def test_known_user_login_still_works():
    au.create_user("real@x.com", "correct-horse")
    assert au.verify_credentials("real@x.com", "correct-horse") is True
    assert au.verify_credentials("real@x.com", "wrong") is False


def test_login_throttled_after_repeated_failures():
    au.create_user("target@x.com", "sekret")
    got_429 = False
    for _ in range(server.LOGIN_FAIL_LIMIT + 3):
        r = client.post("/auth/login", json={"email": "target@x.com", "password": "nope"})
        if r.status_code == 429:
            got_429 = True
            break
        assert r.status_code == 401
    assert got_429, "expected a 429 once the login failure limit is exceeded"


def test_successful_login_clears_the_throttle():
    au.create_user("clears@x.com", "goodpw")
    # A few failures (below the limit) then a success must reset the counter so the
    # legitimate owner isn't locked out by earlier fat-finger attempts.
    for _ in range(server.LOGIN_FAIL_LIMIT - 1):
        assert client.post("/auth/login", json={"email": "clears@x.com", "password": "no"}).status_code == 401
    ok = client.post("/auth/login", json={"email": "clears@x.com", "password": "goodpw"})
    assert ok.status_code == 200 and ok.json().get("ok") is True
    # Bucket cleared → another wrong attempt is a plain 401, not an immediate 429.
    assert client.post("/auth/login", json={"email": "clears@x.com", "password": "no"}).status_code == 401
