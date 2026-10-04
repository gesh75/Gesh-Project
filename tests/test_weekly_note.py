"""Weekly learning note sender: the "at most weekly, confirmed only, never twice" promises.

Offline: a recorder replaces the provider, storage is a per-test SQLite file.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import auth_users
import marketing_capture as mc
import weekly_note as wn

ET = ZoneInfo("America/New_York")
WED_11AM_ET = int(datetime(2026, 10, 7, 11, 0, tzinfo=ET).timestamp())
DAY = 24 * 3600


class Box:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.ok = True

    def __call__(self, to, subject, html, headers=None, log_recipient=True):  # noqa: ANN001
        self.sent.append({"to": to, "subject": subject, "html": html, "headers": headers or {}, "log": log_recipient})
        return self.ok


@pytest.fixture(autouse=True)
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mc, "MARKETING_DB_PATH", tmp_path / "m.db")
    monkeypatch.setenv("GESH_AUTH_DB", str(tmp_path / "a.db"))
    monkeypatch.setenv("GESH_AUTH_SECRET", "test-secret-deterministic")
    monkeypatch.setattr(auth_users, "AUTH_DB_PATH", tmp_path / "a.db")
    monkeypatch.setenv(wn.ADDRESS_ENV, "1 Example Street, Example City")


@pytest.fixture
def notes(tmp_path: Path) -> Path:
    d = tmp_path / "notes"
    d.mkdir()
    for i, status in (("001-first-note", "ready"), ("002-second-note", "ready"), ("003-draft-note", "draft")):
        (d / f"{i}.md").write_text(
            f"---\nid: {i}\nsubject: Subject {i}\nstatus: {status}\n---\nBody of {i} <script>x</script>.\n\n"
            "## Head\n\n- one\n- two\n\nSee https://pay.geshfinance.com/tools/kelly-calculator and https://evil.example/x\n"
        )
    return d


def _add(email: str, status: str = mc.STATUS_CONFIRMED, n: int = 1) -> str:
    sub_id = f"{n:032x}"
    con = mc._connect()
    con.execute(
        "INSERT INTO marketing_subscribers (id,email,status,source,created_at,confirmed_at,consent_text) VALUES (?,?,?,?,?,?,?)",
        (sub_id, email, status, "other", "2026-10-01T00:00:00Z", "2026-10-01T00:00:00Z", "t"),
    )
    con.close()
    return sub_id


def test_dry_run_sends_nothing_and_reports_count(notes: Path) -> None:
    _add("a@example.com")
    box = Box()
    r = wn.run(notes_dir=notes, now=WED_11AM_ET, sender=box)
    assert (r.eligible, r.sent) == (1, 0) and box.sent == []


def test_only_confirmed_subscribers_are_mailed(notes: Path) -> None:
    _add("ok@example.com", n=1)
    _add("pending@example.com", mc.STATUS_PENDING, n=2)
    _add("gone@example.com", mc.STATUS_UNSUBSCRIBED, n=3)
    box = Box()
    r = wn.run(send=True, notes_dir=notes, now=WED_11AM_ET, sender=box)
    assert r.sent == 1 and [m["to"] for m in box.sent] == ["ok@example.com"]
    assert box.sent[0]["log"] is False


def test_at_most_one_note_per_six_days_and_never_the_same_twice(notes: Path) -> None:
    _add("a@example.com")
    box = Box()
    wn.run(send=True, notes_dir=notes, now=WED_11AM_ET, sender=box)
    assert wn.run(send=True, notes_dir=notes, now=WED_11AM_ET + 5 * DAY, sender=box).sent == 0
    assert wn.run(send=True, notes_dir=notes, now=WED_11AM_ET + 7 * DAY, sender=box).sent == 1
    assert [m["subject"] for m in box.sent] == ["Subject 001-first-note", "Subject 002-second-note"]
    # ready notes exhausted (the draft never goes out): nothing more, however long we wait
    assert wn.run(send=True, notes_dir=notes, now=WED_11AM_ET + 30 * DAY, ignore_window=True, sender=box).sent == 0


def test_drafts_only_with_the_testing_flag(notes: Path) -> None:
    _add("a@example.com")
    box = Box()
    for k in range(3):
        wn.run(send=True, notes_dir=notes, now=WED_11AM_ET + 7 * k * DAY, ignore_window=True, sender=box)
    assert len(box.sent) == 2
    r = wn.run(send=True, notes_dir=notes, now=WED_11AM_ET + 30 * DAY, ignore_window=True, include_drafts=True, sender=box)
    assert r.sent == 1 and box.sent[-1]["subject"].endswith("003-draft-note")


def test_failed_send_is_rolled_back_so_it_retries(notes: Path) -> None:
    _add("a@example.com")
    box = Box()
    box.ok = False
    r = wn.run(send=True, notes_dir=notes, now=WED_11AM_ET, sender=box)
    assert (r.sent, r.failed) == (0, 1)
    box.ok = True
    assert wn.run(send=True, notes_dir=notes, now=WED_11AM_ET + 60, sender=box).sent == 1


def test_provider_exception_counts_as_failure_not_crash(notes: Path) -> None:
    _add("a@example.com")

    def boom(*a, **k):  # noqa: ANN002, ANN003
        raise RuntimeError("x")

    assert wn.run(send=True, notes_dir=notes, now=WED_11AM_ET, sender=boom).failed == 1


@pytest.mark.parametrize(
    "when",
    [
        datetime(2026, 10, 5, 11, 0, tzinfo=ET),  # Monday
        datetime(2026, 10, 7, 9, 59, tzinfo=ET),  # too early
        datetime(2026, 10, 7, 15, 0, tzinfo=ET),  # too late
        datetime(2026, 10, 10, 11, 0, tzinfo=ET),  # Saturday
    ],
)
def test_outside_window_nothing_is_sent(notes: Path, when: datetime) -> None:
    _add("a@example.com")
    box = Box()
    r = wn.run(send=True, notes_dir=notes, now=int(when.timestamp()), sender=box)
    assert box.sent == [] and "window" in r.skipped_reason


def test_mass_send_refused_without_postal_address(notes: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(wn.ADDRESS_ENV)
    _add("a@example.com")
    box = Box()
    r = wn.run(send=True, notes_dir=notes, now=WED_11AM_ET, sender=box)
    assert box.sent == [] and wn.ADDRESS_ENV in r.skipped_reason
    # a single-address test send is still allowed
    assert wn.run(send=True, only_email="a@example.com", notes_dir=notes, now=WED_11AM_ET, sender=box).sent == 1


def test_email_has_unsubscribe_headers_valid_token_and_is_escaped(notes: Path) -> None:
    sub = _add("a@example.com")
    box = Box()
    wn.run(send=True, notes_dir=notes, now=WED_11AM_ET, sender=box)
    m = box.sent[0]
    assert "<script>" not in m["html"] and "&lt;script&gt;" in m["html"]
    assert m["headers"]["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    token = re.search(r"/marketing/unsubscribe\?token=([^\">]+)", m["html"]).group(1)
    assert mc.verify_token(token, mc.TYP_UNSUB) == sub
    assert "1 Example Street" in m["html"] and "not investment advice" in m["html"]
    assert 'href="https://pay.geshfinance.com/tools/kelly-calculator"' in m["html"]
    assert "evil.example" in m["html"] and 'href="https://evil.example' not in m["html"]


def test_unsubscribed_after_a_send_gets_nothing_more(notes: Path) -> None:
    sub = _add("a@example.com")
    box = Box()
    wn.run(send=True, notes_dir=notes, now=WED_11AM_ET, sender=box)
    token = mc.issue_token(sub, mc.TYP_UNSUB, 3600)
    assert mc.unsubscribe(token) == "changed"
    assert wn.run(send=True, notes_dir=notes, now=WED_11AM_ET + 7 * DAY, sender=box).sent == 0


def test_shipped_notes_parse_and_first_is_ready() -> None:
    notes = wn.load_notes()
    assert notes and notes[0].status == "ready" and len(notes[0].subject) <= 120
    assert "buy" not in notes[0].body.lower().split()
