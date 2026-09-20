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
    for email in ("a@x.com", "b@x.com", "cust@x.com"):
        _au.create_user(email, "test-password")
    return _au


_PRICES = {"AAA": 10.0, "BBB": 100.0, "CCC": 250.0, "DDD": 40.0, "NVDA": 100.0, "AAPL": 200.0}


@pytest.fixture
def client(au, monkeypatch):
    import server

    monkeypatch.setattr(server.auth_users, "AUTH_DB_PATH", au.AUTH_DB_PATH)
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: True)
    # default everything to a priced $50 so cap/import tests don't drop symbols
    monkeypatch.setattr(server, "_latest_close", lambda sym: _PRICES.get(sym, 50.0), raising=True)
    return server, TestClient(server.app, raise_server_exceptions=False)


def _as(server, monkeypatch, email):
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: email)
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda p, c: "allow", raising=False)
    monkeypatch.setattr(
        server.auth_users, "is_owner", lambda e: e == "georgy75@gmail.com", raising=False
    )


# ─── parser (pure) ────────────────────────────────────────────────────────────


def test_parse_symbols_only_yields_no_shares():
    import server

    items, invalid = server._parse_bulk_book("AAPL\nnvda\nMSFT")
    assert [i["symbol"] for i in items] == ["AAPL", "NVDA", "MSFT"]  # uppercased
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

    items, invalid = server._parse_bulk_book("Symbol,Shares,Cost\nAAPL\n???,,,\n   \nNVDA")
    assert [i["symbol"] for i in items] == ["AAPL", "NVDA"]
    assert any("Symbol" in x or "???" in x for x in invalid)


def test_parse_dedupes_first_wins():
    import server

    items, _ = server._parse_bulk_book("AAPL 10\naapl 99\nNVDA")
    assert [i["symbol"] for i in items] == ["AAPL", "NVDA"]
    assert items[0]["shares"] == 10.0  # first wins


def test_parse_rejects_excessive_line_count():
    import server

    text = "\n".join(f"SYM{i}" for i in range(server._BULK_MAX_LINES + 1))
    with pytest.raises(server.HTTPException) as exc_info:
        server._parse_bulk_book(text)
    assert exc_info.value.status_code == 413


# ─── storage layer ────────────────────────────────────────────────────────────


def test_max_positions_raised_to_100(au):
    assert au._MAX_POSITIONS == 100


def test_equal_weight_flag_roundtrip(au):
    assert au.get_equal_weight("a@x.com") is False  # default off
    au.set_equal_weight("a@x.com", True)
    assert au.get_equal_weight("a@x.com") is True
    au.set_equal_weight("a@x.com", False)
    assert au.get_equal_weight("a@x.com") is False
    assert au.get_equal_weight("b@x.com") is False  # per-user


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


def test_bulk_ticker_only_does_not_clobber_sized_book(client, monkeypatch):
    """Replace is off by default. A ticker-only paste must not wipe sizes or flip EW."""
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    seed = c.post("/api/mybook", json={"symbol": "NVDA", "shares": 50, "avg_cost": 800})
    assert seed.status_code == 200
    body = c.post("/api/mybook/bulk", json={"text": "NVDA\nAAA"}).json()
    assert "NVDA" not in body["imported"]
    assert "AAA" in body["imported"]
    assert body["equal_weight"] is False
    stored = {p["symbol"]: p for p in server.auth_users.get_positions("cust@x.com")}
    assert stored["NVDA"]["shares"] == 50.0
    assert stored["NVDA"]["avg_cost"] == 800.0
    assert stored["AAA"]["shares"] == 1.0
    d = c.get("/api/mybook").json()["data"]
    assert d["equal_weight"] is False
    nvda = [p for p in d["positions"] if p["symbol"] == "NVDA"][0]
    assert nvda["quantity"] == 50.0
    assert nvda["average_buy_price"] == 800.0


def test_bulk_ticker_only_on_equal_weight_book_stays_equal_weight(client, monkeypatch):
    """Empty/EW ticker-only import is unchanged: new names join, EW stays on."""
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    first = c.post("/api/mybook/bulk", json={"text": "AAA\nBBB"}).json()
    assert first["equal_weight"] is True
    body = c.post("/api/mybook/bulk", json={"text": "AAA\nCCC"}).json()
    assert "AAA" not in body["imported"]
    assert "CCC" in body["imported"]
    assert body["equal_weight"] is True
    stored = {p["symbol"]: p["shares"] for p in server.auth_users.get_positions("cust@x.com")}
    assert stored["AAA"] == 1.0
    assert stored["CCC"] == 1.0


def test_bulk_sized_line_still_upserts_existing(client, monkeypatch):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    assert c.post(
        "/api/mybook", json={"symbol": "NVDA", "shares": 50, "avg_cost": 800}
    ).status_code == 200
    body = c.post("/api/mybook/bulk", json={"text": "NVDA 60 900"}).json()
    assert "NVDA" in body["imported"]
    assert body["equal_weight"] is False
    stored = {p["symbol"]: p for p in server.auth_users.get_positions("cust@x.com")}
    assert stored["NVDA"]["shares"] == 60.0
    assert stored["NVDA"]["avg_cost"] == 900.0


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
    assert nvda["quantity"] == 10  # real shares kept


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


def test_bulk_rejects_oversized_body_before_import(client, monkeypatch):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    payload = b'{"text":"' + (b"A" * server._BULK_BODY_MAX_BYTES) + b'"}'
    r = c.post("/api/mybook/bulk", content=payload, headers={"content-type": "application/json"})
    assert r.status_code == 413
    assert server.auth_users.get_positions("cust@x.com") == []


def test_bulk_rejects_excessive_lines_before_database_work(client, monkeypatch):
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    text = "\n".join(f"SYM{i}" for i in range(server._BULK_MAX_LINES + 1))
    r = c.post("/api/mybook/bulk", json={"text": text})
    assert r.status_code == 413
    assert server.auth_users.get_positions("cust@x.com") == []


# ─── large-book analysis: cap detailed holdings so the LLM can't truncate ──────


def _fake_engines(server, monkeypatch):
    captured = {}

    def _deep(payload, regime_context=None, **kwargs):
        captured["holdings_seen"] = len(payload["holdings"])
        captured["concise"] = kwargs.get("concise")
        return {"holdings_analysis": [], "portfolio_score": 50}

    monkeypatch.setattr(server, "deep_analyze_portfolio", _deep, raising=True)
    monkeypatch.setattr(server, "detect_regimes", lambda s: {"regime_summary": "x"}, raising=True)
    monkeypatch.setattr(server, "run_monte_carlo", lambda h, e, **k: {"ok": 1}, raising=True)
    monkeypatch.setattr(server, "run_kelly_analysis", lambda *a, **k: {"ok": 1}, raising=True)
    monkeypatch.setattr(server, "get_analyst_targets", lambda s: {}, raising=True)
    # Radar screens a live universe — stubbed here, contract-tested in
    # test_mybook_radar.py.
    monkeypatch.setattr(server, "_run_book_radar", lambda h, t: {"ok": 1}, raising=True)
    return captured


def _book_payload(n):
    holdings = [
        {
            "symbol": f"S{i}",
            "equity": 100.0 + i,
            "quantity": 1.0,
            "average_buy_price": 0.0,
            "current_price": 1.0,
            "portfolio_percentage": 1.0,
        }
        for i in range(n)
    ]
    return {
        "holdings": holdings,
        "unpriced": [],
        "equity_summary": {"portfolio_equity": sum(h["equity"] for h in holdings)},
    }


def test_deep_analysis_holdings_are_capped_for_large_book(monkeypatch):
    import server

    captured = _fake_engines(server, monkeypatch)
    result = server._run_book_engines(_book_payload(60))
    assert captured["holdings_seen"] == server._BOOK_ANALYSIS_MAX_HOLDINGS  # capped
    assert result["analysis"]["holdings_truncated"] == {
        "analyzed": server._BOOK_ANALYSIS_MAX_HOLDINGS,
        "total": 60,
    }
    assert len(result["book"]["positions"]) == 60  # MC/Kelly saw ALL


def test_small_book_is_not_capped(monkeypatch):
    import server

    captured = _fake_engines(server, monkeypatch)
    result = server._run_book_engines(_book_payload(6))
    assert captured["holdings_seen"] == 6
    assert captured.get("concise") is True
    assert "holdings_truncated" not in result["analysis"]


def test_large_book_uses_concise_deep_analysis(monkeypatch):
    import server

    captured = _fake_engines(server, monkeypatch)
    server._run_book_engines(_book_payload(33))
    assert captured["holdings_seen"] == server._BOOK_ANALYSIS_MAX_HOLDINGS
    assert captured.get("concise") is True


def test_large_book_kelly_enrichment_is_capped_but_kelly_sizes_full_book(monkeypatch):
    """Network analyst enrichment is capped while Kelly still sizes all holdings."""
    import server

    _fake_engines(server, monkeypatch)
    analyst_symbols = []
    kelly_upside = {}
    kelly_holding_symbols = []

    def _analyst(symbols):
        analyst_symbols.extend(symbols)
        return {symbol: {"upside_pct": 5.0} for symbol in symbols}

    def _kelly(holdings, total_equity, upside, ai_verdicts):
        kelly_holding_symbols.extend(h["symbol"] for h in holdings)
        kelly_upside.update(upside)
        return {"ok": 1}

    monkeypatch.setattr(server, "get_analyst_targets", _analyst, raising=True)
    monkeypatch.setattr(server, "run_kelly_analysis", _kelly, raising=True)

    book_size = 33
    server._run_book_engines(_book_payload(book_size))

    # The enrichment cap keeps the LARGEST-WEIGHT names, not the first N inserted
    # (server.py: "Cap DETAILED cards to the largest-weight names"). _book_payload
    # gives S{i} an equity of 100.0 + i, so the largest holdings are the HIGHEST
    # indices and they arrive weight-descending: S32, S31, ... The original
    # expectation assumed insertion order, so it could never have matched.
    expected_analyst = [
        f"S{i}"
        for i in range(
            book_size - 1,
            book_size - 1 - server._BOOK_ANALYST_ENRICHMENT_MAX_SYMBOLS,
            -1,
        )
    ]
    expected_full_book = [f"S{i}" for i in range(book_size)]
    assert analyst_symbols == expected_analyst
    assert list(kelly_upside) == expected_analyst
    assert kelly_holding_symbols == expected_full_book


# ─── error classifier must not crash on a cause-less exception ─────────────────


def test_upstream_classifier_handles_exception_without_cause():
    """Regression: a RuntimeError with no __cause__/__context__ must classify to
    None, not crash on None.__cause__ (which turned every analysis error into a
    confusing AttributeError 500)."""
    import server

    assert server._upstream_unavailable_error(RuntimeError("truncated")) is None
    he = server._server_error(RuntimeError("truncated"))
    assert he.status_code == 500  # clean 500


# ─── PR #307 Opus Audit Gap Regression Tests ─────────────────────────────────


def test_bulk_skipped_existing_reported_and_does_not_wipe(client, monkeypatch):
    """Gap 2: Skipped tickers on already-held positions must return in skipped_existing."""
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")

    c.post("/api/mybook", json={"symbol": "NVDA", "shares": 50, "avg_cost": 800.0})
    c.post("/api/mybook", json={"symbol": "BBB", "shares": 10, "avg_cost": 40.0})

    resp = c.post("/api/mybook/bulk", json={"text": "NVDA\nBBB"}).json()
    assert resp["imported"] == []
    assert resp["skipped_existing"] == ["NVDA", "BBB"]

    stored = {p["symbol"]: p for p in server.auth_users.get_positions("cust@x.com")}
    assert stored["NVDA"]["shares"] == 50.0
    assert stored["NVDA"]["avg_cost"] == 800.0
    assert stored["BBB"]["shares"] == 10.0
    assert stored["BBB"]["avg_cost"] == 40.0


def test_bulk_replace_with_oversized_lines_does_not_wipe_positions(client, monkeypatch):
    """Gap 5: If text exceeds _BULK_MAX_LINES with replace=True, book must not be wiped."""
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")

    c.post("/api/mybook", json={"symbol": "NVDA", "shares": 50, "avg_cost": 800.0})
    text = "\n".join(f"SYM{i}" for i in range(server._BULK_MAX_LINES + 1))

    resp = c.post("/api/mybook/bulk", json={"text": text, "replace": True})
    assert resp.status_code == 413

    stored = {p["symbol"]: p for p in server.auth_users.get_positions("cust@x.com")}
    assert "NVDA" in stored
    assert stored["NVDA"]["shares"] == 50.0


def test_explicit_zero_cost_stored_and_accepted(client, monkeypatch):
    """Gap 6: An explicit 0.0 cost basis is stored as 0.0, not None."""
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")

    # Via bulk import
    resp = c.post("/api/mybook/bulk", json={"text": "NVDA 10 0.0"}).json()
    assert "NVDA" in resp["imported"]
    stored = {p["symbol"]: p for p in server.auth_users.get_positions("cust@x.com")}
    assert stored["NVDA"]["avg_cost"] == 0.0

    # Via single add
    resp2 = c.post("/api/mybook", json={"symbol": "NVDA", "shares": 15, "avg_cost": 0.0}).json()
    assert resp2["status"] == "ok"
    stored = {p["symbol"]: p for p in server.auth_users.get_positions("cust@x.com")}
    assert stored["NVDA"]["shares"] == 15.0
    assert stored["NVDA"]["avg_cost"] == 0.0


def test_sized_book_privacy_toggle(client, monkeypatch):
    """Gap 3: Sized book can toggle equal-weight mode via /api/mybook/privacy."""
    server, c = client
    _as(server, monkeypatch, "cust@x.com")
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")

    c.post("/api/mybook", json={"symbol": "NVDA", "shares": 50, "avg_cost": 800.0})
    assert server.auth_users.get_equal_weight("cust@x.com") is False

    # Flip to privacy mode
    p_resp = c.post("/api/mybook/privacy", json={"equal_weight": True}).json()
    assert p_resp["status"] == "ok"
    assert p_resp["equal_weight"] is True
    assert server.auth_users.get_equal_weight("cust@x.com") is True

    # Stored shares and basis remain intact
    stored = {p["symbol"]: p for p in server.auth_users.get_positions("cust@x.com")}
    assert stored["NVDA"]["shares"] == 50.0
    assert stored["NVDA"]["avg_cost"] == 800.0

    # Flip back to sized mode
    p_resp2 = c.post("/api/mybook/privacy", json={"equal_weight": False}).json()
    assert p_resp2["status"] == "ok"
    assert p_resp2["equal_weight"] is False
    assert server.auth_users.get_equal_weight("cust@x.com") is False
