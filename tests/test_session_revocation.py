"""Logout must blacklist the signed session, not only delete the cookie.

The 7-day HMAC token stays valid until ``exp`` unless its ``jti`` is stored.
``verify_token`` stays pure crypto. These tests never import the server.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path
from typing import Generator

import pytest

REPO = Path(__file__).resolve().parents[1]


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


def _sign(claims: dict) -> str:
    payload = au._b64u(json.dumps(claims, separators=(",", ":")).encode())
    signature = au._b64u(hmac.new(au._secret(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{signature}"


def test_revoke_session_blocks_that_token_and_leaves_a_new_one():
    au.create_user("a@b.com", "pw")
    token = au.issue_token("a@b.com")
    assert au.authenticate_session(token) == "a@b.com"

    assert au.revoke_session(token) == "revoked"
    assert au.revoke_session(token) == "revoked"
    assert au.authenticate_session(token) is None
    assert au.verify_token(token) == au.resolve_user_id("a@b.com")

    fresh = au.issue_token("a@b.com")
    assert au.authenticate_session(fresh) == "a@b.com"


def test_magic_and_garbage_tokens_are_absent_and_not_stored():
    au.create_user("a@b.com", "pw")
    session = au.issue_token("a@b.com")
    magic = au.issue_token("a@b.com", ttl=900, typ="magic")

    assert au.revoke_session(magic) == "absent"
    assert au.revoke_session("not-a-token") == "absent"
    assert au.revoke_session(None) == "absent"
    assert au.revoke_session("") == "absent"
    assert au.revoke_session(au.issue_token("a@b.com", ttl=-1)) == "absent"
    assert au.authenticate_session(session) == "a@b.com"

    with au._conn() as con:
        count = con.execute("SELECT COUNT(*) AS n FROM revoked_sessions").fetchone()["n"]
    assert count == 0


def test_session_without_jti_cannot_be_blacklisted_and_fails_closed():
    au.create_user("a@b.com", "pw")
    user_id = au.resolve_user_id("a@b.com")
    token = _sign(
        {
            "sub": user_id,
            "exp": int(time.time()) + 900,
            "typ": "session",
            "ver": 2,
        }
    )

    assert au.verify_token(token) == user_id
    assert au.revoke_session(token) == "failed"
    assert au.authenticate_session(token) is None
    with au._conn() as con:
        count = con.execute("SELECT COUNT(*) AS n FROM revoked_sessions").fetchone()["n"]
    assert count == 0


def test_revoke_prunes_expired_rows_and_authenticate_does_not():
    au.create_user("a@b.com", "pw")
    live = au.issue_token("a@b.com")
    with au._conn() as con:
        con.execute(
            "INSERT INTO revoked_sessions (jti, expires_at, revoked_at) VALUES (?,?,?)",
            ("stale-jti", int(time.time()) - 10, "2000-01-01T00:00:00+00:00"),
        )

    assert au.authenticate_session(live) == "a@b.com"
    with au._conn() as con:
        stale = con.execute("SELECT 1 FROM revoked_sessions WHERE jti='stale-jti'").fetchone()
    assert stale is not None

    assert au.revoke_session(live) == "revoked"
    with au._conn() as con:
        jtis = {row["jti"] for row in con.execute("SELECT jti FROM revoked_sessions").fetchall()}
    assert "stale-jti" not in jtis
    assert len(jtis) == 1


def test_existing_v2_db_gains_revoked_sessions_without_a_full_migration():
    au.create_user("a@b.com", "pw")
    with au._conn() as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 2
        con.execute("DROP TABLE revoked_sessions")

    au.init_auth_db()
    with au._conn() as con:
        name = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='revoked_sessions'"
        ).fetchone()
        version = con.execute("PRAGMA user_version").fetchone()[0]
    assert name is not None
    assert version == 2


def test_auth_logout_revokes_before_clearing_the_cookie():
    src = (REPO / "server.py").read_text()
    body = src.split("def auth_logout", 1)[1].split("\n@app.", 1)[0]
    assert body.index("auth_users.revoke_session") < body.index("delete_cookie")
    assert "Sign-out could not revoke the session. Try again." in body
    assert "503" in body
