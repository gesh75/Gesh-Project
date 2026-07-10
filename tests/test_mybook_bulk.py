"""Bulk import of a customer book + equal-weight (privacy) mode.

The customer can paste a block of tickers — one per line — instead of adding 85
positions one at a time. The privacy path: paste SYMBOLS ONLY (no share counts).
When no sizes are given, the book is flagged equal-weight, so the analysis weights
every name equally (1/N) rather than by an unknown — and never asks for size.

Same offline rules as the rest of the suite: prices/engines/LLM are stubbed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient


@pytest.fixture
def au(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import auth_users as _au
    monkeypatch.setattr(_au, "AUTH_DB_PATH", tmp_path / "auth.db")
    _au.init_auth_db()
    return _au


_PRICES = {"AAA": 10.0, "BBB": 100.0, "CCC": 250.0, "DDD": 40.0,
           "NVDA": 100.0, "AAPL": 200.0}


@pytest.fixture
def client(au, monkeypatch):
    import server
    monkeypatch.setattr(server.auth_users, "AUTH_DB_PATH", au.AUTH_DB_PATH)
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: True)
    # default everything to a priced $50 so cap/import tests don't drop symbols
    monkeypatch.setattr(server, "_latest_close",
                        lambda sym: _PRICES.get(sym, 50.0), raising=True)
    return server, TestClient(server.app, raise_server_exceptions=False)


def _as(server, monkeypatch, email):
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: email)
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda p, c: "allow", raising=False)
    monkeypatch.setattr(server.auth_users, "is_owner",
                        lambda e: e == "georgy75@gmail.com", raising=False)


# ─── parser (pure) ────────────────────────────────────────────────────────────

def test_parse_symbols_only_yields_no_shares():
    import server
    items, invalid = server._parse_bulk_book("AAPL\nnvda\nMSFT")
    assert [i["symbol"] for i in items] == ["AAPL", "NVDA", "MSFT"]   # uppercased
    assert all(i["shares"] is None and i["avg_cost"] is None for i in items)
    assert invalid == []


def test_parse_with_sizes_and_mixed_delimiters():
    import server
    items, _ = server._parse_bulk_book("AAPL,100,150.25\nNVDA 50 800\nTSLA\t3")
    by = {i["symbol"]: i for i in items}
    assert by["AAPL"]["shares"] == 100.0 and by["AAPL"]["avg_cost"] == 150.25
    assert by["NVDA"]["shares"] == 50.0 and by["NVDA"]["avg_cost"] == 800.0
    assert by["TSLA"]["shares"] == 3.0 and by["TSLA"]["avg_cost"] is None


def test_parse_skips_header_and_junk():
    import server
    items, invalid = server._parse_bulk_book(
        "Symbol,Shares,Cost\nAAPL\n???,,,\n   \nNVDA")
    assert [i["symbol"] for i in items] == ["AAPL", "NVDA"]
    assert any("Symbol" in x or "???" in x for x in invalid)


def test_parse_dedupes_first_wins():
    import server
    items, _ = server._parse_bulk_book("AAPL 10\naapl 99\nNVDA")
    assert [i["symbol"] for i in items] == ["AAPL", "NVDA"]
    assert items[0]["shares"] == 10.0                                # first wins


# ─── storage layer ────────────────────────────────────────────────────────────

def test_max_positions_raised_to_100(au):
    assert au._MAX_POSITIONS == 100


def test_equal_weight_flag_roundtrip(au):
    assert au.get_equal_weight("a@x.com") is False                   # default off
    au.set_equal_weight("a@x.com", True)
    assert au.get_equal_weight("a@x.com") is True
    au.set_equal_weight("a@x.com", False)
    assert au.get_equal_weight("a@x.com") is False
    assert au.get_equal_weight("b@x.com") is False                   # per-user


# ─── /api/mybook/bulk endpoint ────────────────────────────────────────────────

def test_bulk_symbols_only_imports_and_sets_equal_weight(client, monkeypatch):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    r = c.post("/api/mybook/bulk", json={"text": "AAA\nBBB\nCCC\nDDD"})
    assert r.status_code == 200
    body = r.json()
    assert body["imported"] == ["AAA", "BBB", "CCC", "DDD"]
    assert body["equal_weight"] is True
    assert body["data"]["equal_weight"] is True


def test_bulk_equal_weight_makes_weights_equal_regardless_of_price(client, monkeypatch):
    """The privacy payoff: 4 names at wildly different prices each weigh 25%."""
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    c.post("/api/mybook/bulk", json={"text": "AAA\nBBB\nCCC\nDDD"})
    d = c.get("/api/mybook").json()["data"]
    weights = sorted(p["portfolio_percentage"] for p in d["positions"])
    assert weights == [25.0, 25.0, 25.0, 25.0]
    assert d["equal_weight"] is True


def test_bulk_with_sizes_disables_equal_weight(client, monkeypatch):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    body = c.post("/api/mybook/bulk", json={"text": "NVDA 10\nAAPL 5"}).json()
    assert body["equal_weight"] is False
    d = c.get("/api/mybook").json()["data"]
    # real weights: NVDA 10×$100=1000, AAPL 5×$200=1000 → 50/50 here, but sized
    assert d["equal_weight"] is False
    nvda = [p for p in d["positions"] if p["symbol"] == "NVDA"][0]
    assert nvda["quantity"] == 10                                    # real shares kept


def test_bulk_respects_position_cap(client, monkeypatch):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    text = "\n".join(f"SYM{i}" for i in range(120))
    body = c.post("/api/mybook/bulk", json={"text": text}).json()
    assert len(body["imported"]) == 100
    assert len(body["skipped_cap"]) == 20


def test_bulk_accepts_symbols_list(client, monkeypatch):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    body = c.post("/api/mybook/bulk", json={"symbols": ["AAA", "bbb"]}).json()
    assert body["imported"] == ["AAA", "BBB"]


def test_bulk_requires_session(client, monkeypatch):
    server, c = client
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: None)
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda p, c: "allow", raising=False)
    assert c.post("/api/mybook/bulk", json={"text": "AAPL"}).status_code == 401


def test_bulk_reports_invalid_lines(client, monkeypatch):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    body = c.post("/api/mybook/bulk", json={"text": "AAA\n@@@bad\nBBB"}).json()
    assert body["imported"] == ["AAA", "BBB"]
    assert any("bad" in x for x in body["invalid"])
