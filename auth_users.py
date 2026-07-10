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
import logging
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
CREATE TABLE IF NOT EXISTS watchlist (
    email    TEXT NOT NULL,
    symbol   TEXT NOT NULL,
    added_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (email, symbol)
);
CREATE TABLE IF NOT EXISTS customer_positions (
    email      TEXT NOT NULL,
    symbol     TEXT NOT NULL,
    shares     REAL NOT NULL,
    avg_cost   REAL,
    updated_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (email, symbol)
);
CREATE TABLE IF NOT EXISTS customer_analyses (
    email       TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_customer_analyses_email
    ON customer_analyses (email, created_at);
CREATE TABLE IF NOT EXISTS usage_events (
    email  TEXT NOT NULL,
    event  TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_usage_events_at ON usage_events (at);
CREATE TABLE IF NOT EXISTS customer_book_prefs (
    email        TEXT PRIMARY KEY,
    equal_weight INTEGER NOT NULL DEFAULT 0,
    updated_at   TEXT NOT NULL DEFAULT ''
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
    # One account per subscription (multi-worker safety): a partial UNIQUE index makes the DB
    # itself reject a second row for the same billing_sub_id, so a race between
    # subscription_created / subscription_updated can't create two accounts on one sub.
    # Best-effort: if a legacy DB already holds duplicate sub ids the index won't build —
    # log and continue rather than break every connect.
    try:
        con.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_users_billing_sub_id "
            "ON users(billing_sub_id) WHERE billing_sub_id IS NOT NULL"
        )
    except sqlite3.IntegrityError as e:
        logging.getLogger(__name__).warning(
            "auth_users: could not build unique billing_sub_id index (dupes exist): %s", e)
    # Prune spent magic tokens older than a day — they are dead long before that (15-min
    # TTL), so this just stops the table growing unbounded (M3).
    cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    con.execute("DELETE FROM used_magic_tokens WHERE used_at < ?", (cutoff,))


def _harden_db_perms() -> None:
    """Restrict gesh_auth.db to owner-only (0600). It holds emails, password hashes, and
    billing identifiers — world/group-readable (0644) is needless exposure. Best-effort:
    a managed/FUSE mount may reject chmod, which is non-fatal."""
    try:
        if AUTH_DB_PATH.exists():
            os.chmod(AUTH_DB_PATH, 0o600)
    except OSError:
        pass


@contextmanager
def _conn():
    con = sqlite3.connect(AUTH_DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        con.executescript(_DDL)
        _migrate(con)
        _harden_db_perms()
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


def list_owed_onboarding(limit: int = 100) -> list[dict[str, Any]]:
    """Active subscribers who are still owed a sign-in link (welcome_emailed_at IS NULL).

    Backs the async onboarding safety net: when the webhook-inline send fails and Lemon
    Squeezy exhausts its retries, these users would otherwise be silently stuck. A scheduled
    sweep re-mints + re-sends to them (billing.resend_owed_onboarding). Only ever returns
    ACTIVE users with a tier — a churned/never-paid row is not owed an onboarding link."""
    with _conn() as con:
        rows = con.execute(
            "SELECT email, tier FROM users "
            "WHERE is_active=1 AND welcome_emailed_at IS NULL AND tier IS NOT NULL "
            "ORDER BY created_at LIMIT ?",
            (max(1, int(limit)),),
        ).fetchall()
    return [dict(r) for r in rows]


# ── gate decision (pure, testable) ──────────────────────────────────────────────

def is_public_path(path: str) -> bool:
    return any(path == p or path.startswith(p) for p in PUBLIC_PREFIXES)


def is_owner(email: str | None) -> bool:
    """True for the account that owns the underlying brokerage book.

    Everyone else is a CUSTOMER session: the server masks absolute dollars /
    share quantities (normalized to a model book) and owner-only views
    (/report, /invest, /progress) are refused. Override via GESH_OWNER_EMAIL."""
    owner = os.getenv("GESH_OWNER_EMAIL", "georgy75@gmail.com").strip().lower()
    return bool(email) and email.strip().lower() == owner


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


# ── per-user watchlist (customer analyze-any-ticker: saved symbols) ────────────

_MAX_WATCH = 50   # per-user cap — abuse guard


def add_watch(email: str, symbol: str) -> bool:
    """Add a symbol to the user's watchlist. Idempotent (dup → True, no duplicate);
    returns False only when the per-user cap is hit. Symbols stored UPPER."""
    em, sym = _norm(email), (symbol or "").strip().upper()
    if not em or not sym:
        return False
    with _conn() as con:
        if con.execute("SELECT 1 FROM watchlist WHERE email=? AND symbol=?", (em, sym)).fetchone():
            return True
        # Single-statement cap check: a separate count-then-insert could exceed the
        # cap under concurrent adds. OR IGNORE absorbs a concurrent duplicate insert.
        cur = con.execute(
            "INSERT OR IGNORE INTO watchlist (email, symbol, added_at) "
            "SELECT ?,?,? WHERE (SELECT COUNT(*) FROM watchlist WHERE email=?) < ?",
            (em, sym, datetime.now(timezone.utc).isoformat(), em, _MAX_WATCH))
        if cur.rowcount:
            return True
        # rowcount 0 = cap hit, or a concurrent add of the same symbol won the
        # race — the latter is still a success (the symbol IS on the list).
        return con.execute("SELECT 1 FROM watchlist WHERE email=? AND symbol=?",
                           (em, sym)).fetchone() is not None


def remove_watch(email: str, symbol: str) -> None:
    em, sym = _norm(email), (symbol or "").strip().upper()
    with _conn() as con:
        con.execute("DELETE FROM watchlist WHERE email=? AND symbol=?", (em, sym))


def get_watch(email: str) -> list[str]:
    """The user's saved symbols, oldest-first. Empty list for an unknown user."""
    em = _norm(email)
    if not em:
        return []
    with _conn() as con:
        rows = con.execute(
            "SELECT symbol FROM watchlist WHERE email=? ORDER BY added_at, symbol", (em,)
        ).fetchall()
    return [r["symbol"] for r in rows]


# ── per-user portfolio book (customer analyze-MY-portfolio: their own positions) ──
# The customer's OWN book — symbol + share count (+ optional avg cost), entered by
# them, valued live at read time. Contains ZERO owner data. Same isolation contract
# as the watchlist: every read/write is scoped to the session email.

_MAX_POSITIONS = 100   # per-user cap — abuse guard (also bounds analyze-engine cost).
#                        Raised 30→100 (2026-07-10) to fit a real book via bulk import.


def set_position(email: str, symbol: str, shares: float, avg_cost: float | None = None) -> bool:
    """Upsert one position on the user's book. Returns False on invalid input or
    when adding a NEW symbol would exceed the cap (updating an existing one is
    always allowed). Symbols stored UPPER; shares must be > 0 (use
    remove_position to delete)."""
    em, sym = _norm(email), (symbol or "").strip().upper()
    try:
        sh = float(shares)
        ac = None if avg_cost in (None, "") else float(avg_cost)
    except (TypeError, ValueError):
        return False
    if not em or not sym or not (0 < sh < 1e9) or (ac is not None and not (0 <= ac < 1e9)):
        return False
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        if con.execute("SELECT 1 FROM customer_positions WHERE email=? AND symbol=?",
                       (em, sym)).fetchone():
            con.execute("UPDATE customer_positions SET shares=?, avg_cost=?, updated_at=? "
                        "WHERE email=? AND symbol=?", (sh, ac, now, em, sym))
            return True
        # Single-statement cap check (same concurrency shape as add_watch).
        cur = con.execute(
            "INSERT OR IGNORE INTO customer_positions (email, symbol, shares, avg_cost, updated_at) "
            "SELECT ?,?,?,?,? WHERE (SELECT COUNT(*) FROM customer_positions WHERE email=?) < ?",
            (em, sym, sh, ac, now, em, _MAX_POSITIONS))
        if cur.rowcount:
            return True
        return con.execute("SELECT 1 FROM customer_positions WHERE email=? AND symbol=?",
                           (em, sym)).fetchone() is not None


def remove_position(email: str, symbol: str) -> None:
    em, sym = _norm(email), (symbol or "").strip().upper()
    with _conn() as con:
        con.execute("DELETE FROM customer_positions WHERE email=? AND symbol=?", (em, sym))


def get_positions(email: str) -> list[dict]:
    """The user's book, oldest-first: [{symbol, shares, avg_cost}]. Empty list for
    an unknown user."""
    em = _norm(email)
    if not em:
        return []
    with _conn() as con:
        rows = con.execute(
            "SELECT symbol, shares, avg_cost FROM customer_positions "
            "WHERE email=? ORDER BY updated_at, symbol", (em,)
        ).fetchall()
    return [{"symbol": r["symbol"], "shares": r["shares"], "avg_cost": r["avg_cost"]}
            for r in rows]


# ── equal-weight (privacy) flag ──────────────────────────────────────────────
# When a customer bulk-imports SYMBOLS ONLY (no share counts — sizes private), the
# book is flagged equal-weight: the analysis weights every name 1/N instead of by an
# unknown/placeholder size. A bulk import that DOES carry share counts clears it.

def set_equal_weight(email: str, on: bool) -> None:
    em = _norm(email)
    if not em:
        return
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        con.execute(
            "INSERT INTO customer_book_prefs (email, equal_weight, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(email) DO UPDATE SET equal_weight=excluded.equal_weight, "
            "updated_at=excluded.updated_at",
            (em, 1 if on else 0, now))


def get_equal_weight(email: str) -> bool:
    """True when the user's book is in equal-weight (sizes-private) mode."""
    em = _norm(email)
    if not em:
        return False
    with _conn() as con:
        row = con.execute(
            "SELECT equal_weight FROM customer_book_prefs WHERE email=?", (em,)).fetchone()
    return bool(row and row["equal_weight"])


# ── persisted book analyses (customer report page) ─────────────────────────────
# The in-memory analyze cache dies with the process; the /mybook/report page needs
# the customer's LAST analysis durably. Keep a short per-user history (latest wins).

_MAX_ANALYSES_PER_USER = 5


def save_book_analysis(email: str, result: dict) -> None:
    em = _norm(email)
    if not em or not isinstance(result, dict):
        return
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        con.execute("INSERT INTO customer_analyses (email, result_json, created_at) VALUES (?,?,?)",
                    (em, json.dumps(result), now))
        # prune: keep only the newest _MAX_ANALYSES_PER_USER rows per user
        con.execute(
            "DELETE FROM customer_analyses WHERE email=? AND rowid NOT IN ("
            "  SELECT rowid FROM customer_analyses WHERE email=?"
            "  ORDER BY created_at DESC, rowid DESC LIMIT ?)",
            (em, em, _MAX_ANALYSES_PER_USER))


def get_latest_book_analysis(email: str) -> dict | None:
    """Newest persisted analysis for the user, with its timestamp under `_saved_at`.
    Corruption-tolerant: an unparseable row returns None rather than raising."""
    em = _norm(email)
    if not em:
        return None
    with _conn() as con:
        row = con.execute(
            "SELECT result_json, created_at FROM customer_analyses WHERE email=? "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1", (em,)).fetchone()
    if not row:
        return None
    try:
        out = json.loads(row["result_json"])
        out["_saved_at"] = row["created_at"]
        return out
    except Exception:
        return None


# ── usage metering (per-customer event counters — tier/billing groundwork) ─────

def log_usage(email: str | None, event: str, detail: str = "") -> None:
    """Fire-and-forget usage counter. NEVER raises — metering must not be able to
    break a product endpoint."""
    try:
        em = _norm(email or "")
        if not em or not event:
            return
        with _conn() as con:
            con.execute("INSERT INTO usage_events (email, event, detail, at) VALUES (?,?,?,?)",
                        (em, event, (detail or "")[:64],
                         datetime.now(timezone.utc).isoformat()))
    except Exception:
        pass


def get_tier(email: str) -> str | None:
    """The user's billing tier ('observer' | 'trader' | 'desk'), set by the Lemon
    Squeezy webhook on provision. None for unknown/pre-billing rows AND for
    INACTIVE users — revoke clears is_active but NOT tier, so a churned/revoked
    account must not keep a scaled quota multiplier via a stale tier. Gating the
    lookup on is_active makes tier-scaling authoritative on active accounts only."""
    u = get_user(email)
    if not u or not u.get("is_active"):
        return None
    return u.get("tier") or None


def usage_count_24h(email: str, event: str) -> int:
    """Rolling-24h count of one event for one user — the quota denominator.
    Counts LIVE events only by design: callers meter cached hits under a
    separate `*_cached` event name, which never consumes quota."""
    em = _norm(email)
    if not em:
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    with _conn() as con:
        row = con.execute(
            "SELECT COUNT(*) AS n FROM usage_events WHERE email=? AND event=? AND at >= ?",
            (em, event, cutoff)).fetchone()
    return int(row["n"])


def usage_summary(days: int = 30) -> list[dict]:
    """Per-user, per-event counts over the trailing window — the owner's metering
    view (billing-tier groundwork). Newest-heaviest first."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with _conn() as con:
        rows = con.execute(
            "SELECT email, event, COUNT(*) AS n, MAX(at) AS last_at FROM usage_events "
            "WHERE at >= ? GROUP BY email, event ORDER BY n DESC", (cutoff,)).fetchall()
    return [{"email": r["email"], "event": r["event"], "count": r["n"], "last_at": r["last_at"]}
            for r in rows]


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
