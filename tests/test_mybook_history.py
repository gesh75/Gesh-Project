"""Customer analysis HISTORY — the reader side of `customer_analyses`.

The owner has GET /api/history over `analysis_runs`; the customer had NO reader at
all, so every persisted run but the most recent one held in the page was
unreachable. These endpoints close that: a newest-first index plus a full replay of
one past run, strictly scoped to the authenticated user and spending zero LLM.

Shapes here are NOT invented — they mirror what the producer actually persists:
`_run_book_engines` (server.py) writes {analysis, kelly, monte_carlo, radar,
detailed_coverage, book}, `_mybook_analyze_worker` stamps `_fp`/`_job_id`, and
`auth_users.save_book_analysis` adds `created_at`. Verified by running a real
stubbed analyze through the fixture below and dumping the row (see
test_history_entry_matches_persisted_producer_shape, which asserts exactly that
against a REAL analyze call rather than a hand-written fixture).

Same offline rules as the rest of the suite: prices/engines/LLM are stubbed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

# Helpers (plain functions) are shared with the sibling module — the same
# cross-test-module import idiom test_ledger_host.py already uses. The FIXTURES
# are re-declared below instead of imported: importing a fixture makes every test
# that names it as a parameter an F811 redefinition under ruff.
from test_mybook import _as, _mybook_analyze, _stub_engines


@pytest.fixture
def au(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import auth_users as _au
    monkeypatch.setattr(_au, "AUTH_DB_PATH", tmp_path / "auth.db")
    _au.init_auth_db()
    for email in ("a@x.com", "b@x.com", "cust@x.com", "other@x.com", "georgy75@gmail.com"):
        _au.create_user(email, "test-password")
    return _au


@pytest.fixture
def client(au, monkeypatch: pytest.MonkeyPatch):
    import server
    monkeypatch.setattr(server.auth_users, "AUTH_DB_PATH", au.AUTH_DB_PATH)
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: True)
    prices = {"NVDA": 100.0, "AAPL": 200.0}
    monkeypatch.setattr(server, "_latest_close", lambda sym: prices.get(sym), raising=True)
    monkeypatch.setattr(server, "_latest_close_meta",
                        lambda sym: (prices.get(sym), {"source": "yahoo", "substituted": False}),
                        raising=True)
    return server, TestClient(server.app, raise_server_exceptions=False)


def _seed(au_mod, email: str, n: int, verdict: str = "HOLD") -> None:
    """Persist `n` analyses for `email` through the REAL writer.

    Share counts start at 77 so the seeded `_fp` can never collide with a book a
    test actually builds (10 shares of NVDA is the suite's house position) — a
    collision would make a seeded row report is_current_book=True and quietly
    invert the staleness assertions.
    """
    for i in range(n):
        au_mod.save_book_analysis(
            email,
            {
                "analysis": {
                    "portfolio_score": 50 + i,
                    "portfolio_verdict": f"{verdict}-{i}",
                    "portfolio_type": "Growth",
                    "risk_score": 40 + i,
                    "diversification_score": 30 + i,
                },
                "kelly": {"holdings": []},
                "monte_carlo": {"var_95_pct": 12.0},
                "detailed_coverage": {"analyzed": 1, "total": 1},
                "book": {"positions": [{"symbol": "NVDA", "shares": 10}],
                         "total_equity": 1000.0 + i, "unpriced": []},
                "_fp": f'[["NVDA", {77 + i}.0, null]]',
                "_job_id": f"job-{i}",
            },
        )


# ─── storage layer ────────────────────────────────────────────────────────────

def test_list_book_analyses_newest_first_and_per_user(au):
    _seed(au, "a@x.com", 3)
    _seed(au, "b@x.com", 1, verdict="SELL")
    rows = au.list_book_analyses("a@x.com")
    assert len(rows) == 3
    verdicts = [r["result"]["analysis"]["portfolio_verdict"] for r in rows]
    assert verdicts == ["HOLD-2", "HOLD-1", "HOLD-0"]          # newest first
    assert all(r["saved_at"] for r in rows)
    assert all(isinstance(r["id"], int) for r in rows)
    # user B's single row never appears in A's list
    assert all("SELL" not in v for v in verdicts)
    assert len(au.list_book_analyses("b@x.com")) == 1
    assert au.list_book_analyses("nobody@x.com") == []


def test_list_book_analyses_respects_limit_and_prune_cap(au):
    _seed(au, "a@x.com", 10)                                   # writer prunes to 5
    assert len(au.list_book_analyses("a@x.com")) == au._MAX_ANALYSES_PER_USER
    assert len(au.list_book_analyses("a@x.com", limit=2)) == 2
    assert au.list_book_analyses("a@x.com", limit=0) == []


def test_get_book_analysis_by_id_is_user_scoped(au):
    _seed(au, "a@x.com", 2)
    _seed(au, "b@x.com", 1, verdict="SELL")
    a_rows = au.list_book_analyses("a@x.com")
    b_rows = au.list_book_analyses("b@x.com")
    got = au.get_book_analysis("a@x.com", a_rows[1]["id"])      # the OLDER of A's
    assert got["analysis"]["portfolio_verdict"] == "HOLD-0"
    assert got["_saved_at"] == a_rows[1]["saved_at"]
    assert got["_id"] == a_rows[1]["id"]
    # B's row id handed to A resolves to nothing — never B's data
    assert au.get_book_analysis("a@x.com", b_rows[0]["id"]) is None
    assert au.get_book_analysis("b@x.com", a_rows[0]["id"]) is None
    assert au.get_book_analysis("a@x.com", 999999) is None
    assert au.get_book_analysis("nobody@x.com", a_rows[0]["id"]) is None


def test_corrupt_row_degrades_to_parse_error_never_raises(au):
    import sqlite3
    _seed(au, "a@x.com", 1)
    con = sqlite3.connect(au.AUTH_DB_PATH)
    con.execute(
        "INSERT INTO customer_analyses (user_id, result_json, created_at) VALUES (?,?,?)",
        (au.resolve_user_id("a@x.com"), "{not json", "2099-01-01T00:00:00+00:00"),
    )
    con.commit()
    con.close()
    rows = au.list_book_analyses("a@x.com")
    assert len(rows) == 2
    assert rows[0]["result"] is None and rows[0]["parse_error"] is True   # newest = corrupt
    assert rows[1]["result"] is not None and rows[1]["parse_error"] is False
    assert au.get_book_analysis("a@x.com", rows[0]["id"]) is None         # unreadable, not a 500


# ─── HTTP: the index ──────────────────────────────────────────────────────────

def test_history_lists_own_runs_newest_first(client, monkeypatch, au):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    _seed(au, "cust@x.com", 3)
    r = c.get("/api/mybook/history")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    data = body["data"]
    assert data["count"] == 3 and data["max_kept"] == au._MAX_ANALYSES_PER_USER
    items = data["analyses"]
    assert [i["portfolio_verdict"] for i in items] == ["HOLD-2", "HOLD-1", "HOLD-0"]
    top = items[0]
    assert top["portfolio_score"] == 52 and top["risk_score"] == 42
    assert top["diversification_score"] == 32 and top["portfolio_type"] == "Growth"
    assert top["saved_at"] and isinstance(top["id"], int)
    assert top["fingerprint"] == '[["NVDA", 79.0, null]]'
    assert top["symbols"] == ["NVDA"] and top["positions_count"] == 1
    assert top["total_equity"] == 1002.0
    assert top["coverage"] == {"analyzed": 1, "total": 1}
    assert top["is_current_book"] is False          # the book is empty right now
    # the index is an INDEX — it must not ship whole result payloads
    assert "holdings_analysis" not in top and "kelly" not in top


def test_history_empty_when_no_runs(client, monkeypatch):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    body = c.get("/api/mybook/history").json()
    assert body["status"] == "ok"
    assert body["data"]["analyses"] == [] and body["data"]["count"] == 0


def test_history_marks_the_run_matching_the_current_book(client, monkeypatch, au):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    counters = _stub_engines(server, monkeypatch)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    c.post("/api/mybook", json={"symbol": "NVDA", "shares": 10})
    _seed(au, "cust@x.com", 1)                       # a stale run for a different book
    assert _mybook_analyze(c).status_code == 200      # a run for the CURRENT book
    items = c.get("/api/mybook/history").json()["data"]["analyses"]
    assert len(items) == 2
    assert items[0]["is_current_book"] is True        # newest = the real analyze
    assert items[1]["is_current_book"] is False
    assert counters["llm"] == 1                       # history itself never spends


def test_history_requires_session(client, monkeypatch):
    server, c = client
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: None)
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda p, c: "allow", raising=False)
    assert c.get("/api/mybook/history").status_code == 401
    assert c.get("/api/mybook/history/1").status_code == 401


# ─── HTTP: one past run ───────────────────────────────────────────────────────

def test_history_detail_replays_a_past_run_without_llm(client, monkeypatch, au):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    counters = _stub_engines(server, monkeypatch)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    _seed(au, "cust@x.com", 2)
    items = c.get("/api/mybook/history").json()["data"]["analyses"]
    older = items[1]["id"]
    r = c.get(f"/api/mybook/history/{older}")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["cached"] is True
    data = body["data"]
    # the FULL persisted payload — enough to re-render without a new Claude call
    assert data["analysis"]["portfolio_verdict"] == "HOLD-0"
    assert set(data) >= {"analysis", "kelly", "monte_carlo", "detailed_coverage", "book"}
    assert data["book"]["positions"] == [{"symbol": "NVDA", "shares": 10}]
    assert data["saved_at"] == items[1]["saved_at"]
    assert data["_id"] == older
    assert body["meta"]["saved_at"] == items[1]["saved_at"]
    assert body["meta"]["is_current_book"] is False
    assert counters["llm"] == 0                       # zero spend, guaranteed


def test_saved_at_survives_json_encoding(client, monkeypatch, au):
    """FastAPI's jsonable_encoder runs with sqlalchemy_safe=True, which DROPS every
    dict key starting with "_sa" — and the storage layer's timestamp key is
    `_saved_at`. Shipping the persisted dict verbatim therefore loses the
    timestamp silently (present server-side, absent on the wire). The endpoint
    re-keys it to a plain `saved_at`; this test is the tripwire."""
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    _seed(au, "cust@x.com", 1)
    entry = c.get("/api/mybook/history").json()["data"]["analyses"][0]
    stored = au.get_book_analysis("cust@x.com", entry["id"])
    assert stored["_saved_at"]                              # storage keeps it
    data = c.get(f"/api/mybook/history/{entry['id']}").json()["data"]
    assert "_saved_at" not in data                          # the encoder eats it
    assert data["saved_at"] == stored["_saved_at"]          # so the wire uses this
    assert entry["saved_at"] == stored["_saved_at"]         # index agrees


def test_history_detail_unknown_id_is_404(client, monkeypatch, au):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    _seed(au, "cust@x.com", 1)
    assert c.get("/api/mybook/history/999999").status_code == 404
    assert c.get("/api/mybook/history/not-an-int").status_code == 422
    # Wider than a signed 64-bit rowid: sqlite3 would raise OverflowError on bind,
    # so an out-of-range id must resolve to "not found", never a 500.
    assert c.get("/api/mybook/history/99999999999999999999999").status_code == 404
    assert c.get("/api/mybook/history/-5").status_code == 404
    assert au.get_book_analysis("cust@x.com", 2**63) is None
    assert au.get_book_analysis("cust@x.com", "abc") is None
    assert au.get_book_analysis("cust@x.com", None) is None


# ─── the governing rule: one customer can never read another's ───────────────

def test_one_customer_cannot_read_another_customers_history(client, monkeypatch, au):
    server, c = client
    _seed(au, "a@x.com", 2)
    _seed(au, "b@x.com", 1, verdict="SECRET")
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda p, c: "allow", raising=False)

    # B learns the ids of B's OWN rows first…
    _as(server, monkeypatch, "b@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    b_items = c.get("/api/mybook/history").json()["data"]["analyses"]
    assert [i["portfolio_verdict"] for i in b_items] == ["SECRET-0"]
    b_id = b_items[0]["id"]

    # …then A, authenticated as A, asks for B's id and for A's own list.
    _as(server, monkeypatch, "a@x.com")
    a_body = c.get("/api/mybook/history").json()["data"]
    assert a_body["count"] == 2
    assert all("SECRET" not in i["portfolio_verdict"] for i in a_body["analyses"])
    assert b_id not in [i["id"] for i in a_body["analyses"]]
    r = c.get(f"/api/mybook/history/{b_id}")
    assert r.status_code == 404                      # not 200, not another user's row
    assert "SECRET" not in r.text

    # and symmetrically, B cannot read A's ids
    _as(server, monkeypatch, "b@x.com")
    for i in a_body["analyses"]:
        assert c.get(f"/api/mybook/history/{i['id']}").status_code == 404


def test_history_never_touches_owner_analysis_runs(client, monkeypatch, au):
    """The owner's history lives in gesh_financial.db::analysis_runs. A customer's
    history must come from customer_analyses ONLY — with zero customer rows the
    endpoint returns empty even if the owner DB is full of runs."""
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")

    def _boom(*a, **k):  # any owner-history read is a bug, not a fallback
        raise AssertionError("customer history must never read owner analysis_runs")

    monkeypatch.setattr(server, "get_analysis_history", _boom, raising=True)
    monkeypatch.setattr(server, "get_analysis_by_id", _boom, raising=True)
    monkeypatch.setattr(server, "get_equity_history", _boom, raising=True)
    body = c.get("/api/mybook/history").json()
    assert body["data"]["analyses"] == []


# ─── shape provenance: assert against the REAL producer, never a fixture ──────

def test_history_entry_matches_persisted_producer_shape(client, monkeypatch, au):
    """Runs a REAL /api/mybook/analyze and asserts the history index/detail line up
    with what the producer actually persisted — the guard against a panel built on
    invented keys."""
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    _stub_engines(server, monkeypatch)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    c.post("/api/mybook", json={"symbol": "NVDA", "shares": 10})
    analyzed = _mybook_analyze(c)
    assert analyzed.status_code == 200
    produced = analyzed.json()["data"]

    items = c.get("/api/mybook/history").json()["data"]["analyses"]
    assert len(items) == 1
    entry = items[0]
    assert entry["portfolio_verdict"] == produced["analysis"]["portfolio_verdict"]
    assert entry["portfolio_score"] == produced["analysis"]["portfolio_score"]
    assert entry["risk_score"] == produced["analysis"]["risk_score"]
    assert entry["diversification_score"] == produced["analysis"]["diversification_score"]
    assert entry["fingerprint"] == produced["_fp"]
    assert entry["coverage"] == produced["detailed_coverage"]
    assert entry["total_equity"] == produced["book"]["total_equity"]
    assert entry["symbols"] == [p["symbol"] for p in produced["book"]["positions"]]
    assert entry["positions_count"] == len(produced["book"]["positions"])
    assert entry["is_current_book"] is True

    detail = c.get(f"/api/mybook/history/{entry['id']}").json()["data"]
    for key in ("analysis", "kelly", "monte_carlo", "radar", "detailed_coverage", "book"):
        assert detail[key] == produced[key], key


def test_history_detail_is_not_money_masked(client, monkeypatch, au):
    """/api/mybook is mask-exempt — the customer's OWN dollars must come back raw.
    A prefix miss here would silently multiply their equity by the model-book
    factor (the 2.7971 export defect)."""
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    monkeypatch.setattr(server.auth_users, "is_owner", lambda e: False, raising=False)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    _seed(au, "cust@x.com", 1)
    items = c.get("/api/mybook/history").json()["data"]["analyses"]
    assert items[0]["total_equity"] == 1000.0
    detail = c.get(f"/api/mybook/history/{items[0]['id']}").json()["data"]
    assert detail["book"]["total_equity"] == 1000.0


@pytest.mark.parametrize("path", ["/api/mybook/history", "/api/mybook/history/1"])
def test_history_is_registered(client, path: str, monkeypatch):
    """Route registration guard: /api/mybook/{symbol} style catch-alls must never
    swallow these paths (a 405/404-from-routing would be a silent regression)."""
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    assert c.get(path).status_code in (200, 404)   # 404 = "no such run", not "no such route"
