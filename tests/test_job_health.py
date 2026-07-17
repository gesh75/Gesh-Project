"""Job health check: run_signal_alerts / run_weekly_digest record every real run
to a job_runs table (same DB + lazy-DDL pattern as alert_log), and the owner-only
/usage page surfaces last-run + summary per job — red when com.gesh.signal-alerts
is stale (>25h weekday-aware) or the last summary carries email_delivery DISABLED.
Grok review 2026-07-17: a failing launchd job must not be invisible."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import job_health
import signal_history as sh

# ─── storage layer ────────────────────────────────────────────────────────────

def test_record_and_latest_run_roundtrip():
    assert job_health.record_job_run("com.gesh.signal-alerts",
                                     {"emails_sent": 2, "flips": 3}) is True
    runs = job_health.latest_runs()
    info = runs["com.gesh.signal-alerts"]
    assert info["summary"] == {"emails_sent": 2, "flips": 3}
    # ran_at is a parseable UTC ISO timestamp, roughly "now"
    ran = datetime.fromisoformat(info["ran_at"])
    assert abs((datetime.now(timezone.utc) - ran).total_seconds()) < 60


def test_latest_runs_returns_newest_row_per_job():
    old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    assert job_health.record_job_run("com.gesh.signal-alerts", {"run": "old"}, ran_at=old)
    assert job_health.record_job_run("com.gesh.signal-alerts", {"run": "new"})
    assert job_health.record_job_run("com.gesh.weekly-digest", {"customers": 4})
    runs = job_health.latest_runs()
    assert runs["com.gesh.signal-alerts"]["summary"] == {"run": "new"}
    assert runs["com.gesh.weekly-digest"]["summary"] == {"customers": 4}


def test_record_job_run_fail_soft_on_unopenable_db(tmp_path: Path, monkeypatch):
    """A broken DB must never sink the job — record returns False, reads return {}."""
    monkeypatch.setattr(sh, "DB_PATH", tmp_path)          # a directory, not a file
    assert job_health.record_job_run("com.gesh.signal-alerts", {"x": 1}) is False
    assert job_health.latest_runs() == {}


def test_latest_runs_survives_malformed_summary_json():
    assert job_health.record_job_run("com.gesh.signal-alerts", {"ok": 1})
    import sqlite3
    with sqlite3.connect(Path(sh.DB_PATH)) as con:
        con.execute("UPDATE job_runs SET summary_json='{not json'")
    assert job_health.latest_runs()["com.gesh.signal-alerts"]["summary"] == {}


# ─── weekday-aware staleness ─────────────────────────────────────────────────

# 2026-07-17 is a Friday; 2026-07-13 a Monday.
_FRI = datetime(2026, 7, 17, 15, 0, tzinfo=timezone.utc)
_MON = datetime(2026, 7, 20, 15, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("last,now,stale", [
    (None, _FRI, True),                                    # never ran → stale
    ((_FRI - timedelta(hours=2)).isoformat(), _FRI, False),        # fresh
    ((_FRI - timedelta(hours=26)).isoformat(), _FRI, True),        # Thu→Fri 26h → stale
    (_FRI.isoformat(), _MON, False),                       # Fri→Mon 72h: weekend allowance
    (_FRI.isoformat(), _MON + timedelta(hours=2), True),   # Fri→Mon 74h > 73h → stale
    (_FRI.isoformat(), _FRI + timedelta(hours=29), False), # Fri→Sat: no run expected Sat
])
def test_is_stale_weekday_aware(last, now, stale):
    assert job_health.is_stale(last, now=now) is stale


def test_is_stale_accepts_naive_timestamps_as_utc():
    naive = (_FRI - timedelta(hours=2)).replace(tzinfo=None).isoformat()
    assert job_health.is_stale(naive, now=_FRI) is False


def test_is_stale_garbage_timestamp_is_stale():
    assert job_health.is_stale("not-a-date", now=_FRI) is True


# ─── job __main__ wiring (_cli) ──────────────────────────────────────────────

def test_signal_alerts_cli_records_job_run(monkeypatch, capsys):
    import run_signal_alerts as job
    monkeypatch.setattr(job, "main", lambda dry_run=False: {"emails_sent": 0, "flips": 0})
    job._cli([])
    assert '"emails_sent": 0' in capsys.readouterr().out   # JSON summary still printed
    assert job_health.latest_runs()[job.JOB_NAME]["summary"] == {"emails_sent": 0, "flips": 0}


def test_signal_alerts_cli_dry_run_records_nothing(monkeypatch, capsys):
    import run_signal_alerts as job
    monkeypatch.setattr(job, "main", lambda dry_run=False: {"emails_sent": 0})
    job._cli(["--dry-run"])
    capsys.readouterr()
    assert job_health.latest_runs() == {}


def test_weekly_digest_cli_records_job_run(monkeypatch, capsys):
    import run_weekly_digest as job
    monkeypatch.setattr(job, "main", lambda dry_run=False: {"customers": 3, "emails_sent": 1})
    job._cli([])
    capsys.readouterr()
    assert job_health.latest_runs()[job.JOB_NAME]["summary"]["customers"] == 3


def test_cli_recording_failure_never_sinks_the_run(monkeypatch, capsys, tmp_path):
    """The health write is best-effort — a broken DB must not break the job output."""
    import run_signal_alerts as job
    monkeypatch.setattr(sh, "DB_PATH", tmp_path)           # unopenable
    monkeypatch.setattr(job, "main", lambda dry_run=False: {"emails_sent": 1})
    job._cli([])
    assert '"emails_sent": 1' in capsys.readouterr().out


# ─── /usage owner page: Job Health section ───────────────────────────────────

@pytest.fixture
def au(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import auth_users as _au
    monkeypatch.setattr(_au, "AUTH_DB_PATH", tmp_path / "auth.db")
    _au.init_auth_db()
    return _au


@pytest.fixture
def client(au, monkeypatch):
    import server
    monkeypatch.setattr(server.auth_users, "AUTH_DB_PATH", au.AUTH_DB_PATH)
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: True)
    return server, TestClient(server.app, raise_server_exceptions=False)


def _as_owner(server, monkeypatch, c):
    monkeypatch.setattr(server.auth_users, "authenticate_session",
                        lambda tok: "georgy75@gmail.com")
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda p, cc: "allow", raising=False)
    monkeypatch.setattr(server.auth_users, "is_owner",
                        lambda e: e == "georgy75@gmail.com", raising=False)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")


def test_usage_page_shows_job_health_ok(client, monkeypatch):
    server, c = client
    _as_owner(server, monkeypatch, c)
    job_health.record_job_run("com.gesh.signal-alerts", {"emails_sent": 2, "flips": 1})
    job_health.record_job_run("com.gesh.weekly-digest", {"customers": 4, "emails_sent": 4})
    r = c.get("/usage")
    assert r.status_code == 200
    assert "Job Health" in r.text
    assert "com.gesh.signal-alerts" in r.text and "com.gesh.weekly-digest" in r.text
    assert '"emails_sent": 2' in r.text                    # summary rendered
    assert "STALE" not in r.text and "EMAIL DISABLED" not in r.text


def test_usage_page_flags_stale_signal_alerts(client, monkeypatch):
    server, c = client
    _as_owner(server, monkeypatch, c)
    # 8 days beats any weekend allowance (25h + max 4 weekend days = 121h < 192h)
    old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    job_health.record_job_run("com.gesh.signal-alerts", {"emails_sent": 0}, ran_at=old)
    r = c.get("/usage")
    assert "STALE" in r.text


def test_usage_page_flags_never_ran_signal_alerts(client, monkeypatch):
    server, c = client
    _as_owner(server, monkeypatch, c)
    r = c.get("/usage")
    assert "STALE" in r.text


def test_usage_page_flags_email_delivery_disabled(client, monkeypatch):
    server, c = client
    _as_owner(server, monkeypatch, c)
    job_health.record_job_run("com.gesh.signal-alerts",
                              {"emails_sent": 0,
                               "email_delivery": "DISABLED — RESEND_API_KEY unset"})
    r = c.get("/usage")
    assert "EMAIL DISABLED" in r.text


def test_usage_page_job_health_is_escaped(client, monkeypatch):
    """Summary JSON is attacker-ish data (env-derived strings) — must be escaped."""
    server, c = client
    _as_owner(server, monkeypatch, c)
    job_health.record_job_run("com.gesh.signal-alerts",
                              {"note": "<script>alert(1)</script>"})
    r = c.get("/usage")
    assert "<script>alert(1)</script>" not in r.text
    assert "&lt;script&gt;" in r.text
