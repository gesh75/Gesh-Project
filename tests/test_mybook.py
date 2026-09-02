"""Customer 'analyze MY portfolio' (v2 of the customer surface).

The customer enters their OWN positions (symbol + shares [+ avg cost]); the server
values them from public quotes and runs the owner engine suite on THEIR book —
deep AI analysis, Kelly, Monte Carlo. Everything is derived from
customer_positions + live prices (zero owner data), so /api/mybook is mask-exempt:
the customer must see their OWN dollars raw.

Same offline rules as the rest of the suite: prices/engines/LLM are stubbed.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient


def _mybook_analyze(c: TestClient):
    """POST /api/mybook/analyze — poll through HTTP 202 until a terminal response."""
    last = None
    for _ in range(80):
        last = c.post("/api/mybook/analyze", json={})
        if last.status_code != 202:
            return last
        time.sleep(0.02)
    return last


@pytest.fixture
def au(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import auth_users as _au
    monkeypatch.setattr(_au, "AUTH_DB_PATH", tmp_path / "auth.db")
    _au.init_auth_db()
    for email in (
        "a@x.com",
        "b@x.com",
        "cust@x.com",
        "other@x.com",
        "georgy75@gmail.com",
    ):
        _au.create_user(email, "test-password")
    return _au


# ─── storage layer ────────────────────────────────────────────────────────────

def test_set_get_remove_roundtrip(au):
    assert au.get_positions("a@x.com") == []
    assert au.set_position("a@x.com", "nvda", 10) is True
    assert au.set_position("a@x.com", "AAPL", 2.5, avg_cost=150.0) is True
    got = au.get_positions("a@x.com")
    assert [p["symbol"] for p in got] == ["NVDA", "AAPL"]     # uppercased, insertion order
    assert got[1]["shares"] == 2.5 and got[1]["avg_cost"] == 150.0
    assert au.set_position("a@x.com", "NVDA", 20) is True     # upsert = update
    assert [p for p in au.get_positions("a@x.com") if p["symbol"] == "NVDA"][0]["shares"] == 20
    au.remove_position("a@x.com", "nvda")
    assert [p["symbol"] for p in au.get_positions("a@x.com")] == ["AAPL"]


def test_positions_per_user_isolated(au):
    au.set_position("a@x.com", "NVDA", 1)
    au.set_position("b@x.com", "TSLA", 2)
    assert [p["symbol"] for p in au.get_positions("a@x.com")] == ["NVDA"]
    assert [p["symbol"] for p in au.get_positions("b@x.com")] == ["TSLA"]


def test_set_position_rejects_garbage(au):
    assert au.set_position("a@x.com", "NVDA", 0) is False       # zero shares
    assert au.set_position("a@x.com", "NVDA", -5) is False      # negative
    assert au.set_position("a@x.com", "NVDA", "lots") is False  # non-numeric
    assert au.set_position("a@x.com", "NVDA", 1, avg_cost="x") is False
    assert au.get_positions("a@x.com") == []


def test_position_cap_new_refused_update_allowed(au):
    for i in range(au._MAX_POSITIONS):
        assert au.set_position("a@x.com", f"SYM{i}", 1) is True
    assert au.set_position("a@x.com", "NEWSYM", 1) is False       # cap
    assert au.set_position("a@x.com", "SYM0", 99) is True         # update at cap OK
    assert len(au.get_positions("a@x.com")) == au._MAX_POSITIONS


# ─── endpoints (session-scoped, mask-exempt) ─────────────────────────────────

@pytest.fixture
def client(au, monkeypatch):
    import server
    monkeypatch.setattr(server.auth_users, "AUTH_DB_PATH", au.AUTH_DB_PATH)
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: True)
    prices = {"NVDA": 100.0, "AAPL": 200.0}
    # Book valuation reads _latest_close (via _cached_close); /api/analyze-ticker
    # reads _latest_close_meta. Stub BOTH from one map — stubbing only the wrapper
    # left the analyze-ticker tests below fetching a live quote, which conftest's
    # socket-level `_no_network` cannot see (yfinance fetches via curl_cffi).
    monkeypatch.setattr(server, "_latest_close", lambda sym: prices.get(sym), raising=True)
    monkeypatch.setattr(server, "_latest_close_meta",
                        lambda sym: (prices.get(sym), {"source": "yahoo", "substituted": False}),
                        raising=True)
    return server, TestClient(server.app, raise_server_exceptions=False)


def _as(server, monkeypatch, email):
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: email)
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda p, c: "allow", raising=False)
    monkeypatch.setattr(server.auth_users, "is_owner",
                        lambda e: e == "georgy75@gmail.com", raising=False)


_ANALYSIS = {"portfolio_score": 71, "portfolio_verdict": "HOLD", "portfolio_type": "Growth",
             "risk_score": 55, "diversification_score": 40, "market_context": "test",
             # fair_value carries estimate+current (the real schema, ai_analyzer.py):
             # Kelly's pct_diff is now the CANONICAL (estimate-current)/|current|*100,
             # = (91.8-100)/100*100 = -8.2 — NOT the model's own pct_diff field (Codex #2).
             "holdings_analysis": [{"symbol": "NVDA", "verdict": "TRIM",
                                    "fair_value": {"estimate": 91.8, "current": 100.0,
                                                   "status": "OVERVALUED", "pct_diff": 999.0}}],
             "portfolio_risks": []}


def _stub_engines(server, monkeypatch, counters=None):
    c = counters if counters is not None else {}
    c.setdefault("llm", 0)

    def _deep(payload, regime_context=None, **kwargs):
        c["llm"] += 1
        return dict(_ANALYSIS)
    monkeypatch.setattr(server, "deep_analyze_portfolio", _deep, raising=True)
    monkeypatch.setattr(server, "run_monte_carlo",
                        lambda holdings, eq, **k: {"median": 2100.0, "p5": 1500.0, "p95": 2600.0,
                                                   "var_95": 300.0, "var_95_pct": 15.0,
                                                   "prob_profit_pct": 60.0, "regime": "NEUTRAL"},
                        raising=True)

    def _kelly(holdings, total, upside, verdicts):
        c["kelly_verdicts"] = verdicts
        return {"holdings": [{"symbol": h["symbol"], "action": "HOLD",
                              "current_pct": 50.0, "optimal_pct": 40.0,
                              "delta_usd": -100.0} for h in holdings],
                "total_optimal_allocated_pct": 80.0, "cash_recommended_pct": 20.0}
    monkeypatch.setattr(server, "run_kelly_analysis", _kelly, raising=True)
    monkeypatch.setattr(server, "detect_regimes",
                        lambda syms: {"regimes": [], "regime_summary": "NEUTRAL macro"},
                        raising=True)
    monkeypatch.setattr(server, "get_analyst_targets", lambda syms: {}, raising=True)
    # The Radar screens a live ~95-name universe. Unstubbed it burns the offline
    # guard once per symbol per run, which is slow enough to exhaust the analyze
    # poll budget. Contract tests for it live in test_mybook_radar.py.
    monkeypatch.setattr(
        server, "_run_book_radar", lambda h, t: {"mode": "SCAN", "candidates": []},
        raising=True,
    )
    return c


def test_book_crud_and_valuation(client, monkeypatch):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    r = c.post("/api/mybook", json={"symbol": "nvda", "shares": 10})
    assert r.status_code == 200 and r.json()["status"] == "ok"
    r = c.post("/api/mybook", json={"symbol": "AAPL", "shares": 5, "avg_cost": 150})
    d = r.json()["data"]
    assert d["total_equity"] == 2000.0                       # 10×$100 + 5×$200
    nvda = [p for p in d["positions"] if p["symbol"] == "NVDA"][0]
    assert nvda["equity"] == 1000.0 and nvda["portfolio_percentage"] == 50.0
    c.delete("/api/mybook/NVDA")
    d = c.get("/api/mybook").json()["data"]
    assert [p["symbol"] for p in d["positions"]] == ["AAPL"]


def test_book_unpriced_symbol_reported_not_dropped_silently(client, monkeypatch):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    c.post("/api/mybook", json={"symbol": "ZZZZ9", "shares": 3})
    d = c.get("/api/mybook").json()["data"]
    assert d["unpriced"] == ["ZZZZ9"]
    assert d["positions"] == []


def test_book_bad_shares_is_400(client, monkeypatch):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    assert c.post("/api/mybook", json={"symbol": "NVDA", "shares": -1}).status_code == 400
    assert c.post("/api/mybook", json={"symbol": "NVDA"}).status_code == 400


def test_book_requires_session(client, monkeypatch):
    server, c = client
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: None)
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda p, c: "allow", raising=False)
    assert c.get("/api/mybook").status_code == 401
    assert c.post("/api/mybook", json={"symbol": "NVDA", "shares": 1}).status_code == 401


def test_book_per_user_isolation(client, monkeypatch):
    server, c = client
    _as(server, monkeypatch, "a@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    c.post("/api/mybook", json={"symbol": "NVDA", "shares": 1})
    _as(server, monkeypatch, "b@x.com")
    assert c.get("/api/mybook").json()["data"]["positions"] == []


# ─── /api/mybook/analyze ─────────────────────────────────────────────────────

def test_analyze_book_runs_engines_and_is_not_masked(client, monkeypatch):
    """A CUSTOMER session gets the full suite on their book, and their OWN dollars
    survive the masking middleware raw (mask-exempt path)."""
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    counters = _stub_engines(server, monkeypatch)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    c.post("/api/mybook", json={"symbol": "NVDA", "shares": 10})
    r = _mybook_analyze(c)
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["analysis"]["portfolio_score"] == 71
    assert d["monte_carlo"]["var_95"] == 300.0
    assert d["kelly"]["holdings"][0]["symbol"] == "NVDA"
    assert d["book"]["total_equity"] == 1000.0            # raw dollars, not "•••"
    assert "•••" not in r.text
    assert counters["llm"] == 1


def test_analyze_book_kelly_receives_ai_verdicts(client, monkeypatch):
    """Parity with the owner pipeline: Kelly must receive the deep analysis's
    per-holding verdicts (verdict + fair-value gap), not an empty map — the
    verdict steers TRIM/ADD sizing."""
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    counters = _stub_engines(server, monkeypatch)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    c.post("/api/mybook", json={"symbol": "NVDA", "shares": 10})
    assert _mybook_analyze(c).status_code == 200
    assert counters["kelly_verdicts"] == {"NVDA": {"verdict": "TRIM", "pct_diff": -8.2}}


def test_analyze_book_anonymous_is_401_no_spend(client, monkeypatch):
    server, c = client
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: None)
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda p, c: "allow", raising=False)
    counters = _stub_engines(server, monkeypatch)
    r = _mybook_analyze(c)
    assert r.status_code == 401
    assert counters["llm"] == 0


def test_analyze_book_empty_is_400_no_spend(client, monkeypatch):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    counters = _stub_engines(server, monkeypatch)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    r = _mybook_analyze(c)
    assert r.status_code == 400
    assert counters["llm"] == 0


def test_analyze_book_cached_until_book_changes(client, monkeypatch):
    """Same book within the TTL = one LLM spend; changing a position invalidates
    the fingerprint and re-runs."""
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    counters = _stub_engines(server, monkeypatch)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    c.post("/api/mybook", json={"symbol": "NVDA", "shares": 10})
    assert _mybook_analyze(c).status_code == 200
    r2 = _mybook_analyze(c)
    assert r2.status_code == 200 and r2.json()["cached"] is True
    assert counters["llm"] == 1
    c.post("/api/mybook", json={"symbol": "NVDA", "shares": 20})   # book changed
    r3 = _mybook_analyze(c)
    assert r3.status_code == 200 and r3.json()["cached"] is False
    assert counters["llm"] == 2


def test_analyze_book_different_book_while_running_is_202_no_double_spend(client, monkeypatch):
    """A book edit mid-run must NOT spawn a second concurrent analysis (double
    Claude spend + clobbered job slot). One in-flight analysis per user: the new
    request gets 202 'running' (the client keeps polling — same contract as the
    normal in-flight poll), and the running job is left untouched so the original
    run completes. (Reconciled to the 202 keep-polling contract; the earlier draft
    used 409 'busy'.)"""
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    counters = _stub_engines(server, monkeypatch)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    c.post("/api/mybook", json={"symbol": "NVDA", "shares": 10})
    # Simulate a durable analysis already in flight for a DIFFERENT fingerprint.
    user_id = server.auth_users.resolve_user_id("cust@x.com")
    job_id = "job-existing"
    started = server.auth_users.start_mybook_job(
        user_id, job_id, "some-other-book", "book_analysis", 10
    )
    assert started["accepted"]
    r = c.post("/api/mybook/analyze", json={})
    assert r.status_code == 202
    assert r.json()["status"] == "running"
    # the in-flight job is untouched (not overwritten) so the original run completes
    assert server.auth_users.get_active_mybook_job(user_id)["fingerprint"] == "some-other-book"
    assert counters["llm"] == 0                       # no second worker → no double spend


def test_failed_analyze_poll_retries_once_then_stops(client, monkeypatch, au):
    """The dashboard polls POST /api/mybook/analyze every 3s. A failed job may
    recover once (one Analyze click survives a transient outage) but must not
    reserve another live LLM slot on every subsequent poll."""
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    submitted: list[str] = []

    def _submit(email, user_id, job_id, fp, positions, ew):
        submitted.append(job_id)
        return True

    monkeypatch.setattr(server, "_submit_mybook_job", _submit)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    c.post("/api/mybook", json={"symbol": "NVDA", "shares": 10})
    fp = server._book_fingerprint(au.get_positions("cust@x.com"))
    user_id = au.resolve_user_id("cust@x.com")
    first_id = "job-failed-once"
    assert au.start_mybook_job(user_id, first_id, fp, "book_analysis", 10)["accepted"]
    assert au.finish_mybook_job(user_id, first_id, fp, "failed", error_code="llm_down")

    first = c.post("/api/mybook/analyze", json={})
    assert first.status_code == 202
    assert len(submitted) == 1
    retry_id = submitted[0]
    assert au.finish_mybook_job(user_id, retry_id, fp, "failed", error_code="llm_down")

    second = c.post("/api/mybook/analyze", json={})
    assert second.status_code == 503
    assert "temporarily unavailable" in str(second.json().get("detail", "")).lower()
    assert submitted == [retry_id]


# ─── watchlist → book bulk import ─────────────────────────────────────────────

def test_import_watchlist_adds_missing_skips_existing(client, monkeypatch, au):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    au.add_watch("cust@x.com", "NVDA")
    au.add_watch("cust@x.com", "AAPL")
    c.post("/api/mybook", json={"symbol": "NVDA", "shares": 10})   # already held
    r = c.post("/api/mybook/import-watchlist")
    assert r.status_code == 200
    body = r.json()
    assert body["imported"] == ["AAPL"]                            # only the missing one
    nvda = [p for p in body["data"]["positions"] if p["symbol"] == "NVDA"][0]
    assert nvda["quantity"] == 10                                  # existing untouched
    aapl = [p for p in body["data"]["positions"] if p["symbol"] == "AAPL"][0]
    assert aapl["quantity"] == 1                                   # placeholder share


def test_import_watchlist_reports_cap_refusals(client, monkeypatch, au):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    for i in range(au._MAX_POSITIONS):
        au.set_position("cust@x.com", f"S{i}", 1)
    au.add_watch("cust@x.com", "NVDA")
    body = c.post("/api/mybook/import-watchlist").json()
    assert body["imported"] == [] and body["skipped_cap"] == ["NVDA"]


def test_import_watchlist_requires_session(client, monkeypatch):
    server, c = client
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: None)
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda p, c: "allow", raising=False)
    assert c.post("/api/mybook/import-watchlist").status_code == 401


# ─── persisted analyses + /mybook/report ─────────────────────────────────────

def test_save_and_get_latest_analysis_roundtrip(au):
    au.save_book_analysis("a@x.com", {"analysis": {"portfolio_verdict": "HOLD"}})
    au.save_book_analysis("a@x.com", {"analysis": {"portfolio_verdict": "BUY"}})
    got = au.get_latest_book_analysis("a@x.com")
    assert got["analysis"]["portfolio_verdict"] == "BUY"           # newest wins
    assert got["_saved_at"]
    assert au.get_latest_book_analysis("other@x.com") is None      # per-user


def test_saved_analyses_pruned(au):
    for i in range(10):
        au.save_book_analysis("a@x.com", {"n": i})
    import sqlite3
    con = sqlite3.connect(au.AUTH_DB_PATH)
    n = con.execute(
        "SELECT COUNT(*) FROM customer_analyses WHERE user_id=?",
        (au.resolve_user_id("a@x.com"),),
    ).fetchone()[0]
    con.close()
    assert n == au._MAX_ANALYSES_PER_USER
    assert au.get_latest_book_analysis("a@x.com")["n"] == 9


def test_report_page_renders_book_and_saved_analysis(client, monkeypatch):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    counters = _stub_engines(server, monkeypatch)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    c.post("/api/mybook", json={"symbol": "NVDA", "shares": 10})
    assert _mybook_analyze(c).status_code == 200   # persists a copy
    r = c.get("/mybook/report")
    assert r.status_code == 200
    assert "NVDA" in r.text and "$1,000.00" in r.text                  # own dollars, raw
    assert "HOLD" in r.text                                            # portfolio verdict
    assert "not financial advice" in r.text.lower()
    assert counters["llm"] == 1                                        # report itself never spends


def test_report_page_without_analysis_prompts_run(client, monkeypatch):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    c.post("/api/mybook", json={"symbol": "NVDA", "shares": 10})
    r = c.get("/mybook/report")
    assert r.status_code == 200
    assert "No analysis yet" in r.text


def test_report_page_requires_session_and_is_isolated(client, monkeypatch, au):
    server, c = client
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda p, c: "allow", raising=False)
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: None)
    assert c.get("/mybook/report").status_code == 401
    # user B's report never shows user A's book/analysis
    au.set_position("a@x.com", "NVDA", 10)
    au.save_book_analysis("a@x.com", {"analysis": {"portfolio_verdict": "SECRETVERDICT"}})
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: "b@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    r = c.get("/mybook/report")
    assert r.status_code == 200
    assert "SECRETVERDICT" not in r.text and "NVDA" not in r.text


def test_report_escapes_llm_strings(client, monkeypatch, au):
    """The report is server-rendered HTML outside the dashboard's audited JS
    builders — LLM-originated strings must be escaped."""
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    au.set_position("cust@x.com", "NVDA", 1)
    au.save_book_analysis("cust@x.com", {
        "analysis": {"portfolio_verdict": "HOLD", "market_context": "<script>alert(1)</script>",
                     "holdings_analysis": [], "portfolio_risks": []},
        "monte_carlo": {}, "kelly": {}})
    r = c.get("/mybook/report")
    assert r.status_code == 200
    assert "<script>alert(1)</script>" not in r.text
    assert "&lt;script&gt;" in r.text


# ─── usage metering ──────────────────────────────────────────────────────────

def test_log_usage_and_summary(au):
    au.log_usage("a@x.com", "ticker_card", "NVDA")
    au.log_usage("a@x.com", "ticker_card", "AAPL")
    au.log_usage("b@x.com", "book_analysis")
    rows = au.usage_summary(days=30)
    by = {(r["email"], r["event"]): r["count"] for r in rows}
    assert by[("a@x.com", "ticker_card")] == 2
    assert by[("b@x.com", "book_analysis")] == 1


def test_log_usage_never_raises(au, monkeypatch):
    monkeypatch.setattr(au, "_conn", lambda: (_ for _ in ()).throw(RuntimeError("db down")))
    au.log_usage("a@x.com", "ticker_card")           # must not raise


def test_analyze_book_meters_live_vs_cached(client, monkeypatch, au):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    _stub_engines(server, monkeypatch)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    c.post("/api/mybook", json={"symbol": "NVDA", "shares": 10})
    _mybook_analyze(c)
    _mybook_analyze(c)           # cache hit
    by = {r["event"]: r["count"] for r in au.usage_summary() if r["email"] == "cust@x.com"}
    assert by.get("book_analysis") == 1
    assert by.get("book_analysis_cached") == 1


def test_usage_count_24h_counts_only_named_event(au):
    au.log_usage("a@x.com", "book_analysis")
    au.log_usage("a@x.com", "book_analysis_cached")   # cached is a SEPARATE event
    au.log_usage("b@x.com", "book_analysis")
    assert au.usage_count_24h("a@x.com", "book_analysis") == 1


# ─── daily quota enforcement (billing-tier groundwork) ────────────────────────

def test_book_analysis_quota_blocks_live_but_never_cached(client, monkeypatch, au):
    """At the cap: cached results stay free; a NEW live run 429s before any
    engine/LLM work."""
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    counters = _stub_engines(server, monkeypatch)
    monkeypatch.setenv("QUOTA_DAILY_BOOK_ANALYSIS", "1")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    c.post("/api/mybook", json={"symbol": "NVDA", "shares": 10})
    assert _mybook_analyze(c).status_code == 200   # live #1 = at cap
    r = _mybook_analyze(c)
    assert r.status_code == 200 and r.json()["cached"] is True         # cached = free
    c.post("/api/mybook", json={"symbol": "NVDA", "shares": 20})       # new fingerprint
    r = _mybook_analyze(c)
    assert r.status_code == 429                                        # capped
    assert counters["llm"] == 1                                        # no spend past cap


def test_quota_owner_exempt(client, monkeypatch):
    server, c = client
    _as(server, monkeypatch, "georgy75@gmail.com")                     # owner session
    counters = _stub_engines(server, monkeypatch)
    monkeypatch.setenv("QUOTA_DAILY_BOOK_ANALYSIS", "1")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    c.post("/api/mybook", json={"symbol": "NVDA", "shares": 10})
    assert _mybook_analyze(c).status_code == 200
    c.post("/api/mybook", json={"symbol": "NVDA", "shares": 20})
    assert _mybook_analyze(c).status_code == 200   # over cap, owner OK
    assert counters["llm"] == 2


def test_quota_zero_disables_cap(client, monkeypatch):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    counters = _stub_engines(server, monkeypatch)
    monkeypatch.setenv("QUOTA_DAILY_BOOK_ANALYSIS", "0")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    for shares in (10, 20, 30):
        c.post("/api/mybook", json={"symbol": "NVDA", "shares": shares})
        assert _mybook_analyze(c).status_code == 200
    assert counters["llm"] == 3


def test_ticker_card_quota_blocks_live(client, monkeypatch, au):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    monkeypatch.setenv("QUOTA_DAILY_TICKER_CARD", "1")
    calls = {"n": 0}

    def _card(sym, current_price=None, regime_context=None):
        calls["n"] += 1
        return {"symbol": sym, "scores": {}}
    monkeypatch.setattr(server, "deep_analyze_ticker", _card, raising=True)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    assert c.post("/api/analyze-ticker", json={"symbol": "NVDA"}).status_code == 200
    assert c.post("/api/analyze-ticker", json={"symbol": "NVDA"}).status_code == 200  # cached, free
    r = c.post("/api/analyze-ticker", json={"symbol": "AAPL"})                        # new live
    assert r.status_code == 429
    assert calls["n"] == 1


def test_ticker_card_quota_reservation_is_atomic_across_symbols(client, monkeypatch, au):
    """Different-symbol requests must contend on the durable customer quota,
    not only on the endpoint's per-symbol, process-local single-flight locks."""
    import concurrent.futures
    import time

    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    monkeypatch.setenv("QUOTA_DAILY_TICKER_CARD", "1")
    monkeypatch.setattr(
        server,
        "_latest_close_meta",
        lambda _sym: (100.0, {"source": "test", "substituted": False}),
    )
    monkeypatch.setattr(server, "_price_quality", lambda *_args: (False, ""))
    calls = {"n": 0}

    def _card(sym, current_price=None, regime_context=None):
        calls["n"] += 1
        time.sleep(0.05)
        return {"symbol": sym, "scores": {}}

    monkeypatch.setattr(server, "deep_analyze_ticker", _card, raising=True)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")

    def request(symbol):
        return c.post("/api/analyze-ticker", json={"symbol": symbol}).status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        statuses = list(executor.map(request, ("AAPL", "MSFT", "NVDA", "AMD", "META")))

    assert statuses.count(200) == 1
    assert statuses.count(429) == 4
    assert calls["n"] == 1
    assert au.usage_count_24h("cust@x.com", "ticker_card") == 1


def test_ticker_card_releases_reservation_when_pre_spend_validation_fails(
    client, monkeypatch, au
):
    """A no-data request costs no quota and must not strand its reserved slot."""
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    monkeypatch.setenv("QUOTA_DAILY_TICKER_CARD", "1")
    monkeypatch.setattr(
        server,
        "_latest_close_meta",
        lambda sym: (None, {}) if sym == "BAD" else (
            100.0, {"source": "test", "substituted": False}
        ),
    )
    monkeypatch.setattr(server, "_price_quality", lambda *_args: (False, ""))
    monkeypatch.setattr(
        server,
        "deep_analyze_ticker",
        lambda sym, *_args, **_kwargs: {"symbol": sym, "scores": {}},
    )
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")

    assert c.post("/api/analyze-ticker", json={"symbol": "BAD"}).status_code == 404
    assert c.post("/api/analyze-ticker", json={"symbol": "AAPL"}).status_code == 200
    assert au.usage_count_24h("cust@x.com", "ticker_card") == 1


def test_desk_tier_multiplies_quota(client, monkeypatch, au):
    """Billing tier scales the cap without env/code changes: base 1 × desk 5 = 5
    live runs allowed; the 6th 429s. Tier comes from users.tier (set on provision
    by the billing webhook)."""
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    counters = _stub_engines(server, monkeypatch)
    monkeypatch.setenv("QUOTA_DAILY_BOOK_ANALYSIS", "1")
    au.create_magic_link_user("cust@x.com", tier="desk")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    for i in range(5):                                    # 5 distinct books, 5 live runs
        c.post("/api/mybook", json={"symbol": "NVDA", "shares": 10 + i})
        assert _mybook_analyze(c).status_code == 200, i
    c.post("/api/mybook", json={"symbol": "NVDA", "shares": 99})
    assert _mybook_analyze(c).status_code == 429   # 6th capped
    assert counters["llm"] == 5


def test_unknown_tier_gets_base_quota(client, monkeypatch, au):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")               # user has no billing tier
    counters = _stub_engines(server, monkeypatch)
    monkeypatch.setenv("QUOTA_DAILY_BOOK_ANALYSIS", "1")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    c.post("/api/mybook", json={"symbol": "NVDA", "shares": 10})
    assert _mybook_analyze(c).status_code == 200
    c.post("/api/mybook", json={"symbol": "NVDA", "shares": 20})
    assert _mybook_analyze(c).status_code == 429
    assert counters["llm"] == 1


# ─── /usage owner page ────────────────────────────────────────────────────────

def test_usage_page_owner_renders_counters(client, monkeypatch, au):
    server, c = client
    au.log_usage("cust@x.com", "ticker_card", "NVDA")
    au.create_magic_link_user("cust@x.com", tier="trader")
    _as(server, monkeypatch, "georgy75@gmail.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    r = c.get("/usage")
    assert r.status_code == 200
    assert "cust@x.com" in r.text and "ticker_card" in r.text and "trader" in r.text


def test_usage_page_customer_403(client, monkeypatch):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    assert c.get("/usage").status_code == 403


# ─── report polish ───────────────────────────────────────────────────────────

def test_report_includes_sector_breakdown_and_track_record_link(client, monkeypatch, au):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    au.set_position("cust@x.com", "NVDA", 1)
    au.save_book_analysis("cust@x.com", {
        "analysis": {"portfolio_verdict": "HOLD", "market_context": "ctx",
                     "holdings_analysis": [], "portfolio_risks": [],
                     "sector_breakdown": [{"sector": "Technology", "allocation_pct": 87.5,
                                           "assessment": "concentrated"}]},
        "monte_carlo": {}, "kelly": {}})
    r = c.get("/mybook/report")
    assert r.status_code == 200
    assert "Technology" in r.text and "87.5%" in r.text and "concentrated" in r.text
    assert 'href=\'/track-record\'' in r.text


def test_report_tolerates_none_valued_kelly_pcts(client, monkeypatch, au):
    """A persisted Kelly row can carry current_pct/optimal_pct as explicit None —
    dict.get('x', 0) only covers ABSENT keys, so the :.1f format used to crash
    the whole report. Values must be normalized with `or 0`."""
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    au.set_position("cust@x.com", "NVDA", 1)
    au.save_book_analysis("cust@x.com", {
        "analysis": {"portfolio_verdict": "HOLD", "market_context": "ctx",
                     "holdings_analysis": [], "portfolio_risks": []},
        "monte_carlo": {},
        "kelly": {"holdings": [{"symbol": "NVDA", "action": "ADD",
                                "current_pct": None, "optimal_pct": None}]}})
    r = c.get("/mybook/report")
    assert r.status_code == 200
    assert "0.0% → 0.0%" in r.text


def test_usage_summary_endpoint_owner_only(client, monkeypatch):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")          # customer session
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    assert c.get("/api/usage/summary").status_code == 403
    _as(server, monkeypatch, "georgy75@gmail.com")  # owner session
    r = c.get("/api/usage/summary")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


# ─── concurrency: single-flight + atomic quota reservation (audit item 7) ─────

def test_book_change_midrun_does_not_spawn_second_worker(client, monkeypatch):
    """Single-flight is per-USER, not per-fingerprint: while one analysis is
    running, a book edit + new analyze must return 202 (keep polling) and must NOT
    start a second concurrent worker. A concurrent worker would double-spend the
    quota and let a slow stale run overwrite a newer result (GPT-5.6 audit item 7)."""
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    counters = _stub_engines(server, monkeypatch)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    c.post("/api/mybook", json={"symbol": "NVDA", "shares": 10})
    # Simulate an in-flight run for a DIFFERENT book fingerprint.
    user_id = server.auth_users.resolve_user_id("cust@x.com")
    existing = server.auth_users.start_mybook_job(
        user_id, "job-existing", "STALE_FP", "book_analysis", 10
    )
    assert existing["accepted"]
    submitted: list = []
    monkeypatch.setattr(server, "_submit_mybook_job", lambda *args: submitted.append(args))
    r = c.post("/api/mybook/analyze", json={})
    assert r.status_code == 202 and r.json()["status"] == "running"
    assert submitted == [], "must not spawn a concurrent second worker while one runs"
    assert server.auth_users.get_active_mybook_job(user_id)["fingerprint"] == "STALE_FP"
    assert counters["llm"] == 0


def test_quota_reserved_at_accept_time_not_in_worker(client, monkeypatch):
    """The quota gate runs when the request is ACCEPTED (before the worker starts),
    so an over-cap call 429s immediately instead of spinning up a thread that only
    discovers the cap minutes later — closing the check→log window a concurrent
    caller could otherwise exploit to exceed the cap (audit item 7)."""
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    counters = _stub_engines(server, monkeypatch)
    monkeypatch.setenv("QUOTA_DAILY_BOOK_ANALYSIS", "1")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    c.post("/api/mybook", json={"symbol": "NVDA", "shares": 10})
    assert _mybook_analyze(c).status_code == 200          # run #1 = at cap
    c.post("/api/mybook", json={"symbol": "NVDA", "shares": 20})   # new book, over cap
    submitted: list = []
    monkeypatch.setattr(server, "_submit_mybook_job", lambda *args: submitted.append(args))
    r = c.post("/api/mybook/analyze", json={})
    assert r.status_code == 429                            # immediate, from the handler
    assert submitted == [], "over-cap request must not submit a worker"
    assert counters["llm"] == 1


def test_analyze_book_partial_engine_failure_still_returns(client, monkeypatch):
    """Kelly/MC are best-effort — an engine exploding degrades to an error field,
    it does not 500 the whole analysis."""
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    _stub_engines(server, monkeypatch)

    def _boom(*a, **k):
        raise RuntimeError("kelly exploded")
    monkeypatch.setattr(server, "run_kelly_analysis", _boom, raising=True)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    c.post("/api/mybook", json={"symbol": "NVDA", "shares": 10})
    r = _mybook_analyze(c)
    assert r.status_code == 200
    assert "error" in r.json()["data"]["kelly"]
    assert r.json()["data"]["analysis"]["portfolio_score"] == 71


# ─── report staleness guard (audit 2026-07-15 Test 4) ────────────────────────
# The report renders CURRENT positions next to the LAST persisted analysis. When
# the book changed after that analysis ran, the report used to pair a 40-stock
# book with a single-stock analysis with no warning — every portfolio-level
# number silently wrong. The renderer must flag that mismatch.

_STALE_MARK = "Analysis out of date"


def test_report_flags_stale_analysis_when_book_changed(client, monkeypatch):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    _stub_engines(server, monkeypatch)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    c.post("/api/mybook", json={"symbol": "NVDA", "shares": 10})
    assert _mybook_analyze(c).status_code == 200          # persists w/ fingerprint
    c.post("/api/mybook", json={"symbol": "AAPL", "shares": 5})   # book changes
    r = c.get("/mybook/report")
    assert r.status_code == 200
    assert _STALE_MARK in r.text


def test_report_no_stale_flag_when_book_unchanged(client, monkeypatch):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    _stub_engines(server, monkeypatch)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    c.post("/api/mybook", json={"symbol": "NVDA", "shares": 10})
    assert _mybook_analyze(c).status_code == 200
    r = c.get("/mybook/report")
    assert r.status_code == 200
    assert _STALE_MARK not in r.text


# ─── "Where does new cash go?" — Kelly-gap proportional cash deployment ──────

def test_deploy_cash_allocates_proportionally_to_kelly_gaps(client, monkeypatch, au):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    au.set_position("cust@x.com", "NVDA", 10)
    au.save_book_analysis("cust@x.com", {
        "_fp": server._book_fingerprint(au.get_positions("cust@x.com")),
        "analysis": {"portfolio_verdict": "HOLD", "holdings_analysis": [],
                     "portfolio_risks": []},
        "kelly": {"holdings": [
            {"symbol": "NVDA", "action": "ADD", "current_pct": 2.0, "optimal_pct": 6.0},
            {"symbol": "AAPL", "action": "ADD", "current_pct": 3.0, "optimal_pct": 5.0},
            {"symbol": "MU",   "action": "TRIM", "current_pct": 9.0, "optimal_pct": 4.0}]},
        "monte_carlo": {}, "book": {"positions": [], "unpriced": []}})
    r = c.post("/api/mybook/deploy-cash", json={"cash": 3000})
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["stale"] is False
    by = {a["symbol"]: a for a in d["allocations"]}
    assert set(by) == {"NVDA", "AAPL"}                     # TRIM rows never receive cash
    assert by["NVDA"]["dollars"] > by["AAPL"]["dollars"]   # 4pp gap beats 2pp gap
    # client fixture prices NVDA=100, AAPL=200
    assert by["NVDA"]["shares"] == int(by["NVDA"]["dollars"] // 100)


def test_deploy_cash_requires_analysis_and_valid_cash(client, monkeypatch):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    assert c.post("/api/mybook/deploy-cash", json={"cash": 1000}).status_code == 404
    assert c.post("/api/mybook/deploy-cash", json={"cash": -5}).status_code == 400
    assert c.post("/api/mybook/deploy-cash", json={}).status_code == 400


def test_deploy_cash_flags_stale_analysis(client, monkeypatch, au):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    au.set_position("cust@x.com", "NVDA", 10)
    au.save_book_analysis("cust@x.com", {
        "_fp": "some-other-book",
        "analysis": {}, "monte_carlo": {},
        "kelly": {"holdings": [{"symbol": "NVDA", "action": "ADD",
                                "current_pct": 1.0, "optimal_pct": 3.0}]},
        "book": {"positions": [], "unpriced": []}})
    d = c.post("/api/mybook/deploy-cash", json={"cash": 1000}).json()["data"]
    assert d["stale"] is True


def test_deploy_cash_tolerates_legacy_kelly_rows_missing_current_pct(client, monkeypatch, au):
    """Legacy persisted analyses can carry Kelly rows with current_pct/optimal_pct
    absent or None — the allocator must normalize them to 0, not 500."""
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    au.set_position("cust@x.com", "NVDA", 10)
    au.save_book_analysis("cust@x.com", {
        "_fp": server._book_fingerprint(au.get_positions("cust@x.com")),
        "analysis": {}, "monte_carlo": {},
        "kelly": {"holdings": [{"symbol": "NVDA", "action": "ADD",
                                "optimal_pct": 4.0}]},   # current_pct ABSENT
        "book": {"positions": [], "unpriced": []}})
    r = c.post("/api/mybook/deploy-cash", json={"cash": 1000})
    assert r.status_code == 200
    d = r.json()["data"]
    by = {a["symbol"]: a for a in d["allocations"]}
    assert "NVDA" in by and by["NVDA"]["shares"] >= 1


# ─── customer Home hero (/api/mybook/summary) ────────────────────────────────

def test_mybook_summary_shapes_hero_payload(client, monkeypatch, au):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    au.set_position("cust@x.com", "NVDA", 10)          # NVDA=100 in this fixture
    au.save_book_analysis("cust@x.com", {
        "_fp": server._book_fingerprint(au.get_positions("cust@x.com")),
        "analysis": {"portfolio_verdict": "HOLD"}, "kelly": {}, "monte_carlo": {},
        "book": {"positions": [], "unpriced": []}})
    d = c.get("/api/mybook/summary").json()["data"]
    assert d["total_equity"] == 1000.0 and d["positions_count"] == 1
    assert d["last_verdict"] == "HOLD" and d["analysis_stale"] is False
    assert d["next_action"]["target"] == "desk"
    assert isinstance(d["top_changes"], list) and len(d["top_changes"]) <= 3
    assert d["actions"] == []


def test_mybook_summary_emits_advisory_actions_for_kelly_conflict(client, monkeypatch, au):
    """Home tickets come from the saved analysis + latest signal, never a new LLM."""
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    au.set_position("cust@x.com", "LLY", 2)
    import signal_history as sh
    sh.record_snapshots(
        [{"symbol": "LLY", "verdict": "BUY", "score": 0.3, "confidence": 70}],
        date="2026-07-16",
    )
    au.save_book_analysis("cust@x.com", {
        "_fp": server._book_fingerprint(au.get_positions("cust@x.com")),
        "analysis": {"portfolio_verdict": "Quality compounder"},
        "monte_carlo": {},
        "kelly": {"holdings": [{"symbol": "LLY", "action": "TRIM",
                                "current_pct": 15.9, "optimal_pct": 8.3}]},
        "book": {"positions": [], "unpriced": []}})
    d = c.get("/api/mybook/summary").json()["data"]
    assert d["actions"], "fresh analysis with a conflict must produce tickets"
    assert d["actions"][0]["symbol"] == "LLY"
    assert d["actions"][0]["kind"] == "conflict"
    assert "Kelly" not in d["actions"][0]["headline"]
    assert d["next_action"]["target"] == "desk"


def test_mybook_summary_next_action_ladder(client, monkeypatch, au):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    # empty book → add holdings
    assert c.get("/api/mybook/summary").json()["data"]["next_action"]["target"] == "book"
    # book, no analysis → research a name (never the whole-book job)
    au.set_position("cust@x.com", "NVDA", 10)
    assert c.get("/api/mybook/summary").json()["data"]["next_action"]["target"] == "research"
    # stale analysis → still research, not a 3-minute book job
    au.save_book_analysis("cust@x.com", {"_fp": "other-book", "analysis": {},
                                         "kelly": {}, "monte_carlo": {},
                                         "book": {"positions": [], "unpriced": []}})
    d = c.get("/api/mybook/summary").json()["data"]
    assert d["analysis_stale"] is True and d["next_action"]["target"] == "research"


def test_mybook_summary_flags_stale_for_legacy_analysis_with_changed_symbols(client, monkeypatch, au):
    """Finding #2: /api/mybook/summary used to return analysis_stale=False for any
    legacy (pre-fingerprint) row, while /mybook/report independently ran its own
    symbol-set fallback and could flag the SAME row stale — the hero said "fresh",
    the report said "out of date". Both must now agree via the shared
    _analysis_stale_for helper."""
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    au.set_position("cust@x.com", "NVDA", 10)
    au.set_position("cust@x.com", "AAPL", 5)
    au.save_book_analysis("cust@x.com", {
        "analysis": {"portfolio_verdict": "HOLD"}, "monte_carlo": {}, "kelly": {},
        "book": {"positions": [{"symbol": "NVDA"}], "total_equity": 1000.0,
                 "unpriced": []}})   # legacy row: no _fp, recorded only NVDA
    d = c.get("/api/mybook/summary").json()["data"]
    assert d["analysis_stale"] is True
    assert d["next_action"]["target"] == "research"


def test_mybook_summary_flags_any_legacy_analysis_without_fingerprint(client, monkeypatch, au):
    """A legacy (no-_fp) row is ALWAYS stale, even when the recorded symbol set
    matches today's book: without the fingerprint we cannot verify SHARE COUNTS,
    and a same-symbols/different-shares book would otherwise read as fresh
    (external review 2026-07-17). Honest answer: can't verify → stale, prompting
    one re-run that mints a fingerprinted row."""
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    au.set_position("cust@x.com", "NVDA", 10)
    au.save_book_analysis("cust@x.com", {
        "analysis": {"portfolio_verdict": "HOLD"}, "monte_carlo": {}, "kelly": {},
        "book": {"positions": [{"symbol": "NVDA"}], "total_equity": 1000.0,
                 "unpriced": []}})
    d = c.get("/api/mybook/summary").json()["data"]
    assert d["analysis_stale"] is True
    assert d["next_action"]["target"] == "research"


def test_mybook_summary_catch_up_is_mechanical_never_analyze(client, monkeypatch, au):
    """Home catch-up is quotes + educational copy. No LLM, no whole-book job."""
    from advisory_copy import EDUCATIONAL

    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    au.set_position("cust@x.com", "NVDA", 10)
    d = c.get("/api/mybook/summary").json()["data"]
    assert d["next_action"]["target"] == "research"
    assert d["educational"] == EDUCATIONAL
    assert "not investment advice" in d["educational"].lower()
    assert isinstance(d["day_change_available"], bool)
    assert d["top_symbol"] == "NVDA"
    assert d["top_weight_pct"] == 100.0
    assert d["unpriced"] == []
    assert "analyze" not in d["next_action"]["target"]


def test_mybook_summary_all_unpriced_does_not_invent_zero_day(client, monkeypatch, au):
    """A quote outage is not a $0 book or a flat day."""
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    au.set_position("cust@x.com", "ZZZZ9", 3)
    d = c.get("/api/mybook/summary").json()["data"]
    assert d["positions_count"] == 1
    assert d["unpriced"] == ["ZZZZ9"]
    assert d["total_equity"] is None
    assert d["day_change_available"] is False
    assert d["day_change_amount"] is None
    assert d["day_change_percent"] is None
    assert d["top_weight_pct"] is None
    assert d["equal_weight"] is False


def test_mybook_summary_mixed_unpriced_omits_concentration(client, monkeypatch, au):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    au.set_position("cust@x.com", "NVDA", 10)
    au.set_position("cust@x.com", "ZZZZ9", 3)
    d = c.get("/api/mybook/summary").json()["data"]
    assert d["unpriced"] == ["ZZZZ9"]
    assert d["total_equity"] == 1000.0
    assert d["top_weight_pct"] is None
    assert d["day_change_available"] is False


def test_mybook_summary_survives_missing_signal_history(client, monkeypatch, au):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    au.set_position("cust@x.com", "NVDA", 10)
    import builtins
    real_import = builtins.__import__
    def _no_sighist(name, *a, **k):
        if name == "signal_history":
            raise ImportError("simulated absence")
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", _no_sighist)
    r = c.get("/api/mybook/summary")
    assert r.status_code == 200 and r.json()["data"]["top_changes"] == []


# ─── book table signal columns (CONSENSUS / KELLY Δ / RISK) ─────────────────

def test_mybook_payload_carries_signal_and_kelly_columns(client, monkeypatch, au):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    au.set_position("cust@x.com", "NVDA", 10)
    import signal_history as sh
    sh.record_snapshots([{"symbol": "NVDA", "verdict": "BUY", "score": 0.3,
                          "confidence": 80}], date="2026-07-16")
    au.save_book_analysis("cust@x.com", {
        "_fp": server._book_fingerprint(au.get_positions("cust@x.com")),
        "analysis": {}, "monte_carlo": {},
        "kelly": {"holdings": [{"symbol": "NVDA", "action": "ADD",
                                "current_pct": 2.5, "optimal_pct": 5.4}]},
        "book": {"positions": [], "unpriced": []}})
    d = c.get("/api/mybook").json()["data"]
    assert d["signals"]["NVDA"]["verdict"] == "BUY"
    assert d["kelly_deltas"]["NVDA"] == {"action": "ADD", "delta_pp": 2.9}
    assert d["analysis_stale"] is False


def test_mybook_signal_columns_fail_soft(client, monkeypatch):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    c.post("/api/mybook", json={"symbol": "NVDA", "shares": 1})
    d = c.get("/api/mybook").json()["data"]
    assert d["signals"] == {} or "NVDA" in d["signals"]   # empty history is fine
    assert d["kelly_deltas"] == {}                        # no analysis yet


def test_mybook_risky_symbols_use_word_boundaries(client, monkeypatch, au):
    """Real tickers are English words (ON, SO, ALL, NOW, IT, T) — a naive
    substring match would flag an ON holding on the risk text 'based on
    weakening margins'. Only whole-word symbol mentions count."""
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    au.set_position("cust@x.com", "ON", 5)      # onsemi — unpriced in fixture, still in syms
    au.set_position("cust@x.com", "AAPL", 10)
    au.save_book_analysis("cust@x.com", {
        "_fp": server._book_fingerprint(au.get_positions("cust@x.com")),
        "analysis": {"portfolio_risks": [
            {"text": "based on weakening margins for AAPL", "severity": "HIGH"}]},
        "monte_carlo": {}, "kelly": {},
        "book": {"positions": [], "unpriced": []}})
    d = c.get("/api/mybook").json()["data"]
    assert d["risky_symbols"] == ["AAPL"]       # ON must NOT match "based on"


def test_report_flags_stale_for_legacy_analysis_without_fingerprint(client, monkeypatch, au):
    """Rows persisted before the fingerprint existed still get the guard via a
    symbol-set comparison against the analysis's own recorded book."""
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    au.set_position("cust@x.com", "NVDA", 10)
    au.set_position("cust@x.com", "AAPL", 5)
    au.save_book_analysis("cust@x.com", {
        "analysis": {"portfolio_verdict": "HOLD", "market_context": "ctx",
                     "holdings_analysis": [], "portfolio_risks": []},
        "monte_carlo": {}, "kelly": {},
        "book": {"positions": [{"symbol": "NVDA"}], "total_equity": 1000.0,
                 "unpriced": []}})
    r = c.get("/mybook/report")
    assert r.status_code == 200
    assert _STALE_MARK in r.text


# ─── progressive disclosure: conclusions first, mechanics folded (plan T2) ────

def test_report_leads_with_conclusions_and_folds_mechanics(client, monkeypatch, au):
    """/mybook/report must LEAD each engine section with one plain-language
    conclusion line, then fold the raw tables behind <details> — nothing is
    deleted, only folded (customer-dashboard-simplification plan, Task 2)."""
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    au.set_position("cust@x.com", "NVDA", 10)
    au.save_book_analysis("cust@x.com", {
        "_fp": server._book_fingerprint(au.get_positions("cust@x.com")),  # genuinely fresh
        "analysis": {"portfolio_verdict": "HOLD", "market_context": "ctx",
                     "holdings_analysis": [], "portfolio_risks": [],
                     "sector_breakdown": [
                         {"sector": "Healthcare", "allocation_pct": 43.8, "assessment": "heavy"},
                         {"sector": "Technology", "allocation_pct": 12.0, "assessment": "ok"},
                     ]},
        "monte_carlo": {"median": 2100.0, "p5": 1500.0, "p95": 2600.0,
                        "var_95": 300.0, "horizon_days": 21},
        "kelly": {"holdings": [{"symbol": "NVDA", "action": "TRIM",
                                "current_pct": 50.0, "optimal_pct": 40.0}]},
        "book": {"positions": [{"symbol": "NVDA"}], "unpriced": []}})
    text = c.get("/mybook/report").text
    assert "Worst plausible" in text and "$300" in text      # conclusion line
    assert "<details>" in text and "How this was computed" in text
    assert "What this book is" in text and "43.8% Healthcare" in text
    assert "more than 40%" in text
    assert "1 name differs from the formula" in text
    assert "largest gap: NVDA" in text
    assert "Educational math, not an order" in text
    assert "NVDA · TRIM" in text
    # mechanics still present, just folded
    assert "Median outcome" in text
    # fresh book: the headline must NOT carry the staleness caveat (finding 3)
    assert "re-run for current positions" not in text


def test_report_conclusions_survive_empty_engines(client, monkeypatch, au):
    """Empty Monte Carlo / Kelly blocks must still render a conclusion line and the
    folded mechanics (with the existing 'unavailable' row), never a 500."""
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    au.set_position("cust@x.com", "NVDA", 1)
    au.save_book_analysis("cust@x.com", {
        "analysis": {"portfolio_verdict": "HOLD", "market_context": "ctx",
                     "holdings_analysis": [], "portfolio_risks": []},
        "monte_carlo": {}, "kelly": {}})
    r = c.get("/mybook/report")
    assert r.status_code == 200
    assert "0 names differ from the formula" in r.text
    assert "How this was computed" in r.text


def test_report_details_print_expanded_css(client, monkeypatch, au):
    """Printed PDFs must include the folded mechanics — the report stylesheet
    carries print rules that force <details> content to display."""
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    au.set_position("cust@x.com", "NVDA", 1)
    r = c.get("/mybook/report")
    assert r.status_code == 200
    assert "@media print" in r.text
    assert "details" in r.text and "page-break-inside:avoid" in r.text


# ─── structured-only AI output: lead + folded prose (plan T3) ─────────────────

def test_lead_and_fold_splits_on_sentence_boundary():
    import server
    text = ("First sentence about the macro backdrop. " * 3
            + "Later detail that belongs in the fold. " * 10)
    lead, rest = server._lead_and_fold(text)
    assert lead.endswith(".") and len(lead) <= 320
    assert rest and rest in text


def test_lead_and_fold_short_text_has_no_fold():
    import server
    lead, rest = server._lead_and_fold("Short and sweet.")
    assert lead == "Short and sweet." and rest == ""
    assert server._lead_and_fold("") == ("", "")
    assert server._lead_and_fold(None) == ("", "")


def test_lead_and_fold_no_boundary_returns_whole_text():
    import server
    text = "x" * 500                       # no sentence boundary anywhere
    lead, rest = server._lead_and_fold(text)
    assert lead == text and rest == ""


def test_report_folds_long_market_context(client, monkeypatch, au):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    au.set_position("cust@x.com", "NVDA", 1)
    au.save_book_analysis("cust@x.com", {
        "analysis": {"portfolio_verdict": "HOLD",
                     "market_context": "Lead sentence here. " + ("Filler detail. " * 40),
                     "holdings_analysis": [], "portfolio_risks": []},
        "monte_carlo": {}, "kelly": {}, "book": {"positions": [], "unpriced": []}})
    text = c.get("/mybook/report").text
    assert "Lead sentence here." in text
    assert text.count("Filler detail.") >= 1 and "Full context" in text


def test_gemini_commentary_rejects_customer_sessions(client, monkeypatch):
    """Structured-only AI output for customers (plan T3): the free-form gemini
    commentary surfaces stay OWNER-only. A customer session gets 403 from the
    require_api_key gate before any handler or model code can run — the live,
    non-vacuous guarantee behind the template-level nav check."""
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    for path in ("/api/gemini/analyze", "/api/gemini/market-commentary"):
        assert c.post(path, json={}).status_code == 403, path


def test_report_kelly_headline_names_largest_delta_not_first_row(client, monkeypatch, au):
    """Kelly holdings arrive sorted by optimal_pct DESC, not by adjustment size —
    'largest' must be selected by |optimal_pct - current_pct|, or a 0.1pp trim
    can headline over a 14pp add (PR #79 review, finding 1)."""
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    au.set_position("cust@x.com", "NVDA", 10)
    au.save_book_analysis("cust@x.com", {
        "analysis": {"portfolio_verdict": "HOLD", "market_context": "ctx",
                     "holdings_analysis": [], "portfolio_risks": []},
        "monte_carlo": {},
        "kelly": {"holdings": [
            # first by list order (highest optimal_pct) but the SMALLER delta:
            {"symbol": "NVDA", "action": "TRIM", "current_pct": 50.0, "optimal_pct": 49.9},
            {"symbol": "AAPL", "action": "ADD", "current_pct": 2.0, "optimal_pct": 16.0},
        ]},
        "book": {"positions": [{"symbol": "NVDA"}], "unpriced": []}})
    text = c.get("/mybook/report").text
    assert "2 names differ from the formula" in text
    assert "largest gap: AAPL" in text
    assert "largest gap: NVDA" not in text


def test_report_mc_conclusion_carries_stale_caveat(client, monkeypatch, au):
    """The Monte Carlo headline mixes the SAVED analysis's VaR with TODAY'S live
    book total — when the analysis is stale the conclusion line itself must say
    so; the separate banner is skippable, the headline is not (finding 3)."""
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    au.set_position("cust@x.com", "NVDA", 10)
    au.save_book_analysis("cust@x.com", {
        "_fp": "other-book",                    # stale by fingerprint
        "analysis": {"portfolio_verdict": "HOLD", "market_context": "ctx",
                     "holdings_analysis": [], "portfolio_risks": []},
        "monte_carlo": {"var_95": 300.0, "horizon_days": 21},
        "kelly": {}, "book": {"positions": [], "unpriced": []}})
    text = c.get("/mybook/report").text
    assert "Worst plausible" in text
    assert "re-run for current positions" in text
