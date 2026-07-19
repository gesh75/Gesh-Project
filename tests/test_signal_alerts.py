"""Signal-change alert job: snapshot the customer symbol universe, then email
each affected customer ONE digest of their confirmed flips. LLM-free."""
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
    _au.create_user("b@x.com", "pw-test")
    _au.create_user("oc@x.com", "pw-test")
    return _au


@pytest.fixture(autouse=True)
def _sig_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sh, "DB_PATH", tmp_path / "sig.db")


def _days_ago(n: int) -> str:
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%d")


def test_all_customer_symbols_unions_watchlist_and_book(au):
    au.add_watch("a@x.com", "NVDA")
    au.set_position("a@x.com", "TSLA", 1)
    au.add_watch("b@x.com", "AMD")
    assert au.all_customer_symbols() == {"NVDA", "TSLA", "AMD"}


def test_customers_watching_respects_opt_out(au):
    au.add_watch("a@x.com", "NVDA")
    au.add_watch("b@x.com", "NVDA")
    au.set_alerts_opt_out("b@x.com", True)
    assert au.customers_watching("NVDA") == ["a@x.com"]


def test_job_snapshots_and_emails_one_digest_per_customer(au, monkeypatch):
    import run_signal_alerts as job
    monkeypatch.setattr(job, "auth_users", au, raising=True)
    au.add_watch("a@x.com", "NVDA")
    au.add_watch("a@x.com", "AMD")
    # history: NVDA and AMD both confirm a flip today
    for d in (_days_ago(3), _days_ago(2)):
        sh.record_snapshots([{"symbol": s, "verdict": "BUY", "score": 0.4,
                              "confidence": 80} for s in ("NVDA", "AMD")], date=d)
    for d in (_days_ago(1), _days_ago(0)):
        sh.record_snapshots([{"symbol": s, "verdict": "HOLD", "score": 0.1,
                              "confidence": 70} for s in ("NVDA", "AMD")], date=d)
    monkeypatch.setattr(job, "_consensus_for", lambda syms: [], raising=True)  # snapshot already seeded
    sent = []
    monkeypatch.setattr(job.email_sender, "send_email",
                        lambda to, subject, html, **kw: sent.append((to, subject, html)) or True)
    summary = job.main()
    assert summary["emails_sent"] == 1                    # ONE digest, not one per symbol
    to, subject, html = sent[0]
    assert to == "a@x.com" and "NVDA" in html and "AMD" in html
    assert "BUY" in html and "HOLD" in html               # prior → new verdict shown
    assert "/alerts/unsubscribe" in html                  # opt-out link present


def test_job_is_idempotent_per_day(au, monkeypatch):
    """Second run the same day must not re-email the same flip."""
    import run_signal_alerts as job
    monkeypatch.setattr(job, "auth_users", au, raising=True)
    au.add_watch("a@x.com", "NVDA")
    for d in (_days_ago(3), _days_ago(2)):
        sh.record_snapshots([{"symbol": "NVDA", "verdict": "BUY"}], date=d)
    for d in (_days_ago(1), _days_ago(0)):
        sh.record_snapshots([{"symbol": "NVDA", "verdict": "HOLD"}], date=d)
    monkeypatch.setattr(job, "_consensus_for", lambda syms: [], raising=True)
    sent = []
    monkeypatch.setattr(job.email_sender, "send_email",
                        lambda to, subject, html, **kw: sent.append(to) or True)
    job.main()
    job.main()
    assert len(sent) == 1


def test_summary_flags_disabled_email_delivery_when_key_unset(au, monkeypatch):
    """RESEND_API_KEY unset -> email_sender takes the DEV NO-OP path silently
    (returns False, sends nothing). Under launchd (no shell env inheritance)
    this happens for real, so the job's summary must say so — otherwise a
    silent no-op looks identical to "no flips today" in the logs."""
    import run_signal_alerts as job
    monkeypatch.setattr(job, "auth_users", au, raising=True)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    au.add_watch("a@x.com", "NVDA")
    for d in (_days_ago(3), _days_ago(2)):
        sh.record_snapshots([{"symbol": "NVDA", "verdict": "BUY"}], date=d)
    for d in (_days_ago(1), _days_ago(0)):
        sh.record_snapshots([{"symbol": "NVDA", "verdict": "HOLD"}], date=d)
    monkeypatch.setattr(job, "_consensus_for", lambda syms: [], raising=True)
    # do NOT monkeypatch send_email — exercise the real DEV NO-OP path
    summary = job.main()
    assert summary["emails_sent"] == 0
    assert summary["email_delivery"] == "DISABLED — RESEND_API_KEY unset"


def test_summary_omits_email_delivery_marker_on_quiet_day(au, monkeypatch):
    """No flips -> nothing was attempted, so the marker must NOT appear (a quiet
    day is not the same failure mode as a misconfigured key)."""
    import run_signal_alerts as job
    monkeypatch.setattr(job, "auth_users", au, raising=True)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.setattr(job, "_consensus_for", lambda syms: [], raising=True)
    summary = job.main()
    assert summary["emails_sent"] == 0
    assert "email_delivery" not in summary


def test_unsubscribe_get_confirms_post_mutates(au, monkeypatch):
    """Scanner-safe contract (Codex P1 2026-07-17): email link scanners FETCH
    every URL, so GET must never mutate — it renders a confirmation page; only
    POST performs the opt-out."""
    from starlette.testclient import TestClient

    import server
    monkeypatch.setattr(server.auth_users, "AUTH_DB_PATH", au.AUTH_DB_PATH)
    c = TestClient(server.app, raise_server_exceptions=False)
    tok = au.issue_token("a@x.com", ttl=86400, typ="alerts_unsub")
    # GET = a scanner prefetch: 200 confirmation page, NO mutation
    r = c.get(f"/alerts/unsubscribe?token={tok}")
    assert r.status_code == 200 and "confirm" in r.text.lower()
    assert au.get_alerts_opt_out("a@x.com") is False       # scanner did not unsubscribe
    # POST (the page's confirm button, JSON body) actually mutates
    r = c.post("/alerts/unsubscribe", json={"token": tok})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert au.get_alerts_opt_out("a@x.com") is True
    # a session token must NOT work on either method (typed-token discipline)
    sess = au.issue_token("b@x.com", ttl=86400, typ="session")
    assert c.get(f"/alerts/unsubscribe?token={sess}").status_code == 400
    assert c.post(f"/alerts/unsubscribe?token={sess}").status_code == 400


def test_unsubscribe_one_click_rfc8058(au, monkeypatch):
    """Mail providers' one-click unsubscribe POSTs List-Unsubscribe=One-Click as
    an urlencoded body with the token still in the URL (RFC 8058)."""
    from starlette.testclient import TestClient

    import server
    monkeypatch.setattr(server.auth_users, "AUTH_DB_PATH", au.AUTH_DB_PATH)
    c = TestClient(server.app, raise_server_exceptions=False)
    tok = au.issue_token("oc@x.com", ttl=86400, typ="digest_unsub")
    r = c.post(f"/digest/unsubscribe?token={tok}",
               content="List-Unsubscribe=One-Click",
               headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert au.get_digest_opt_out("oc@x.com") is True


def test_alert_email_carries_one_click_headers(au, monkeypatch):
    """The alert job must pass RFC 8058 List-Unsubscribe[-Post] headers so mail
    clients show their native unsubscribe button (which POSTs, never GETs)."""
    import run_signal_alerts as job
    monkeypatch.setattr(job, "auth_users", au, raising=True)
    au.add_watch("a@x.com", "NVDA")
    for d in (_days_ago(3), _days_ago(2)):
        sh.record_snapshots([{"symbol": "NVDA", "verdict": "BUY"}], date=d)
    for d in (_days_ago(1), _days_ago(0)):
        sh.record_snapshots([{"symbol": "NVDA", "verdict": "HOLD"}], date=d)
    monkeypatch.setattr(job, "_consensus_for", lambda syms: [], raising=True)
    captured = {}
    monkeypatch.setattr(job.email_sender, "send_email",
                        lambda to, subject, html, **kw: captured.update(kw) or True)
    job.main()
    hdrs = captured.get("headers") or {}
    assert "/alerts/unsubscribe?token=" in hdrs.get("List-Unsubscribe", "")
    assert hdrs.get("List-Unsubscribe-Post") == "List-Unsubscribe=One-Click"
