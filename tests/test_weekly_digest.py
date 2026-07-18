from __future__ import annotations

from pathlib import Path

import pytest

import signal_history as sh


@pytest.fixture
def au(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import auth_users as _au
    monkeypatch.setattr(_au, "AUTH_DB_PATH", tmp_path / "auth.db")
    _au.init_auth_db()
    # [CLAUDE 2026-07-18] immutable-user-ID migration: watchlist/book/tokens now
    # require a real active user — register the fixture emails up front.
    _au.create_user("a@x.com", "pw-test")
    _au.create_user("boom@x.com", "pw-test")
    _au.create_user("empty@x.com", "pw-test")
    _au.create_user("ok@x.com", "pw-test")
    return _au


@pytest.fixture(autouse=True)
def _sig_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sh, "DB_PATH", tmp_path / "sig.db")


def _seed_flip(sym):
    for d in ("2026-07-13", "2026-07-14"):
        sh.record_snapshots([{"symbol": sym, "verdict": "BUY"}], date=d)
    for d in ("2026-07-15", "2026-07-16"):
        sh.record_snapshots([{"symbol": sym, "verdict": "HOLD"}], date=d)


def test_digest_sent_to_active_customer_with_book(au, monkeypatch):
    import run_weekly_digest as job
    monkeypatch.setattr(job, "auth_users", au, raising=True)
    monkeypatch.setattr(job, "_close_for", lambda sym: 100.0, raising=True)
    au.set_position("a@x.com", "NVDA", 10)
    _seed_flip("NVDA")
    sent = []
    monkeypatch.setattr(job.email_sender, "send_email",
                        lambda to, subject, html: sent.append((to, html)) or True)
    out = job.main()
    assert out["emails_sent"] == 1
    to, html = sent[0]
    assert to == "a@x.com"
    assert "$1,000" in html                       # book value from stubbed close
    assert "NVDA" in html and "HOLD" in html      # the week's confirmed change
    assert "/digest/unsubscribe" in html
    assert "/track-record" in html


def test_digest_skips_opted_out_and_empty_customers(au, monkeypatch):
    import run_weekly_digest as job
    monkeypatch.setattr(job, "auth_users", au, raising=True)
    monkeypatch.setattr(job, "_close_for", lambda sym: 100.0, raising=True)
    au.set_position("a@x.com", "NVDA", 10)
    au.set_digest_opt_out("a@x.com", True)
    au.add_watch("empty@x.com", "NVDA")   # watch-only user still gets a digest
    sent = []
    monkeypatch.setattr(job.email_sender, "send_email",
                        lambda to, subject, html: sent.append(to) or True)
    job.main()
    assert sent == ["empty@x.com"]


def test_digest_never_calls_llm(au, monkeypatch):
    import run_weekly_digest as job
    assert not hasattr(job, "deep_analyze_portfolio")     # structural: no LLM import


def test_summary_flags_disabled_email_delivery_when_key_unset(au, monkeypatch):
    """RESEND_API_KEY unset -> email_sender takes the DEV NO-OP path silently.
    Under launchd (no shell env inheritance) this happens for real, so the
    digest's summary must say so instead of looking like a quiet week."""
    import run_weekly_digest as job
    monkeypatch.setattr(job, "auth_users", au, raising=True)
    monkeypatch.setattr(job, "_close_for", lambda sym: 100.0, raising=True)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    au.set_position("a@x.com", "NVDA", 10)
    _seed_flip("NVDA")
    # do NOT monkeypatch send_email — exercise the real DEV NO-OP path
    summary = job.main()
    assert summary["emails_sent"] == 0
    assert summary["email_delivery"] == "DISABLED — RESEND_API_KEY unset"


def test_summary_omits_email_delivery_marker_when_no_customers(au, monkeypatch):
    """No eligible customers -> nothing was attempted, so the marker must NOT
    appear."""
    import run_weekly_digest as job
    monkeypatch.setattr(job, "auth_users", au, raising=True)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    summary = job.main()
    assert summary["emails_sent"] == 0
    assert "email_delivery" not in summary


def test_one_customer_failure_does_not_sink_the_run(au, monkeypatch):
    """Fail-soft per customer: a compose/DB error for one customer must not kill
    the digest for everyone after them in the loop (transient SQLite failures on
    gesh_financial.db are a documented reality in this repo)."""
    import run_weekly_digest as job
    monkeypatch.setattr(job, "auth_users", au, raising=True)
    au.set_position("boom@x.com", "NVDA", 10)   # sorts first — fails
    au.set_position("ok@x.com", "NVDA", 10)     # sorts second — must still get theirs
    real_compose = job._compose

    def _flaky_compose(email):
        if email == "boom@x.com":
            raise RuntimeError("database disk image is malformed")
        return real_compose(email)

    monkeypatch.setattr(job, "_compose", _flaky_compose, raising=True)
    monkeypatch.setattr(job, "_close_for", lambda sym: 100.0, raising=True)
    sent = []
    monkeypatch.setattr(job.email_sender, "send_email",
                        lambda to, subject, html: sent.append(to) or True)
    out = job.main()
    assert sent == ["ok@x.com"]
    assert out["emails_sent"] == 1


def test_digest_not_resent_within_same_iso_week(au, monkeypatch):
    """launchd can double-fire around sleep/wake — a second run in the same ISO
    week must not send the identical digest again (external review 2026-07-17:
    duplicate emails erode the exact trust the digest exists to build)."""
    import run_weekly_digest as job
    monkeypatch.setattr(job, "auth_users", au, raising=True)
    monkeypatch.setattr(job, "_close_for", lambda sym: 100.0, raising=True)
    au.set_position("a@x.com", "NVDA", 10)
    _seed_flip("NVDA")
    sent = []
    monkeypatch.setattr(job.email_sender, "send_email",
                        lambda to, subject, html: sent.append(to) or True)
    assert job.main()["emails_sent"] == 1
    assert job.main()["emails_sent"] == 0          # double-fire → dedup
    assert sent == ["a@x.com"]


def test_digest_dry_run_does_not_consume_the_week(au, monkeypatch):
    """A --dry-run (operator smoke) must not mark the week as delivered."""
    import run_weekly_digest as job
    monkeypatch.setattr(job, "auth_users", au, raising=True)
    monkeypatch.setattr(job, "_close_for", lambda sym: 100.0, raising=True)
    au.set_position("a@x.com", "NVDA", 10)
    _seed_flip("NVDA")
    sent = []
    monkeypatch.setattr(job.email_sender, "send_email",
                        lambda to, subject, html: sent.append(to) or True)
    assert job.main(dry_run=True)["emails_sent"] == 0
    assert job.main()["emails_sent"] == 1          # real run still delivers
