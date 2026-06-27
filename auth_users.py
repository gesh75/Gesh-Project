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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

AUTH_DB_PATH = Path(os.getenv("GESH_AUTH_DB", str(Path(__file__).parent / "gesh_auth.db")))
TOKEN_TTL_SECONDS = int(os.getenv("GESH_AUTH_TTL", "604800"))   # 7 days
COOKIE_NAME = "gesh_session"
_PBKDF2_ROUNDS = 200_000

# Paths reachable without a session (login flow, public proof/legal pages, health).
PUBLIC_PREFIXES = (
    "/login", "/auth/login", "/auth/logout", "/auth/magic", "/auth/request-link", "/static/",
    "/track-record", "/terms", "/disclaimer", "/healthz", "/favicon.ico",
    # Payment surface: pricing page + provider webhook + post-checkout landing must be
    # reachable WITHOUT a session (the webhook is signature-gated, not cookie-gated).
    "/pricing", "/api/billing/webhook", "/billing/success",
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
    except Exception as e:
        # When the login wall is ON, a predictable hardcoded key would let anyone
        # forge session cookies — fail closed instead. Only fall back to a random
        # ephemeral key when auth is disabled (dev convenience).
        if auth_enabled():
            raise RuntimeError(
                "GESH_AUTH_SECRET unset and .secret file unavailable while auth is "
                f"enabled — refusing insecure fallback key: {e}"
            )
        return secrets.token_bytes(32)


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


def issue_token(email: str, ttl: int | None = None, typ: str = "session") -> str:
    """Mint an HMAC-signed token. `typ` ('session' | 'magic') is BOUND into the signed
    payload so the two purposes are NOT interchangeable: a short-TTL magic-link token can
    never be replayed as a long-lived session cookie (H1). A random `jti` makes each token
    unique so it can be individually tracked/spent."""
    exp = int(time.time()) + (TOKEN_TTL_SECONDS if ttl is None else ttl)
    payload = _b64u(json.dumps(
        {"sub": email, "exp": exp, "typ": typ, "jti": secrets.token_urlsafe(8)},
        separators=(",", ":"),
    ).encode())
    sig = _b64u(hmac.new(_secret(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def verify_token(token: str | None, expected_typ: str | None = "session") -> str | None:
    """Return the subject (email) for a valid, unexpired token; else None.

    `expected_typ` enforces the token's purpose claim. Tokens minted before the `typ`
    claim existed have no `typ` and are treated as 'session' (back-compat grace for
    cookies issued before this change). Pass expected_typ=None to skip the check."""
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
        if expected_typ is not None and data.get("typ", "session") != expected_typ:
            return None        # wrong purpose (e.g. a magic token presented as a session)
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
CREATE TABLE IF NOT EXISTS used_magic_tokens (
    token_hash TEXT PRIMARY KEY,
    used_at    TEXT NOT NULL
);
"""

# Billing columns added 2026-06-26 (payment integration). Kept as an idempotent ALTER
# migration rather than baked into _DDL so EXISTING gesh_auth.db files pick them up too
# (CREATE TABLE IF NOT EXISTS never alters an existing table). tier/billing_* are NULL for
# pre-payment alpha users; the login wall still gates only on is_active.
_BILLING_COLUMNS = {
    "tier":                "TEXT",   # 'observer' | 'trader' | 'desk'
    "billing_customer_id": "TEXT",   # provider customer id (Lemon Squeezy)
    "billing_sub_id":      "TEXT",   # provider subscription id
    "billing_status":      "TEXT",   # 'active' | 'past_due' | 'cancelled'
    "welcome_emailed_at":  "TEXT",   # set when the magic-link onboarding email is delivered;
                                     # NULL = still owed a link (drives reliable retry, H5)
    "billing_event_at":    "TEXT",   # subscription updated_at of the last applied billing
                                     # event — drives monotonic ordering (drops stale events)
}


def _migrate(con) -> None:
    """Add any missing billing columns (idempotent — safe on every connect)."""
    have = {r["name"] for r in con.execute("PRAGMA table_info(users)").fetchall()}
    for col, decl in _BILLING_COLUMNS.items():
        if col not in have:
            con.execute(f"ALTER TABLE users ADD COLUMN {col} {decl}")
    # Prune spent magic tokens older than a day — they are dead long before that (15-min
    # TTL), so this just stops the table growing unbounded (M3).
    cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    con.execute("DELETE FROM used_magic_tokens WHERE used_at < ?", (cutoff,))


@contextmanager
def _conn():
    con = sqlite3.connect(AUTH_DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        con.executescript(_DDL)
        _migrate(con)
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


def get_user_by_sub_id(sub_id: str) -> dict[str, Any] | None:
    """Look a user up by their provider subscription id — the authoritative key for a
    subscription's lifecycle (email is mutable, so it can't be the only key, #5)."""
    if not sub_id:
        return None
    with _conn() as con:
        row = con.execute("SELECT * FROM users WHERE billing_sub_id=?", (sub_id,)).fetchone()
    return dict(row) if row else None


def rename_user_email(old_email: str, new_email: str) -> bool:
    """Move a subscription's account to a new email (LS lets a customer change their email).
    Returns True on success, False if the target email is already taken by a DIFFERENT row
    (caller then falls back rather than clobbering)."""
    old, new = _norm(old_email), _norm(new_email)
    if not new or old == new:
        return False
    with _conn() as con:
        clash = con.execute("SELECT 1 FROM users WHERE email=?", (new,)).fetchone()
        if clash:
            return False
        con.execute("UPDATE users SET email=? WHERE email=?", (new, old))
    return True


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


def set_billing(email: str, *, tier: str | None = None, customer_id: str | None = None,
                sub_id: str | None = None, status: str | None = None,
                event_at: str | None = None) -> None:
    """Update only the billing fields that are provided (None = leave unchanged)."""
    sets, params = [], []
    for col, val in (("tier", tier), ("billing_customer_id", customer_id),
                     ("billing_sub_id", sub_id), ("billing_status", status),
                     ("billing_event_at", event_at)):
        if val is not None:
            sets.append(f"{col}=?")
            params.append(val)
    if not sets:
        return
    params.append(_norm(email))
    with _conn() as con:
        con.execute(f"UPDATE users SET {', '.join(sets)} WHERE email=?", params)


def create_magic_link_user(email: str, tier: str | None = None) -> bool:
    """Create a passwordless subscriber (logs in via magic link) OR re-activate an
    existing one. Returns True if a new row was created. Idempotent — safe to call on
    every 'subscription active' webhook. The random unusable password keeps the NOT NULL
    constraint satisfied; the user authenticates via /auth/magic, not a password."""
    em = _norm(email)
    if not em:
        raise ValueError("email required")
    if get_user(em):
        set_active(em, True)
        if tier:
            set_billing(em, tier=tier)
        return False
    create_user(em, secrets.token_urlsafe(24))   # unusable random pw; magic-link login
    if tier:
        set_billing(em, tier=tier)
    return True


def set_welcome_emailed(email: str, ts: str | None) -> None:
    """Stamp (or clear with None) when the onboarding magic-link email was delivered."""
    with _conn() as con:
        con.execute("UPDATE users SET welcome_emailed_at=? WHERE email=?", (ts, _norm(email)))


def issue_magic_token(email: str, ttl: int = 900) -> str | None:
    """Mint a fresh single-use magic token for an ACTIVE user, else None.

    Recovery path (H5): lets a passwordless subscriber re-request a sign-in link if the
    onboarding email never arrived or the link expired. Returns None (don't reveal whether
    the address exists) for unknown/inactive users — the caller emails only on a token."""
    em = _norm(email)
    u = get_user(em)
    if not u or not u["is_active"]:
        return None
    return issue_token(em, ttl=ttl, typ="magic")


def list_users() -> list[dict[str, Any]]:
    with _conn() as con:
        rows = con.execute(
            "SELECT id, email, is_active, created_at, last_login_at FROM users ORDER BY created_at"
        ).fetchall()
    return [dict(r) for r in rows]


# ── gate decision (pure, testable) ──────────────────────────────────────────────

def is_public_path(path: str) -> bool:
    return any(path == p or path.startswith(p) for p in PUBLIC_PREFIXES)


def consume_magic_token(token: str | None) -> str | None:
    """Single-use validation for a magic-link token. Returns the subject email on the
    FIRST valid use and atomically marks the token spent; any later use (or an invalid/
    expired token) returns None. This prevents a leaked link (browser history, Referer,
    proxy logs) from being replayed: it works exactly once, then is dead.

    Note: the magic token is NOT the session cookie — it is typ='magic' and is rejected by
    authenticate_session, and the caller mints a fresh typ='session' token after consuming
    this one, so URL exposure never yields a reusable session."""
    sub = verify_token(token, expected_typ="magic")
    if not sub:
        return None
    th = hashlib.sha256(token.encode()).hexdigest()
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            "INSERT OR IGNORE INTO used_magic_tokens (token_hash, used_at) VALUES (?,?)",
            (th, now),
        )
        if cur.rowcount == 0:        # already present → token was already consumed
            return None
    return sub


def authenticate_session(token: str | None) -> str | None:
    """
    Full session check used by the gate on every request: a cryptographically
    valid, unexpired token AND a subject that is still a CURRENT, ACTIVE user.

    This makes set_active(False) (revocation — e.g. churn / non-payment) and user
    deletion take effect on the very next request, instead of leaving a signed
    cookie valid until it expires. (verify_token alone only proves the token was
    issued by us and hasn't expired — it cannot know the user was since revoked.)

    Only typ='session' tokens are accepted — a leaked typ='magic' URL token cannot be
    replayed here as a session cookie (H1).
    """
    sub = verify_token(token, expected_typ="session")
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
