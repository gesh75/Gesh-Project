"""Tests for the five retention P1 fixes (Codex review, 2026-07-17).

P1#1  ET schedule gate + catch-up (schedule_gate.py — jobs promised ET, ran EEST)
P1#2  Durable flip events + retry (signal_flips — failed sends were lost forever)
P1#3  Weekly digest honesty ($0/partial totals, equal-weight books, stale verdicts)
P1#4  Complete yfinance serialization (locked adapter + static raw-call ban)
P1#5  is covered in test_signal_alerts.py / test_preferences.py (GET confirms,
      POST mutates, RFC 8058 one-click) — kept there beside the older pins.

All offline; DBs are per-test tmp files.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import schedule_gate as sg
import signal_history as sh


@pytest.fixture(autouse=True)
def _dbs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sh, "DB_PATH", tmp_path / "sig.db")
    monkeypatch.setattr(sg, "DB_PATH", tmp_path / "gate.db")


@pytest.fixture
def au(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import auth_users as _au
    monkeypatch.setattr(_au, "AUTH_DB_PATH", tmp_path / "auth.db")
    _au.init_auth_db()
    return _au


def _days_ago(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%d")


def _seed(sym: str, older: str, newer: str) -> None:
    for d in (_days_ago(3), _days_ago(2)):
        sh.record_snapshots([{"symbol": sym, "verdict": older}], date=d)
    for d in (_days_ago(1), _days_ago(0)):
        sh.record_snapshots([{"symbol": sym, "verdict": newer}], date=d)


# ───────────────────────── P1#1 — ET schedule gate ───────────────────────────

def _utc(y, m, d, hh, mm):
    # Build a UTC instant whose ET wall clock is what the test names. July =
    # EDT (UTC-4): ET 11:05 == 15:05 UTC.
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


def test_alerts_window_is_eastern_not_local():
    # Fri 2026-07-17 15:05 UTC == 11:05 ET → open. 14:00 UTC == 10:00 ET → closed.
    ok, _ = sg.should_run("alerts", _utc(2026, 7, 17, 15, 5))
    assert ok
    ok, why = sg.should_run("alerts", _utc(2026, 7, 17, 14, 0))
    assert not ok and "window closed" in why


def test_alerts_weekend_closed():
    ok, _ = sg.should_run("alerts", _utc(2026, 7, 18, 16, 0))   # Saturday ET
    assert not ok


def test_alerts_once_per_et_day_with_catchup():
    # 11:05 ET fires; a later tick the SAME ET day is refused (already ran)…
    t1 = _utc(2026, 7, 17, 15, 5)
    assert sg.should_run("alerts", t1)[0]
    sg.mark_ran("alerts", t1)
    ok, why = sg.should_run("alerts", _utc(2026, 7, 17, 19, 0))
    assert not ok and "already ran" in why
    # …but a tick at 20:00 ET on a day whose 11:05 was slept through still runs
    # (catch-up): next Monday, nothing marked.
    assert sg.should_run("alerts", _utc(2026, 7, 20, 23, 30))[0]   # Mon 19:30 ET


def test_digest_friday_et_with_weekend_catchup():
    fri_early = _utc(2026, 7, 17, 20, 0)    # Fri 16:00 ET — before 17:15
    fri_late = _utc(2026, 7, 17, 21, 20)    # Fri 17:20 ET — open
    sat = _utc(2026, 7, 18, 15, 0)          # Saturday — catch-up open
    mon = _utc(2026, 7, 20, 15, 0)          # Monday — closed
    assert not sg.should_run("digest", fri_early)[0]
    assert sg.should_run("digest", fri_late)[0]
    assert sg.should_run("digest", sat)[0]
    assert not sg.should_run("digest", mon)[0]


def test_digest_once_per_iso_week():
    fri = _utc(2026, 7, 17, 21, 20)
    sg.mark_ran("digest", fri)
    assert not sg.should_run("digest", _utc(2026, 7, 18, 15, 0))[0]   # Sat same week
    assert sg.should_run("digest", _utc(2026, 7, 24, 21, 20))[0]      # next Friday


def test_crash_leaves_period_unmarked_so_next_tick_retries():
    # mark_ran is only called after main() returns — simulate the crash path by
    # simply not calling it: the same-day re-check must still say run.
    t = _utc(2026, 7, 17, 15, 5)
    assert sg.should_run("alerts", t)[0]
    assert sg.should_run("alerts", _utc(2026, 7, 17, 16, 0))[0]


# ─────────────────── P1#2 — durable flip events + retry ──────────────────────

def test_flip_event_survives_streak_moving_on():
    """The live detector forgets a flip once the streak grows past the boundary;
    the EVENT table must not."""
    _seed("NVDA", "BUY", "HOLD")
    assert len(sh.record_confirmed_flips(["NVDA"])) == 1
    # streak extends one more day → live detector now sees nothing…
    sh.record_snapshots([{"symbol": "NVDA", "verdict": "HOLD"}],
                        date=(datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d"))
    assert sh.detect_confirmed_flips(["NVDA"]) == []
    # …but the event is still queryable for delivery.
    flips = sh.flips_since(["NVDA"], _days_ago(6))
    assert len(flips) == 1 and flips[0]["new_verdict"] == "HOLD"


def test_record_is_idempotent():
    _seed("NVDA", "BUY", "HOLD")
    assert len(sh.record_confirmed_flips(["NVDA"])) == 1
    assert sh.record_confirmed_flips(["NVDA"]) == []          # second observe: no new event
    assert len(sh.flips_since(["NVDA"], _days_ago(6))) == 1


def test_insufficient_data_is_not_a_flip():
    """BUY → INSUFFICIENT DATA is a data outage, not a signal — it must never
    become a customer alert (Codex follow-up folded into the event layer)."""
    _seed("NVDA", "BUY", "INSUFFICIENT DATA")
    assert sh.record_confirmed_flips(["NVDA"]) == []
    assert sh.flips_since(["NVDA"], _days_ago(6)) == []


def test_failed_send_is_retried_next_run(au, monkeypatch):
    """THE P1: a send failure must not lose the alert. Run 1 fails → no alert_log
    row → run 2 retries the same durable event and succeeds."""
    import run_signal_alerts as job
    monkeypatch.setattr(job, "auth_users", au, raising=True)
    au.add_watch("a@x.com", "NVDA")
    _seed("NVDA", "BUY", "HOLD")
    monkeypatch.setattr(job, "_consensus_for", lambda syms: [], raising=True)
    attempts = []
    monkeypatch.setattr(job.email_sender, "send_email",
                        lambda to, s, h, **kw: attempts.append(to) or False)   # fail
    assert job.main()["emails_sent"] == 0
    assert attempts == ["a@x.com"]
    monkeypatch.setattr(job.email_sender, "send_email",
                        lambda to, s, h, **kw: attempts.append(to) or True)    # recover
    assert job.main()["emails_sent"] == 1                     # retried, delivered
    assert job.main()["emails_sent"] == 0                     # then deduped
    assert attempts == ["a@x.com", "a@x.com"]


def test_weekly_digest_includes_flip_confirmed_earlier_in_week(au, monkeypatch):
    """Friday's digest must not omit a flip confirmed Monday (the live detector
    would have — the streak boundary moved past it days ago)."""
    import run_weekly_digest as job
    monkeypatch.setattr(job, "auth_users", au, raising=True)
    monkeypatch.setattr(job, "_close_for", lambda sym: 100.0, raising=True)
    au.set_position("a@x.com", "NVDA", 10)
    # flip confirmed 4 days ago, streak has moved on since
    for d in (_days_ago(6), _days_ago(5)):
        sh.record_snapshots([{"symbol": "NVDA", "verdict": "BUY"}], date=d)
    for d in (_days_ago(4), _days_ago(3)):
        sh.record_snapshots([{"symbol": "NVDA", "verdict": "HOLD"}], date=d)
    sh.record_confirmed_flips(["NVDA"])                       # alerts job saw it Monday
    for d in (_days_ago(2), _days_ago(1), _days_ago(0)):      # streak extends
        sh.record_snapshots([{"symbol": "NVDA", "verdict": "HOLD"}], date=d)
    assert sh.detect_confirmed_flips(["NVDA"]) == []          # live detector is past it
    sent = []
    monkeypatch.setattr(job.email_sender, "send_email",
                        lambda to, s, html, **kw: sent.append(html) or True)
    assert job.main()["emails_sent"] == 1
    assert "NVDA" in sent[0] and "HOLD" in sent[0]            # Monday's flip present


# ─────────────────── P1#3 — weekly digest honesty guards ─────────────────────

def _digest_html(au, monkeypatch, closes: dict, email="a@x.com"):
    import run_weekly_digest as job
    monkeypatch.setattr(job, "auth_users", au, raising=True)
    monkeypatch.setattr(job, "_close_for", lambda sym: closes.get(sym), raising=True)
    sent = []
    monkeypatch.setattr(job.email_sender, "send_email",
                        lambda to, s, html, **kw: sent.append(html) or True)
    job.main()
    return sent[0] if sent else None


def test_missing_quote_never_renders_zero_total(au, monkeypatch):
    au.set_position("a@x.com", "NVDA", 10)
    au.set_position("a@x.com", "TSLA", 5)
    html = _digest_html(au, monkeypatch, {"NVDA": 100.0, "TSLA": None})
    assert html is not None
    assert "$0" not in html
    assert "Value unavailable" in html
    assert "1 of 2" in html                                  # honest coverage count


def test_full_coverage_shows_real_total(au, monkeypatch):
    au.set_position("a@x.com", "NVDA", 10)
    html = _digest_html(au, monkeypatch, {"NVDA": 100.0})
    assert "$1,000" in html


def test_equal_weight_book_shows_no_dollars(au, monkeypatch):
    au.set_position("a@x.com", "NVDA", 10)
    au.set_equal_weight("a@x.com", True)
    html = _digest_html(au, monkeypatch, {"NVDA": 100.0})
    assert "$" not in html.split("track-record")[0].split("Your book")[1].split("</p>")[0]
    assert "private-size mode" in html


def test_stale_verdict_is_labeled(au, monkeypatch):
    au.set_position("a@x.com", "NVDA", 10)
    au.save_book_analysis("a@x.com", {
        "analysis": {"portfolio_verdict": "BALANCED GROWTH"},
        "book": {"positions": [{"symbol": "TSLA"}]},         # analysis was for a DIFFERENT book
    })
    html = _digest_html(au, monkeypatch, {"NVDA": 100.0})
    assert "earlier version of your book" in html


# ─────────────── P1#4 — yfinance fully serialized (static ban) ───────────────

def test_no_raw_yf_download_outside_price_source():
    """Every yf.download call must go through price_source (the lock). A raw call
    anywhere else reintroduces the NVDA-priced-as-TSLA cross-symbol race."""
    import re
    import subprocess
    root = Path(__file__).resolve().parent.parent
    tracked = subprocess.run(
        ["git", "ls-files", "*.py"], cwd=root, capture_output=True, text=True,
        check=True).stdout.splitlines()
    offenders = []
    for rel in tracked:
        if rel.startswith(("tests/", ".claude/")) or rel == "price_source.py":
            continue
        text = (root / rel).read_text(errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            code = line.split("#", 1)[0]
            if re.search(r"(?<![\w.])yf\.download\(|(?<![\w.])yfinance\.download\(", code):
                offenders.append(f"{rel}:{i}")
    assert not offenders, (
        "raw yf.download() outside price_source.py — route through "
        f"price_source.locked_yf_download: {offenders}")


def test_locked_adapter_holds_the_lock(monkeypatch):
    import yfinance

    import price_source
    seen = {}

    def fake_download(*a, **k):
        seen["locked"] = price_source._YF_DOWNLOAD_LOCK.locked()
        return None

    monkeypatch.setattr(yfinance, "download", fake_download)
    price_source.locked_yf_download("NVDA", period="1d")
    assert seen["locked"] is True


# ───────────────────────── hygiene — DB file mode ────────────────────────────

def test_signal_db_created_group_unreadable(tmp_path, monkeypatch):
    """alert_log/digest_log carry customer emails — the DB must not be 0644
    (the auth DB was already 0600; this one shipped world-readable)."""
    import os
    import stat
    db = tmp_path / "fresh.db"
    monkeypatch.setattr(sh, "DB_PATH", db)
    sh.record_snapshots([{"symbol": "NVDA", "verdict": "BUY"}])
    mode = stat.S_IMODE(os.stat(db).st_mode)
    assert mode & 0o077 == 0, f"DB is group/world accessible: {oct(mode)}"
