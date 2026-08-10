"""Customer-scoped Scanner — sector pre-filter → consolidation zones on THEIR book.

The owner's Scanner page runs `batch_sector_check` then `scan_consolidations` over
holdings + watchlist resolved from the Robinhood snapshot. Both engines are purely
SYMBOL-scoped (public OHLCV in, trade levels out) and carry no owner state, so the
same pipeline can serve a customer — provided the SYMBOLS come from the customer's
own book/watchlist and never from the owner snapshot.

Two properties get pinned here beyond the leak check, because both are how this
endpoint would hurt in production rather than in a demo:

  * BOUNDEDNESS — the consolidation leg is one OHLCV download per symbol. A 30-name
    book plus a 50-name watchlist is an 80-download fan-out per click. The cap must
    bite and must be disclosed, or the truncation is a silent lie.
  * PER-SYMBOL DEGRADATION — one dead ticker must not take the whole scan with it.

Offline like the rest of the suite: every network edge is stubbed.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from starlette.testclient import TestClient

OWNER_SYMBOLS = {
    "AMD", "BAC", "BKNG", "BLK", "COF", "DELL", "HOOD", "JD",
    "JMKE", "MPC", "MU", "NVDA", "PANW", "REGN", "RTX", "SCHW",
}
OWNER_EMAIL = "georgy75@gmail.com"
CUSTOMER_EMAIL = "cust@x.com"
OTHER_CUSTOMER = "other@x.com"

BOOK = ["ZZZA", "ZZZB"]
WATCH = ["ZZZC"]


@pytest.fixture
def au(tmp_path, monkeypatch):
    import auth_users as _au

    monkeypatch.setattr(_au, "AUTH_DB_PATH", tmp_path / "auth.db")
    _au.init_auth_db()
    for email in (CUSTOMER_EMAIL, OTHER_CUSTOMER, OWNER_EMAIL):
        _au.create_user(email, "test-password")
    return _au


@pytest.fixture
def client(au, monkeypatch):
    import server

    monkeypatch.setattr(server.auth_users, "AUTH_DB_PATH", au.AUTH_DB_PATH)
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: True)
    prices = {s: 10.0 for s in (*BOOK, *WATCH)}
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
    monkeypatch.setattr(
        server.auth_users, "is_owner", lambda e: e == OWNER_EMAIL, raising=False
    )


def _seed(au, email=CUSTOMER_EMAIL, book=BOOK, watch=WATCH):
    for s in book:
        au.set_position(email, s, 10)
    for s in watch:
        au.add_watch(email, s)


def _sector(hot: list[str], cold: list[str] | None = None) -> dict:
    return {
        "hot": [{"symbol": s, "etf": "SPY", "etf_name": "S&P 500 (SPY)", "hot": True,
                 "volume_ratio": 1.5} for s in hot],
        "cold": [{"symbol": s, "etf": "SPY", "etf_name": "S&P 500 (SPY)", "hot": False,
                  "volume_ratio": 0.8} for s in (cold or [])],
        "summary": "## Sector Volume Pre-Filter",
    }


def _cz(found: list[str]) -> dict:
    return {
        "found": [{"symbol": s, "zone_low": 9.0, "zone_high": 11.0, "entry": 10.0,
                   "stop": 8.9, "target": 12.2, "rr_ratio": 2.0, "zone_candles": 12,
                   "target_source": "2:1_rr"} for s in found],
        "no_zone": [],
        "errors": [],
        "summary": "## Consolidation Zone Scan",
    }


def _stub_engines(server, monkeypatch, seen: dict, hot=None, found=None):
    def _sec(symbols, *a, **k):
        seen.setdefault("sector_calls", []).append(list(symbols))
        picked = [s for s in symbols if s in (hot if hot is not None else symbols)]
        return _sector(picked, [s for s in symbols if s not in picked])

    def _scan(symbols, interval="5m", period="1d", min_rr=2.0):
        seen.setdefault("cz_calls", []).append(
            {"symbols": list(symbols), "interval": interval,
             "period": period, "min_rr": min_rr}
        )
        return _cz(found if found is not None else list(symbols))

    monkeypatch.setattr(server, "batch_sector_check", _sec, raising=True)
    monkeypatch.setattr(server, "scan_consolidations", _scan, raising=True)
    return seen


# ─── auth + scoping ──────────────────────────────────────────────────────────

def test_scanner_requires_a_session(client, monkeypatch):
    server, c = client
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: None)
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda p, c: "allow", raising=False)
    assert c.post("/api/mybook/scanner", json={}).status_code == 401


def test_scanner_scopes_symbols_to_the_customers_own_book_and_watchlist(
    client, au, monkeypatch
):
    server, c = client
    _as(server, monkeypatch, CUSTOMER_EMAIL)
    _seed(au)
    seen = _stub_engines(server, monkeypatch, {})
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")

    r = c.post("/api/mybook/scanner", json={})
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert set(d["symbols"]) == set(BOOK) | set(WATCH)
    assert set(seen["sector_calls"][0]) == set(BOOK) | set(WATCH)
    assert not (set(d["symbols"]) & OWNER_SYMBOLS)


def test_scanner_never_touches_the_owner_snapshot(client, au, monkeypatch):
    """The owner scanner resolves symbols from the RH snapshot. If the customer
    path ever reaches for it, the customer receives the owner's book."""
    server, c = client
    _as(server, monkeypatch, CUSTOMER_EMAIL)
    _seed(au)
    _stub_engines(server, monkeypatch, {})

    def _boom(*a, **k):
        raise AssertionError("customer scanner read the owner snapshot")

    monkeypatch.setattr(server, "_resolved_payload", _boom, raising=True)
    monkeypatch.setattr(server, "_context_payload", _boom, raising=True)
    monkeypatch.setattr(server, "load_snapshot_payload", _boom, raising=True)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")

    r = c.post("/api/mybook/scanner", json={})
    assert r.status_code == 200, r.text
    assert not (set(r.json()["data"]["symbols"]) & OWNER_SYMBOLS)


def test_scanner_excludes_the_watchlist_on_request(client, au, monkeypatch):
    server, c = client
    _as(server, monkeypatch, CUSTOMER_EMAIL)
    _seed(au)
    seen = _stub_engines(server, monkeypatch, {})
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")

    r = c.post("/api/mybook/scanner", json={"include_watchlist": False})
    assert set(r.json()["data"]["symbols"]) == set(BOOK)
    assert set(seen["sector_calls"][0]) == set(BOOK)


def test_scanner_one_customer_cannot_scan_anothers_symbols(client, au, monkeypatch):
    server, c = client
    _seed(au, OTHER_CUSTOMER, book=["ZZZX"], watch=[])
    _as(server, monkeypatch, CUSTOMER_EMAIL)
    _seed(au)
    _stub_engines(server, monkeypatch, {})
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")

    r = c.post("/api/mybook/scanner", json={})
    assert "ZZZX" not in r.json()["data"]["symbols"]


# ─── boundedness ─────────────────────────────────────────────────────────────

def test_scanner_caps_symbols_and_discloses_the_truncation(client, au, monkeypatch):
    server, c = client
    cap = server.MYBOOK_SCANNER_MAX_SYMBOLS
    _as(server, monkeypatch, CUSTOMER_EMAIL)
    book = [f"ZB{i:03d}" for i in range(cap + 5)]
    _seed(au, book=book, watch=[])
    seen = _stub_engines(server, monkeypatch, {})
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")

    d = c.post("/api/mybook/scanner", json={}).json()["data"]
    assert d["symbol_cap"] == cap
    assert d["scanned_count"] == cap
    assert d["requested_count"] == cap + 5
    assert d["truncated"] is True
    assert len(seen["sector_calls"][0]) == cap, "cap must bite BEFORE the fan-out"


def test_scanner_book_symbols_outrank_watchlist_when_truncating(client, au, monkeypatch):
    """Money at risk beats aspiration: the book must survive the cut."""
    server, c = client
    cap = server.MYBOOK_SCANNER_MAX_SYMBOLS
    _as(server, monkeypatch, CUSTOMER_EMAIL)
    book = [f"ZB{i:03d}" for i in range(cap)]
    _seed(au, book=book, watch=["ZWWW"])
    _stub_engines(server, monkeypatch, {})
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")

    d = c.post("/api/mybook/scanner", json={}).json()["data"]
    assert set(d["symbols"]) == set(book)
    assert "ZWWW" not in d["symbols"]


def test_scanner_untruncated_book_says_so(client, au, monkeypatch):
    server, c = client
    _as(server, monkeypatch, CUSTOMER_EMAIL)
    _seed(au)
    _stub_engines(server, monkeypatch, {})
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")

    d = c.post("/api/mybook/scanner", json={}).json()["data"]
    assert d["truncated"] is False


def test_scanner_empty_book_is_ok_not_an_error(client, monkeypatch):
    server, c = client
    _as(server, monkeypatch, CUSTOMER_EMAIL)
    _stub_engines(server, monkeypatch, {})
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")

    r = c.post("/api/mybook/scanner", json={})
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["symbols"] == [] and d["scanned_count"] == 0


# ─── pipeline order + owner-identical shape ──────────────────────────────────

def test_consolidation_runs_only_over_hot_sector_symbols(client, au, monkeypatch):
    server, c = client
    _as(server, monkeypatch, CUSTOMER_EMAIL)
    _seed(au)
    seen = _stub_engines(server, monkeypatch, {}, hot=["ZZZA"])
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")

    d = c.post("/api/mybook/scanner", json={}).json()["data"]
    assert seen["cz_calls"][0]["symbols"] == ["ZZZA"]
    assert d["hot_symbols"] == ["ZZZA"]
    assert d["sector_fallback"] is False


def test_consolidation_falls_back_to_all_symbols_when_no_sector_is_hot(
    client, au, monkeypatch
):
    """Mirrors the owner UI: a low-volume session must not blank the scanner."""
    server, c = client
    _as(server, monkeypatch, CUSTOMER_EMAIL)
    _seed(au)
    seen = _stub_engines(server, monkeypatch, {}, hot=[])
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")

    d = c.post("/api/mybook/scanner", json={}).json()["data"]
    assert set(seen["cz_calls"][0]["symbols"]) == set(BOOK) | set(WATCH)
    assert d["sector_fallback"] is True


def test_scanner_returns_owner_shaped_blocks(client, au, monkeypatch):
    """One frontend renderer must serve both surfaces, so the inner blocks are
    byte-shaped like /api/scanner/sector and /api/scanner/consolidation."""
    server, c = client
    _as(server, monkeypatch, CUSTOMER_EMAIL)
    _seed(au)
    _stub_engines(server, monkeypatch, {})
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")

    d = c.post("/api/mybook/scanner", json={}).json()["data"]
    assert set(d["sector"]) >= {"hot", "cold", "summary"}
    assert set(d["consolidation"]) >= {"found", "no_zone", "errors", "summary"}


# ─── request parameters ──────────────────────────────────────────────────────

def test_timeframe_and_min_rr_reach_the_engine(client, au, monkeypatch):
    server, c = client
    _as(server, monkeypatch, CUSTOMER_EMAIL)
    _seed(au)
    seen = _stub_engines(server, monkeypatch, {})
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")

    d = c.post(
        "/api/mybook/scanner", json={"timeframe": "5MIN", "min_rr": 3.5}
    ).json()["data"]
    call = seen["cz_calls"][0]
    assert (call["interval"], call["period"]) == ("5m", "1d")
    assert call["min_rr"] == 3.5
    assert (d["timeframe"], d["interval"], d["period"]) == ("5MIN", "5m", "1d")


def test_explicit_interval_and_period_are_honoured(client, au, monkeypatch):
    server, c = client
    _as(server, monkeypatch, CUSTOMER_EMAIL)
    _seed(au)
    seen = _stub_engines(server, monkeypatch, {})
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")

    c.post("/api/mybook/scanner", json={"interval": "1h", "period": "5d"})
    call = seen["cz_calls"][0]
    assert (call["interval"], call["period"]) == ("1h", "5d")


def test_unknown_timeframe_is_rejected(client, au, monkeypatch):
    server, c = client
    _as(server, monkeypatch, CUSTOMER_EMAIL)
    _seed(au)
    _stub_engines(server, monkeypatch, {})
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")

    assert c.post("/api/mybook/scanner", json={"timeframe": "hourly"}).status_code == 400


def test_invalid_interval_is_rejected(client, au, monkeypatch):
    server, c = client
    _as(server, monkeypatch, CUSTOMER_EMAIL)
    _seed(au)
    _stub_engines(server, monkeypatch, {})
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")

    r = c.post("/api/mybook/scanner", json={"interval": "../etc", "period": "1d"})
    assert r.status_code == 400


def test_absurd_min_rr_is_clamped_not_crashed(client, au, monkeypatch):
    server, c = client
    _as(server, monkeypatch, CUSTOMER_EMAIL)
    _seed(au)
    seen = _stub_engines(server, monkeypatch, {})
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")

    c.post("/api/mybook/scanner", json={"min_rr": "not-a-number"})
    c.post("/api/mybook/scanner", json={"min_rr": -4, "timeframe": "daily"})
    for call in seen["cz_calls"]:
        assert 0.0 <= call["min_rr"] <= 10.0


# ─── degradation ─────────────────────────────────────────────────────────────

def _tight_frame(n: int = 30, base: float = 100.0) -> pd.DataFrame:
    close = np.full(n, base) + np.linspace(0, 0.4, n)
    return pd.DataFrame(
        {
            "Open": close,
            "High": close + 0.05,
            "Low": close - 0.05,
            "Close": close,
            "Volume": np.full(n, 1_000_000.0),
        }
    )


def test_one_symbol_failing_does_not_fail_the_scan(client, au, monkeypatch):
    """Real engine, per-symbol fault injection: ZZZB's download blows up and ZZZA
    must still come back with a setup."""
    server, c = client
    _as(server, monkeypatch, CUSTOMER_EMAIL)
    _seed(au, book=["ZZZA", "ZZZB"], watch=[])

    import consolidation_zone
    import price_source

    def _dl(symbol, **kwargs):
        if symbol == "ZZZB":
            raise RuntimeError("upstream 500 for ZZZB")
        return _tight_frame()

    monkeypatch.setattr(price_source, "locked_yf_download", _dl, raising=True)
    monkeypatch.setattr(consolidation_zone.price_source, "locked_yf_download", _dl,
                        raising=True)
    monkeypatch.setattr(server, "batch_sector_check",
                        lambda syms, *a, **k: _sector(list(syms)), raising=True)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")

    r = c.post("/api/mybook/scanner", json={"timeframe": "daily"})
    assert r.status_code == 200, r.text
    cz = r.json()["data"]["consolidation"]
    surveyed = {z["symbol"] for z in cz["found"]} | set(cz["no_zone"])
    assert "ZZZA" in surveyed
    assert [e["symbol"] for e in cz["errors"]] == ["ZZZB"]


def test_engine_error_text_is_never_echoed_to_the_customer(client, au, monkeypatch):
    server, c = client
    _as(server, monkeypatch, CUSTOMER_EMAIL)
    _seed(au, book=["ZZZB"], watch=[])

    import consolidation_zone
    import price_source

    def _dl(symbol, **kwargs):
        raise RuntimeError("postgres://secret@internal-host/db exploded")

    monkeypatch.setattr(price_source, "locked_yf_download", _dl, raising=True)
    monkeypatch.setattr(consolidation_zone.price_source, "locked_yf_download", _dl,
                        raising=True)
    monkeypatch.setattr(server, "batch_sector_check",
                        lambda syms, *a, **k: _sector(list(syms)), raising=True)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")

    r = c.post("/api/mybook/scanner", json={"timeframe": "daily"})
    assert "internal-host" not in r.text and "postgres" not in r.text


def test_a_dead_sector_leg_does_not_kill_the_consolidation_leg(client, au, monkeypatch):
    server, c = client
    _as(server, monkeypatch, CUSTOMER_EMAIL)
    _seed(au)
    seen: dict = {}

    def _boom(*a, **k):
        raise RuntimeError("sector ETF feed down")

    monkeypatch.setattr(server, "batch_sector_check", _boom, raising=True)
    monkeypatch.setattr(
        server, "scan_consolidations",
        lambda symbols, **k: (seen.setdefault("syms", list(symbols)), _cz(list(symbols)))[1],
        raising=True,
    )
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")

    r = c.post("/api/mybook/scanner", json={})
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["sector"].get("error")
    assert set(seen["syms"]) == set(BOOK) | set(WATCH), "must fall back to all symbols"
    assert d["consolidation"]["found"]


def test_a_dead_consolidation_leg_still_returns_the_sector_block(client, au, monkeypatch):
    server, c = client
    _as(server, monkeypatch, CUSTOMER_EMAIL)
    _seed(au)
    monkeypatch.setattr(server, "batch_sector_check",
                        lambda syms, *a, **k: _sector(list(syms)), raising=True)

    def _boom(*a, **k):
        raise RuntimeError("yahoo down")

    monkeypatch.setattr(server, "scan_consolidations", _boom, raising=True)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")

    r = c.post("/api/mybook/scanner", json={})
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["sector"]["hot"]
    assert d["consolidation"].get("error")


# ─── caching ─────────────────────────────────────────────────────────────────

def test_repeat_calls_are_served_from_cache(client, au, monkeypatch):
    server, c = client
    _as(server, monkeypatch, CUSTOMER_EMAIL)
    _seed(au)
    seen = _stub_engines(server, monkeypatch, {})
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")

    first = c.post("/api/mybook/scanner", json={}).json()
    second = c.post("/api/mybook/scanner", json={}).json()
    assert len(seen["sector_calls"]) == 1, "second call re-ran the fan-out"
    assert first["data"]["symbols"] == second["data"]["symbols"]


def test_cache_is_keyed_per_user(client, au, monkeypatch):
    server, c = client
    _seed(au, OTHER_CUSTOMER, book=["ZZZX"], watch=[])
    _as(server, monkeypatch, CUSTOMER_EMAIL)
    _seed(au)
    seen = _stub_engines(server, monkeypatch, {})
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")

    mine = c.post("/api/mybook/scanner", json={}).json()["data"]["symbols"]
    _as(server, monkeypatch, OTHER_CUSTOMER)
    theirs = c.post("/api/mybook/scanner", json={}).json()["data"]["symbols"]

    assert set(mine) == set(BOOK) | set(WATCH)
    assert theirs == ["ZZZX"], "one customer was served another's cached scan"
    assert len(seen["sector_calls"]) == 2


def test_changing_the_timeframe_bypasses_the_cache(client, au, monkeypatch):
    server, c = client
    _as(server, monkeypatch, CUSTOMER_EMAIL)
    _seed(au)
    seen = _stub_engines(server, monkeypatch, {})
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")

    c.post("/api/mybook/scanner", json={"timeframe": "daily"})
    c.post("/api/mybook/scanner", json={"timeframe": "5MIN"})
    assert len(seen["cz_calls"]) == 2


def test_adding_a_symbol_bypasses_the_cache(client, au, monkeypatch):
    server, c = client
    _as(server, monkeypatch, CUSTOMER_EMAIL)
    _seed(au)
    seen = _stub_engines(server, monkeypatch, {})
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")

    c.post("/api/mybook/scanner", json={})
    au.set_position(CUSTOMER_EMAIL, "ZZZD", 3)
    d = c.post("/api/mybook/scanner", json={}).json()["data"]
    assert len(seen["sector_calls"]) == 2
    assert "ZZZD" in d["symbols"]


# ─── no LLM spend ────────────────────────────────────────────────────────────

def test_scanner_spends_no_llm(client, au, monkeypatch):
    """Pure price math. A customer must be able to hammer this without burning
    the owner's Anthropic budget."""
    server, c = client
    _as(server, monkeypatch, CUSTOMER_EMAIL)
    _seed(au)
    _stub_engines(server, monkeypatch, {})

    def _boom(*a, **k):
        raise AssertionError("scanner made an LLM call")

    monkeypatch.setattr(server, "deep_analyze_portfolio", _boom, raising=True)
    monkeypatch.setattr(server, "deep_analyze_ticker", _boom, raising=True)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")

    assert c.post("/api/mybook/scanner", json={}).status_code == 200
