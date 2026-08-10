"""Customer-scoped consensus: the owner's equity curve must never vote.

`consensus.node_ecg` calls `db.get_equity_history(symbol, days=30)`, and
`equity_snapshots` has NO user column — it returns the OWNER's curve for any
symbol. A customer holding AMD (which IS in the owner's real book) would
otherwise receive the owner's AMD equity history as a signal input to THEIR
verdict. So the customer path forces that node to ABSTAIN.

ABSTAIN, not a neutral vote. The two are different claims and the engine already
encodes the difference (`ABSTAIN = None` vs `VOTE_NEUTRAL = 0`): abstaining says
"we don't know", voting neutral says "we know it's neutral". Collapsing them
would tell the customer a node they never got agreed with the others, and would
silently inflate the denominator behind `data_coverage`.

Two properties make the assertions here non-vacuous, and both are load-bearing:

  * The equity history is SEEDED, so `node_ecg` demonstrably WOULD vote. With
    fewer than 3 rows it abstains on its own ("Insufficient ECG history") and a
    bare "it abstained" assertion would pass against an unfixed engine.
  * `node_ecg` is spied on, so we prove the owner table is never even READ on the
    customer path — stronger than reading it and discarding the vote.

Same offline rules as the rest of the suite: sockets AND curl_cffi are blocked by
conftest, so every network-bound node is stubbed or left to abstain.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from starlette.testclient import TestClient

OWNER_SYMBOLS = {
    "AMD", "BAC", "BKNG", "BLK", "COF", "DELL", "HOOD", "JD",
    "JMKE", "MPC", "MU", "NVDA", "PANW", "REGN", "RTX", "SCHW",
}
# Deliberately disjoint from OWNER_SYMBOLS, so an owner ticker in a customer
# response is unambiguous contamination rather than an honest coincidence.
CUSTOMER_SYMBOLS = ["ZZZA", "ZZZB"]
OWNER_EMAIL = "georgy75@gmail.com"
CUSTOMER_EMAIL = "cust@x.com"


# ─── fixtures ────────────────────────────────────────────────────────────────

def _seed_equity_history(symbol: str, equities: list[float]) -> None:
    """Insert a REAL owner-keyed equity curve for `symbol` into the isolated DB.

    conftest's `_isolate_equity_snapshots_db` points `db.DB_PATH` at a per-test
    throwaway, so this writes nowhere near the production file. A monotonically
    RISING curve has no drawdown and no loss streak, so `ecg._classify` returns
    HEALTHY and the node votes BULLISH — i.e. the node has something real to say,
    which is exactly what makes "it abstained" a meaningful assertion below.
    """
    import db

    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with db._conn() as con:
        con.executemany(
            "INSERT INTO equity_snapshots "
            "(snapshot_at, symbol, equity, percent_change, current_price) "
            "VALUES (?,?,?,?,?)",
            [
                ((start + timedelta(days=i)).isoformat(), symbol, eq, 0.0, 10.0)
                for i, eq in enumerate(equities)
            ],
        )


@pytest.fixture
def offline_nodes(monkeypatch: pytest.MonkeyPatch):
    """Deterministic consensus with the network down.

    Three cheap nodes are pinned BULLISH so `data_coverage` has a non-zero base to
    fall from; the network-bound remainder abstains on its own. ECG is
    deliberately NOT stubbed — it is the node under test and must hit the seeded
    DB for real, or these tests would prove nothing about it.
    """
    import consensus

    monkeypatch.setattr(consensus, "_regime_weights", lambda sym: dict(consensus._BASE_WEIGHTS))
    monkeypatch.setattr(consensus.price_source, "yf_download", lambda *a, **k: None)
    for name in ("node_momentum", "node_trend", "node_volume"):
        monkeypatch.setattr(consensus, name, lambda *a, **k: (consensus.VOTE_BULLISH, "stub"))
    for name in (
        "node_relative_strength", "node_fundamentals",
        "node_analyst", "node_news", "node_kelly",
    ):
        monkeypatch.setattr(consensus, name, lambda *a, **k: (consensus.ABSTAIN, "offline"))
    return consensus


def _book(*symbols: str) -> list[dict]:
    return [
        {"symbol": s, "equity": 1000.0, "average_buy_price": 9.0, "current_price": 10.0}
        for s in symbols
    ]


# ─── the ECG abstention (the whole point of this file) ───────────────────────

def test_ecg_votes_when_allowed_so_the_abstention_test_is_not_vacuous(offline_nodes):
    """Control case. If this fails, every assertion below is meaningless: it would
    mean the seeded history never produced a vote in the first place, so an
    abstention proves nothing about the customer path."""
    consensus = offline_nodes
    _seed_equity_history("ZZZA", [100.0, 110.0, 120.0, 130.0])

    row = consensus.run_consensus_holding("ZZZA", 1000.0, 5000.0, 9.0, 10.0, allow_ecg=True)

    assert row["votes"]["ECG"] == consensus.VOTE_BULLISH, (
        f"seeded ECG history did not produce a vote: {row['details']['ECG']}"
    )


def test_ecg_abstains_on_the_customer_path(offline_nodes):
    """The leak guard: same seeded owner curve, customer entrypoint, no ECG vote."""
    consensus = offline_nodes
    _seed_equity_history("ZZZA", [100.0, 110.0, 120.0, 130.0])

    out = consensus.run_consensus_for_customer(_book("ZZZA"), 5000.0)
    row = out["holdings"][0]

    assert row["votes"]["ECG"] is consensus.ABSTAIN
    # ABSTAIN is None; VOTE_NEUTRAL is 0. Both are falsey, so `not vote` would pass
    # against a neutral vote — assert the distinction the engine actually encodes.
    assert row["votes"]["ECG"] != consensus.VOTE_NEUTRAL


def test_customer_consensus_never_reads_the_owner_equity_table(offline_nodes, monkeypatch):
    """Stronger than "it abstained": the owner-keyed table is never touched.

    A vote that is computed and then discarded still means owner data was read
    into the process for a customer request."""
    consensus = offline_nodes
    _seed_equity_history("ZZZA", [100.0, 110.0, 120.0, 130.0])
    calls: list[str] = []

    def _spy(symbol: str):
        calls.append(symbol)
        return (consensus.VOTE_BULLISH, "should never be reached")

    monkeypatch.setattr(consensus, "node_ecg", _spy)

    consensus.run_consensus_for_customer(_book("ZZZA", "ZZZB"), 5000.0)

    assert calls == [], f"node_ecg was called on the customer path for {calls}"


def test_ecg_disabled_is_the_default_for_customers(offline_nodes):
    """The safe value must be the DEFAULT — a customer surface that leaks unless
    the caller remembers a keyword argument is one forgotten kwarg from a breach."""
    import inspect

    consensus = offline_nodes
    sig = inspect.signature(consensus.run_consensus_for_customer)
    assert sig.parameters["allow_ecg"].default is False


def test_owner_path_keeps_its_ecg_vote(offline_nodes):
    """The customer fix must not silently disarm the owner's own engine."""
    consensus = offline_nodes
    _seed_equity_history("ZZZA", [100.0, 110.0, 120.0, 130.0])

    out = consensus.run_consensus_portfolio(_book("ZZZA"), 5000.0)

    assert out["holdings"][0]["votes"]["ECG"] == consensus.VOTE_BULLISH


# ─── coverage must visibly drop ──────────────────────────────────────────────

def test_coverage_reflects_the_abstention(offline_nodes):
    """The customer is never told a node they didn't get agreed with the others.

    Asserted as a DELTA against the owner run rather than a hardcoded percentage,
    so it keeps holding as nodes are added or reweighted."""
    consensus = offline_nodes
    _seed_equity_history("ZZZA", [100.0, 110.0, 120.0, 130.0])

    owner = consensus.run_consensus_holding("ZZZA", 1000.0, 5000.0, 9.0, 10.0, allow_ecg=True)
    cust = consensus.run_consensus_for_customer(_book("ZZZA"), 5000.0)["holdings"][0]

    assert cust["active_nodes"] == owner["active_nodes"] - 1
    assert cust["abstains"] == owner["abstains"] + 1
    assert cust["data_coverage"] < owner["data_coverage"]
    # The bullish tally must lose exactly the ECG vote — not be quietly retained.
    assert cust["bullish"] == owner["bullish"] - 1


def test_disabled_ecg_is_disclosed_not_silently_missing(offline_nodes):
    """A missing node must read as a stated exclusion, not as an outage."""
    consensus = offline_nodes
    _seed_equity_history("ZZZA", [100.0, 110.0, 120.0, 130.0])

    out = consensus.run_consensus_for_customer(_book("ZZZA"), 5000.0)

    assert out["ecg_enabled"] is False
    detail = out["holdings"][0]["details"]["ECG"].lower()
    assert "disabled" in detail
    # The reason must name the cause, so nobody "fixes" it by re-enabling the node.
    assert "per-user" in detail or "not per-user" in detail


# ─── the cap ─────────────────────────────────────────────────────────────────

def test_cap_is_a_named_module_constant(offline_nodes):
    consensus = offline_nodes
    assert isinstance(consensus.MAX_CUSTOMER_CONSENSUS_SYMBOLS, int)
    assert 0 < consensus.MAX_CUSTOMER_CONSENSUS_SYMBOLS <= 30


def test_large_book_is_capped_and_truncation_is_disclosed(offline_nodes):
    """Consensus is a per-symbol network fan-out; a 30-name book must not become
    30 unbounded engine runs. Truncation is disclosed, never silent."""
    consensus = offline_nodes
    cap = consensus.MAX_CUSTOMER_CONSENSUS_SYMBOLS
    book = _book(*[f"ZZ{i:02d}" for i in range(cap + 5)])

    out = consensus.run_consensus_for_customer(book, 5000.0, max_symbols=cap)

    assert len(out["holdings"]) == cap
    assert out["coverage"]["analyzed"] == cap
    assert out["coverage"]["total"] == cap + 5
    assert out["coverage"]["truncated"] is True
    assert len(out["coverage"]["omitted_symbols"]) == 5


def test_small_book_reports_no_truncation(offline_nodes):
    consensus = offline_nodes
    out = consensus.run_consensus_for_customer(_book("ZZZA", "ZZZB"), 5000.0)

    assert out["coverage"]["truncated"] is False
    assert out["coverage"]["omitted_symbols"] == []
    assert out["coverage"]["analyzed"] == out["coverage"]["total"] == 2


def test_cap_keeps_the_largest_positions(offline_nodes):
    """Truncating by equity DESC matches the deep-analysis convention, so the
    names that matter most to the customer's risk are the ones analyzed."""
    consensus = offline_nodes
    book = [
        {"symbol": "ZZZS", "equity": 10.0, "average_buy_price": 9.0, "current_price": 10.0},
        {"symbol": "ZZZL", "equity": 9000.0, "average_buy_price": 9.0, "current_price": 10.0},
    ]

    out = consensus.run_consensus_for_customer(book, 9010.0, max_symbols=1)

    assert [h["symbol"] for h in out["holdings"]] == ["ZZZL"]
    assert out["coverage"]["omitted_symbols"] == ["ZZZS"]


# ─── the endpoint ────────────────────────────────────────────────────────────

@pytest.fixture
def au(tmp_path, monkeypatch: pytest.MonkeyPatch):
    import auth_users as _au

    monkeypatch.setattr(_au, "AUTH_DB_PATH", tmp_path / "auth.db")
    _au.init_auth_db()
    for email in (CUSTOMER_EMAIL, OWNER_EMAIL):
        _au.create_user(email, "test-password")
    return _au


@pytest.fixture
def client(au, monkeypatch: pytest.MonkeyPatch):
    import server

    monkeypatch.setattr(server.auth_users, "AUTH_DB_PATH", au.AUTH_DB_PATH)
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: True)
    prices = {s: 10.0 for s in CUSTOMER_SYMBOLS}
    prices.update({s: 50.0 for s in OWNER_SYMBOLS})
    monkeypatch.setattr(server, "_latest_close", lambda sym: prices.get(sym), raising=True)
    monkeypatch.setattr(
        server,
        "_latest_close_meta",
        lambda sym: (prices.get(sym), {"source": "yahoo", "substituted": False}),
        raising=True,
    )
    return server, TestClient(server.app, raise_server_exceptions=False)


def _as(server, monkeypatch, email):
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: email)
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda p, c: "allow", raising=False)
    monkeypatch.setattr(server.auth_users, "is_owner", lambda e: e == OWNER_EMAIL, raising=False)


def _seed_book(c: TestClient) -> None:
    for sym in CUSTOMER_SYMBOLS:
        c.post("/api/mybook", json={"symbol": sym, "shares": 10})


def test_endpoint_requires_a_session(client):
    _server, c = client
    assert c.post("/api/mybook/consensus", json={}).status_code == 401


def test_endpoint_returns_per_symbol_verdicts_with_node_breakdown(
    client, monkeypatch, offline_nodes
):
    """Shape parity with the owner Signals page, so one renderer serves both."""
    server, c = client
    _as(server, monkeypatch, CUSTOMER_EMAIL)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    _seed_book(c)

    r = c.post("/api/mybook/consensus", json={})
    assert r.status_code == 200, r.text
    data = r.json()["data"]

    assert {h["symbol"] for h in data["holdings"]} == set(CUSTOMER_SYMBOLS)
    row = data["holdings"][0]
    for key in (
        "verdict", "raw_score", "bullish", "bearish", "neutral", "abstains",
        "data_coverage", "conviction", "active_nodes", "votes", "details",
    ):
        assert key in row, f"missing {key} — owner Signals renderer would break"


def test_endpoint_forces_the_ecg_node_to_abstain(client, monkeypatch, offline_nodes):
    """End-to-end: a seeded owner curve for a symbol the CUSTOMER holds still
    yields no ECG vote in the HTTP response."""
    server, c = client
    _as(server, monkeypatch, CUSTOMER_EMAIL)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    _seed_book(c)
    for sym in CUSTOMER_SYMBOLS:
        _seed_equity_history(sym, [100.0, 110.0, 120.0, 130.0])

    data = c.post("/api/mybook/consensus", json={}).json()["data"]

    assert data["ecg_enabled"] is False
    for row in data["holdings"]:
        assert row["votes"]["ECG"] is None, f"{row['symbol']} voted on owner equity data"


def test_endpoint_response_carries_no_owner_symbols(client, monkeypatch, offline_nodes):
    import re

    server, c = client
    _as(server, monkeypatch, CUSTOMER_EMAIL)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    _seed_book(c)

    text = c.post("/api/mybook/consensus", json={}).text
    leaked = set(re.findall(r"\b[A-Z]{1,5}\b", text)) & OWNER_SYMBOLS
    assert not leaked, f"owner symbols leaked: {leaked}"
    assert "33758" not in text


def test_endpoint_discloses_truncation(client, monkeypatch, offline_nodes):
    server, c = client
    _as(server, monkeypatch, CUSTOMER_EMAIL)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    monkeypatch.setattr(server, "_BOOK_CONSENSUS_MAX_SYMBOLS", 1, raising=True)
    _seed_book(c)

    data = c.post("/api/mybook/consensus", json={}).json()["data"]

    assert data["coverage"]["truncated"] is True
    assert data["coverage"]["analyzed"] == 1
    assert data["coverage"]["total"] == 2


def test_endpoint_on_an_empty_book_does_not_run_the_engine(client, monkeypatch):
    """No positions = nothing to score. It must not fan out to the network."""
    import consensus

    server, c = client
    _as(server, monkeypatch, CUSTOMER_EMAIL)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")

    def _boom(*a, **k):
        raise AssertionError("engine ran on an empty book")

    monkeypatch.setattr(consensus, "run_consensus_holding", _boom)

    r = c.post("/api/mybook/consensus", json={})
    assert r.status_code == 200
    assert r.json()["data"]["holdings"] == []
