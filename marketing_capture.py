"""Consent-first, double opt-in email capture for the free public tools (spec item B8).

Deliberately isolated from the customer system:

* Storage is its OWN SQLite file (``gesh_marketing.db``, override ``GESH_MARKETING_DB``),
  never ``gesh_auth.db``. Nothing here reads or writes the ``users`` table, creates an
  account, or touches billing, owner or broker state.
* A visitor is PENDING until they follow the HMAC-signed confirm link; only then are they
  CONFIRMED. Every email carries an HMAC-signed unsubscribe link (and RFC 8058 headers).
* The address is stored lowercase for delivery; rate-limit keys and the IP are stored only
  as keyed hashes. The raw address never goes to a log line.

This module has no FastAPI imports: routes live in ``routers/marketing.py``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html as _html
import json
import logging
import os
import re
import secrets
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import auth_users
import email_sender
import seo_pages

logger = logging.getLogger("marketing_capture")

MARKETING_DB_PATH = Path(
    os.getenv("GESH_MARKETING_DB", str(Path(__file__).resolve().parent / "gesh_marketing.db"))
)

# ── policy constants ────────────────────────────────────────────────────────────

CONFIRM_TTL_SECONDS = 7 * 24 * 3600  # confirm link lifetime (spec: 7 days)
UNSUB_TTL_SECONDS = 365 * 24 * 3600  # an unsubscribe link must outlive the email by a long way
TOKEN_MAX_LEN = 512
EMAIL_MAX_LEN = 254
PENDING_RETENTION_SECONDS = 30 * 24 * 3600  # unconfirmed rows are deleted after this

IP_LIMIT, IP_WINDOW = 5, 3600  # subscribe attempts per IP hash per hour
EMAIL_SEND_LIMIT, EMAIL_SEND_WINDOW = 3, 24 * 3600  # confirmation emails per address per day
EMAIL_SEND_COOLDOWN = 15 * 60  # minimum gap between two confirmation emails to one address
GLOBAL_SEND_LIMIT, GLOBAL_SEND_WINDOW = 300, 24 * 3600  # provider-quota / reputation backstop

STATUS_PENDING, STATUS_CONFIRMED, STATUS_UNSUBSCRIBED = "pending", "confirmed", "unsubscribed"
TYP_CONFIRM, TYP_UNSUB = "mkt_confirm", "mkt_unsub"  # token purposes

SOURCE_SLUGS = frozenset({"portfolio-xray", "monte-carlo-simulator", "kelly-calculator"})
DEFAULT_SOURCE = "other"

CONSENT_TEXT = (
    "Email me the weekly learning note: short educational explainers about portfolio risk "
    "and position sizing, sent at most once a week. It is educational information, not "
    "investment advice, and it promises no returns. We will first send one email asking you "
    "to confirm this address, and nothing else until you do. You can unsubscribe at any time "
    "using the link in every email. Only your email address is stored; anything you type into "
    "the calculators stays in your browser."
)

GENERIC_ACCEPTED = (
    "Thanks. If this address can receive email, we have sent a message with a confirmation "
    "link. Nothing is subscribed until you click it. If it does not arrive within a few "
    "minutes, check your spam folder."
)
MSG_INVALID = "Please enter a valid email address."
MSG_NO_CONSENT = "Please tick the box to confirm you want the weekly learning note."
MSG_RATE_LIMITED = "Too many attempts. Please try again later."
MSG_UNAVAILABLE = "Email signup is not open yet. Please check back soon. The tools work without it."

_EMAIL_RE = re.compile(
    r"^(?=[a-z0-9])[a-z0-9._%+\-]{1,64}@"
    r"(?:[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$"
)

_DDL = (
    """
    CREATE TABLE IF NOT EXISTS marketing_subscribers (
        id              TEXT PRIMARY KEY,
        email           TEXT NOT NULL UNIQUE,
        status          TEXT NOT NULL CHECK (status IN ('pending','confirmed','unsubscribed')),
        source          TEXT NOT NULL,
        created_at      TEXT NOT NULL,
        confirmed_at    TEXT,
        unsubscribed_at TEXT,
        consent_text    TEXT NOT NULL,
        ip_hash         TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_marketing_subscribers_status "
    "ON marketing_subscribers (status, created_at)",
    """
    CREATE TABLE IF NOT EXISTS marketing_rate_events (
        kind     TEXT NOT NULL,
        key_hash TEXT NOT NULL,
        at       INTEGER NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_marketing_rate_events "
    "ON marketing_rate_events (kind, key_hash, at)",
)


# ── clock / storage ─────────────────────────────────────────────────────────────


def _now() -> int:
    return int(time.time())


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _connect() -> sqlite3.Connection:
    path = Path(MARKETING_DB_PATH)
    con = sqlite3.connect(path, timeout=5, isolation_level=None)  # explicit BEGIN/COMMIT
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 5000")
    for ddl in _DDL:  # additive + idempotent, run on every connect (signal_history convention)
        con.execute(ddl)
    try:  # the list is PII: owner-only, best effort (read-only mounts must not break signup)
        if path.exists() and (path.stat().st_mode & 0o077):
            os.chmod(path, 0o600)
    except OSError:
        pass
    return con


# ── tokens (HMAC-SHA256, domain-separated from session tokens) ──────────────────


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64u_dec(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _key() -> bytes:
    """Signing key derived from the same secret as auth/unsubscribe tokens.

    Domain separation means a marketing token can never validate as a session or
    alerts/digest token (and the reverse), even though they share GESH_AUTH_SECRET."""
    return hmac.new(auth_users._secret(), b"gesh-marketing-capture-v1", hashlib.sha256).digest()


def _sign(payload_b64: str) -> str:
    return _b64u(hmac.new(_key(), payload_b64.encode(), hashlib.sha256).digest())


def issue_token(sub: str, typ: str, ttl: int) -> str:
    """Token bound to an opaque subscriber id (never the email), a purpose and an expiry."""
    payload = _b64u(
        json.dumps(
            {"sub": sub, "typ": typ, "exp": _now() + int(ttl), "v": 1}, separators=(",", ":")
        ).encode()
    )
    return f"{payload}.{_sign(payload)}"


def verify_token(token: str | None, typ: str) -> str | None:
    """Return the subscriber id for a valid, unexpired token of this purpose, else None."""
    if not token or not isinstance(token, str) or len(token) > TOKEN_MAX_LEN:
        return None
    try:
        payload_b64, sig = token.split(".")
        if not hmac.compare_digest(sig.encode(), _sign(payload_b64).encode()):
            return None
        data = json.loads(_b64u_dec(payload_b64))
        if not isinstance(data, dict) or data.get("v") != 1 or data.get("typ") != typ:
            return None
        if int(data.get("exp", 0)) < _now():
            return None
        sub = data.get("sub")
        return sub if isinstance(sub, str) and re.fullmatch(r"[0-9a-f]{32}", sub) else None
    except Exception:  # malformed input of any kind is simply "invalid"
        return None


def _hash(kind: str, value: str) -> str:
    return hmac.new(_key(), f"{kind}:{value}".encode(), hashlib.sha256).hexdigest()[:32]


# ── validation ──────────────────────────────────────────────────────────────────


def normalize_email(raw: object) -> str | None:
    """Lowercased address if it passes strict validation, else None."""
    if not isinstance(raw, str):
        return None
    email = raw.strip().lower()
    if not email or len(email) > EMAIL_MAX_LEN or ".." in email:
        return None
    if not _EMAIL_RE.fullmatch(email):
        return None
    local = email.split("@", 1)[0]
    return None if local.endswith(".") else email


def normalize_source(raw: object) -> str:
    return raw if isinstance(raw, str) and raw in SOURCE_SLUGS else DEFAULT_SOURCE


# ── subscribe ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ConfirmationJob:
    sub_id: str
    email: str  # in memory only; never logged


@dataclass(frozen=True)
class SubscribeOutcome:
    kind: str  # accepted | invalid | no_consent | rate_limited | unavailable
    job: ConfirmationJob | None = None


def _count(con: sqlite3.Connection, kind: str, key: str, since: int) -> int:
    row = con.execute(
        "SELECT COUNT(*) AS n FROM marketing_rate_events WHERE kind=? AND key_hash=? AND at>=?",
        (kind, key, since),
    ).fetchone()
    return int(row["n"])


def _record(con: sqlite3.Connection, kind: str, key: str, now: int) -> None:
    con.execute(
        "INSERT INTO marketing_rate_events (kind, key_hash, at) VALUES (?,?,?)", (kind, key, now)
    )


def _prune(con: sqlite3.Connection, now: int) -> None:
    con.execute("DELETE FROM marketing_rate_events WHERE at < ?", (now - 2 * 24 * 3600,))
    con.execute(
        "DELETE FROM marketing_subscribers WHERE status=? AND created_at < ?",
        (STATUS_PENDING, _iso(now - PENDING_RETENTION_SECONDS)),
    )


def request_subscription(
    *, email: object, consent: bool, source: object, ip: str, honeypot: object = ""
) -> SubscribeOutcome:
    """State machine entry. Never raises; the caller maps ``kind`` to a response.

    ``accepted`` is returned for a brand-new address, an existing pending/unsubscribed
    one, a confirmed one, a cooled-down one and a honeypot hit alike, so the response
    cannot be used to learn whether an address is already on the list."""
    if str(honeypot or "").strip():
        return SubscribeOutcome("accepted")  # bots get the same success; nothing stored or sent
    try:
        return _request_subscription(email, consent, source, ip)
    except Exception as exc:  # noqa: BLE001 - signup must degrade, never 500
        logger.error("marketing signup unavailable: %s", type(exc).__name__)
        return SubscribeOutcome("unavailable")


def _request_subscription(
    email_raw: object, consent: bool, source: object, ip: str
) -> SubscribeOutcome:
    now = _now()
    ip_key = _hash("ip", ip or "unknown")
    con = _connect()
    try:
        con.execute("BEGIN IMMEDIATE")
        try:
            _prune(con, now)
            if _count(con, "ip", ip_key, now - IP_WINDOW) >= IP_LIMIT:
                con.execute("COMMIT")
                return SubscribeOutcome("rate_limited")
            _record(con, "ip", ip_key, now)  # every attempt counts, valid or not

            email = normalize_email(email_raw)
            if email is None:
                con.execute("COMMIT")
                return SubscribeOutcome("invalid")
            if not consent:
                con.execute("COMMIT")
                return SubscribeOutcome("no_consent")
            if not email_sender.is_configured():
                # Nothing could be sent, so do not collect an address we cannot confirm.
                con.execute("COMMIT")
                return SubscribeOutcome("unavailable")

            row = con.execute(
                "SELECT id, status FROM marketing_subscribers WHERE email=?", (email,)
            ).fetchone()
            if row is not None and row["status"] == STATUS_CONFIRMED:
                con.execute("COMMIT")
                return SubscribeOutcome("accepted")  # never mail a confirmed address

            em_key = _hash("em", email)
            recent = _count(con, "send", em_key, now - EMAIL_SEND_WINDOW)
            last = con.execute(
                "SELECT MAX(at) AS t FROM marketing_rate_events WHERE kind='send' AND key_hash=?",
                (em_key,),
            ).fetchone()["t"]
            cooling = last is not None and now - int(last) < EMAIL_SEND_COOLDOWN
            if (
                recent >= EMAIL_SEND_LIMIT
                or cooling
                or _count(con, "global", "all", now - GLOBAL_SEND_WINDOW) >= GLOBAL_SEND_LIMIT
            ):
                con.execute("COMMIT")
                return SubscribeOutcome("accepted")  # identical response, no mail

            if row is None:
                sub_id = secrets.token_hex(16)
                con.execute(
                    "INSERT INTO marketing_subscribers "
                    "(id, email, status, source, created_at, consent_text, ip_hash) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (
                        sub_id,
                        email,
                        STATUS_PENDING,
                        normalize_source(source),
                        _iso(now),
                        CONSENT_TEXT,
                        ip_key,
                    ),
                )
            else:  # pending resend, or a fresh opt-in after an unsubscribe
                sub_id = row["id"]
                con.execute(
                    "UPDATE marketing_subscribers SET status=?, confirmed_at=NULL, "
                    "unsubscribed_at=NULL, consent_text=?, ip_hash=? WHERE id=?",
                    (STATUS_PENDING, CONSENT_TEXT, ip_key, sub_id),
                )
            _record(con, "send", em_key, now)
            _record(con, "global", "all", now)
            con.execute("COMMIT")
            return SubscribeOutcome("accepted", ConfirmationJob(sub_id, email))
        except BaseException:
            if con.in_transaction:
                con.execute("ROLLBACK")
            raise
    finally:
        con.close()


# ── confirm / unsubscribe ───────────────────────────────────────────────────────


def _transition(token: str | None, typ: str, target: str) -> str:
    """Apply a token-authorised status change. Returns changed | already | invalid."""
    sub_id = verify_token(token, typ)
    if sub_id is None:
        return "invalid"
    now = _now()
    con = _connect()
    try:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            "SELECT status FROM marketing_subscribers WHERE id=?", (sub_id,)
        ).fetchone()
        if row is None:
            con.execute("COMMIT")
            return "invalid"
        status = row["status"]
        if status == target:
            con.execute("COMMIT")
            return "already"
        if target == STATUS_CONFIRMED and status != STATUS_PENDING:
            con.execute("COMMIT")
            return "invalid"  # an old confirm link must not undo an unsubscribe
        if target == STATUS_CONFIRMED:
            con.execute(
                "UPDATE marketing_subscribers SET status=?, confirmed_at=? WHERE id=?",
                (STATUS_CONFIRMED, _iso(now), sub_id),
            )
        else:
            con.execute(
                "UPDATE marketing_subscribers SET status=?, unsubscribed_at=? WHERE id=?",
                (STATUS_UNSUBSCRIBED, _iso(now), sub_id),
            )
        con.execute("COMMIT")
        return "changed"
    except Exception:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    finally:
        con.close()


def token_is_valid(token: str | None, typ: str) -> bool:
    """Read-only check used by the GET interstitials (never mutates)."""
    sub_id = verify_token(token, typ)
    if sub_id is None:
        return False
    con = _connect()
    try:
        row = con.execute(
            "SELECT status FROM marketing_subscribers WHERE id=?", (sub_id,)
        ).fetchone()
    finally:
        con.close()
    if row is None:
        return False
    return not (typ == TYP_CONFIRM and row["status"] == STATUS_UNSUBSCRIBED)


def confirm(token: str | None) -> str:
    return _transition(token, TYP_CONFIRM, STATUS_CONFIRMED)


def unsubscribe(token: str | None) -> str:
    return _transition(token, TYP_UNSUB, STATUS_UNSUBSCRIBED)


# ── the confirmation email ──────────────────────────────────────────────────────


def build_confirmation_email(job: ConfirmationJob) -> tuple[str, str, dict[str, str]]:
    base = seo_pages.site_base_url()
    confirm_url = f"{base}/marketing/confirm?token={issue_token(job.sub_id, TYP_CONFIRM, CONFIRM_TTL_SECONDS)}"
    unsub_url = f"{base}/marketing/unsubscribe?token={issue_token(job.sub_id, TYP_UNSUB, UNSUB_TTL_SECONDS)}"
    e = lambda s: _html.escape(s, quote=True)  # noqa: E731
    body = (
        '<div style="font-family:system-ui,sans-serif;max-width:520px;margin:auto">'
        "<h2>Confirm your GESH weekly learning note</h2>"
        "<p>Someone, hopefully you, asked on a GESH Financial free tool page to receive the "
        "weekly learning note at this address. Nothing is subscribed until you confirm.</p>"
        f'<p><a href="{e(confirm_url)}" style="display:inline-block;background:#00d4ff;'
        "color:#001018;padding:12px 20px;border-radius:8px;text-decoration:none;"
        'font-weight:600">Yes, confirm my email</a></p>'
        "<p>This link works for 7 days. If you did not ask for this, ignore this email and "
        "you will not be emailed again.</p>"
        f'<p style="color:#667;font-size:13px">{e(CONSENT_TEXT)}</p>'
        f'<p style="color:#667;font-size:13px"><a href="{e(unsub_url)}">Unsubscribe</a></p>'
        '<hr><p style="color:#889;font-size:12px">Educational information only, not '
        "investment advice. GESH Financial is not a registered investment adviser.</p></div>"
    )
    headers = {
        "List-Unsubscribe": f"<{unsub_url}>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }
    return "Confirm your GESH weekly learning note", body, headers


def send_confirmation(job: ConfirmationJob) -> bool:
    """Send the single confirmation email. Never raises; never logs the address."""
    try:
        subject, body, headers = build_confirmation_email(job)
        ok = email_sender.send_email(job.email, subject, body, headers=headers, log_recipient=False)
    except Exception as exc:  # noqa: BLE001
        logger.error("marketing confirmation failed id=%s: %s", job.sub_id[:8], type(exc).__name__)
        return False
    if not ok:
        logger.warning("marketing confirmation not delivered id=%s", job.sub_id[:8])
    return ok
