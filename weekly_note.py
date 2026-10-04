"""Weekly learning note: the sender behind the consent promised on the free tool pages.

The promise (marketing_capture.CONSENT_TEXT) is "at most once a week", educational only,
unsubscribe in every email. This module keeps that promise in code rather than in prose:

* only CONFIRMED subscribers are ever selected (pending and unsubscribed never are);
* each subscriber gets at most one note per ``MIN_GAP_SECONDS`` (6 days) and never the same
  note twice (UNIQUE(sub_id, note_id) in ``marketing_note_sends``);
* the send is recorded BEFORE the provider call and rolled back on failure, so a crash can
  never turn into a duplicate;
* sends only happen Tuesday to Thursday, 10:00-15:00 America/New_York, so a frequent
  launchd tick is safe and no wall-clock time is pinned to the host timezone;
* notes live in ``docs/weekly_notes/*.md`` and only ``status: ready`` notes go out;
* dry-run is the default; mass sending also needs a postal address in the footer.

No FastAPI imports. Entry point: ``scripts/send_weekly_note.py``.
"""

from __future__ import annotations

import html as _html
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

import email_sender
import marketing_capture as mc
import seo_pages

logger = logging.getLogger("weekly_note")

NOTES_DIR = Path(__file__).resolve().parent / "docs" / "weekly_notes"
MIN_GAP_SECONDS = 6 * 24 * 3600
SEND_TZ = ZoneInfo("America/New_York")
SEND_WEEKDAYS = frozenset({1, 2, 3})  # Tue, Wed, Thu
SEND_HOURS = range(10, 15)  # 10:00 through 14:59 ET
DEFAULT_LIMIT = 50  # recipients per run; the next tick picks up the rest
ADDRESS_ENV = "GESH_MAIL_POSTAL_ADDRESS"

_NOTE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{2,80}$")

_DDL = (
    """
    CREATE TABLE IF NOT EXISTS marketing_note_sends (
        sub_id  TEXT NOT NULL,
        note_id TEXT NOT NULL,
        sent_at INTEGER NOT NULL,
        UNIQUE (sub_id, note_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_marketing_note_sends_sub "
    "ON marketing_note_sends (sub_id, sent_at)",
)

Sender = Callable[..., bool]


@dataclass(frozen=True)
class Note:
    id: str
    subject: str
    status: str  # "draft" | "ready"
    body: str  # lightweight markdown: paragraphs, "## " headings, "- " bullets


@dataclass(frozen=True)
class RunReport:
    note_id: str | None
    eligible: int
    sent: int
    failed: int
    skipped_reason: str = ""


# ── notes ───────────────────────────────────────────────────────────────────────


def parse_note(text: str, fallback_id: str) -> Note:
    meta: dict[str, str] = {}
    body = text
    if text.startswith("---\n"):
        head, _, body = text[4:].partition("\n---\n")
        for line in head.splitlines():
            key, _, value = line.partition(":")
            if value:
                meta[key.strip().lower()] = value.strip()
    note_id = meta.get("id", fallback_id)
    if not _NOTE_ID_RE.match(note_id):
        raise ValueError(f"bad note id: {note_id!r}")
    subject = meta.get("subject", "").strip()
    if not subject or len(subject) > 120:
        raise ValueError(f"note {note_id}: subject missing or too long")
    status = meta.get("status", "draft").lower()
    if status not in {"draft", "ready"}:
        raise ValueError(f"note {note_id}: status must be draft or ready")
    return Note(id=note_id, subject=subject, status=status, body=body.strip())


def load_notes(directory: Path | None = None) -> list[Note]:
    folder = Path(directory) if directory else NOTES_DIR
    if not folder.is_dir():
        return []
    notes = [parse_note(p.read_text(encoding="utf-8"), p.stem) for p in sorted(folder.glob("*.md"))]
    ids = [n.id for n in notes]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate note ids")
    return sorted(notes, key=lambda n: n.id)


_OWN_URL_RE = re.compile(r"https://pay\.geshfinance\.com/[A-Za-z0-9/\-]*")


def _link_own_urls(escaped: str) -> str:
    """Only links to our own host become anchors; any other URL stays inert text."""
    return _OWN_URL_RE.sub(lambda m: f'<a href="{m.group(0)}">{m.group(0)}</a>', escaped)


def render_body_html(body: str) -> str:
    """Escape first, then apply the tiny markup subset. No raw HTML survives."""
    e = lambda s: _html.escape(s, quote=True)  # noqa: E731
    out: list[str] = []
    for block in re.split(r"\n\s*\n", body.strip()):
        lines = [ln.rstrip() for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        if lines[0].startswith("## "):
            out.append(f'<h3 style="margin:22px 0 6px">{e(lines[0][3:])}</h3>')
            lines = lines[1:]
            if not lines:
                continue
        if all(ln.startswith("- ") for ln in lines):
            items = "".join(f"<li>{e(ln[2:])}</li>" for ln in lines)
            out.append(f'<ul style="padding-left:20px">{items}</ul>')
        else:
            out.append(f"<p>{_link_own_urls(e(' '.join(lines)))}</p>")
    return "".join(out)


def build_email(note: Note, sub_id: str) -> tuple[str, str, dict[str, str]]:
    base = seo_pages.site_base_url()
    token = mc.issue_token(sub_id, mc.TYP_UNSUB, mc.UNSUB_TTL_SECONDS)
    unsub_url = f"{base}/marketing/unsubscribe?token={token}"
    e = lambda s: _html.escape(s, quote=True)  # noqa: E731
    address = os.getenv(ADDRESS_ENV, "").strip()
    address_html = f"<br>{e(address)}" if address else ""
    html = (
        '<div style="font-family:system-ui,sans-serif;max-width:560px;margin:auto;line-height:1.55">'
        f"<h2>{e(note.subject)}</h2>{render_body_html(note.body)}"
        '<hr><p style="color:#667;font-size:13px">You are receiving this because you asked for '
        "the GESH weekly learning note on a free tool page and confirmed this address. "
        f'<a href="{e(unsub_url)}">Unsubscribe</a> at any time.{address_html}</p>'
        '<p style="color:#889;font-size:12px">Educational information only, not investment '
        "advice. Numbers in examples are hypothetical and promise no returns. GESH Financial "
        "is not a registered investment adviser.</p></div>"
    )
    headers = {
        "List-Unsubscribe": f"<{unsub_url}>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }
    return note.subject, html, headers


# ── scheduling rules ────────────────────────────────────────────────────────────


def in_send_window(now_epoch: float | None = None) -> bool:
    moment = datetime.fromtimestamp(now_epoch if now_epoch is not None else time.time(), tz=timezone.utc)
    local = moment.astimezone(SEND_TZ)
    return local.weekday() in SEND_WEEKDAYS and local.hour in SEND_HOURS


def _connect():  # noqa: ANN202
    con = mc._connect()
    for ddl in _DDL:
        con.execute(ddl)
    return con


def _eligible(con, notes: list[Note], now: int, only_email: str | None) -> list[tuple[str, str, Note]]:  # noqa: ANN001
    """(sub_id, email, next note) for every confirmed subscriber who is due one."""
    sql = (
        "SELECT s.id, s.email, "
        "  COALESCE((SELECT MAX(sent_at) FROM marketing_note_sends x WHERE x.sub_id = s.id), 0) AS last_sent "
        "FROM marketing_subscribers s WHERE s.status = ?"
    )
    args: list[object] = [mc.STATUS_CONFIRMED]
    if only_email:
        sql += " AND s.email = ?"
        args.append(only_email)
    sql += " ORDER BY s.confirmed_at, s.id"
    out: list[tuple[str, str, Note]] = []
    for row in con.execute(sql, args).fetchall():
        if now - int(row["last_sent"]) < MIN_GAP_SECONDS:
            continue
        got = {r["note_id"] for r in con.execute("SELECT note_id FROM marketing_note_sends WHERE sub_id=?", (row["id"],))}
        nxt = next((n for n in notes if n.id not in got), None)
        if nxt is not None:
            out.append((row["id"], row["email"], nxt))
    return out


# ── the run ─────────────────────────────────────────────────────────────────────


def run(
    *,
    send: bool = False,
    only_email: str | None = None,
    include_drafts: bool = False,
    ignore_window: bool = False,
    limit: int = DEFAULT_LIMIT,
    notes_dir: Path | None = None,
    now: int | None = None,
    sender: Sender | None = None,
) -> RunReport:
    now_ts = int(time.time()) if now is None else int(now)
    notes = [n for n in load_notes(notes_dir) if n.status == "ready" or include_drafts]
    if not notes:
        return RunReport(None, 0, 0, 0, "no ready notes")
    only = mc.normalize_email(only_email) if only_email else None
    if only_email and only is None:
        return RunReport(None, 0, 0, 0, "invalid --only-email")
    if send and not ignore_window and not in_send_window(now_ts):
        return RunReport(None, 0, 0, 0, "outside send window (Tue-Thu 10:00-15:00 ET)")
    if send and only is None and not os.getenv(ADDRESS_ENV, "").strip():
        return RunReport(None, 0, 0, 0, f"{ADDRESS_ENV} is not set; refusing to mail the list")

    con = _connect()
    try:
        due = _eligible(con, notes, now_ts, only)[: max(0, limit)]
        first = due[0][2].id if due else None
        if not send:
            return RunReport(first, len(due), 0, 0, "dry run")
        deliver = sender or email_sender.send_email
        sent = failed = 0
        for sub_id, email, note in due:
            cur = con.execute(
                "INSERT OR IGNORE INTO marketing_note_sends (sub_id, note_id, sent_at) VALUES (?,?,?)",
                (sub_id, note.id, now_ts),
            )
            if cur.rowcount == 0:
                continue  # already recorded: never send twice
            subject, html, headers = build_email(note, sub_id)
            try:
                ok = deliver(email, subject, html, headers=headers, log_recipient=False)
            except Exception as exc:  # noqa: BLE001
                logger.error("weekly note failed id=%s: %s", sub_id[:8], type(exc).__name__)
                ok = False
            if ok:
                sent += 1
            else:
                failed += 1
                con.execute("DELETE FROM marketing_note_sends WHERE sub_id=? AND note_id=?", (sub_id, note.id))
        return RunReport(first, len(due), sent, failed)
    finally:
        con.close()
