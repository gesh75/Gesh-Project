"""Company-name → ticker typeahead (Analyze box).

`symbol_search.search()` matches a typed name/prefix against the Alpaca asset
universe (loaded once, cached 24h). Offline: the Alpaca load returns [] (no keys),
so the module uses its bundled fallback list — deterministic for these tests.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

import symbol_search as ss


@pytest.fixture(autouse=True)
def _fresh_universe(monkeypatch):
    # force the bundled fallback (no Alpaca) and a cold cache each test
    monkeypatch.setattr(ss, "_load_from_alpaca", lambda: [])
    monkeypatch.setattr(ss, "_UNIVERSE", [])
    monkeypatch.setattr(ss, "_LOADED_AT", 0.0)


# ── search ranking / matching ─────────────────────────────────────────────────

def test_name_prefix_matches_ticker():
    out = ss.search("Apple")
    assert out and out[0]["symbol"] == "AAPL"


def test_curated_name_wins_lookalike_tie(monkeypatch):
    # Full-universe case: both "Apple Inc." (AAPL) and "Apple Hospitality REIT"
    # (APLE) name-prefix-match "Apple". APLE is listed FIRST here, but AAPL is a
    # curated megacap, so the tiebreaker must still rank it #1 (the live bug that
    # surfaced APLE for a plain "Apple" search).
    monkeypatch.setattr(ss, "_universe", lambda: [
        {"symbol": "APLE", "name": "Apple Hospitality REIT, Inc."},
        {"symbol": "AAPL", "name": "Apple Inc."},
    ])
    out = ss.search("Apple")
    assert out[0]["symbol"] == "AAPL"


def test_partial_name_substring():
    syms = [r["symbol"] for r in ss.search("micro")]
    assert "MSFT" in syms          # "Microsoft" matched by name


def test_ticker_prefix_matches():
    syms = [r["symbol"] for r in ss.search("NV")]
    assert "NVDA" in syms


def test_exact_ticker_ranks_first():
    out = ss.search("V")           # 1-char → below min length → empty
    assert out == []
    out = ss.search("MA")          # exact ticker Mastercard ranks first
    assert out[0]["symbol"] == "MA"


def test_short_query_returns_empty():
    assert ss.search("") == []
    assert ss.search("A") == []    # < 2 chars, don't dump the universe


def test_limit_is_capped():
    assert len(ss.search("a", limit=99)) == 0        # still respects min length
    assert len(ss.search("in", limit=3)) <= 3


def test_result_shape():
    out = ss.search("tesla")
    assert out and set(out[0]) == {"symbol", "name"}
    assert out[0]["symbol"] == "TSLA"


# ── endpoint ──────────────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch):
    import server
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: False)
    return TestClient(server.app, raise_server_exceptions=False)


def test_endpoint_returns_matches(client):
    r = client.get("/api/symbol-search?q=apple")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert any(m["symbol"] == "AAPL" for m in body["data"])


def test_endpoint_short_query_empty(client):
    r = client.get("/api/symbol-search?q=a")
    assert r.status_code == 200
    assert r.json()["data"] == []


def test_endpoint_reachable_by_customer(monkeypatch):
    """A logged-in CUSTOMER can use the typeahead (public reference data)."""
    import server
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: True)
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: "cust@x.com")
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda p, c: "allow", raising=False)
    monkeypatch.setattr(server.auth_users, "is_owner", lambda e: False, raising=False)
    c = TestClient(server.app, raise_server_exceptions=False)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    r = c.get("/api/symbol-search?q=nvidia")
    assert r.status_code == 200
    assert any(m["symbol"] == "NVDA" for m in r.json()["data"])
