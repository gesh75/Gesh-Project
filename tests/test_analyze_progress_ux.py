"""Analyze-progress UX: the customer must never mistake "working" for "broken".

Context (QA audit 2026-07-19). `POST /api/mybook/analyze` is an async 202+poll
contract: the job runs for minutes and the finished result is handed to the NEXT
poll. A QA agent clicked Analyze, waited 1m13s of a 3m02s run, navigated to
/mybook/report, saw the stale-analysis banner, and filed "Test 4 FAILED — daily
rate limit exhausted". Nothing was broken. Three gaps made that misreading the
rational conclusion, and these tests pin all three:

  1. the spinner was STATIC, so elapsed progress was invisible;
  2. /mybook/report showed "Analysis out of date" while a COMPLETED analysis for
     those exact positions sat undelivered in mybook_jobs;
  3. the offline-RH retry logged every 60s forever (26,445 identical lines,
     ~6% of a 21MB log), which buries a real error.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


@pytest.fixture
def au(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import auth_users as _au

    monkeypatch.setattr(_au, "AUTH_DB_PATH", tmp_path / "auth.db")
    _au.init_auth_db()
    _au.create_user("cust@x.com", "test-password")
    return _au


def _finished_job(au, email: str, fingerprint: str, *, delivered: bool) -> str:
    """Drive a job through the real state machine to done (optionally delivered)."""
    user_id = au.resolve_user_id(email)
    job_id = f"job-{fingerprint}-{delivered}"
    au.start_mybook_job(user_id, job_id, fingerprint, "book_analysis", 10)
    au.mark_mybook_job_running(user_id, job_id, fingerprint)
    au.finish_mybook_job(user_id, job_id, fingerprint, "done", result={"analysis": {}})
    if delivered:
        au.mark_mybook_job_delivered(user_id, job_id)
    return job_id


# ─── 2. undelivered-result handoff on /mybook/report ──────────────────────────

def test_completed_undelivered_analysis_for_current_book_is_detected(au):
    """The exact state the QA agent hit: job done, never collected."""
    import server

    au.set_position("cust@x.com", "NVDA", 10)
    fp = server._book_fingerprint(au.get_positions("cust@x.com"))
    _finished_job(au, "cust@x.com", fp, delivered=False)

    assert server._ready_analysis_pending("cust@x.com") is True


def test_delivered_result_is_not_reported_as_pending(au):
    """Once collected it is no longer 'ready to load' — that would loop forever."""
    import server

    au.set_position("cust@x.com", "NVDA", 10)
    fp = server._book_fingerprint(au.get_positions("cust@x.com"))
    _finished_job(au, "cust@x.com", fp, delivered=True)

    assert server._ready_analysis_pending("cust@x.com") is False


def test_result_for_a_different_book_is_not_reported_as_pending(au):
    """A held result for the OLD book must not be advertised as current — that
    would replace an honest stale warning with a false all-clear."""
    import server

    au.set_position("cust@x.com", "NVDA", 10)
    _finished_job(au, "cust@x.com", "fp-of-some-other-book", delivered=False)

    assert server._ready_analysis_pending("cust@x.com") is False


def test_no_job_at_all_is_not_pending(au):
    import server

    au.set_position("cust@x.com", "NVDA", 10)
    assert server._ready_analysis_pending("cust@x.com") is False


def test_failed_job_is_not_advertised_as_a_ready_result(au):
    """A failed run leaves an undelivered row too — it must not be offered."""
    import server

    au.set_position("cust@x.com", "NVDA", 10)
    fp = server._book_fingerprint(au.get_positions("cust@x.com"))
    user_id = au.resolve_user_id("cust@x.com")
    au.start_mybook_job(user_id, "j-fail", fp, "book_analysis", 10)
    au.finish_mybook_job(user_id, "j-fail", fp, "failed", error_code="boom")

    assert server._ready_analysis_pending("cust@x.com") is False


def test_pending_check_never_raises_on_a_broken_auth_db(au, monkeypatch):
    """Read-only nicety on a print-friendly page — it must degrade, not 500."""
    import server

    monkeypatch.setattr(
        au, "get_latest_mybook_job", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("db gone"))
    )
    assert server._ready_analysis_pending("cust@x.com") is False


def test_report_offers_the_held_result_instead_of_a_bare_stale_warning(au, monkeypatch):
    """The whole point: don't tell someone their analysis is out of date when a
    matching fresh one is sitting on disk waiting to be collected."""
    import server

    au.set_position("cust@x.com", "NVDA", 10)
    monkeypatch.setattr(server, "_customer_book_payload", lambda *_a, **_k: {
        "holdings": [{"symbol": "NVDA", "quantity": 10, "current_price": 200.0,
                      "equity": 2000.0, "portfolio_percentage": 100.0}],
        "equity_summary": {"portfolio_equity": 2000.0},
        "unpriced": [],
    })
    monkeypatch.setattr(au, "get_latest_book_analysis",
                        lambda *_a, **_k: {"_fp": "stale-fp", "_saved_at": "2026-07-19T07:09"})
    monkeypatch.setattr(server, "_ready_analysis_pending", lambda *_a, **_k: True)

    html = server._render_book_report("cust@x.com")

    assert "newer analysis is ready" in html.lower()
    # The alarming "your positions changed / numbers are wrong" copy must be gone,
    # replaced by an actionable one-click message.
    assert "reflect the OLD portfolio" not in html


def test_report_still_warns_when_nothing_is_held(au, monkeypatch):
    """Regression guard: the honest stale banner must survive for the real
    stale case — this fix must not silence it."""
    import server

    au.set_position("cust@x.com", "NVDA", 10)
    monkeypatch.setattr(server, "_customer_book_payload", lambda *_a, **_k: {
        "holdings": [{"symbol": "NVDA", "quantity": 10, "current_price": 200.0,
                      "equity": 2000.0, "portfolio_percentage": 100.0}],
        "equity_summary": {"portfolio_equity": 2000.0},
        "unpriced": [],
    })
    monkeypatch.setattr(au, "get_latest_book_analysis",
                        lambda *_a, **_k: {"_fp": "stale-fp", "_saved_at": "2026-07-19T07:09"})
    monkeypatch.setattr(server, "_ready_analysis_pending", lambda *_a, **_k: False)

    html = server._render_book_report("cust@x.com")

    assert "Analysis out of date" in html
    assert "reflect the OLD portfolio" in html


# ─── 3. throttled repeat logging ──────────────────────────────────────────────

def test_first_occurrence_logs_immediately():
    """A new failure must be loud on the FIRST hit — throttling must never delay
    the signal, only the repeats."""
    import server

    server._reset_throttled_logs()
    assert server._throttled_log_due("k", interval=300.0) == 0


def test_repeats_inside_the_window_are_suppressed():
    import server

    server._reset_throttled_logs()
    assert server._throttled_log_due("k", interval=300.0) == 0
    for _ in range(50):
        assert server._throttled_log_due("k", interval=300.0) is None


def test_suppressed_count_is_reported_when_the_window_expires(monkeypatch):
    """The count is the honesty requirement: a throttled log must say how much
    it swallowed, or it silently understates an outage."""
    import server

    server._reset_throttled_logs()
    clock = {"t": 1000.0}
    monkeypatch.setattr(server.time, "monotonic", lambda: clock["t"])

    assert server._throttled_log_due("k", interval=300.0) == 0
    for _ in range(9):
        assert server._throttled_log_due("k", interval=300.0) is None
    clock["t"] += 301.0
    assert server._throttled_log_due("k", interval=300.0) == 9


def test_distinct_keys_throttle_independently():
    """One noisy endpoint must not mute a different, unrelated failure."""
    import server

    server._reset_throttled_logs()
    assert server._throttled_log_due("a", interval=300.0) == 0
    assert server._throttled_log_due("b", interval=300.0) == 0


def test_portfolio_offline_fallback_logs_are_throttled(monkeypatch, caplog):
    """End-to-end on the real call site: 60 offline hits must not emit 60 lines."""
    import logging

    import server
    from data_fetcher import RobinhoodOfflineError

    server._reset_throttled_logs()
    monkeypatch.setattr(server, "build_data_payload",
                        lambda *_a, **_k: (_ for _ in ()).throw(RobinhoodOfflineError("dead")))
    monkeypatch.setattr(server, "load_snapshot_payload", lambda *_a, **_k: {
        "holdings": [], "watchlist": [], "snapshot_age_seconds": 94609,
        "equity_summary": {"portfolio_equity": 0.0, "adjusted_equity_previous_close": 0.0},
    })

    with caplog.at_level(logging.WARNING):
        for _ in range(60):
            server.get_portfolio()

    offline_lines = [r for r in caplog.records if "live RH offline" in r.getMessage()]
    assert len(offline_lines) == 1, f"expected 1 throttled line, got {len(offline_lines)}"


# ─── 1. live elapsed counter in the analyze spinner ───────────────────────────

def _dashboard() -> str:
    return (Path(__file__).resolve().parents[1] / "templates" / "dashboard.html").read_text()


def _analyze_fn() -> str:
    """Just the body of mybookAnalyze() — bounded at the col-0 closing brace, so
    these assertions can't be satisfied by unrelated code elsewhere in the file."""
    html = _dashboard()
    body = html[html.index("async function mybookAnalyze()"):]
    return body[:body.index("\n}")]


def test_analyze_spinner_renders_a_live_elapsed_counter():
    """Static text cannot distinguish 'working' from 'hung'. A ticking counter can."""
    body = _analyze_fn()

    assert "mybook-elapsed" in body, "spinner has no elapsed-time element"
    assert "setInterval" in body, "elapsed element is never updated"


def test_analyze_elapsed_timer_is_always_cleared():
    """A leaked setInterval keeps ticking against a detached node forever."""
    body = _analyze_fn()

    assert "clearInterval" in body, "elapsed timer is never cleared"
    assert "finally" in body, "timer cleanup is not in a finally block"


def test_analyze_tells_the_user_leaving_does_not_lose_the_result():
    """Navigating away is exactly what the QA agent did. The copy must say the
    result survives, because it does (the job completes and is held)."""
    body = _analyze_fn()

    assert "navigate away" in body.lower() or "leave" in body.lower()


def test_analyze_poll_budget_still_outlasts_a_slow_run():
    """The 202+poll contract only works if the budget exceeds a large-book run
    (~3min observed). Guard the constant so a future edit can't silently shrink it."""
    body = _analyze_fn()

    attempts = int(re.search(r"attempt<(\d+)", body).group(1))
    wait_ms = int(re.search(r"if\(res\.status===202\)\{ await wait\((\d+)\)", body).group(1))
    assert attempts * wait_ms / 1000 >= 300, "poll budget dropped below 5 minutes"
