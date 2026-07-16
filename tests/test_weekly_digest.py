from __future__ import annotations

from pathlib import Path

import pytest

import signal_history as sh


@pytest.fixture
def au(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import auth_users as _au
    monkeypatch.setattr(_au, "AUTH_DB_PATH", tmp_path / "auth.db")
    _au.init_auth_db()
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
