"""
auth_users.py — minimal multi-user login wall for the :9000 product.

Right-sized for a self-hosted alpha (~10 users who view the owner's research, not
their own brokerage): per-user credentials so access can be issued/revoked on
payment, stdlib-only password hashing (PBKDF2-HMAC-SHA256) and a compact
HMAC-signed session token (JWT-style — no external dependency added to the running
server). Users live in a dedicated `gesh_auth.db`, isolated from the
(corruption-prone) analysis DB.

OFF BY DEFAULT: the gate is inert unless `GESH_AUTH_ENABLED` is truthy, so wiring
it into the live server changes nothing until you create users and flip the flag.

Provision users (after setting GESH_AUTH_SECRET in .env):
    python3 auth_users.py create alice@example.com 's3cret'
    python3 auth_users.py list
    python3 auth_users.py disable alice@example.com      # revoke (e.g. churn)
    python3 auth_users.py enable  alice@example.com
Then enable the wall:
    # in .env:  GESH_AUTH_ENABLED=1   GESH_AUTH_SECRET=<32+ random chars>
    # restart :9000
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

AUTH_DB_PATH = Path(os.getenv("GESH_AUTH_DB", str(Path(__file__).parent / "gesh_auth.db")))
TOKEN_TTL_SECONDS = int(os.getenv("GESH_AUTH_TTL", "604800"))   # 7 days
COOKIE_NAME = "gesh_session"
_PBKDF2_ROUNDS = 200_000

# Paths reachable without a session (login flow, public proof/legal pages, health).
PUBLIC_PREFIXES = (
    "/login", "/auth/login", "/auth/logout", "/static/",
    "/track-record", "/terms", "/disclaimer", "/healthz", "/favicon.ico",
)


# ── config flags ────────────────────────────────────────────────────────────────

def auth_enabled() -> bool:
    """The login wall is OFF unless GESH_AUTH_ENABLED is explicitly truthy."""
    return os.getenv("GESH_AUTH_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")


def _secret() -> bytes:
    """HMAC signing key for session tokens.

    Prefers GESH_AUTH_SECRET (set this in production). Falls back to a persisted
    random secret next to the auth DB so tokens survive restarts in dev.
    """
    s = os.getenv("GESH_AUTH_SECRET", "")
    if s:
        return s.encode()
    p = AUTH_DB_PATH.with_suffix(".secret")
    try:
        if p.exists():
            return p.read_bytes()
        gen = secrets.token_bytes(32)
        p.write_bytes(gen)
        os.chmod(p, 0o600)
        return gen
    except Exception:
        # Last resort: ephemeral (tokens won't survive restart) — never crash.
        return b"gesh-insecure-fallback-set-GESH_AUTH_SECRET"


# ── password hashing (PBKDF2-HMAC-SHA256, stdlib) ───────────────────────────────

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, b64salt, b64dk = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(b64salt)
        expected = base64.b64decode(b64dk)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(rounds))
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False


# ── session token (HMAC-signed, JWT-style, stdlib) ──────────────────────────────

def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64u_dec(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue_token(email: str, ttl: int | None = None) -> str:
    exp = int(time.time()) + (TOKEN_TTL_SECONDS if ttl is None else ttl)
    payload = _b64u(json.dumps({"sub": email, "exp": exp}, separators=(",", ":")).encode())
    sig = _b64u(hmac.new(_secret(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def verify_token(token: str | None) -> str | None:
    """Return the subject (email) for a valid, unexpired token; else None."""
    if not token:
        return None
    try:
        payload_b64, sig = token.split(".")
        expected = _b64u(hmac.new(_secret(), payload_b64.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        data = json.loads(_b64u_dec(payload_b64))
        if int(data.get("exp", 0)) < int(time.time()):
            return None
        return data.get("sub")
    except Exception:
        return None


# ── user store ──────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL,
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT    NOT NULL DEFAULT '',
    last_login_at TEXT
);
"""


@contextmanager
def _conn():
    con = sqlite3.connect(AUTH_DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        con.executescript(_DDL)
        yield con
        con.commit()
    finally:
        con.close()


def init_auth_db() -> None:
    with _conn():
        pass


def _norm(email: str) -> str:
    return (email or "").strip().lower()


def create_user(email: str, password: str) -> int:
    """Create an active user. Raises ValueError if the email already exists."""
    em = _norm(email)
    if not em or not password:
        raise ValueError("email and password are required")
    now = datetime.now(timezone.utc).isoformat()
    try:
        with _conn() as con:
            cur = con.execute(
                "INSERT INTO users (email, password_hash, is_active, created_at) VALUES (?,?,1,?)",
                (em, hash_password(password), now),
            )
            return cur.lastrowid
    except sqlite3.IntegrityError as e:
        raise ValueError(f"user already exists: {em}") from e


def get_user(email: str) -> dict[str, Any] | None:
    with _conn() as con:
        row = con.execute("SELECT * FROM users WHERE email=?", (_norm(email),)).fetchone()
    return dict(row) if row else None


def verify_credentials(email: str, password: str) -> bool:
    """True only when the user exists, is active, and the password matches."""
    u = get_user(email)
    if not u or not u["is_active"]:
        return False
    return verify_password(password, u["password_hash"])


def touch_last_login(email: str) -> None:
    with _conn() as con:
        con.execute("UPDATE users SET last_login_at=? WHERE email=?",
                    (datetime.now(timezone.utc).isoformat(), _norm(email)))


def set_active(email: str, active: bool) -> None:
    with _conn() as con:
        con.execute("UPDATE users SET is_active=? WHERE email=?", (1 if active else 0, _norm(email)))


def list_users() -> list[dict[str, Any]]:
    with _conn() as con:
        rows = con.execute(
            "SELECT id, email, is_active, created_at, last_login_at FROM users ORDER BY created_at"
        ).fetchall()
    return [dict(r) for r in rows]


# ── gate decision (pure, testable) ──────────────────────────────────────────────

def is_public_path(path: str) -> bool:
    return any(path == p or path.startswith(p) for p in PUBLIC_PREFIXES)


def authenticate_session(token: str | None) -> str | None:
    """
    Full session check used by the gate on every request: a cryptographically
    valid, unexpired token AND a subject that is still a CURRENT, ACTIVE user.

    This makes set_active(False) (revocation — e.g. churn / non-payment) and user
    deletion take effect on the very next request, instead of leaving a signed
    cookie valid until it expires. (verify_token alone only proves the token was
    issued by us and hasn't expired — it cannot know the user was since revoked.)
    """
    sub = verify_token(token)
    if not sub:
        return None
    u = get_user(sub)
    if not u or not u["is_active"]:
        return None
    return sub


def gate_decision(path: str, cookie_value: str | None) -> str:
    """
    Decide what to do with a request, assuming auth IS enabled.

    Returns one of:
      'allow'         — public path, or a valid session for a still-active user
      'unauthorized'  — protected /api/* call without a valid session (→ 401 JSON)
      'redirect'      — protected HTML page without a valid session (→ 302 /login)
    """
    if is_public_path(path):
        return "allow"
    if authenticate_session(cookie_value):
        return "allow"
    return "unauthorized" if path.startswith("/api/") else "redirect"


# ── CLI (provision the alpha users) ─────────────────────────────────────────────

def _cli(argv: list[str]) -> int:
    init_auth_db()
    if not argv:
        print(__doc__)
        return 0
    cmd = argv[0]
    if cmd == "create" and len(argv) >= 2:
        email = argv[1]
        pw = argv[2] if len(argv) >= 3 else secrets.token_urlsafe(12)
        try:
            create_user(email, pw)
        except ValueError as e:
            print(f"✗ {e}")
            return 1
        gen = "" if len(argv) >= 3 else f"  (generated password: {pw})"
        print(f"✓ created {email}{gen}")
        if not os.getenv("GESH_AUTH_SECRET"):
            print("⚠  Set GESH_AUTH_SECRET in .env (32+ random chars) before enabling the wall.")
        return 0
    if cmd in ("disable", "enable") and len(argv) >= 2:
        set_active(argv[1], cmd == "enable")
        print(f"✓ {argv[1]} is now {'active' if cmd == 'enable' else 'disabled'}")
        return 0
    if cmd == "list":
        users = list_users()
        if not users:
            print("(no users)")
        for u in users:
            state = "active" if u["is_active"] else "DISABLED"
            print(f"  {u['email']:32} {state:9} created {u['created_at'][:10]} "
                  f"last_login={u['last_login_at'] or '—'}")
        print(f"\nWall: {'ENABLED' if auth_enabled() else 'OFF (set GESH_AUTH_ENABLED=1)'}")
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
