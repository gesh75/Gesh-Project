from __future__ import annotations

import concurrent.futures
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


@pytest.fixture
def au(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import auth_users

    monkeypatch.setattr(auth_users, "AUTH_DB_PATH", tmp_path / "auth.db")
    auth_users.init_auth_db()
    auth_users.create_user("customer@example.com", "password")
    return auth_users


def test_reserve_usage_is_atomic_under_30_request_race(au) -> None:
    user_id = au.resolve_user_id("customer@example.com")
    assert user_id

    def attempt(_: int) -> bool:
        return au.reserve_usage(user_id, "book_analysis", str(uuid.uuid4()), limit=5)

    with concurrent.futures.ThreadPoolExecutor(max_workers=30) as executor:
        results = list(executor.map(attempt, range(30)))
    assert sum(results) == 5


def test_one_durable_running_job_per_user_and_reservation(au) -> None:
    user_id = au.resolve_user_id("customer@example.com")
    first_id, second_id = str(uuid.uuid4()), str(uuid.uuid4())
    first = au.start_mybook_job(user_id, first_id, "fp-old", "book_analysis", limit=10)
    second = au.start_mybook_job(user_id, second_id, "fp-new", "book_analysis", limit=10)

    assert first["accepted"] is True
    assert second == {"accepted": False, "quota_exceeded": False, "job": first["job"]}
    assert au.get_active_mybook_job(user_id)["job_id"] == first_id


def test_30_concurrent_job_starts_create_one_worker_generation(au) -> None:
    user_id = au.resolve_user_id("customer@example.com")

    def attempt(index: int) -> dict:
        return au.start_mybook_job(
            user_id, f"job-{index}", f"fp-{index}", "book_analysis", limit=30
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=30) as executor:
        results = list(executor.map(attempt, range(30)))
    assert sum(result["accepted"] for result in results) == 1
    assert au.get_active_mybook_job(user_id) is not None


def test_complete_and_release_reservation_states(au) -> None:
    user_id = au.resolve_user_id("customer@example.com")
    completed = str(uuid.uuid4())
    released = str(uuid.uuid4())
    assert au.reserve_usage(user_id, "book_analysis", completed, limit=10)
    assert au.reserve_usage(user_id, "book_analysis", released, limit=10)
    assert au.complete_usage(completed) is True
    assert au.release_usage(released) is True
    assert au.get_usage_reservation(completed)["state"] == "completed"
    assert au.get_usage_reservation(released)["state"] == "released"


def test_stale_generation_cannot_publish_over_newer_job(au) -> None:
    user_id = au.resolve_user_id("customer@example.com")
    old_id, new_id = str(uuid.uuid4()), str(uuid.uuid4())
    assert au.start_mybook_job(user_id, old_id, "fp-old", "book_analysis", 10)["accepted"]
    assert au.finish_mybook_job(user_id, old_id, "fp-old", "failed", error_code="superseded")
    assert au.start_mybook_job(user_id, new_id, "fp-new", "book_analysis", 10)["accepted"]
    assert au.finish_mybook_job(user_id, new_id, "fp-new", "done", result={"generation": "new"})

    assert (
        au.finish_mybook_job(user_id, old_id, "fp-old", "done", result={"generation": "old"})
        is False
    )
    assert au.get_latest_mybook_job(user_id)["result"] == {"generation": "new"}


def test_crashed_running_job_expires_and_releases_reservation(au) -> None:
    user_id = au.resolve_user_id("customer@example.com")
    job_id = str(uuid.uuid4())
    assert au.start_mybook_job(user_id, job_id, "fp", "book_analysis", 10)["accepted"]
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    with au._conn() as con:
        con.execute(
            "UPDATE mybook_jobs SET status='running',updated_at=? WHERE job_id=?",
            (old, job_id),
        )

    assert au.expire_stale_mybook_jobs(max_age_seconds=3600) == 1
    assert au.get_active_mybook_job(user_id) is None
    assert au.get_usage_reservation(job_id)["state"] == "released"


def test_large_book_gives_llm_full_book_summary_and_explicit_coverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import server

    holdings = [
        {"symbol": f"S{i}", "equity": float(1000 - i), "portfolio_percentage": 10.0}
        for i in range(10)
    ]
    payload = {
        "holdings": holdings,
        "equity_summary": {"portfolio_equity": 9955.0},
        "unpriced": [],
    }
    seen: dict = {}

    def deep(analysis_payload, regime_context=None, truncated=None):
        seen.update(analysis_payload)
        return {"holdings_analysis": []}

    monkeypatch.setattr(server, "_deep_analyze_for_book", deep)
    monkeypatch.setattr(server, "detect_regimes", lambda symbols: {})
    monkeypatch.setattr(server, "run_monte_carlo", lambda *args, **kwargs: {})
    monkeypatch.setattr(server, "get_analyst_targets", lambda symbols: {})
    monkeypatch.setattr(server, "run_kelly_analysis", lambda *args, **kwargs: {})
    monkeypatch.setattr(server, "_run_book_radar", lambda *args, **kwargs: {})

    result = server._run_book_engines(payload)

    assert len(seen["holdings"]) == server._BOOK_ANALYSIS_MAX_HOLDINGS
    assert len(seen["all_holdings_summary"]) == 10
    assert seen["book_meta"]["portfolio_scope_source"] == "all_holdings_summary"
    assert result["detailed_coverage"] == {"analyzed": 8, "total": 10}


def test_deep_analyze_for_book_typed_truncation_retries_smaller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import llm_gateway
    import server

    holdings = [{"symbol": f"S{i}", "equity": 100 - i} for i in range(8)]
    payload = {"holdings": holdings}
    truncated = {"analyzed": len(holdings)}
    attempted_caps: list[int] = []

    def deep(analysis_payload, **kwargs):
        attempted_caps.append(len(analysis_payload["holdings"]))
        if len(attempted_caps) == 1:
            raise llm_gateway.LLMTruncatedError("AI response exceeded its configured output limit")
        return {"holdings_analysis": []}

    monkeypatch.setattr(server, "deep_analyze_portfolio", deep)
    monkeypatch.setattr(server.fair_value, "canonicalize_analysis", lambda result: result)

    result = server._deep_analyze_for_book(payload, None, truncated)

    assert result == {"holdings_analysis": []}
    assert attempted_caps == [8, 6]
    assert truncated["analyzed"] == 6


def test_run_book_engines_survives_llm_outage_with_local_engines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Customer Analyze My Portfolio must not 500 when Anthropic is out of credits.

    Kelly / Monte Carlo / radar are local math (API spend $0). The LLM narrative
    is optional. Production 2026-08-22 returned 202 then 500 because the worker
    treated Claude as the core deliverable.
    """
    import llm_gateway
    import server

    payload = {
        "holdings": [{"symbol": "TALK", "equity": 100.0, "portfolio_percentage": 50.0}],
        "equity_summary": {"portfolio_equity": 200.0},
        "unpriced": [],
    }

    def boom(*args, **kwargs):
        raise llm_gateway.LLMUnavailableError("credit balance too low")

    monkeypatch.setattr(server, "_deep_analyze_for_book", boom)
    monkeypatch.setattr(server, "detect_regimes", lambda symbols: {})
    monkeypatch.setattr(
        server, "run_monte_carlo", lambda *a, **k: {"var_95": 10.0, "current_equity": 200.0}
    )
    monkeypatch.setattr(server, "get_analyst_targets", lambda symbols: {})
    monkeypatch.setattr(
        server, "run_kelly_analysis", lambda *a, **k: {"holdings": [], "action": "HOLD"}
    )
    monkeypatch.setattr(server, "_run_book_radar", lambda *a, **k: {"mode": "NO_ACTION"})

    result = server._run_book_engines(payload)

    assert result["analysis"]["ai_unavailable"] is True
    assert result["analysis"]["holdings_analysis"] == []
    assert result["analysis"].get("portfolio_score") is None
    assert result["kelly"]["action"] == "HOLD"
    assert result["monte_carlo"]["var_95"] == 10.0
    assert "error" not in result["kelly"]


# ── [CLAUDE V5 2026-07-19] reservation transitions are tenant-scoped ────────────
def test_release_usage_scoped_by_user_id(au):
    au.create_user("t1@x.com", "pw")
    au.create_user("t2@x.com", "pw")
    uid1 = au.resolve_user_id("t1@x.com")
    uid2 = au.resolve_user_id("t2@x.com")
    assert au.reserve_usage(uid1, "book_analysis", "job-uid1", limit=10) is True
    # a wrong-tenant release must NOT touch tenant 1's reservation
    assert au.release_usage("job-uid1", uid2) is False
    assert au.get_usage_reservation("job-uid1")["state"] == "reserved"
    # the rightful tenant can release it
    assert au.release_usage("job-uid1", uid1) is True
    assert au.get_usage_reservation("job-uid1")["state"] == "released"


def test_allow_mybook_fail_retry_cools_down_per_user_and_book():
    import server

    server._MYBOOK_FAIL_RETRY_AT.clear()
    assert server._allow_mybook_fail_retry("u1", "fp-a", now=100.0) is True
    assert server._allow_mybook_fail_retry("u1", "fp-a", now=101.0) is False
    assert server._allow_mybook_fail_retry("u2", "fp-a", now=101.0) is True
    assert server._allow_mybook_fail_retry("u1", "fp-b", now=101.0) is True
    cooled = 100.0 + server._MYBOOK_FAIL_RETRY_COOLDOWN_S
    assert server._allow_mybook_fail_retry("u1", "fp-a", now=cooled) is True
