"""Round-2 fixes for the Codex re-review of PR #95 (2026-07-18).

Covers: #1 shared yf lock across price_source + gesh_core (runtime, not regex),
#2 ET-consistent digest week key, #3 failed send does not consume the week,
#4 zero/NaN/inf quotes are not shown as a book value, #5 shares-aware staleness.
All offline; per-test tmp DBs.
"""
from __future__ import annotations

import threading
import time
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


# ─────────────── #1 shared yf lock — price_source AND gesh_core ───────────────

def test_shared_yf_lock_serializes_price_source_and_gesh_core(monkeypatch):
    """A price_source book-warm and a gesh_core SPY fetch must NOT run yfinance's
    process-global download() concurrently (cross-symbol contamination). Runtime
    proof, not a source regex (Codex re-review)."""
    import price_source
    if not price_source.install_shared_yf_lock():
        pytest.skip("gesh_core.market_fetch not importable")
    import gesh_core.market_fetch as mf
    import yfinance

    active = {"n": 0, "max": 0}
    guard = threading.Lock()

    def slow_download(*_a, **_k):
        with guard:
            active["n"] += 1
            active["max"] = max(active["max"], active["n"])
        time.sleep(0.02)
        with guard:
            active["n"] -= 1
        return None

    monkeypatch.setattr(yfinance, "download", slow_download)

    def via_gesh():
        mf.yf.download("SPY", period="1d")          # gesh_core's entry point

    def via_price_source():
        price_source.locked_yf_download("NVDA", period="1d")

    threads = [threading.Thread(target=via_gesh if i % 2 else via_price_source)
               for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert active["max"] == 1, f"cross-module concurrency reached {active['max']} — lock not shared"


def test_shared_yf_lock_install_is_idempotent():
    import price_source
    if not price_source.install_shared_yf_lock():
        pytest.skip("gesh_core.market_fetch not importable")
    import gesh_core.market_fetch as mf
    first = mf.yf
    assert price_source.install_shared_yf_lock() is True
    assert mf.yf is first                            # not re-wrapped/nested


# ─────────────── #4 zero / NaN / inf quotes are not a book value ──────────────

@pytest.mark.parametrize("bad", [0.0, -1.0, float("nan"), float("inf"),
                                 float("-inf"), True, None, "100"])
def test_valid_close_rejects_junk(bad):
    import run_weekly_digest as job
    assert job._valid_close(bad) is False


@pytest.mark.parametrize("good", [0.01, 1, 205.275, 1e6])
def test_valid_close_accepts_real(good):
    import run_weekly_digest as job
    assert job._valid_close(good) is True


def test_digest_zero_quote_is_not_shown_as_dollars(au, monkeypatch):
    import run_weekly_digest as job
    monkeypatch.setattr(job, "auth_users", au, raising=True)
    monkeypatch.setattr(job, "_close_for", lambda s: 0.0, raising=True)  # broker-offline 0
    au.set_position("a@x.com", "NVDA", 10)
    sent = []
    monkeypatch.setattr(job.email_sender, "send_email",
                        lambda to, s, html, **k: sent.append(html) or True)
    job.main()
    assert sent and "$0" not in sent[0]
    assert "Value unavailable" in sent[0]


# ─────────────── #2 digest dedup uses the SAME ET week key as the gate ────────

def test_digest_dedup_key_is_gate_et_period(au, monkeypatch):
    import sqlite3

    import run_weekly_digest as job
    monkeypatch.setattr(job, "auth_users", au, raising=True)
    monkeypatch.setattr(job, "_close_for", lambda s: 100.0, raising=True)
    au.set_position("a@x.com", "NVDA", 10)
    monkeypatch.setattr(job.email_sender, "send_email", lambda to, s, h, **k: True)
    job.main()
    con = sqlite3.connect(sh.DB_PATH)
    weeks = [r[0] for r in con.execute("SELECT iso_week FROM digest_log").fetchall()]
    con.close()
    assert weeks == [sg.period_key("digest")]        # ET gate key, not UTC strftime


# ─────────────── #3 a failed send must NOT consume the week ───────────────────

def test_failed_send_reports_failed_and_blocks_mark_ran(au, monkeypatch):
    import run_weekly_digest as job
    monkeypatch.setattr(job, "auth_users", au, raising=True)
    monkeypatch.setattr(job, "_close_for", lambda s: 100.0, raising=True)
    au.set_position("a@x.com", "NVDA", 10)
    monkeypatch.setattr(job.email_sender, "send_email", lambda to, s, h, **k: False)  # fail
    out = job.main()
    assert out["emails_sent"] == 0 and out["failed"] == 1

    # _cli must mark the period ONLY when failed == 0.
    marked = []
    monkeypatch.setattr(sg, "should_run", lambda job_, now_utc=None: (True, "open"))
    monkeypatch.setattr(sg, "mark_ran", lambda job_, now_utc=None: marked.append(job_))
    monkeypatch.setattr(job, "main", lambda dry_run=False: {"failed": 1, "emails_sent": 0})
    monkeypatch.setattr(job.job_health, "record_job_run", lambda *a, **k: None)
    job._cli([])
    assert marked == []                              # failure → period NOT consumed → retries
    monkeypatch.setattr(job, "main", lambda dry_run=False: {"failed": 0, "emails_sent": 1})
    job._cli([])
    assert marked == ["digest"]                      # clean run → marked done


# ─────────────── #5 staleness catches a share-count-only change ───────────────

def test_stale_flagged_on_share_count_change(au, monkeypatch):
    import run_weekly_digest as job
    monkeypatch.setattr(job, "auth_users", au, raising=True)
    monkeypatch.setattr(job, "_close_for", lambda s: 100.0, raising=True)
    au.set_position("a@x.com", "NVDA", 100)          # book NOW: 100 shares
    au.save_book_analysis("a@x.com", {
        "analysis": {"portfolio_verdict": "BALANCED"},
        "book": {"positions": [{"symbol": "NVDA", "shares": 1}]},   # analysed at 1 share
    })
    html = job._compose("a@x.com")
    assert "earlier version of your book" in html    # same symbol, different size → stale
