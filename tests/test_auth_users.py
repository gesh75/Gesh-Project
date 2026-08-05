"""
tests/test_auth_users.py — minimal multi-user login wall (gesh-project :9000).

Right-sized for a self-hosted alpha (~10 users viewing the owner's research):
per-user credentials, stdlib PBKDF2 password hashing, HMAC-signed session token,
and an env-gated gate that is OFF by default (so wiring it into the live server
is a no-op until GESH_AUTH_ENABLED is set and users exist).

No network. Temp SQLite + fixed secret.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from pathlib import Path
from typing import Generator

import pytest


@pytest.fixture(autouse=True)
def isolated_auth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator:
    monkeypatch.setenv("GESH_AUTH_DB", str(tmp_path / "auth.db"))
    monkeypatch.setenv("GESH_AUTH_SECRET", "test-secret-deterministic")
    monkeypatch.delenv("GESH_AUTH_ENABLED", raising=False)
    import auth_users as au
    monkeypatch.setattr(au, "AUTH_DB_PATH", tmp_path / "auth.db")
    au.init_auth_db()
    yield


import auth_users as au


def _signed_v2_token(subject: str, *, typ: str = "session") -> str:
    payload = au._b64u(
        json.dumps(
            {
                "sub": subject,
                "exp": int(time.time()) + 900,
                "typ": typ,
                "ver": 2,
                "jti": "test-jti",
            },
            separators=(",", ":"),
        ).encode()
    )
    signature = au._b64u(hmac.new(au._secret(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{signature}"

# ── password hashing ──────────────────────────────────────────────────────────

class TestPasswordHashing:
    def test_round_trip(self):
        h = au.hash_password("hunter2")
        assert au.verify_password("hunter2", h) is True

    def test_wrong_password_fails(self):
        h = au.hash_password("hunter2")
        assert au.verify_password("nope", h) is False

    def test_salted_hashes_differ(self):
        assert au.hash_password("x") != au.hash_password("x")

    def test_hash_is_not_plaintext(self):
        h = au.hash_password("supersecret")
        assert "supersecret" not in h

    def test_malformed_hash_returns_false(self):
        assert au.verify_password("x", "garbage") is False


# ── session token ─────────────────────────────────────────────────────────────

class TestSessionToken:
    def test_issue_verify_round_trip(self):
        au.create_user("a@b.com", "pw")
        tok = au.issue_token("a@b.com")
        user_id = au.resolve_user_id("a@b.com")
        claims = json.loads(au._b64u_dec(tok.split(".", maxsplit=1)[0]))

        assert user_id is not None
        assert claims["ver"] == au._TOKEN_SCHEMA_VERSION == 2
        assert claims["sub"] == user_id
        assert au.verify_token(tok) == user_id

    def test_tampered_token_rejected(self):
        au.create_user("a@b.com", "pw")
        tok = au.issue_token("a@b.com")
        bad = tok[:-2] + ("aa" if not tok.endswith("aa") else "bb")
        assert au.verify_token(bad) is None

    def test_expired_token_rejected(self):
        au.create_user("a@b.com", "pw")
        tok = au.issue_token("a@b.com", ttl=-1)   # already expired
        assert au.verify_token(tok) is None

    def test_unknown_email_cannot_mint_an_identity_token(self):
        with pytest.raises(ValueError, match="active user"):
            au.issue_token("missing@example.com")

    def test_garbage_token_rejected(self):
        assert au.verify_token("not.a.token") is None
        assert au.verify_token("") is None


# ── user store ────────────────────────────────────────────────────────────────

class TestUserStore:
    def test_create_and_get(self):
        au.create_user("u@x.com", "pw")
        u = au.get_user("u@x.com")
        assert u is not None
        assert u["email"] == "u@x.com"
        assert u["is_active"] == 1

    def test_duplicate_email_rejected(self):
        au.create_user("dup@x.com", "pw")
        with pytest.raises(ValueError):
            au.create_user("dup@x.com", "pw2")

    def test_verify_credentials_true(self):
        au.create_user("v@x.com", "rightpw")
        assert au.verify_credentials("v@x.com", "rightpw") is True

    def test_verify_credentials_wrong_password(self):
        au.create_user("v@x.com", "rightpw")
        assert au.verify_credentials("v@x.com", "wrongpw") is False

    def test_verify_credentials_unknown_user(self):
        assert au.verify_credentials("ghost@x.com", "pw") is False

    def test_inactive_user_cannot_authenticate(self):
        au.create_user("off@x.com", "pw")
        au.set_active("off@x.com", False)
        assert au.verify_credentials("off@x.com", "pw") is False   # revoked on non-payment

    def test_reactivate(self):
        au.create_user("re@x.com", "pw")
        au.set_active("re@x.com", False)
        au.set_active("re@x.com", True)
        assert au.verify_credentials("re@x.com", "pw") is True

    def test_email_normalised_lowercase(self):
        au.create_user("Mixed@Case.com", "pw")
        assert au.verify_credentials("mixed@case.com", "pw") is True

    def test_list_users(self):
        au.create_user("a@x.com", "pw")
        au.create_user("b@x.com", "pw")
        emails = {u["email"] for u in au.list_users()}
        assert {"a@x.com", "b@x.com"} <= emails


# ── tier CLI (comp a tester to higher LLM quotas) ─────────────────────────────

class TestTierCli:
    def test_tier_sets_billing_tier(self):
        au.create_user("t@x.com", "pw")
        rc = au._cli(["tier", "t@x.com", "desk"])
        assert rc == 0
        assert au.get_tier("t@x.com") == "desk"

    def test_invalid_tier_rejected(self):
        au.create_user("t@x.com", "pw")
        rc = au._cli(["tier", "t@x.com", "platinum"])
        assert rc == 1
        assert au.get_tier("t@x.com") is None      # unchanged

    def test_unknown_user_rejected(self):
        rc = au._cli(["tier", "ghost@x.com", "desk"])
        assert rc == 1

    def test_list_query_exposes_tier(self):
        au.create_user("t@x.com", "pw")
        au._cli(["tier", "t@x.com", "trader"])
        row = next(u for u in au.list_users() if u["email"] == "t@x.com")
        assert row["tier"] == "trader"             # list_users SELECT includes tier


# ── gate decision (OFF by default) ────────────────────────────────────────────

class TestGate:
    def test_auth_disabled_by_default(self):
        assert au.auth_enabled() is False

    def test_auth_enabled_when_flag_set(self, monkeypatch):
        monkeypatch.setenv("GESH_AUTH_ENABLED", "1")
        assert au.auth_enabled() is True

    def test_public_paths_allowed(self):
        for p in ("/login", "/auth/login", "/terms", "/disclaimer", "/healthz"):
            assert au.is_public_path(p) is True

    def test_protected_paths_not_public(self):
        assert au.is_public_path("/") is False
        assert au.is_public_path("/api/portfolio") is False
        assert au.is_public_path("/track-record") is False

    def test_public_prefix_respects_path_boundary(self):
        # '/api/congress' must expose itself and sub-paths…
        assert au.is_public_path("/api/congress") is True
        assert au.is_public_path("/api/congress/NVDA") is True
        assert au.is_public_path("/congress-watch") is True
        # …but a future adjacent route must NOT silently inherit the bypass.
        assert au.is_public_path("/api/congress-admin") is False
        assert au.is_public_path("/congress-watchlist") is False
        assert au.is_public_path("/track-record-evil") is False

    def test_gate_allows_public(self):
        assert au.gate_decision("/login", None) == "allow"

    def test_gate_allows_valid_cookie(self):
        au.create_user("a@b.com", "pw")
        tok = au.issue_token("a@b.com")
        assert au.gate_decision("/api/portfolio", tok) == "allow"

    def test_gate_unauthorized_api_without_cookie(self):
        assert au.gate_decision("/api/portfolio", None) == "unauthorized"

    def test_gate_redirect_html_without_cookie(self):
        assert au.gate_decision("/", None) == "redirect"

    def test_gate_rejects_expired_cookie(self):
        au.create_user("a@b.com", "pw")
        tok = au.issue_token("a@b.com", ttl=-1)
        assert au.gate_decision("/api/portfolio", tok) == "unauthorized"

    # ── session revocation (immediate, not after TTL) ──────────────────────────
    def test_gate_rejects_disabled_user_immediately(self):
        au.create_user("churned@x.com", "pw")
        tok = au.issue_token("churned@x.com")
        assert au.gate_decision("/api/portfolio", tok) == "allow"        # active
        au.set_active("churned@x.com", False)                           # revoke
        assert au.gate_decision("/api/portfolio", tok) == "unauthorized"  # same valid token now rejected

    def test_gate_rejects_unknown_subject(self):
        tok = _signed_v2_token(str(uuid.uuid4()))   # crypto-valid token, no such user
        assert au.gate_decision("/", tok) == "redirect"

    def test_authenticate_session_active_user(self):
        au.create_user("s@x.com", "pw")
        assert au.authenticate_session(au.issue_token("s@x.com")) == "s@x.com"

    def test_authenticate_session_inactive_user_none(self):
        au.create_user("s@x.com", "pw")
        token = au.issue_token("s@x.com")
        au.set_active("s@x.com", False)
        assert au.authenticate_session(token) is None

    def test_verify_token_stays_pure_crypto_check(self):
        # verify_token validates signature+expiry only (no user lookup) — the
        # active-user check lives in authenticate_session / gate_decision.
        au.create_user("anyone@x.com", "pw")
        user_id = au.resolve_user_id("anyone@x.com")
        token = au.issue_token("anyone@x.com")
        with au._conn() as con:
            con.execute("DELETE FROM users WHERE user_id=?", (user_id,))

        assert au.verify_token(token) == user_id
        assert au.authenticate_session(token) is None
