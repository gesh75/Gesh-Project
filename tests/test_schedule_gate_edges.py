"""Extra ET-window / DST / unknown-job edges for schedule_gate.

The July (EDT) happy path lives in test_retention_p1s.py. These cases pin the
original timezone bug class: host-local time must never decide the window, and
a winter EST offset (UTC-5) must not silently close a window that is open in ET.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

import schedule_gate as sg


@pytest.fixture(autouse=True)
def _gate_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sg, "DB_PATH", tmp_path / "gate.db")


def _utc(y: int, m: int, d: int, hh: int, mm: int) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


def test_unknown_job_is_closed():
    ok, why = sg.should_run("not-a-job", _utc(2026, 7, 17, 15, 5))
    assert ok is False
    assert "unknown job" in why


def test_alerts_window_uses_winter_est_not_edt_offset():
    # Fri 2026-01-16 is EST (UTC-5). 11:05 ET == 16:05 UTC; 10:05 ET == 15:05 UTC.
    # A hardcoded UTC-4 (the July tests' offset) would treat 15:05 UTC as 11:05 ET
    # and incorrectly open the window.
    ok, _ = sg.should_run("alerts", _utc(2026, 1, 16, 16, 5))
    assert ok is True
    ok, why = sg.should_run("alerts", _utc(2026, 1, 16, 15, 5))
    assert ok is False
    assert "window closed" in why


def test_alerts_thursday_winter_is_open_after_1105_et():
    # Thu 2026-01-15 16:05 UTC == 11:05 EST.
    assert sg.should_run("alerts", _utc(2026, 1, 15, 16, 5))[0] is True


def test_digest_opens_at_exact_1715_et_in_winter():
    # Fri 2026-01-16 17:15 EST == 22:15 UTC. 17:14 EST == 22:14 UTC.
    assert sg.should_run("digest", _utc(2026, 1, 16, 22, 14))[0] is False
    assert sg.should_run("digest", _utc(2026, 1, 16, 22, 15))[0] is True


def test_digest_sunday_catchup_stays_open_in_winter():
    # Sun 2026-01-18 is weekday()==6 — weekend catch-up must still fire.
    assert sg.should_run("digest", _utc(2026, 1, 18, 16, 0))[0] is True
    # Monday is closed.
    assert sg.should_run("digest", _utc(2026, 1, 19, 16, 0))[0] is False


def test_alerts_period_key_is_et_date_not_utc_date():
    # Sat 2026-01-17 03:00 UTC == Fri 2026-01-16 22:00 EST. The period must be
    # the ET calendar date, or a late-evening tick would look like a new day.
    assert sg.period_key("alerts", _utc(2026, 1, 17, 3, 0)) == "2026-01-16"


def test_digest_period_key_is_iso_week():
    # Fri 2026-01-16 is ISO week 2026-W03.
    assert sg.period_key("digest", _utc(2026, 1, 16, 22, 15)) == "2026-W03"


def test_mark_ran_uses_et_period_so_late_utc_tick_is_deduped():
    # Mark at 22:00 EST Fri (03:00 UTC Sat); a later same-ET-day tick must refuse.
    sg.mark_ran("alerts", _utc(2026, 1, 17, 3, 0))
    ok, why = sg.should_run("alerts", _utc(2026, 1, 17, 3, 30))
    assert ok is False
    assert "already ran" in why
    assert "2026-01-16" in why
