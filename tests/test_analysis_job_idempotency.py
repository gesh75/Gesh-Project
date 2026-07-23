"""Analysis-job idempotency — determined, not assumed.

The reviewers disagreed on whether duplicate jobs were possible. Neither was
taken on trust: this measures the ACTUAL behaviour.

What the implementation already provides, independently of the frontend:

  1. `auth_users.start_mybook_job` opens `BEGIN IMMEDIATE`, so the write lock is
     taken BEFORE the active-job SELECT. Two concurrent creators serialize;
     the loser sees the winner's row and is refused.
  2. A partial UNIQUE INDEX — `ux_mybook_jobs_active_user ON mybook_jobs(user_id)
     WHERE status IN ('queued','running')` — makes a second active row
     impossible even if (1) were bypassed.

The frontend `_mybookAnalyzeInFlight` boolean is a request-volume optimisation
on top, NOT the protection; these tests never touch it.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

USER_A = "a@x.com"
USER_B = "b@x.com"


@pytest.fixture
def au(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import auth_users as _au
    monkeypatch.setattr(_au, "AUTH_DB_PATH", tmp_path / "auth.db")
    _au.init_auth_db()
    for e in (USER_A, USER_B):
        _au.create_user(e, "test-password")
    return _au


def _uid(au, email):
    return au.resolve_user_id(email)


def _start(au, uid, job_id, fp="fp-1", limit=0):
    return au.start_mybook_job(uid, job_id, fp, "book_analysis", limit)


def _active_count(au, uid) -> int:
    with au._conn() as con:
        return con.execute(
            "SELECT COUNT(*) FROM mybook_jobs WHERE user_id=? "
            "AND status IN ('queued','running')", (uid,)).fetchone()[0]


# ─── the constraint exists and is what we think it is ────────────────────────

def test_partial_unique_index_is_present(au):
    with au._conn() as con:
        idx = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    assert "ux_mybook_jobs_active_user" in idx


def test_database_refuses_a_second_active_row_directly(au):
    """Bypass the handler entirely — the DB itself must refuse."""
    import sqlite3
    uid = _uid(au, USER_A)
    assert _start(au, uid, "job-1")["accepted"] is True
    with pytest.raises(sqlite3.IntegrityError):
        with au._conn() as con:
            con.execute(
                "INSERT INTO mybook_jobs (job_id,user_id,fingerprint,status,"
                "created_at,updated_at) VALUES (?,?,?,'queued',?,?)",
                ("job-2", uid, "fp-1", "2026-07-23", "2026-07-23"))


# ─── concurrency ─────────────────────────────────────────────────────────────

def test_true_concurrent_creation_yields_exactly_one_job(au):
    """Eight threads race to create a job for the same user before any finishes."""
    uid = _uid(au, USER_A)
    results, errors = [], []
    barrier = threading.Barrier(8)

    def worker(n: int) -> None:
        try:
            barrier.wait(timeout=10)
            results.append(_start(au, uid, f"job-{n}"))
        except Exception as exc:  # noqa: BLE001 — recorded, asserted below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    assert not errors, f"concurrent creation raised: {errors[:2]}"
    accepted = [r for r in results if r["accepted"]]
    assert len(accepted) == 1, f"{len(accepted)} jobs accepted, expected exactly 1"
    assert _active_count(au, uid) == 1
    # every loser is told about the winner rather than getting a silent failure
    for r in results:
        if not r["accepted"] and not r["quota_exceeded"]:
            assert r["job"] and r["job"]["job_id"] == accepted[0]["job"]["job_id"]


def test_rapid_sequential_creation_is_refused(au):
    """Double-click / retry / refresh-while-polling all reduce to this."""
    uid = _uid(au, USER_A)
    first = _start(au, uid, "job-1")
    assert first["accepted"] is True
    for n in range(2, 6):
        again = _start(au, uid, f"job-{n}")
        assert again["accepted"] is False
        assert again["job"]["job_id"] == "job-1"
    assert _active_count(au, uid) == 1


def test_a_different_book_state_still_cannot_open_a_second_job(au):
    """Same customer, materially different fingerprint: still one job at a time.
    The in-flight run finishes and the next POST starts the new one."""
    uid = _uid(au, USER_A)
    assert _start(au, uid, "job-1", fp="book-v1")["accepted"] is True
    second = _start(au, uid, "job-2", fp="book-v2-completely-different")
    assert second["accepted"] is False
    assert _active_count(au, uid) == 1


def test_separate_customers_are_not_blocked_by_each_other(au):
    """Tenant scope: the constraint is per user_id, not global."""
    a, b = _uid(au, USER_A), _uid(au, USER_B)
    assert _start(au, a, "job-a", fp="same-symbol")["accepted"] is True
    assert _start(au, b, "job-b", fp="same-symbol")["accepted"] is True
    assert _active_count(au, a) == 1 and _active_count(au, b) == 1


def test_concurrent_creation_across_two_users_is_not_serialized_away(au):
    a, b = _uid(au, USER_A), _uid(au, USER_B)
    out = {}
    barrier = threading.Barrier(2)

    def worker(uid, key):
        barrier.wait(timeout=10)
        out[key] = _start(au, uid, f"job-{key}")

    ta = threading.Thread(target=worker, args=(a, "a"))
    tb = threading.Thread(target=worker, args=(b, "b"))
    ta.start()
    tb.start()
    ta.join(timeout=20)
    tb.join(timeout=20)
    assert out["a"]["accepted"] is True and out["b"]["accepted"] is True


# ─── terminal states free the slot ───────────────────────────────────────────

def test_a_finished_job_frees_the_slot(au):
    uid = _uid(au, USER_A)
    _start(au, uid, "job-1")
    au.finish_mybook_job(uid, "job-1", "fp-1", "done", result={"ok": True})
    assert _active_count(au, uid) == 0
    assert _start(au, uid, "job-2")["accepted"] is True


def test_a_failed_job_frees_the_slot_so_retry_works(au):
    uid = _uid(au, USER_A)
    _start(au, uid, "job-1")
    au.finish_mybook_job(uid, "job-1", "fp-1", "failed", error_code="boom")
    assert _active_count(au, uid) == 0
    retry = _start(au, uid, "job-2")
    assert retry["accepted"] is True, "a failed job must not wedge the user out"


def test_expiring_a_stuck_job_frees_the_slot(au):
    """A worker that dies must not lock the customer out permanently."""
    uid = _uid(au, USER_A)
    _start(au, uid, "job-1")
    # Back-date the row rather than sleeping: expire_stale_mybook_jobs clamps
    # max_age to >= 1s, so a just-created job can never be expired by argument.
    with au._conn() as con:
        con.execute("UPDATE mybook_jobs SET updated_at='2020-01-01T00:00:00+00:00' "
                    "WHERE job_id='job-1'")
    assert au.expire_stale_mybook_jobs(max_age_seconds=1) == 1
    assert _active_count(au, uid) == 0
    assert _start(au, uid, "job-2")["accepted"] is True


def test_marking_running_does_not_open_a_second_slot(au):
    uid = _uid(au, USER_A)
    _start(au, uid, "job-1")
    assert au.mark_mybook_job_running(uid, "job-1", "fp-1") is True
    assert _active_count(au, uid) == 1
    assert _start(au, uid, "job-2")["accepted"] is False
