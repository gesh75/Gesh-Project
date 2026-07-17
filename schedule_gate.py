"""Eastern-Time schedule gate for customer-messaging cron jobs.

Why this exists (Codex review P1, 2026-07-17): the retention jobs promised
"11:05 ET" and "Friday 17:15 ET" but were scheduled with launchd
``StartCalendarInterval``, which fires in the **Mac's local timezone** (this
host runs EEST — 7 hours ahead of ET) and silently skips runs missed while
the machine sleeps. The weekly digest had literally never run.

The fix inverts control: launchd now just *ticks* the job every 30 minutes
(``StartInterval`` + ``RunAtLoad``), and this module decides — explicitly in
``America/New_York`` — whether the job's window is open and whether it already
ran this period. That buys:

  * correct wall-clock semantics regardless of host timezone or DST;
  * catch-up: a tick after wake still runs the job if the window is open and
    the period hasn't run yet (the old calendar trigger just vanished);
  * idempotency: one successful run per period, recorded durably.

Windows (both end-of-day inclusive so a late wake still delivers):
  * ``alerts``  — Mon–Fri, 11:05 ET → 23:59 ET, once per ET date.
  * ``digest``  — Fri 17:15 ET → Sunday 23:59 ET, once per ISO week
                  (the weekend tail is the catch-up window: a Friday-evening
                  digest that arrives Saturday morning beats one that never
                  arrives at all).

Markers live in ``job_gate_runs`` in the same DB as signal_history, written
only after ``main()`` returns without raising — a crashed run retries on the
next tick, and the jobs' own per-customer ledgers (alert_log / digest_log)
make that retry safe.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from db import DB_PATH

ET = ZoneInfo("America/New_York")

_DDL = """
CREATE TABLE IF NOT EXISTS job_gate_runs (
    job    TEXT NOT NULL,
    period TEXT NOT NULL,           -- ET date (alerts) or ISO week (digest)
    ran_at TEXT NOT NULL,           -- UTC ISO timestamp
    PRIMARY KEY (job, period)
);
"""

# job -> (window predicate on an ET datetime, period key fn)
_JOBS = {
    "alerts": {
        "open": lambda et: et.weekday() < 5 and (et.hour, et.minute) >= (11, 5),
        "period": lambda et: et.strftime("%Y-%m-%d"),
    },
    "digest": {
        # Friday from 17:15 ET, plus the whole weekend as catch-up.
        "open": lambda et: (et.weekday() == 4 and (et.hour, et.minute) >= (17, 15))
                            or et.weekday() in (5, 6),
        "period": lambda et: et.strftime("%G-W%V"),
    },
}


def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(Path(DB_PATH))
    con.execute(_DDL)
    return con


def period_key(job: str, now_utc: datetime | None = None) -> str:
    et = (now_utc or datetime.now(timezone.utc)).astimezone(ET)
    return _JOBS[job]["period"](et)


def should_run(job: str, now_utc: datetime | None = None) -> tuple[bool, str]:
    """(run?, human reason). Pure ET-window + already-ran check.

    ``now_utc`` is injectable for tests; production passes nothing.
    """
    if job not in _JOBS:
        return False, f"unknown job {job!r}"
    et = (now_utc or datetime.now(timezone.utc)).astimezone(ET)
    if not _JOBS[job]["open"](et):
        return False, f"window closed (ET now {et.strftime('%a %H:%M')})"
    period = _JOBS[job]["period"](et)
    with _conn() as con:
        row = con.execute(
            "SELECT ran_at FROM job_gate_runs WHERE job=? AND period=?",
            (job, period)).fetchone()
    if row:
        return False, f"already ran for {period} (at {row[0]})"
    return True, f"window open, {period} not yet run"


def mark_ran(job: str, now_utc: datetime | None = None) -> None:
    """Record a successful run for the current period. Call ONLY after the job's
    main() returned without raising — a crash must leave the period unmarked so
    the next tick retries."""
    now = now_utc or datetime.now(timezone.utc)
    period = period_key(job, now)
    with _conn() as con:
        con.execute(
            "INSERT OR REPLACE INTO job_gate_runs (job, period, ran_at) VALUES (?,?,?)",
            (job, period, now.isoformat(timespec="seconds")))
