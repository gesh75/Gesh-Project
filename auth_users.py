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
    python3 auth_users.py tier    alice@example.com desk # comp a tester: observer|trader|desk
                                                         #   scales LLM quotas 1× / 2× / 5×
Then enable the wall:
    # in .env:  GESH_AUTH_ENABLED=1   GESH_AUTH_SECRET=<32+ random chars>
    # restart :9000
"""

from __future__ import annotations

import base64
import errno
import hashlib
import hmac
import json
import logging
import math
import os
import secrets
import sqlite3
import stat
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

AUTH_DB_PATH = Path(os.getenv("GESH_AUTH_DB", str(Path(__file__).parent / "gesh_auth.db")))
TOKEN_TTL_SECONDS = int(os.getenv("GESH_AUTH_TTL", "604800"))   # 7 days
COOKIE_NAME = "gesh_session"
_PBKDF2_ROUNDS = 200_000

# Paths reachable without a session (login flow, public legal pages, health).
PUBLIC_PREFIXES = (
    "/login", "/auth/login", "/auth/logout", "/auth/magic", "/auth/request-link", "/static/",
    # /privacy is public by necessity: a policy behind a login wall is not a
    # published policy — people must be able to read it BEFORE creating an account.
    "/terms", "/disclaimer", "/privacy", "/healthz", "/favicon.ico",
    # Congress-Watch: public informational page + its public-data API (STOCK Act
    # disclosures — no owner/customer data, no execution). The shareable acquisition magnet.
    "/congress-watch", "/api/congress",
    # Payment surface: pricing page + provider webhook + post-checkout landing must be
    # reachable WITHOUT a session (the webhook is signature-gated, not cookie-gated).
    "/pricing", "/api/billing/webhook", "/billing/success",
    # Email opt-out links (link-in-email, HMAC-token-gated — not cookie-gated).
    "/alerts/unsubscribe", "/digest/unsubscribe",
    # Signal-accuracy proof page — the #1 sales asset. Never paywalled
    # (CLAUDE.md). Prefix match is path-bounded so /track-record-evil stays gated.
    "/track-record",
)

# SEO surface (crawlers + logged-out visitors). EXACT match only — unlike the prefixes
# above these never expose a sub-path, so '/robots.txt.bak' or '/sitemap.xml/x' stay gated.
PUBLIC_EXACT_PATHS = frozenset({
    "/",  # logged-out marketing landing; the handler serves the dashboard ONLY to a valid session
    "/robots.txt", "/sitemap.xml",
    # Free tools (SSR shell + client-side calculators; no owner data, no server state).
    "/tools", "/tools/portfolio-xray", "/tools/monte-carlo-simulator", "/tools/kelly-calculator",
})


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

_TOKEN_SCHEMA_VERSION = 2

def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64u_dec(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _is_canonical_user_id(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except (ValueError, AttributeError):
        return False


def issue_token(email: str, ttl: int | None = None, typ: str = "session") -> str:
    """Mint a versioned token bound to the active user's immutable UUID.

    The public caller still supplies email for server compatibility, but mutable email is
    never written into the signed identity claim. Purpose and schema version are also
    signed, and a random ``jti`` keeps magic tokens independently spendable.
    """
    user_id = resolve_user_id(email)
    if user_id is None:
        raise ValueError("token subject must be a current active user")
    issued = int(time.time())
    exp = issued + (TOKEN_TTL_SECONDS if ttl is None else ttl)
    payload = _b64u(json.dumps(
        {
            "sub": user_id,
            "exp": exp,
            "iat": issued,
            "typ": typ,
            "ver": _TOKEN_SCHEMA_VERSION,
            "jti": secrets.token_urlsafe(8),
        },
        separators=(",", ":"),
    ).encode())
    sig = _b64u(hmac.new(_secret(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def _verified_claims(
    token: str | None, expected_typ: str | None = "session"
) -> dict | None:
    """Return the signed claims for a valid schema-v2 token; else None.

    Pure crypto, same rules as ``verify_token``. A missing ``jti`` still returns
    the claims: callers that must blacklist a session decide what to do with it.
    """
    if not token:
        return None
    try:
        payload_b64, sig = token.split(".")
        expected = _b64u(hmac.new(_secret(), payload_b64.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        data = json.loads(_b64u_dec(payload_b64))
        if not isinstance(data, dict) or data.get("ver") != _TOKEN_SCHEMA_VERSION:
            return None
        if int(data.get("exp", 0)) < int(time.time()):
            return None
        if expected_typ is not None and data.get("typ") != expected_typ:
            return None        # wrong purpose (e.g. a magic token presented as a session)
        subject = data.get("sub")
        if not _is_canonical_user_id(subject):
            return None
        return data
    except Exception:
        return None


def verify_token(token: str | None, expected_typ: str | None = "session") -> str | None:
    """Return the immutable UUID subject for a valid schema-v2 token; else None.

    Tokens without the exact schema version, including all legacy email-subject tokens,
    fail closed. ``expected_typ`` enforces token purpose; pass ``None`` only to skip the
    purpose comparison, never identity/version validation.

    This stays a pure signature/expiry check. Revocation lives in
    ``authenticate_session``, which also requires a ``jti``.
    """
    claims = _verified_claims(token, expected_typ)
    if not claims:
        return None
    return str(claims["sub"])


# ── user store ──────────────────────────────────────────────────────────────────

_SCHEMA_VERSION = 2
_BUSY_TIMEOUT_MS = 5_000
_PRE_V2_BACKUP_SUFFIX = ".pre-v2.bak"
_BACKUP_MANIFEST_VERSION = 1
_OWNER_METADATA_KEY = "owner_user_id"
_OWNER_INITIALIZED_KEY = "owner_marker_initialized"

_CORE_DDL = (
    """
    CREATE TABLE IF NOT EXISTS users (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        email         TEXT    NOT NULL UNIQUE,
        password_hash TEXT    NOT NULL,
        is_active     INTEGER NOT NULL DEFAULT 1,
        created_at    TEXT    NOT NULL DEFAULT '',
        last_login_at TEXT,
        user_id       TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS used_magic_tokens (
        token_hash TEXT PRIMARY KEY,
        used_at    TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS auth_metadata (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS revoked_sessions (
        jti        TEXT PRIMARY KEY,
        user_id    TEXT,
        expires_at INTEGER NOT NULL,
        revoked_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_sessions (
        jti        TEXT PRIMARY KEY,
        user_id    TEXT NOT NULL,
        expires_at INTEGER NOT NULL
    )
    """,
)

_CHILD_TABLE_DDL = {
    "watchlist": """
        CREATE TABLE {table} (
            user_id  TEXT NOT NULL,
            symbol   TEXT NOT NULL,
            added_at TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (user_id, symbol),
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
    """,
    "customer_positions": """
        CREATE TABLE {table} (
            user_id    TEXT NOT NULL,
            symbol     TEXT NOT NULL,
            shares     REAL NOT NULL,
            avg_cost   REAL,
            updated_at TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (user_id, symbol),
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
    """,
    "customer_analyses": """
        CREATE TABLE {table} (
            user_id     TEXT NOT NULL,
            result_json TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
    """,
    "usage_events": """
        CREATE TABLE {table} (
            user_id TEXT NOT NULL,
            event   TEXT NOT NULL,
            detail  TEXT NOT NULL DEFAULT '',
            at      TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
    """,
    "customer_book_prefs": """
        CREATE TABLE {table} (
            user_id      TEXT PRIMARY KEY NOT NULL,
            equal_weight INTEGER NOT NULL DEFAULT 0,
            updated_at   TEXT NOT NULL DEFAULT '',
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
    """,
    "mybook_jobs": """
        CREATE TABLE {table} (
            job_id      TEXT PRIMARY KEY NOT NULL,
            user_id     TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            status      TEXT NOT NULL CHECK(status IN ('queued','running','done','failed')),
            result_json TEXT,
            error_code  TEXT,
            delivered_at TEXT,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
    """,
    "usage_reservations": """
        CREATE TABLE {table} (
            job_id      TEXT PRIMARY KEY NOT NULL,
            user_id     TEXT NOT NULL,
            event       TEXT NOT NULL,
            state       TEXT NOT NULL CHECK(state IN ('reserved','completed','released')),
            reserved_at TEXT NOT NULL,
            completed_at TEXT,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
    """,
    # The CUSTOMER's own equity curve — the per-user counterpart of the owner-only
    # `equity_snapshots` table in gesh_financial.db, which has NO user column and
    # therefore hands every caller the owner's curve. Rows here are derived
    # exclusively from customer_positions valued at public closes.
    #
    # `percent_change` is deliberately ABSENT. In the owner table that column is
    # the position's lifetime gain/loss vs average COST (a level, not a session
    # change); feeding it to ecg._loss_streak auto-SUSPENDed every underwater
    # holding, a bug this repo has fixed twice. Not storing it means nobody can
    # reach for it: per-session changes are derived from the equity series itself.
    "customer_equity_snapshots": """
        CREATE TABLE {table} (
            user_id       TEXT NOT NULL,
            symbol        TEXT NOT NULL,
            snapshot_at   TEXT NOT NULL,
            equity        REAL NOT NULL,
            current_price REAL NOT NULL,
            PRIMARY KEY (user_id, symbol, snapshot_at),
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
    """,
}

_CHILD_DATA_COLUMNS = {
    "watchlist": ("symbol", "added_at"),
    "customer_positions": ("symbol", "shares", "avg_cost", "updated_at"),
    "customer_analyses": ("result_json", "created_at"),
    "usage_events": ("event", "detail", "at"),
    "customer_book_prefs": ("equal_weight", "updated_at"),
    "mybook_jobs": (
        "job_id", "fingerprint", "status", "result_json", "error_code", "delivered_at",
        "created_at", "updated_at",
    ),
    "usage_reservations": (
        "job_id", "event", "state", "reserved_at", "completed_at",
    ),
    "customer_equity_snapshots": ("symbol", "snapshot_at", "equity", "current_price"),
}

# Billing columns added 2026-06-26 (payment integration). Kept as an idempotent ALTER
# migration rather than baked into the base DDL so EXISTING gesh_auth.db files pick them up too
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

# Email preference columns (retention features, 2026-07-16). Same idempotent-ALTER
# migration pattern as _BILLING_COLUMNS — existing gesh_auth.db files pick these up
# on next connect without a manual migration step.
_PREF_COLUMNS = {
    "alerts_opt_out": "INTEGER NOT NULL DEFAULT 0",   # signal-change alert emails
    "digest_opt_out": "INTEGER NOT NULL DEFAULT 0",   # weekly digest emails
}


def _migrate(con: sqlite3.Connection) -> None:
    """Add missing billing/preference columns and indexes (idempotent — safe on every connect)."""
    have = {r["name"] for r in con.execute("PRAGMA table_info(users)").fetchall()}
    for col, decl in _BILLING_COLUMNS.items():
        if col not in have:
            con.execute(f"ALTER TABLE users ADD COLUMN {col} {decl}")
    for col, decl in _PREF_COLUMNS.items():
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


def _prune_used_magic_tokens(con: sqlite3.Connection) -> None:
    """Prune dead tokens when an existing write path already owns a transaction."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    con.execute("DELETE FROM used_magic_tokens WHERE used_at < ?", (cutoff,))


def _column_names(con: sqlite3.Connection, table: str) -> set[str]:
    return {str(row["name"]) for row in con.execute(f"PRAGMA table_info({table})").fetchall()}


def _ensure_revoked_sessions(con: sqlite3.Connection) -> None:
    """Create the session blacklist without forcing a schema-v2 rebuild.

    ``_schema_v2_ready`` returns before ``_ensure_core_schema`` on every existing
    v2 database. These tables are not part of that ready-check, so they have to
    be created on the fast path or logout cannot revoke anything. ``user_id`` is
    added in place on a blacklist created before rotation existed.
    Once the columns exist, this stays a read.
    """
    exists = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='revoked_sessions'"
    ).fetchone()
    sessions = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='user_sessions'"
    ).fetchone()
    if (
        exists
        and sessions
        and "user_id" in _column_names(con, "revoked_sessions")
        and "session_not_before" in _column_names(con, "users")
    ):
        return
    if not exists:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS revoked_sessions (
                jti        TEXT PRIMARY KEY,
                user_id    TEXT,
                expires_at INTEGER NOT NULL,
                revoked_at TEXT NOT NULL
            )
            """
        )
    elif "user_id" not in _column_names(con, "revoked_sessions"):
        con.execute("ALTER TABLE revoked_sessions ADD COLUMN user_id TEXT")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS user_sessions (
            jti        TEXT PRIMARY KEY,
            user_id    TEXT NOT NULL,
            expires_at INTEGER NOT NULL
        )
        """
    )
    if "session_not_before" not in _column_names(con, "users"):
        con.execute("ALTER TABLE users ADD COLUMN session_not_before INTEGER")


def _prune_revoked_sessions(con: sqlite3.Connection) -> None:
    """Drop blacklist rows whose tokens have already expired.

    Called from the revoke write path only. ``authenticate_session`` must stay
    a read so a request does not take a write lock just to garbage-collect.
    """
    con.execute(
        "DELETE FROM revoked_sessions WHERE expires_at < ?",
        (int(time.time()),),
    )


def _new_user_id() -> str:
    return str(uuid.uuid4())


def _configured_owner_email() -> str:
    return _norm(os.getenv("GESH_OWNER_EMAIL", "georgy75@gmail.com"))


def _claim_owner_marker(
    con: sqlite3.Connection, email: str, user_id: str
) -> None:
    """Persist the first configured owner's UUID; never transfer it by email reuse."""
    if email != _configured_owner_email():
        return
    con.execute(
        "INSERT OR IGNORE INTO auth_metadata (key, value) VALUES (?, ?)",
        (_OWNER_METADATA_KEY, user_id),
    )


def _ensure_owner_marker(con: sqlite3.Connection) -> None:
    """Backfill legacy owner email once, retaining the immutable marker thereafter."""
    initialized = con.execute(
        "SELECT 1 FROM auth_metadata WHERE key=?",
        (_OWNER_INITIALIZED_KEY,),
    ).fetchone()
    if initialized:
        return

    existing = con.execute(
        "SELECT 1 FROM auth_metadata WHERE key=?",
        (_OWNER_METADATA_KEY,),
    ).fetchone()
    if not existing:
        row = con.execute(
            "SELECT user_id FROM users WHERE email=? ORDER BY id LIMIT 1",
            (_configured_owner_email(),),
        ).fetchone()
        if row:
            _claim_owner_marker(con, _configured_owner_email(), str(row["user_id"]))
    con.execute(
        "INSERT INTO auth_metadata (key, value) VALUES (?, '1')",
        (_OWNER_INITIALIZED_KEY,),
    )


def _pre_v2_backup_path(source: Path) -> Path:
    return source.with_suffix(_PRE_V2_BACKUP_SUFFIX)


def _pre_v2_manifest_path(source: Path) -> Path:
    backup = _pre_v2_backup_path(source)
    return backup.with_name(f"{backup.name}.manifest.json")


def _database_path(con: sqlite3.Connection) -> Path | None:
    row = con.execute("PRAGMA database_list").fetchone()
    filename = str(row[2] or "") if row else ""
    return Path(filename) if filename else None


def _path_entry_exists(path: Path) -> bool:
    """Return true for every directory entry, including a broken symlink."""
    return os.path.lexists(path)


def _require_regular_file(path: Path, label: str) -> os.stat_result:
    """Reject missing, symlinked, and non-regular security artifacts."""
    try:
        state = path.lstat()
    except FileNotFoundError as exc:
        raise sqlite3.DatabaseError(f"{label} is missing: {path}") from exc
    if stat.S_ISLNK(state.st_mode):
        raise sqlite3.DatabaseError(
            f"{label} must be a regular file, not a symbolic link: {path}"
        )
    if not stat.S_ISREG(state.st_mode):
        raise sqlite3.DatabaseError(f"{label} must be a regular file: {path}")
    return state


def _ensure_backup_private(path: Path) -> None:
    _require_regular_file(path, "backup artifact")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    if _require_regular_file(path, "backup artifact").st_mode & 0o077:
        raise PermissionError(f"backup permissions are not owner-only: {path}")


def _fsync_file(path: Path) -> None:
    """Durably flush a regular file without following a replacement symlink."""
    _require_regular_file(path, "backup artifact")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise sqlite3.DatabaseError(f"backup artifact must be a regular file: {path}")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    """Flush directory entries where the host filesystem supports it."""
    unsupported = {
        value
        for value in (
            errno.EINVAL,
            getattr(errno, "ENOTSUP", None),
            getattr(errno, "EOPNOTSUPP", None),
        )
        if value is not None
    }
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        if exc.errno in unsupported:
            return
        raise
    try:
        try:
            os.fsync(descriptor)
        except OSError as exc:
            if exc.errno not in unsupported:
                raise
    finally:
        os.close(descriptor)


def _schema_tables(con: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }


def _schema_fingerprint(con: sqlite3.Connection) -> str:
    objects = [
        tuple(row)
        for row in con.execute(
            "SELECT type, name, tbl_name, COALESCE(sql, '') FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name, tbl_name, sql"
        ).fetchall()
    ]
    encoded = json.dumps(objects, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _fingerprint_value(value: object) -> list[str]:
    if value is None:
        return ["null", ""]
    if isinstance(value, bytes):
        return ["blob", base64.b64encode(value).decode()]
    if isinstance(value, float):
        return ["float", value.hex()]
    if isinstance(value, int):
        return ["integer", str(value)]
    if isinstance(value, str):
        return ["text", value]
    return [type(value).__name__, repr(value)]


def _quote_identifier(value: str) -> str:
    return f'"{value.replace(chr(34), chr(34) * 2)}"'


def _logical_database_fingerprint(con: sqlite3.Connection) -> str:
    tables: list[dict[str, object]] = []
    for table in sorted(_schema_tables(con)):
        quoted = _quote_identifier(table)
        columns = [
            str(row[1])
            for row in con.execute(f"PRAGMA table_info({quoted})").fetchall()
        ]
        rows = [
            [_fingerprint_value(value) for value in tuple(row)]
            for row in con.execute(f"SELECT * FROM {quoted}").fetchall()
        ]
        rows.sort(key=lambda row: json.dumps(row, separators=(",", ":")))
        tables.append({"name": table, "columns": columns, "rows": rows})
    payload = {
        "schema": _schema_fingerprint(con),
        "tables": tables,
        "user_version": int(con.execute("PRAGMA user_version").fetchone()[0]),
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _file_sha256(path: Path) -> str:
    _require_regular_file(path, "backup artifact")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_identity(path: Path) -> dict[str, int | str]:
    resolved = path.resolve()
    state = resolved.stat()
    identity = {
        "path": str(resolved),
        "device": int(state.st_dev),
        "inode": int(state.st_ino),
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    return {**identity, "sha256": hashlib.sha256(encoded).hexdigest()}


def _verify_sqlite_file(
    path: Path,
    expected_tables: set[str],
    *,
    expected_schema: str,
    expected_version: int,
) -> dict[str, str | int]:
    _require_regular_file(path, "pre-v2 backup")
    with sqlite3.connect(path) as candidate:
        result = candidate.execute("PRAGMA integrity_check").fetchone()
        actual_tables = _schema_tables(candidate)
        actual_version = int(candidate.execute("PRAGMA user_version").fetchone()[0])
        actual_schema = _schema_fingerprint(candidate)
        snapshot = _logical_database_fingerprint(candidate)
    if not result or result[0] != "ok":
        raise sqlite3.DatabaseError(f"backup integrity check failed for {path}")
    if actual_tables != expected_tables:
        raise sqlite3.DatabaseError(
            f"backup schema mismatch for {path}: "
            f"expected {sorted(expected_tables)}, got {sorted(actual_tables)}"
        )
    if actual_version >= _SCHEMA_VERSION or actual_version != expected_version:
        raise sqlite3.DatabaseError(
            f"backup is not the expected pre-v2 schema version for {path}: "
            f"expected {expected_version}, got {actual_version}"
        )
    if actual_schema != expected_schema:
        raise sqlite3.DatabaseError(f"backup schema fingerprint mismatch for {path}")
    return {
        "schema_sha256": actual_schema,
        "snapshot_sha256": snapshot,
        "user_version": actual_version,
    }


def _read_backup_manifest(path: Path) -> dict[str, object]:
    _require_regular_file(path, "source-linked backup manifest")
    _ensure_backup_private(path)
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise sqlite3.DatabaseError(f"backup manifest is invalid: {path}") from exc
    if not isinstance(manifest, dict):
        raise sqlite3.DatabaseError(f"backup manifest is invalid: {path}")
    return manifest


def _write_private_manifest(path: Path, manifest: dict[str, object]) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _build_backup_manifest(
    *,
    source_identity: dict[str, int | str],
    source_user_version: int,
    source_schema: str,
    snapshot: str,
    backup_path: Path,
) -> dict[str, object]:
    return {
        "manifest_version": _BACKUP_MANIFEST_VERSION,
        "source_identity_sha256": source_identity["sha256"],
        "source_path": source_identity["path"],
        "source_device": source_identity["device"],
        "source_inode": source_identity["inode"],
        "source_user_version": source_user_version,
        "source_schema_sha256": source_schema,
        "snapshot_sha256": snapshot,
        "backup_file_sha256": _file_sha256(backup_path),
    }


def _adopt_exact_unmanifested_backup(
    con: sqlite3.Connection,
    source_path: Path,
    destination: Path,
    manifest_path: Path,
    *,
    expected_tables: set[str],
    expected_schema: str,
    expected_version: int,
) -> None:
    """Provenance a crash-left backup only when it exactly matches the source."""
    backup = _verify_sqlite_file(
        destination,
        expected_tables,
        expected_schema=expected_schema,
        expected_version=expected_version,
    )
    _ensure_backup_private(destination)
    source_identity = _source_identity(source_path)
    source_snapshot = _logical_database_fingerprint(con)
    if backup["snapshot_sha256"] != source_snapshot:
        raise sqlite3.DatabaseError(
            f"unmanifested backup logical snapshot does not match source: {destination}"
        )

    nonce = uuid.uuid4().hex
    temporary_manifest = manifest_path.with_name(
        f".{manifest_path.name}.{nonce}.tmp"
    )
    try:
        manifest = _build_backup_manifest(
            source_identity=source_identity,
            source_user_version=expected_version,
            source_schema=expected_schema,
            snapshot=source_snapshot,
            backup_path=destination,
        )
        _write_private_manifest(temporary_manifest, manifest)
        _ensure_backup_private(temporary_manifest)
        _fsync_file(destination)
        _fsync_file(temporary_manifest)
        if _source_identity(source_path) != source_identity:
            raise sqlite3.DatabaseError("source identity changed during backup adoption")
        os.replace(temporary_manifest, manifest_path)
        _fsync_file(manifest_path)
        _fsync_directory(manifest_path.parent)
        _validate_existing_pre_v2_backup(
            con, source_path, destination, manifest_path
        )
    except Exception:
        temporary_manifest.unlink(missing_ok=True)
        raise


def _validate_existing_pre_v2_backup(
    con: sqlite3.Connection,
    source_path: Path,
    destination: Path,
    manifest_path: Path,
) -> None:
    expected_tables = _schema_tables(con)
    expected_schema = _schema_fingerprint(con)
    expected_version = int(con.execute("PRAGMA user_version").fetchone()[0])
    backup = _verify_sqlite_file(
        destination,
        expected_tables,
        expected_schema=expected_schema,
        expected_version=expected_version,
    )
    _ensure_backup_private(destination)
    manifest = _read_backup_manifest(manifest_path)
    identity = _source_identity(source_path)
    required = {
        "manifest_version": _BACKUP_MANIFEST_VERSION,
        "source_identity_sha256": identity["sha256"],
        "source_path": identity["path"],
        "source_device": identity["device"],
        "source_inode": identity["inode"],
        "source_user_version": expected_version,
        "source_schema_sha256": expected_schema,
        "snapshot_sha256": backup["snapshot_sha256"],
        "backup_file_sha256": _file_sha256(destination),
    }
    for key, expected in required.items():
        if manifest.get(key) != expected:
            label = "source identity" if key.startswith("source_") else "content"
            raise sqlite3.DatabaseError(
                f"backup manifest {label} mismatch for {destination}: {key}"
            )


def _ensure_pre_v2_backup(con: sqlite3.Connection) -> None:
    """Create one source-linked, owner-only backup before legacy schema changes."""
    source_path = _database_path(con)
    if source_path is None:
        return
    destination = _pre_v2_backup_path(source_path)
    manifest_path = _pre_v2_manifest_path(source_path)
    expected_tables = _schema_tables(con)
    expected_schema = _schema_fingerprint(con)
    expected_version = int(con.execute("PRAGMA user_version").fetchone()[0])
    if _path_entry_exists(destination):
        _require_regular_file(destination, "pre-v2 backup")
        if _path_entry_exists(manifest_path):
            _validate_existing_pre_v2_backup(
                con, source_path, destination, manifest_path
            )
        else:
            _adopt_exact_unmanifested_backup(
                con,
                source_path,
                destination,
                manifest_path,
                expected_tables=expected_tables,
                expected_schema=expected_schema,
                expected_version=expected_version,
            )
        return
    if _path_entry_exists(manifest_path):
        _require_regular_file(manifest_path, "source-linked backup manifest")
        raise sqlite3.DatabaseError(
            f"backup manifest exists without its source-linked backup: {manifest_path}"
        )

    nonce = uuid.uuid4().hex
    temporary = destination.with_name(f".{destination.name}.{nonce}.tmp")
    temporary_manifest = manifest_path.with_name(f".{manifest_path.name}.{nonce}.tmp")
    source_identity = _source_identity(source_path)
    source_snapshot = _logical_database_fingerprint(con)
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        os.close(descriptor)
        with (
            sqlite3.connect(source_path, timeout=_BUSY_TIMEOUT_MS / 1_000) as source,
            sqlite3.connect(temporary) as backup,
        ):
            source.backup(backup)
        verified = _verify_sqlite_file(
            temporary,
            expected_tables,
            expected_schema=expected_schema,
            expected_version=expected_version,
        )
        if verified["snapshot_sha256"] != source_snapshot:
            raise sqlite3.DatabaseError(
                f"backup logical snapshot does not match source: {temporary}"
            )
        if _source_identity(source_path) != source_identity:
            raise sqlite3.DatabaseError("source identity changed during backup")
        manifest = _build_backup_manifest(
            source_identity=source_identity,
            source_user_version=expected_version,
            source_schema=expected_schema,
            snapshot=source_snapshot,
            backup_path=temporary,
        )
        _write_private_manifest(temporary_manifest, manifest)
        _ensure_backup_private(temporary)
        _ensure_backup_private(temporary_manifest)
        _fsync_file(temporary)
        _fsync_file(temporary_manifest)
        os.replace(temporary, destination)
        _fsync_file(destination)
        _fsync_directory(destination.parent)
        os.replace(temporary_manifest, manifest_path)
        _fsync_file(manifest_path)
        _fsync_directory(manifest_path.parent)
        _ensure_backup_private(destination)
        _ensure_backup_private(manifest_path)
        _validate_existing_pre_v2_backup(
            con, source_path, destination, manifest_path
        )
    except Exception:
        temporary.unlink(missing_ok=True)
        temporary_manifest.unlink(missing_ok=True)
        raise


def _has_existing_schema(con: sqlite3.Connection) -> bool:
    return bool(_schema_tables(con))


def _ensure_core_schema(con: sqlite3.Connection) -> None:
    for statement in _CORE_DDL:
        con.execute(statement)


def _create_child_table(con: sqlite3.Connection, table: str, target: str) -> None:
    con.execute(_CHILD_TABLE_DDL[table].format(table=target))


def _has_user_id_cascade(con: sqlite3.Connection, table: str) -> bool:
    return any(
        row[2] == "users"
        and row[3] == "user_id"
        and row[4] == "user_id"
        and row[6].upper() == "CASCADE"
        for row in con.execute(f"PRAGMA foreign_key_list({table})").fetchall()
    )


def _has_unique_user_id_index(con: sqlite3.Connection) -> bool:
    for row in con.execute("PRAGMA index_list(users)").fetchall():
        if row[1] != "ux_users_user_id" or not bool(row[2]) or bool(row[4]):
            continue
        columns = [
            info[2]
            for info in con.execute(
                f"PRAGMA index_info({_quote_identifier(str(row[1]))})"
            ).fetchall()
        ]
        return columns == ["user_id"]
    return False


def _schema_v2_ready(con: sqlite3.Connection) -> bool:
    version = int(con.execute("PRAGMA user_version").fetchone()[0])
    if version < _SCHEMA_VERSION:
        return False

    tables = {
        row["name"]
        for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    if not {"users", "used_magic_tokens", "auth_metadata", *_CHILD_TABLE_DDL}.issubset(
        tables
    ):
        return False

    user_columns = {
        row["name"] for row in con.execute("PRAGMA table_info(users)").fetchall()
    }
    if not {"user_id", *_BILLING_COLUMNS}.issubset(user_columns):
        return False
    if con.execute(
        "SELECT 1 FROM users WHERE user_id IS NULL OR trim(user_id)='' LIMIT 1"
    ).fetchone():
        return False
    invalid_user_ids = [
        str(row["user_id"])
        for row in con.execute("SELECT user_id FROM users ORDER BY id").fetchall()
        if not _is_canonical_user_id(row["user_id"])
    ]
    if invalid_user_ids:
        raise sqlite3.DatabaseError(
            f"schema-v2 users contain non-canonical UUID values: {invalid_user_ids}"
        )
    duplicate = con.execute(
        "SELECT user_id FROM users GROUP BY user_id HAVING COUNT(*) > 1 LIMIT 1"
    ).fetchone()
    if duplicate:
        raise sqlite3.DatabaseError(
            f"schema-v2 users contain duplicate user_id: {duplicate['user_id']}"
        )

    initialized = con.execute(
        "SELECT value FROM auth_metadata WHERE key=?",
        (_OWNER_INITIALIZED_KEY,),
    ).fetchone()
    if not initialized or initialized["value"] != "1":
        return False
    owner_marker = con.execute(
        "SELECT value FROM auth_metadata WHERE key=?",
        (_OWNER_METADATA_KEY,),
    ).fetchone()
    if owner_marker and not _is_canonical_user_id(owner_marker["value"]):
        raise sqlite3.DatabaseError("owner marker is not a canonical UUID")

    indexes = {
        row["name"]
        for row in con.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
    }
    required_indexes = {
        "ux_users_user_id",
        "idx_customer_analyses_user_id",
        "idx_usage_events_at",
        "ux_mybook_jobs_active_user",
        "idx_usage_reservations_window",
    }
    if not required_indexes.issubset(indexes):
        return False
    if not _has_unique_user_id_index(con):
        raise sqlite3.DatabaseError("schema-v2 users lack a valid unique user_id index")

    for table in _CHILD_TABLE_DDL:
        columns = {
            row["name"]
            for row in con.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if (
            "user_id" not in columns
            or "email" in columns
            or not _has_user_id_cascade(con, table)
        ):
            return False

    violations = con.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        details = [tuple(row) for row in violations]
        raise sqlite3.IntegrityError(f"foreign key check failed: {details}")
    return True


def _assert_no_orphaned_child_rows(
    con: sqlite3.Connection, table: str, source_owner: str
) -> None:
    join = (
        "owner.email = old.email"
        if source_owner == "email"
        else "owner.user_id = old.user_id"
    )
    count = con.execute(
        f"SELECT COUNT(*) FROM {table} AS old LEFT JOIN users AS owner ON {join} "
        "WHERE owner.user_id IS NULL"
    ).fetchone()[0]
    if count:
        raise sqlite3.IntegrityError(
            f"cannot migrate {table}: {count} orphaned {source_owner} row(s)"
        )


def _rebuild_child_table(
    con: sqlite3.Connection, table: str, source_owner: str
) -> None:
    _assert_no_orphaned_child_rows(con, table, source_owner)
    temporary = f"_{table}_user_id_v2"
    con.execute(f"DROP TABLE IF EXISTS {temporary}")
    _create_child_table(con, table, temporary)

    data_columns = _CHILD_DATA_COLUMNS[table]
    insert_columns = ", ".join(("user_id", *data_columns))
    selected_data = ", ".join(f"old.{column}" for column in data_columns)
    join = (
        "users AS owner ON owner.email = old.email"
        if source_owner == "email"
        else "users AS owner ON owner.user_id = old.user_id"
    )
    con.execute(
        f"INSERT INTO {temporary} ({insert_columns}) "
        f"SELECT owner.user_id, {selected_data} FROM {table} AS old JOIN {join}"
    )
    con.execute(f"DROP TABLE {table}")
    con.execute(f"ALTER TABLE {temporary} RENAME TO {table}")


def _migrate_to_user_ids(con: sqlite3.Connection) -> None:
    """Atomically migrate mutable email ownership to immutable UUID user IDs.

    Normal schema-v2 reads take the lock-free fast path. A genuine migration re-checks
    under ``BEGIN IMMEDIATE``, creates the one verified pre-v2 backup, and fails closed
    before copying if any child row lacks a real owner.
    """
    # Only an already-v2 database may gain this table here. Creating it first
    # makes an empty database look pre-existing, so the migration writes a
    # pre-v2 backup and then rejects that backup for a schema mismatch.
    if _schema_v2_ready(con):
        _ensure_revoked_sessions(con)
        return

    con.execute("BEGIN IMMEDIATE")
    try:
        # Another process may have completed migration while this connection waited
        # for the writer lock. Re-check before backing up or changing any schema.
        if _schema_v2_ready(con):
            _ensure_revoked_sessions(con)
            con.commit()
            return

        version = int(con.execute("PRAGMA user_version").fetchone()[0])
        if version < _SCHEMA_VERSION and _has_existing_schema(con):
            _ensure_pre_v2_backup(con)

        _ensure_core_schema(con)
        _migrate(con)
        _prune_used_magic_tokens(con)

        user_columns = {
            row["name"] for row in con.execute("PRAGMA table_info(users)").fetchall()
        }
        if "user_id" not in user_columns:
            con.execute("ALTER TABLE users ADD COLUMN user_id TEXT")

        seen: set[str] = set()
        for row in con.execute("SELECT id, user_id FROM users ORDER BY id").fetchall():
            user_id = str(row["user_id"] or "").strip()
            if not _is_canonical_user_id(user_id) or user_id in seen:
                user_id = _new_user_id()
                con.execute("UPDATE users SET user_id=? WHERE id=?", (user_id, row["id"]))
            seen.add(user_id)
        if not _has_unique_user_id_index(con):
            con.execute("DROP INDEX IF EXISTS ux_users_user_id")
            con.execute("CREATE UNIQUE INDEX ux_users_user_id ON users(user_id)")
        _ensure_owner_marker(con)

        for table in _CHILD_TABLE_DDL:
            columns = {
                row["name"] for row in con.execute(f"PRAGMA table_info({table})").fetchall()
            }
            if not columns:
                _create_child_table(con, table, table)
                continue
            if (
                "user_id" in columns
                and "email" not in columns
                and _has_user_id_cascade(con, table)
            ):
                continue
            if "email" in columns:
                _rebuild_child_table(con, table, "email")
            elif "user_id" in columns:
                _rebuild_child_table(con, table, "user_id")
            else:
                raise sqlite3.DatabaseError(
                    f"cannot migrate {table}: expected email or user_id owner column"
                )

        con.execute("DROP INDEX IF EXISTS idx_customer_analyses_email")
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_customer_analyses_user_id "
            "ON customer_analyses (user_id, created_at)"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_usage_events_at ON usage_events (at)"
        )
        con.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_mybook_jobs_active_user "
            "ON mybook_jobs(user_id) WHERE status IN ('queued','running')"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_usage_reservations_window "
            "ON usage_reservations(user_id, event, state, reserved_at)"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_customer_equity_snapshots_window "
            "ON customer_equity_snapshots(user_id, symbol, snapshot_at DESC)"
        )
        con.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
        _ensure_revoked_sessions(con)

        violations = con.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            details = [tuple(row) for row in violations]
            raise sqlite3.IntegrityError(f"foreign key check failed: {details}")
        con.commit()
    except Exception:
        con.rollback()
        raise


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
def _conn() -> Iterator[sqlite3.Connection]:
    con = sqlite3.connect(AUTH_DB_PATH, timeout=_BUSY_TIMEOUT_MS / 1_000)
    con.row_factory = sqlite3.Row
    try:
        con.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
        con.execute("PRAGMA foreign_keys = ON")
        _migrate_to_user_ids(con)
        _harden_db_perms()
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
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
    user_id = _new_user_id()
    try:
        with _conn() as con:
            cur = con.execute(
                "INSERT INTO users "
                "(user_id, email, password_hash, is_active, created_at) VALUES (?,?,?,1,?)",
                (user_id, em, hash_password(password), now),
            )
            _claim_owner_marker(con, em, user_id)
            return cur.lastrowid
    except sqlite3.IntegrityError as e:
        raise ValueError(f"user already exists: {em}") from e


def get_user(email: str) -> dict[str, Any] | None:
    with _conn() as con:
        row = con.execute("SELECT * FROM users WHERE email=?", (_norm(email),)).fetchone()
    return dict(row) if row else None


def resolve_user_id(email: str) -> str | None:
    """Resolve an active account's mutable email to its immutable tenant key."""
    with _conn() as con:
        row = con.execute(
            "SELECT user_id FROM users WHERE email=? AND is_active=1", (_norm(email),)
        ).fetchone()
    return str(row["user_id"]) if row else None


def get_user_by_id(user_id: str) -> dict[str, Any] | None:
    if not _is_canonical_user_id(user_id):
        return None
    with _conn() as con:
        row = con.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
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
    """Rename only the user profile; UUID-owned child rows remain unchanged."""
    old, new = _norm(old_email), _norm(new_email)
    if not old or not new or old == new:
        return False
    with _conn() as con:
        clash = con.execute("SELECT 1 FROM users WHERE email=?", (new,)).fetchone()
        if clash:
            return False
        cur = con.execute("UPDATE users SET email=? WHERE email=?", (new, old))
        return cur.rowcount == 1


# A valid-format PBKDF2 hash of a random secret nobody holds. Verifying a
# submitted password against THIS when the account is missing/inactive makes the
# unknown-user path do the same 200k-round work as a real wrong-password miss —
# closing the timing side-channel that would otherwise reveal which emails exist.
_DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(32))


def verify_credentials_constant_time(email: str, password: str) -> bool:
    """True only when the user exists, is active, and the password matches.

    Runs one PBKDF2 verification on EVERY call — even for an unknown or inactive
    account (against a throwaway dummy hash) — so response time never leaks whether
    the email is registered (username enumeration)."""
    u = get_user(email)
    if not u or not u["is_active"]:
        verify_password(password, _DUMMY_PASSWORD_HASH)  # constant-time decoy; result discarded
        return False
    return verify_password(password, u["password_hash"])


def verify_credentials(email: str, password: str) -> bool:
    """Backward-compatible name for the constant-work credential verifier."""
    return verify_credentials_constant_time(email, password)


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


def provision_magic_link_user(
    email: str,
    *,
    tier: str | None,
    customer_id: str | None,
    sub_id: str | None,
    status: str,
    event_at: str | None = None,
) -> bool:
    """Atomically create/reactivate a subscriber and bind its billing identity.

    Taking the write lock before reading prevents concurrent webhook workers from both
    creating active users and only later racing on the unique subscription-id index. If
    that constraint rejects this account, the entire activation/insert is rolled back.
    Returns True when a new row was created.
    """
    em = _norm(email)
    if not em:
        raise ValueError("email required")

    with _conn() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT 1 FROM users WHERE email=?", (em,)).fetchone()
        created = row is None
        if created:
            user_id = _new_user_id()
            con.execute(
                "INSERT INTO users "
                "(user_id, email, password_hash, is_active, created_at) VALUES (?,?,?,1,?)",
                (
                    user_id,
                    em,
                    hash_password(secrets.token_urlsafe(24)),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            _claim_owner_marker(con, em, user_id)

        sets = ["is_active=1"]
        params: list[Any] = []
        for col, val in (
            ("tier", tier),
            ("billing_customer_id", customer_id),
            ("billing_sub_id", sub_id),
            ("billing_status", status),
            ("billing_event_at", event_at),
        ):
            if val is not None:
                sets.append(f"{col}=?")
                params.append(val)
        params.append(em)
        con.execute(f"UPDATE users SET {', '.join(sets)} WHERE email=?", params)
        return created


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
            "SELECT id, email, is_active, tier, created_at, last_login_at FROM users ORDER BY created_at"
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


# ── email preferences (signal alerts / weekly digest opt-out) ──────────────────

def _ensure_user_row(con, em: str) -> None:
    """Guarantee a `users` row exists for `em` so a preference UPDATE isn't a
    silent no-op. Pre-provision alpha customers only exist via watchlist/book
    rows (add_watch/set_position never touch `users`) — an unusable random
    password satisfies the NOT NULL constraint; they still authenticate via
    magic link / billing provisioning, never this placeholder."""
    if con.execute("SELECT 1 FROM users WHERE email=?", (em,)).fetchone():
        return   # common case — skip the 200k-round PBKDF2 hash entirely
    con.execute(
        "INSERT OR IGNORE INTO users (email, password_hash, is_active, created_at) "
        "VALUES (?,?,1,?)",
        (em, hash_password(secrets.token_urlsafe(24)), datetime.now(timezone.utc).isoformat()))


def set_alerts_opt_out(email: str, flag: bool) -> None:
    em = _norm(email)
    if not em:
        return
    with _conn() as con:
        # users row may not exist for pre-provision alpha emails — upsert-lite
        _ensure_user_row(con, em)
        con.execute("UPDATE users SET alerts_opt_out=? WHERE email=?", (int(flag), em))


def get_alerts_opt_out(email: str) -> bool:
    em = _norm(email)
    with _conn() as con:
        row = con.execute("SELECT alerts_opt_out FROM users WHERE email=?", (em,)).fetchone()
    return bool(row and row["alerts_opt_out"])


def set_digest_opt_out(email: str, flag: bool) -> None:
    em = _norm(email)
    if not em:
        return
    with _conn() as con:
        _ensure_user_row(con, em)
        con.execute("UPDATE users SET digest_opt_out=? WHERE email=?", (int(flag), em))


def get_digest_opt_out(email: str) -> bool:
    em = _norm(email)
    with _conn() as con:
        row = con.execute("SELECT digest_opt_out FROM users WHERE email=?", (em,)).fetchone()
    return bool(row and row["digest_opt_out"])


def all_customer_symbols() -> set[str]:
    """Union of every user's watchlist + book symbols — the alert/snapshot universe."""
    with _conn() as con:
        w = con.execute("SELECT DISTINCT symbol FROM watchlist").fetchall()
        p = con.execute("SELECT DISTINCT symbol FROM customer_positions").fetchall()
    return {r["symbol"] for r in w} | {r["symbol"] for r in p}


def customers_watching(symbol: str) -> list[str]:
    """Active, non-opted-out emails with `symbol` on watchlist OR book."""
    sym = str(symbol).upper().strip()
    with _conn() as con:
        # [CLAUDE 2026-07-18] immutable-user-ID migration: child tables key on
        # user_id now — join through users to recover the current email.
        rows = con.execute(
            "SELECT DISTINCT u.email FROM ("
            "  SELECT user_id, symbol FROM watchlist"
            "  UNION SELECT user_id, symbol FROM customer_positions) s "
            "JOIN users u ON u.user_id = s.user_id "
            "WHERE s.symbol=? AND COALESCE(u.alerts_opt_out, 0)=0 "
            "  AND COALESCE(u.is_active, 1)=1 ORDER BY u.email", (sym,)).fetchall()
    return [r["email"] for r in rows]


# ── gate decision (pure, testable) ──────────────────────────────────────────────

def is_public_path(path: str) -> bool:
    """Prefix match on a path boundary: '/api/congress' exposes '/api/congress' and
    '/api/congress/NVDA' but NOT '/api/congress-admin'. Prefixes ending in '/'
    (e.g. '/static/') keep raw-prefix semantics. A future adjacent route must never
    silently inherit a public/auth bypass."""
    if path in PUBLIC_EXACT_PATHS:
        return True
    for p in PUBLIC_PREFIXES:
        if p.endswith("/"):
            if path.startswith(p):
                return True
        elif path == p or path.startswith(p + "/"):
            return True
    return False


def is_owner(email: str | None) -> bool:
    """True for the account that owns the underlying brokerage book.

    Everyone else is a CUSTOMER session: the server masks absolute dollars /
    share quantities (normalized to a model book) and owner-only views
    (/report, /invest, /progress) are refused. When auth is enabled the durable UUID
    marker is authoritative; auth-disabled local development retains email behavior.
    """
    em = _norm(email or "")
    if not em:
        return False
    if not auth_enabled():
        return em == _configured_owner_email()
    with _conn() as con:
        row = con.execute(
            "SELECT users.user_id FROM users "
            "JOIN auth_metadata ON auth_metadata.key=? "
            "AND auth_metadata.value=users.user_id "
            "WHERE users.email=? AND users.is_active=1",
            (_OWNER_METADATA_KEY, em),
        ).fetchone()
    return row is not None


def is_owner_account(email: str | None) -> bool:
    """Owner identity that still holds for a DISABLED account.

    is_owner() deliberately requires is_active=1: it answers "should this SESSION see
    unmasked owner views?", and a revoked session must not. But a guard whose job is to
    PREVENT the owner being disabled cannot be built on it — the moment is_active flips
    to 0 the marker stops resolving and the guard silently stops firing, exactly when it
    is needed. Same durable-UUID vs configured-email resolution as is_owner(), minus the
    liveness clause. Use for revocation/deletion fences, never for view permissions.
    """
    em = _norm(email or "")
    if not em:
        return False
    if not auth_enabled():
        return em == _configured_owner_email()
    with _conn() as con:
        row = con.execute(
            "SELECT users.user_id FROM users "
            "JOIN auth_metadata ON auth_metadata.key=? "
            "AND auth_metadata.value=users.user_id "
            "WHERE users.email=?",
            (_OWNER_METADATA_KEY, em),
        ).fetchone()
    return row is not None


def consume_magic_token(token: str | None) -> str | None:
    """Single-use validation returning the immutable subject's current email on the
    FIRST valid use and atomically marks the token spent; any later use (or an invalid/
    expired token) returns None. This prevents a leaked link (browser history, Referer,
    proxy logs) from being replayed: it works exactly once, then is dead.

    Note: the magic token is NOT the session cookie — it is typ='magic' and is rejected by
    authenticate_session, and the caller mints a fresh typ='session' token after consuming
    this one, so URL exposure never yields a reusable session."""
    user_id = verify_token(token, expected_typ="magic")
    if not user_id:
        return None
    th = hashlib.sha256(token.encode()).hexdigest()
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        user = con.execute(
            "SELECT email FROM users WHERE user_id=? AND is_active=1",
            (user_id,),
        ).fetchone()
        if not user:
            return None
        _prune_used_magic_tokens(con)
        cur = con.execute(
            "INSERT OR IGNORE INTO used_magic_tokens (token_hash, used_at) VALUES (?,?)",
            (th, now),
        )
        if cur.rowcount == 0:        # already present → token was already consumed
            return None
    return str(user["email"])


def _usable_jti(value: object) -> str | None:
    """A non-empty session id. Whitespace-only / non-str cannot be blacklisted."""
    if not isinstance(value, str):
        return None
    jti = value.strip()
    return jti or None


def revoke_session(token: str | None) -> str:
    """Blacklist one still-valid session. Returns ``revoked``, ``absent``, or ``failed``.

    Missing, invalid, expired, and non-session tokens are ``absent`` and are not
    written. A valid session with no ``jti`` cannot be blacklisted (``failed``).
    Revoking the same still-valid token again stays ``revoked``.
    """
    claims = _verified_claims(token, expected_typ="session")
    if not claims:
        return "absent"
    jti = _usable_jti(claims.get("jti"))
    if not jti:
        return "failed"
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        _prune_revoked_sessions(con)
        con.execute(
            "INSERT OR IGNORE INTO revoked_sessions "
            "(jti, user_id, expires_at, revoked_at) VALUES (?,?,?,?)",
            (jti, str(claims["sub"]), int(claims.get("exp", 0)), now),
        )
    return "revoked"


def rotate_session(email: str) -> str:
    """Mint a session and retire every other session for this user.

    Sessions this process recorded are blacklisted by ``jti``. A token from
    before rotation has no ``iat`` and fails once ``session_not_before`` is set.
    The new token's ``iat`` is that watermark, so it stays valid.
    """
    token = issue_token(email, typ="session")
    claims = _verified_claims(token, expected_typ="session")
    if not claims:
        raise ValueError("issued session could not be verified")
    jti = _usable_jti(claims.get("jti"))
    issued = claims.get("iat")
    if not jti or isinstance(issued, bool) or not isinstance(issued, int):
        raise ValueError("issued session is missing jti or iat")
    user_id = str(claims["sub"])
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        con.execute(
            "INSERT OR REPLACE INTO user_sessions (jti, user_id, expires_at) VALUES (?,?,?)",
            (jti, user_id, int(claims["exp"])),
        )
        others = con.execute(
            "SELECT jti, expires_at FROM user_sessions WHERE user_id=? AND jti!=?",
            (user_id, jti),
        ).fetchall()
        for row in others:
            con.execute(
                "INSERT OR IGNORE INTO revoked_sessions "
                "(jti, user_id, expires_at, revoked_at) VALUES (?,?,?,?)",
                (str(row["jti"]), user_id, int(row["expires_at"]), now),
            )
        con.execute("DELETE FROM user_sessions WHERE user_id=? AND jti!=?", (user_id, jti))
        con.execute(
            "UPDATE users SET session_not_before=? WHERE user_id=?",
            (issued, user_id),
        )
    return token


def authenticate_session(token: str | None) -> str | None:
    """
    Full session check used by the gate on every request: a cryptographically
    valid, unexpired token AND a subject that is still a CURRENT, ACTIVE user
    AND a ``jti`` that has not been revoked by logout.

    This makes set_active(False) (revocation — e.g. churn / non-payment), user
    deletion, and logout take effect on the very next request, instead of leaving
    a signed cookie valid until it expires. (verify_token alone only proves the
    token was issued by us and hasn't expired.)

    A session with no ``jti`` fails closed: it cannot be blacklisted. Only
    typ='session' tokens are accepted — a leaked typ='magic' URL token cannot be
    replayed here as a session cookie (H1). A database error propagates so the
    request fails closed instead of treating an unread blacklist as a pass.
    """
    claims = _verified_claims(token, expected_typ="session")
    if not claims:
        return None
    jti = _usable_jti(claims.get("jti"))
    if not jti:
        return None
    issued = claims.get("iat")
    with _conn() as con:
        marker = con.execute(
            "SELECT session_not_before FROM users WHERE user_id=?",
            (str(claims["sub"]),),
        ).fetchone()
        revoked = con.execute(
            "SELECT 1 FROM revoked_sessions WHERE jti=? LIMIT 1",
            (jti,),
        ).fetchone()
    if marker is not None and marker["session_not_before"] is not None:
        not_before = marker["session_not_before"]
        if (
            isinstance(issued, bool)
            or not isinstance(issued, int)
            or isinstance(not_before, bool)
            or not isinstance(not_before, int)
            or issued < not_before
        ):
            return None
    if revoked:
        return None
    u = get_user_by_id(str(claims["sub"]))
    if not u or not u["is_active"]:
        return None
    return str(u["email"])


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
    if cmd == "tier" and len(argv) >= 3:
        email, tier = argv[1], argv[2].strip().lower()
        valid = {"observer", "trader", "desk"}
        if tier not in valid:
            print(f"✗ tier must be one of {sorted(valid)}")
            return 1
        if not get_user(email):
            print(f"✗ no such user: {email}")
            return 1
        set_billing(email, tier=tier)
        print(f"✓ {email} tier set to {tier} (LLM quotas now "
              f"{ {'observer': '1×', 'trader': '2×', 'desk': '5×'}[tier] })")
        return 0
    if cmd == "list":
        users = list_users()
        if not users:
            print("(no users)")
        for u in users:
            state = "active" if u["is_active"] else "DISABLED"
            print(f"  {u['email']:32} {state:9} tier={u.get('tier') or '—':8} "
                  f"created {u['created_at'][:10]} last_login={u['last_login_at'] or '—'}")
        print(f"\nWall: {'ENABLED' if auth_enabled() else 'OFF (set GESH_AUTH_ENABLED=1)'}")
        return 0
    print(__doc__)
    return 1


# ── per-user watchlist (customer analyze-any-ticker: saved symbols) ────────────

_MAX_WATCH = 50   # per-user cap — abuse guard


def add_watch(email: str, symbol: str) -> bool:
    """Add a symbol to the user's watchlist. Idempotent (dup → True, no duplicate);
    returns False only when the per-user cap is hit. Symbols stored UPPER."""
    user_id = resolve_user_id(email)
    sym = (symbol or "").strip().upper()
    if not user_id or not sym:
        return False
    with _conn() as con:
        if con.execute(
            "SELECT 1 FROM watchlist WHERE user_id=? AND symbol=?", (user_id, sym)
        ).fetchone():
            return True
        # Single-statement cap check: a separate count-then-insert could exceed the
        # cap under concurrent adds. OR IGNORE absorbs a concurrent duplicate insert.
        cur = con.execute(
            "INSERT OR IGNORE INTO watchlist (user_id, symbol, added_at) "
            "SELECT ?,?,? WHERE (SELECT COUNT(*) FROM watchlist WHERE user_id=?) < ?",
            (user_id, sym, datetime.now(timezone.utc).isoformat(), user_id, _MAX_WATCH),
        )
        if cur.rowcount:
            return True
        # rowcount 0 = cap hit, or a concurrent add of the same symbol won the
        # race — the latter is still a success (the symbol IS on the list).
        return con.execute(
            "SELECT 1 FROM watchlist WHERE user_id=? AND symbol=?", (user_id, sym)
        ).fetchone() is not None


def remove_watch(email: str, symbol: str) -> None:
    user_id = resolve_user_id(email)
    sym = (symbol or "").strip().upper()
    if not user_id:
        return
    with _conn() as con:
        con.execute("DELETE FROM watchlist WHERE user_id=? AND symbol=?", (user_id, sym))


def get_watch(email: str) -> list[str]:
    """The user's saved symbols, oldest-first. Empty list for an unknown user."""
    user_id = resolve_user_id(email)
    if not user_id:
        return []
    with _conn() as con:
        rows = con.execute(
            "SELECT symbol FROM watchlist WHERE user_id=? ORDER BY added_at, symbol", (user_id,)
        ).fetchall()
    return [r["symbol"] for r in rows]


# ── per-user portfolio book (customer analyze-MY-portfolio: their own positions) ──
# The customer's OWN book — symbol + share count (+ optional avg cost), entered by
# them, valued live at read time. Contains ZERO owner data. Same isolation contract
# as the watchlist: the email interface resolves once to an immutable tenant ID.

_MAX_POSITIONS = 100   # per-user cap — abuse guard (also bounds analyze-engine cost).
#                        Raised 30→100 (2026-07-10) to fit a real book via bulk import.


def set_position(email: str, symbol: str, shares: float, avg_cost: float | None = None) -> bool:
    """Upsert one position on the user's book. Returns False on invalid input or
    when adding a NEW symbol would exceed the cap (updating an existing one is
    always allowed). Symbols stored UPPER; shares must be > 0 (use
    remove_position to delete).

    On update, ``avg_cost=None`` keeps the stored cost basis. Callers that want
    to change cost must pass an explicit number (including 0).
    """
    user_id = resolve_user_id(email)
    sym = (symbol or "").strip().upper()
    try:
        # JSON/Python bools are ints: True must not become 1 share or $1 cost,
        # and False must not overwrite a stored basis with $0.
        if isinstance(shares, bool) or isinstance(avg_cost, bool):
            return False
        sh = float(shares)
        ac = None if avg_cost in (None, "") else float(avg_cost)
    except (TypeError, ValueError, OverflowError):
        return False
    if (
        not user_id
        or not sym
        or not math.isfinite(sh)
        or not (0 < sh < 1e9)
        or (ac is not None and (not math.isfinite(ac) or not (0 <= ac < 1e9)))
    ):
        return False
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        # Atomic single-statement update: preserves stored avg_cost if ac is None,
        # eliminating lost-update concurrency races between share and cost writes.
        cur = con.execute(
            "UPDATE customer_positions "
            "SET shares = ?, avg_cost = COALESCE(?, avg_cost), updated_at = ? "
            "WHERE user_id = ? AND symbol = ?",
            (sh, ac, now, user_id, sym),
        )
        if cur.rowcount:
            return True
        # Single-statement cap check (same concurrency shape as add_watch).
        cur = con.execute(
            "INSERT OR IGNORE INTO customer_positions "
            "(user_id, symbol, shares, avg_cost, updated_at) "
            "SELECT ?,?,?,?,? WHERE "
            "(SELECT COUNT(*) FROM customer_positions WHERE user_id=?) < ?",
            (user_id, sym, sh, ac, now, user_id, _MAX_POSITIONS),
        )
        if cur.rowcount:
            return True
        return con.execute(
            "SELECT 1 FROM customer_positions WHERE user_id=? AND symbol=?", (user_id, sym)
        ).fetchone() is not None


def remove_position(email: str, symbol: str) -> None:
    user_id = resolve_user_id(email)
    sym = (symbol or "").strip().upper()
    if not user_id:
        return
    with _conn() as con:
        con.execute(
            "DELETE FROM customer_positions WHERE user_id=? AND symbol=?", (user_id, sym)
        )


def clear_positions(email: str) -> None:
    """Remove ALL positions for the user and exit equal-weight mode.
    Use this when the user wants a clean slate (e.g. after a symbols-only bulk import
    they now want to replace with real share counts)."""
    user_id = resolve_user_id(email)
    if not user_id:
        return
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        con.execute("DELETE FROM customer_positions WHERE user_id=?", (user_id,))
        con.execute(
            "INSERT INTO customer_book_prefs (user_id, equal_weight, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET equal_weight=excluded.equal_weight, "
            "updated_at=excluded.updated_at",
            (user_id, 0, now),
        )


def get_positions(email: str) -> list[dict]:
    """The user's book, oldest-first: [{symbol, shares, avg_cost}]. Empty list for
    an unknown user."""
    user_id = resolve_user_id(email)
    if not user_id:
        return []
    with _conn() as con:
        rows = con.execute(
            "SELECT symbol, shares, avg_cost FROM customer_positions "
            "WHERE user_id=? ORDER BY updated_at, symbol", (user_id,)
        ).fetchall()
    return [{"symbol": r["symbol"], "shares": r["shares"], "avg_cost": r["avg_cost"]}
            for r in rows]


# ── per-user equity history (the customer ECG's only data source) ────────────
# The owner's `equity_snapshots` table (gesh_financial.db) is keyed by SYMBOL
# ALONE: get_equity_history("AMD") returns the OWNER's curve to whoever asks, so
# it can never feed a customer surface. These rows are the per-user counterpart,
# written from customer_positions valued at public closes and read back scoped to
# one tenant. Nothing here ever touches the owner table.

_MAX_EQUITY_SNAPSHOTS_PER_SYMBOL = 400   # ~18 trading months at one daily snapshot
_MAX_EQUITY_HISTORY_DAYS = 3_650


def _finite_positive(value: object) -> float | None:
    """A real observation, or None. price<=0 / NaN is MISSING data, never a loss —
    persisting sentinels is what read back as a fake -96% crash on the owner curve
    (audit D-07)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and number > 0 else None


def record_equity_snapshots(
    email: str, rows: list[dict], at: str | None = None
) -> int:
    """Append one equity observation per row for this user. Returns rows written.

    `rows` are {symbol, equity, current_price}. Rows whose equity or price is not
    finite and positive are dropped rather than stored. `at` (ISO-8601) overrides
    the timestamp so a backfill or a test can place a row deterministically;
    re-recording the same (user, symbol, timestamp) replaces it rather than
    doubling the session count."""
    user_id = resolve_user_id(email)
    if not user_id or not rows:
        return 0
    stamp = str(at) if at else datetime.now(timezone.utc).isoformat()
    clean: list[tuple[str, str, str, float, float]] = []
    for row in rows:
        symbol = str((row or {}).get("symbol") or "").strip().upper()
        equity = _finite_positive((row or {}).get("equity"))
        price = _finite_positive((row or {}).get("current_price"))
        if symbol and equity is not None and price is not None:
            clean.append((user_id, symbol, stamp, equity, price))
    if not clean:
        return 0
    with _conn() as con:
        con.executemany(
            "INSERT INTO customer_equity_snapshots "
            "(user_id, symbol, snapshot_at, equity, current_price) VALUES (?,?,?,?,?) "
            "ON CONFLICT(user_id, symbol, snapshot_at) DO UPDATE SET "
            "equity=excluded.equity, current_price=excluded.current_price",
            clean,
        )
        # Bound the per-(user, symbol) series so an every-30-min scheduler cannot
        # grow gesh_auth.db without limit. Oldest rows go first.
        for _uid, symbol, _stamp, _eq, _px in clean:
            con.execute(
                "DELETE FROM customer_equity_snapshots "
                "WHERE user_id=? AND symbol=? AND snapshot_at NOT IN ("
                "  SELECT snapshot_at FROM customer_equity_snapshots "
                "  WHERE user_id=? AND symbol=? ORDER BY snapshot_at DESC LIMIT ?)",
                (user_id, symbol, user_id, symbol, _MAX_EQUITY_SNAPSHOTS_PER_SYMBOL),
            )
    return len(clean)


def get_user_equity_history(email: str, symbol: str, days: int = 30) -> list[dict]:
    """This user's last N observations for one symbol, NEWEST FIRST.

    Newest-first mirrors db.get_equity_history because ecg.compute_ecg reverses
    what it is handed before the running-peak math. Empty list for an unknown
    user — never another tenant's rows, and never the owner's."""
    user_id = resolve_user_id(email)
    sym = (symbol or "").strip().upper()
    if not user_id or not sym:
        return []
    try:
        limit = int(days)
    except (TypeError, ValueError):
        limit = 30
    limit = max(1, min(limit, _MAX_EQUITY_HISTORY_DAYS))
    with _conn() as con:
        rows = con.execute(
            "SELECT snapshot_at, symbol, equity, current_price "
            "FROM customer_equity_snapshots WHERE user_id=? AND symbol=? "
            "ORDER BY snapshot_at DESC LIMIT ?",
            (user_id, sym, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def last_equity_snapshot_at(email: str) -> str | None:
    """ISO timestamp of this user's most recent observation, across all symbols —
    the interval guard a scheduler checks before writing another one."""
    user_id = resolve_user_id(email)
    if not user_id:
        return None
    with _conn() as con:
        row = con.execute(
            "SELECT MAX(snapshot_at) AS at FROM customer_equity_snapshots WHERE user_id=?",
            (user_id,),
        ).fetchone()
    return str(row["at"]) if row and row["at"] else None


def customers_with_positions() -> list[str]:
    """Active accounts holding at least one position — the snapshot sweep's
    universe. Emails, resolved through users so a rename is followed."""
    with _conn() as con:
        rows = con.execute(
            "SELECT DISTINCT u.email FROM customer_positions p "
            "JOIN users u ON u.user_id = p.user_id "
            "WHERE COALESCE(u.is_active, 1)=1 ORDER BY u.email"
        ).fetchall()
    return [r["email"] for r in rows]


# ── equal-weight (privacy) flag ──────────────────────────────────────────────
# When a customer bulk-imports SYMBOLS ONLY (no share counts — sizes private), the
# book is flagged equal-weight: the analysis weights every name 1/N instead of by an
# unknown/placeholder size. A bulk import that DOES carry share counts clears it.

def set_equal_weight(email: str, on: bool) -> None:
    user_id = resolve_user_id(email)
    if not user_id:
        return
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        con.execute(
            "INSERT INTO customer_book_prefs (user_id, equal_weight, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET equal_weight=excluded.equal_weight, "
            "updated_at=excluded.updated_at",
            (user_id, 1 if on else 0, now),
        )


def get_equal_weight(email: str) -> bool:
    """True when the user's book is in equal-weight (sizes-private) mode."""
    user_id = resolve_user_id(email)
    if not user_id:
        return False
    with _conn() as con:
        row = con.execute(
            "SELECT equal_weight FROM customer_book_prefs WHERE user_id=?", (user_id,)
        ).fetchone()
    return bool(row and row["equal_weight"])


# ── persisted book analyses (customer report page) ─────────────────────────────
# The in-memory analyze cache dies with the process; the /mybook/report page needs
# the customer's LAST analysis durably. Keep a short per-user history (latest wins).

_MAX_ANALYSES_PER_USER = 5


def save_book_analysis(email: str, result: dict) -> None:
    user_id = resolve_user_id(email)
    if not user_id or not isinstance(result, dict):
        return
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        con.execute(
            "INSERT INTO customer_analyses (user_id, result_json, created_at) VALUES (?,?,?)",
            (user_id, json.dumps(result), now),
        )
        # prune: keep only the newest _MAX_ANALYSES_PER_USER rows per user
        con.execute(
            "DELETE FROM customer_analyses WHERE user_id=? AND rowid NOT IN ("
            "  SELECT rowid FROM customer_analyses WHERE user_id=?"
            "  ORDER BY created_at DESC, rowid DESC LIMIT ?)",
            (user_id, user_id, _MAX_ANALYSES_PER_USER),
        )


def get_latest_book_analysis(email: str) -> dict | None:
    """Newest persisted analysis for the user, with its timestamp under `_saved_at`.
    Corruption-tolerant: an unparseable row returns None rather than raising."""
    user_id = resolve_user_id(email)
    if not user_id:
        return None
    with _conn() as con:
        row = con.execute(
            "SELECT result_json, created_at FROM customer_analyses WHERE user_id=? "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (user_id,),
        ).fetchone()
    if not row:
        return None
    try:
        out = json.loads(row["result_json"])
        out["_saved_at"] = row["created_at"]
        return out
    except Exception:
        return None


# `get_latest_book_analysis` only ever exposed the NEWEST row, so the four older
# ones the writer keeps were unreachable — the customer had no way back to a past
# run. These two readers close that. Both key strictly on the caller's user_id:
# an id belonging to another customer resolves to None, indistinguishable from an
# id that never existed.
#
# The id IS the SQLite rowid — `customer_analyses` has no surrogate key. Safe as
# an identifier here because rowid reuse needs the table's MAX rowid to be freed,
# and the prune below always deletes rows OLDER than the row it just inserted (so
# the max survives every write). It is still an opaque, per-user handle: never
# render it, never assume it survives a table rebuild (a tenant migration copies
# `_CHILD_DATA_COLUMNS`, not rowids), and always re-read the index first.


def list_book_analyses(email: str, limit: int = _MAX_ANALYSES_PER_USER) -> list[dict]:
    """Newest-first index of this user's persisted analyses.

    Each entry is {"id", "saved_at", "result", "parse_error"}. A row whose JSON is
    unreadable is KEPT with result=None / parse_error=True rather than dropped —
    silently shortening a history list would misrepresent what we hold (same
    posture as `export_user_data`). Returns [] for an unknown user or limit <= 0.
    """
    user_id = resolve_user_id(email)
    if not user_id or limit <= 0:
        return []
    with _conn() as con:
        rows = con.execute(
            "SELECT rowid AS id, result_json, created_at FROM customer_analyses "
            "WHERE user_id=? ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (user_id, int(limit)),
        ).fetchall()
    out: list[dict] = []
    for row in rows:
        try:
            parsed = json.loads(row["result_json"])
            if not isinstance(parsed, dict):
                raise ValueError("analysis row is not an object")
        except Exception:  # noqa: BLE001 — one corrupt row must not break the list
            parsed = None
        out.append(
            {
                "id": int(row["id"]),
                "saved_at": row["created_at"],
                "result": parsed,
                "parse_error": parsed is None,
            }
        )
    return out


def get_book_analysis(email: str, analysis_id: int) -> dict | None:
    """One persisted analysis by id, scoped to this user — the full stored payload
    with `_saved_at` and `_id` stamped on (mirrors `get_latest_book_analysis`).

    None when the id is unknown, belongs to ANOTHER user, or is unreadable.
    """
    user_id = resolve_user_id(email)
    if not user_id:
        return None
    try:
        row_id = int(analysis_id)
    except (TypeError, ValueError):
        return None
    # A rowid is a signed 64-bit integer. Binding anything wider raises
    # OverflowError inside sqlite3 — a caller-supplied path param must resolve to
    # "not found", never to a 500.
    if not -(2**63) <= row_id < 2**63:
        return None
    with _conn() as con:
        row = con.execute(
            "SELECT rowid AS id, result_json, created_at FROM customer_analyses "
            "WHERE user_id=? AND rowid=?",
            (user_id, row_id),
        ).fetchone()
    if not row:
        return None
    try:
        out = json.loads(row["result_json"])
        if not isinstance(out, dict):
            return None
    except Exception:  # noqa: BLE001 — corruption degrades to "not found", never a 500
        return None
    out["_saved_at"] = row["created_at"]
    out["_id"] = int(row["id"])
    return out


# ── usage metering (per-customer event counters — tier/billing groundwork) ─────

def _usage_total_in_transaction(
    con: sqlite3.Connection, user_id: str, event: str, cutoff: str
) -> int:
    historical = con.execute(
        "SELECT COUNT(*) FROM usage_events WHERE user_id=? AND event=? AND at>=?",
        (user_id, event, cutoff),
    ).fetchone()[0]
    reserved = con.execute(
        "SELECT COUNT(*) FROM usage_reservations "
        "WHERE user_id=? AND event=? AND state IN ('reserved','completed') AND reserved_at>=?",
        (user_id, event, cutoff),
    ).fetchone()[0]
    return int(historical) + int(reserved)


def _reserve_usage_in_transaction(
    con: sqlite3.Connection, user_id: str, event: str, job_id: str, limit: int
) -> bool:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    if limit > 0 and _usage_total_in_transaction(con, user_id, event, cutoff) >= limit:
        return False
    now = datetime.now(timezone.utc).isoformat()
    con.execute(
        "INSERT INTO usage_reservations "
        "(job_id,user_id,event,state,reserved_at) VALUES (?,?,?,'reserved',?)",
        (job_id, user_id, event, now),
    )
    return True


def reserve_usage(user_id: str, event: str, job_id: str, limit: int) -> bool:
    """Atomically reserve one rolling-window spend slot.

    Reserved and completed rows both count. Released rows do not. ``BEGIN
    IMMEDIATE`` serializes the count-and-insert across processes, closing the
    quota race that an in-process lock cannot close.
    """
    if not _is_canonical_user_id(user_id) or not event or not job_id:
        return False
    with _conn() as con:
        con.execute("BEGIN IMMEDIATE")
        return _reserve_usage_in_transaction(con, user_id, event, job_id, limit)


def _transition_usage(job_id: str, target: str, user_id: str | None = None) -> bool:
    if target not in ("completed", "released"):
        raise ValueError("invalid reservation state")
    now = datetime.now(timezone.utc).isoformat()
    # [CLAUDE V5 2026-07-19] defense-in-depth: when the caller knows the owner, scope
    # the transition by user_id too, so a reservation can only ever be mutated by its
    # own tenant even if a job_id were somehow reused/forged.
    with _conn() as con:
        con.execute("BEGIN IMMEDIATE")
        if user_id is not None:
            cur = con.execute(
                "UPDATE usage_reservations SET state=?, completed_at=? "
                "WHERE job_id=? AND user_id=? AND state='reserved'",
                (target, now, job_id, user_id),
            )
        else:
            cur = con.execute(
                "UPDATE usage_reservations SET state=?, completed_at=? "
                "WHERE job_id=? AND state='reserved'",
                (target, now, job_id),
            )
        return cur.rowcount == 1


def complete_usage(job_id: str, user_id: str | None = None) -> bool:
    return _transition_usage(job_id, "completed", user_id)


def release_usage(job_id: str, user_id: str | None = None) -> bool:
    return _transition_usage(job_id, "released", user_id)


def get_usage_reservation(job_id: str) -> dict[str, Any] | None:
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM usage_reservations WHERE job_id=?", (job_id,)
        ).fetchone()
    return dict(row) if row else None


def _job_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if not row:
        return None
    job = dict(row)
    raw = job.pop("result_json", None)
    try:
        job["result"] = json.loads(raw) if raw else None
    except (TypeError, ValueError):
        job["result"] = None
    return job


def get_active_mybook_job(user_id: str) -> dict[str, Any] | None:
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM mybook_jobs WHERE user_id=? AND status IN ('queued','running') "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (user_id,),
        ).fetchone()
    return _job_dict(row)


def get_latest_mybook_job(user_id: str) -> dict[str, Any] | None:
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM mybook_jobs WHERE user_id=? "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (user_id,),
        ).fetchone()
    return _job_dict(row)


def expire_stale_mybook_jobs(max_age_seconds: int) -> int:
    """Recover queued/running rows left behind by a process crash."""
    cutoff = (
        datetime.now(timezone.utc) - timedelta(seconds=max(1, max_age_seconds))
    ).isoformat()
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        con.execute("BEGIN IMMEDIATE")
        rows = con.execute(
            "SELECT job_id FROM mybook_jobs "
            "WHERE status IN ('queued','running') AND updated_at<?",
            (cutoff,),
        ).fetchall()
        job_ids = [str(row["job_id"]) for row in rows]
        if not job_ids:
            return 0
        placeholders = ",".join("?" for _ in job_ids)
        con.execute(
            f"UPDATE mybook_jobs SET status='failed',error_code='worker_expired',updated_at=? "
            f"WHERE job_id IN ({placeholders})",
            (now, *job_ids),
        )
        con.execute(
            f"UPDATE usage_reservations SET state='released',completed_at=? "
            f"WHERE state='reserved' AND job_id IN ({placeholders})",
            (now, *job_ids),
        )
        return len(job_ids)


def start_mybook_job(
    user_id: str,
    job_id: str,
    fingerprint: str,
    event: str,
    limit: int,
) -> dict[str, Any]:
    """Atomically create one queued job and its spend reservation."""
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        con.execute("BEGIN IMMEDIATE")
        active = con.execute(
            "SELECT * FROM mybook_jobs WHERE user_id=? AND status IN ('queued','running') "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        if active:
            return {"accepted": False, "quota_exceeded": False, "job": _job_dict(active)}
        if not _reserve_usage_in_transaction(con, user_id, event, job_id, limit):
            return {"accepted": False, "quota_exceeded": True, "job": None}
        con.execute(
            "INSERT INTO mybook_jobs "
            "(job_id,user_id,fingerprint,status,created_at,updated_at) "
            "VALUES (?,?,?,'queued',?,?)",
            (job_id, user_id, fingerprint, now, now),
        )
        row = con.execute("SELECT * FROM mybook_jobs WHERE job_id=?", (job_id,)).fetchone()
        return {"accepted": True, "quota_exceeded": False, "job": _job_dict(row)}


def mark_mybook_job_running(user_id: str, job_id: str, fingerprint: str) -> bool:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            "UPDATE mybook_jobs SET status='running', updated_at=? "
            "WHERE user_id=? AND job_id=? AND fingerprint=? AND status='queued'",
            (now, user_id, job_id, fingerprint),
        )
    return cur.rowcount == 1


def finish_mybook_job(
    user_id: str,
    job_id: str,
    fingerprint: str,
    status: str,
    *,
    result: dict[str, Any] | None = None,
    error_code: str | None = None,
) -> bool:
    """Publish only when ``job_id`` is still the user's newest generation."""
    if status not in ("done", "failed"):
        raise ValueError("terminal mybook status must be done or failed")
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        con.execute("BEGIN IMMEDIATE")
        latest = con.execute(
            "SELECT job_id FROM mybook_jobs WHERE user_id=? "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        if not latest or latest["job_id"] != job_id:
            return False
        cur = con.execute(
            "UPDATE mybook_jobs SET status=?, result_json=?, error_code=?, updated_at=? "
            "WHERE user_id=? AND job_id=? AND fingerprint=? AND status IN ('queued','running')",
            (
                status,
                json.dumps(result) if result is not None else None,
                (error_code or "")[:64] or None,
                now,
                user_id,
                job_id,
                fingerprint,
            ),
        )
        if cur.rowcount != 1:
            return False
        if status == "done" and result is not None:
            con.execute(
                "INSERT INTO customer_analyses (user_id,result_json,created_at) VALUES (?,?,?)",
                (user_id, json.dumps(result), now),
            )
            con.execute(
                "DELETE FROM customer_analyses WHERE user_id=? AND rowid NOT IN ("
                "SELECT rowid FROM customer_analyses WHERE user_id=? "
                "ORDER BY created_at DESC,rowid DESC LIMIT ?)",
                (user_id, user_id, _MAX_ANALYSES_PER_USER),
            )
        con.execute(
            "UPDATE usage_reservations SET state=?, completed_at=? "
            "WHERE job_id=? AND user_id=? AND state='reserved'",  # [CLAUDE V5] tenant-scoped
            ("completed" if status == "done" else "released", now, job_id, user_id),
        )
        return True


def mark_mybook_job_delivered(user_id: str, job_id: str) -> bool:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            "UPDATE mybook_jobs SET delivered_at=?, updated_at=? "
            "WHERE user_id=? AND job_id=? AND delivered_at IS NULL "
            "AND status IN ('done','failed')",
            (now, now, user_id, job_id),
        )
    return cur.rowcount == 1

def log_usage(email: str | None, event: str, detail: str = "") -> None:
    """Fire-and-forget usage counter. NEVER raises — metering must not be able to
    break a product endpoint."""
    try:
        user_id = resolve_user_id(email or "")
        if not user_id or not event:
            return
        with _conn() as con:
            con.execute(
                "INSERT INTO usage_events (user_id, event, detail, at) VALUES (?,?,?,?)",
                (
                    user_id,
                    event,
                    (detail or "")[:64],
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
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
    user_id = resolve_user_id(email)
    if not user_id:
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    with _conn() as con:
        return _usage_total_in_transaction(con, user_id, event, cutoff)


def usage_summary(days: int = 30) -> list[dict]:
    """Per-user, per-event counts over the trailing window — the owner's metering
    view (billing-tier groundwork). Newest-heaviest first."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with _conn() as con:
        rows = con.execute(
            "SELECT email,event,COUNT(*) AS n,MAX(at) AS last_at FROM ("
            " SELECT users.email AS email,usage_events.event AS event,usage_events.at AS at"
            " FROM usage_events JOIN users ON users.user_id=usage_events.user_id"
            " WHERE usage_events.at>=?"
            " UNION ALL"
            " SELECT users.email AS email,usage_reservations.event AS event,"
            " usage_reservations.reserved_at AS at"
            " FROM usage_reservations JOIN users ON users.user_id=usage_reservations.user_id"
            " WHERE usage_reservations.state='completed' AND usage_reservations.reserved_at>=?"
            ") GROUP BY email,event ORDER BY n DESC",
            (cutoff, cutoff),
        ).fetchall()
    return [{"email": r["email"], "event": r["event"], "count": r["n"], "last_at": r["last_at"]}
            for r in rows]


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))


# ─── Data-subject rights (L3): export + delete ────────────────────────────────
# The published Privacy Policy §8 promises access/export/delete. These implement
# them. Note: `export_portfolio.py` in this repo is the OWNER's Robinhood
# exporter and is unrelated — there was no customer export primitive before this.

# Every table that keys on user_id. Deletion walks this list, so a NEW customer
# table added later must be appended here or it silently survives a "deletion".
_USER_DATA_TABLES = (
    "customer_positions",
    "customer_book_prefs",
    "customer_equity_snapshots",
    "watchlist",
    "customer_analyses",
    "usage_events",
    "usage_reservations",
    "mybook_jobs",
)


def export_user_data(email: str) -> dict[str, Any]:
    """Everything we hold about this user, as plain JSON-able data.

    Scoped strictly to the caller. Deliberately omits `password_hash` — an
    export is handed to the user and must never carry a credential — and omits
    internal billing identifiers beyond the status the user already sees.
    Returns {} for an unknown user rather than raising.
    """
    user_id = resolve_user_id(email)
    if not user_id:
        return {}
    with _conn() as con:
        acct = con.execute(
            "SELECT email, created_at, last_login_at, tier, billing_status, "
            "alerts_opt_out, digest_opt_out FROM users WHERE user_id=?", (user_id,)
        ).fetchone()
        positions = con.execute(
            "SELECT symbol, shares, avg_cost, updated_at FROM customer_positions "
            "WHERE user_id=? ORDER BY rowid", (user_id,)
        ).fetchall()
        prefs = con.execute(
            "SELECT equal_weight, updated_at FROM customer_book_prefs WHERE user_id=?",
            (user_id,)
        ).fetchone()
        watch = con.execute(
            "SELECT symbol, added_at FROM watchlist WHERE user_id=? ORDER BY rowid",
            (user_id,)
        ).fetchall()
        analyses = con.execute(
            "SELECT result_json, created_at FROM customer_analyses WHERE user_id=? "
            "ORDER BY created_at DESC", (user_id,)
        ).fetchall()
        usage = con.execute(
            "SELECT event, detail, at FROM usage_events WHERE user_id=? ORDER BY at DESC",
            (user_id,)
        ).fetchall()
        equity_history = con.execute(
            "SELECT symbol, snapshot_at, equity, current_price "
            "FROM customer_equity_snapshots WHERE user_id=? "
            "ORDER BY snapshot_at DESC, symbol", (user_id,)
        ).fetchall()

    def _parse(raw: str) -> Any:
        try:
            return json.loads(raw)
        except Exception:  # noqa: BLE001 — a corrupt row must not block an export
            return {"parse_error": True}

    return {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "account": dict(acct) if acct else {},
        "positions": [dict(r) for r in positions],
        "book_preferences": dict(prefs) if prefs else {},
        "watchlist": [r["symbol"] for r in watch],
        "analyses": [
            {"created_at": r["created_at"], "result": _parse(r["result_json"])}
            for r in analyses
        ],
        "usage": [dict(r) for r in usage],
        "equity_history": [dict(r) for r in equity_history],
        "note": (
            "This is everything GESH Financial stores about your account. Payment "
            "records are held by Lemon Squeezy (our merchant of record), not by us."
        ),
    }


def delete_user_data(email: str) -> dict[str, Any]:
    """Hard-delete the user and every row keyed to them. Returns a receipt.

    A real purge, not a deactivation: rows are removed from every table in
    _USER_DATA_TABLES and then from `users`. Scoped by user_id so it can never
    reach another account. Idempotent — deleting an unknown user is a no-op.
    """
    user_id = resolve_user_id(email)
    if not user_id:
        return {"deleted": False, "counts": {}}
    counts: dict[str, int] = {}
    with _conn() as con:
        con.execute("BEGIN IMMEDIATE")
        for table in _USER_DATA_TABLES:
            cur = con.execute(f"DELETE FROM {table} WHERE user_id=?", (user_id,))
            counts[table] = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        cur = con.execute("DELETE FROM users WHERE user_id=?", (user_id,))
        counts["users"] = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    return {"deleted": True, "counts": counts}


def billing_subscription_status(email: str) -> tuple[str | None, str | None]:
    """(subscription_id, status) — used to warn before deleting an account that
    is still being billed. Deleting here does NOT cancel Lemon Squeezy."""
    with _conn() as con:
        row = con.execute(
            "SELECT billing_sub_id, billing_status FROM users WHERE email=?", (_norm(email),)
        ).fetchone()
    return (row["billing_sub_id"], row["billing_status"]) if row else (None, None)
