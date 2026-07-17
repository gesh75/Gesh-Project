"""Launchd job health ledger (job_runs table).

run_signal_alerts / run_weekly_digest print their JSON summary only to /tmp
logs, so a failing or misconfigured job is invisible unless someone reads the
log (Grok review 2026-07-17). Each real run appends a row here; the owner-only
/usage page renders last-run + summary per job and flags staleness.

Same DB + lazy-DDL pattern as alert_log/digest_log: signal_history.DB_PATH,
CREATE TABLE on every connect, and every write is fail-soft — a broken health
ledger must never sink the job it observes.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import signal_history

logger = logging.getLogger("job_health")

STALE_AFTER_HOURS = 25.0   # daily job: >25h since last run = something is wrong

_DDL = """
CREATE TABLE IF NOT EXISTS job_runs (
    job          TEXT NOT NULL,
    ran_at       TEXT NOT NULL,           -- UTC ISO timestamp
    summary_json TEXT NOT NULL
);
"""


def _conn() -> sqlite3.Connection:
    # signal_history.DB_PATH read at call time, not import time — tests (and the
    # suite-wide conftest isolation) monkeypatch that attribute per test.
    con = sqlite3.connect(Path(signal_history.DB_PATH))
    con.row_factory = sqlite3.Row
    con.execute(_DDL)
    return con


def record_job_run(job: str, summary: dict, ran_at: str | None = None) -> bool:
    """Append one run's summary. Fail-soft: never raises."""
    try:
        with _conn() as con:
            con.execute(
                "INSERT INTO job_runs (job, ran_at, summary_json) VALUES (?,?,?)",
                (job,
                 ran_at or datetime.now(timezone.utc).isoformat(),
                 json.dumps(summary, default=str)))
        return True
    except Exception as e:  # noqa: BLE001 — health write is strictly best-effort
        logger.warning("job_health record failed for %s: %s", job, e)
        return False


def latest_runs() -> dict[str, dict]:
    """Newest run per job: {job: {"ran_at": iso, "summary": dict}}. Fail-soft."""
    try:
        with _conn() as con:
            rows = con.execute(
                "SELECT job, ran_at, summary_json FROM job_runs "
                "WHERE rowid IN (SELECT MAX(rowid) FROM job_runs GROUP BY job)").fetchall()
    except Exception as e:  # noqa: BLE001
        logger.warning("job_health read failed: %s", e)
        return {}
    out: dict[str, dict] = {}
    for r in rows:
        try:
            summary = json.loads(r["summary_json"])
            if not isinstance(summary, dict):
                summary = {}
        except Exception:  # noqa: BLE001 — malformed row must not 500 the page
            summary = {}
        out[r["job"]] = {"ran_at": r["ran_at"], "summary": summary}
    return out


def is_stale(last_ran_at: str | None, *, now: datetime | None = None,
             threshold_hours: float = STALE_AFTER_HOURS) -> bool:
    """Weekday-aware staleness for a Mon–Fri daily job.

    The 25h threshold gains a 24h allowance for each weekend day between the
    last run and now, so a normal Fri→Mon gap (~72h) is healthy while a missed
    weekday run still flags within a day. Never-ran / unparsable = stale.
    """
    if not last_ran_at:
        return True
    try:
        last = datetime.fromisoformat(str(last_ran_at))
    except ValueError:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    allowance = threshold_hours
    day = last.date() + timedelta(days=1)
    while day <= now.date():
        if day.weekday() >= 5:            # Sat=5 / Sun=6 — no run scheduled
            allowance += 24.0
        day += timedelta(days=1)
    return (now - last).total_seconds() > allowance * 3600
