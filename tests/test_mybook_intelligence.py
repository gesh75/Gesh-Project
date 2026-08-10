"""Customer-scoped Intelligence — SEC filings + news sentiment + fundamental
momentum over the customer's OWN symbols.

The owner's Intelligence page drives `sec_agent`, `news_agent` and
`fundamental_agent` over holdings resolved from the Robinhood snapshot. All three
are symbol-scoped, but the LIVE entry points (`watch_filings`, `scan_news`,
`run_fundamental_scan`) each spend Claude tokens, and `get_latest_fundamental_scan`
returns the OWNER's whole-universe scan. Neither belongs on a customer path, so
this surface is built from the cached/mechanical readers only.

Three properties are pinned:

  * ENTITLEMENT — every row leaving this endpoint is post-filtered to the symbol
    set the customer entered, so a sloppy engine cannot smuggle a symbol in.
  * NO LLM — a customer clicking this must never move the owner's Anthropic bill.
  * PER-SYMBOL DEGRADATION — one unknown ticker must not blank the panel.
"""

from __future__ import annotations

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


def _filing(sym: str) -> dict:
    return {
        "symbol": sym, "form_type": "8-K", "filing_date": "2026-08-01",
        "filing_url": f"https://sec.gov/{sym}", "action_signal": "NEUTRAL",
        "urgency": "LOW", "analysis": {"summary": f"{sym} filed an 8-K"},
    }


def _sentiment(sym: str, score: int = 40, articles: int = 3) -> dict:
    return {
        "symbol": sym, "overall_sentiment": "POSITIVE", "sentiment_score": score,
        "impact_level": "MEDIUM",
        "analysis": {"article_count": articles,
                     "reasoning": "INTERNAL model prose that must not ship"},
        "analyzed_at": "2026-08-08T12:00:00",
    }


def _fund(sym: str) -> dict:
    return {
        "symbol": sym, "company_name": f"{sym} Inc", "sector": "Technology",
        "price": 10.0, "ret_1m": 4.0, "ret_3m": 9.0, "revenue_growth": 22.0,
        "earnings_growth": 15.0, "forward_pe": 18.0, "analyst_upside": 12.0,
        "insider_signal": "BUYING", "num_analysts": 9, "recommendation": "buy",
    }


def _stub_legs(server, monkeypatch, seen: dict, sec=None, news=None, fund=None):
    import fundamental_agent

    def _sec(symbols, days=30):
        seen.setdefault("sec_calls", []).append({"symbols": list(symbols), "days": days})
        return sec if sec is not None else [_filing(s) for s in symbols]

    def _news(symbols, days=7):
        seen.setdefault("news_calls", []).append({"symbols": list(symbols), "days": days})
        return news if news is not None else [_sentiment(s) for s in symbols]

    def _fund_batch(symbols):
        seen.setdefault("fund_calls", []).append(list(symbols))
        return fund if fund is not None else [_fund(s) for s in symbols]

    monkeypatch.setattr(server, "get_filing_history", _sec, raising=True)
    monkeypatch.setattr(server, "get_news_history", _news, raising=True)
    monkeypatch.setattr(fundamental_agent, "_fetch_fundamentals_batch", _fund_batch,
                        raising=True)
    return seen


# ─── auth + scoping ──────────────────────────────────────────────────────────

def test_intelligence_requires_a_session(client, monkeypatch):
    server, c = client
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: None)
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda p, c: "allow", raising=False)
    assert c.post("/api/mybook/intelligence", json={}).status_code == 401


def test_intelligence_scopes_symbols_to_the_customers_own_symbols(client, au, monkeypatch):
    server, c = client
    _as(server, monkeypatch, CUSTOMER_EMAIL)
    _seed(au)
    seen = _stub_legs(server, monkeypatch, {})
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")

    r = c.post("/api/mybook/intelligence", json={})
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    want = set(BOOK) | set(WATCH)
    assert set(d["symbols"]) == want
    assert set(seen["news_calls"][0]["symbols"]) == want
    assert set(seen["fund_calls"][0]) == want
    assert set(seen["sec_calls"][0]["symbols"]) <= want
    assert not (set(d["symbols"]) & OWNER_SYMBOLS)


def test_intelligence_never_touches_the_owner_snapshot(client, au, monkeypatch):
    server, c = client
    _as(server, monkeypatch, CUSTOMER_EMAIL)
    _seed(au)
    _stub_legs(server, monkeypatch, {})

    def _boom(*a, **k):
        raise AssertionError("customer intelligence read the owner snapshot")

    monkeypatch.setattr(server, "_resolved_payload", _boom, raising=True)
    monkeypatch.setattr(server, "_context_payload", _boom, raising=True)
    monkeypatch.setattr(server, "load_snapshot_payload", _boom, raising=True)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")

    assert c.post("/api/mybook/intelligence", json={}).status_code == 200


def test_engine_rows_for_unentitled_symbols_are_dropped(client, au, monkeypatch):
    """Defence in depth. `sec_filings` / `news_sentiment` are symbol-keyed OWNER
    tables — a widened query or a fuzzy engine match would hand a customer the
    owner's names. Every row is post-filtered against what they entered."""
    server, c = client
    _as(server, monkeypatch, CUSTOMER_EMAIL)
    _seed(au)
    _stub_legs(
        server, monkeypatch, {},
        sec=[_filing("ZZZA"), _filing("NVDA")],
        news=[_sentiment("ZZZB"), _sentiment("AMD")],
        fund=[_fund("ZZZC"), _fund("RTX")],
    )
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")

    r = c.post("/api/mybook/intelligence", json={})
    d = r.json()["data"]
    assert [x["symbol"] for x in d["sec"]["results"]] == ["ZZZA"]
    assert [x["symbol"] for x in d["news"]["results"]] == ["ZZZB"]
    assert [x["symbol"] for x in d["fundamentals"]["results"]] == ["ZZZC"]
    for owner_sym in ("NVDA", "AMD", "RTX"):
        assert owner_sym not in r.text


def test_one_customer_cannot_see_anothers_symbols(client, au, monkeypatch):
    server, c = client
    _seed(au, OTHER_CUSTOMER, book=["ZZZX"], watch=[])
    _as(server, monkeypatch, CUSTOMER_EMAIL)
    _seed(au)
    _stub_legs(server, monkeypatch, {})
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")

    assert "ZZZX" not in c.post("/api/mybook/intelligence", json={}).text


# ─── boundedness ─────────────────────────────────────────────────────────────

def test_intelligence_caps_symbols_and_discloses_the_truncation(client, au, monkeypatch):
    server, c = client
    cap = server.MYBOOK_INTEL_MAX_SYMBOLS
    _as(server, monkeypatch, CUSTOMER_EMAIL)
    _seed(au, book=[f"ZB{i:03d}" for i in range(cap + 4)], watch=[])
    seen = _stub_legs(server, monkeypatch, {})
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")

    d = c.post("/api/mybook/intelligence", json={}).json()["data"]
    assert d["symbol_cap"] == cap
    assert d["scanned_count"] == cap
    assert d["requested_count"] == cap + 4
    assert d["truncated"] is True
    assert len(seen["fund_calls"][0]) == cap


def test_the_sec_leg_carries_its_own_tighter_cap(client, au, monkeypatch):
    """EDGAR costs a CIK lookup plus one RSS pull per form type per symbol — four
    round trips a name, against a 10-second timeout. It is capped below the
    endpoint-wide limit, and the narrowing is disclosed rather than silent."""
    server, c = client
    sec_cap = server.MYBOOK_INTEL_SEC_MAX_SYMBOLS
    assert sec_cap < server.MYBOOK_INTEL_MAX_SYMBOLS
    _as(server, monkeypatch, CUSTOMER_EMAIL)
    _seed(au, book=[f"ZB{i:03d}" for i in range(sec_cap + 3)], watch=[])
    seen = _stub_legs(server, monkeypatch, {})
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")

    d = c.post("/api/mybook/intelligence", json={}).json()["data"]
    assert len(seen["sec_calls"][0]["symbols"]) == sec_cap
    assert d["sec"]["symbol_cap"] == sec_cap
    assert d["sec"]["truncated"] is True
    assert len(d["sec"]["symbols"]) == sec_cap


def test_intelligence_empty_book_is_ok_not_an_error(client, monkeypatch):
    server, c = client
    _as(server, monkeypatch, CUSTOMER_EMAIL)
    _stub_legs(server, monkeypatch, {})
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")

    r = c.post("/api/mybook/intelligence", json={})
    assert r.status_code == 200
    assert r.json()["data"]["symbols"] == []


# ─── news: allow-listed, never fabricated ────────────────────────────────────

def test_news_is_allow_listed_and_never_ships_the_model_prose(client, au, monkeypatch):
    """Matches the /api/news/{symbol} contract: score / mood / article_count /
    as_of only. The raw analysis is owner-side research, not a customer field."""
    server, c = client
    _as(server, monkeypatch, CUSTOMER_EMAIL)
    _seed(au, book=["ZZZA"], watch=[])
    _stub_legs(server, monkeypatch, {}, news=[_sentiment("ZZZA")])
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")

    r = c.post("/api/mybook/intelligence", json={})
    row = r.json()["data"]["news"]["results"][0]
    assert set(row) == {"symbol", "score", "mood", "impact", "article_count", "as_of"}
    assert "INTERNAL model prose" not in r.text
    assert row["score"] == 40 and row["article_count"] == 3


def test_sentiment_over_zero_articles_is_never_reported(client, au, monkeypatch):
    """Project rule: never fabricate sentiment when article_count is 0."""
    server, c = client
    _as(server, monkeypatch, CUSTOMER_EMAIL)
    _seed(au, book=["ZZZA", "ZZZB"], watch=[])
    _stub_legs(server, monkeypatch, {},
               news=[_sentiment("ZZZA", articles=0), _sentiment("ZZZB", articles=2)])
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")

    d = c.post("/api/mybook/intelligence", json={}).json()["data"]
    assert [r["symbol"] for r in d["news"]["results"]] == ["ZZZB"]


def test_news_mood_is_derived_only_from_reported_scores(client, au, monkeypatch):
    server, c = client
    _as(server, monkeypatch, CUSTOMER_EMAIL)
    _seed(au, book=["ZZZA", "ZZZB"], watch=[])
    _stub_legs(server, monkeypatch, {},
               news=[_sentiment("ZZZA", score=60), _sentiment("ZZZB", score=40)])
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")

    d = c.post("/api/mybook/intelligence", json={}).json()["data"]
    assert d["news"]["avg_score"] == 50.0
    assert d["news"]["market_mood"] == "RISK_ON"


def test_empty_news_cache_is_neutral_not_bullish(client, au, monkeypatch):
    server, c = client
    _as(server, monkeypatch, CUSTOMER_EMAIL)
    _seed(au, book=["ZZZA"], watch=[])
    _stub_legs(server, monkeypatch, {}, news=[])
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")

    d = c.post("/api/mybook/intelligence", json={}).json()["data"]
    assert d["news"]["results"] == []
    assert d["news"]["market_mood"] == "NEUTRAL"


# ─── fundamentals ────────────────────────────────────────────────────────────

def test_fundamentals_carry_the_momentum_score(client, au, monkeypatch):
    server, c = client
    _as(server, monkeypatch, CUSTOMER_EMAIL)
    _seed(au, book=["ZZZA"], watch=[])
    _stub_legs(server, monkeypatch, {}, fund=[_fund("ZZZA")])
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")

    row = c.post("/api/mybook/intelligence", json={}).json()["data"]["fundamentals"][
        "results"
    ][0]
    assert isinstance(row["momentum_score"], (int, float))
    assert row["revenue_growth"] == 22.0


def test_fundamentals_are_ranked_best_first(client, au, monkeypatch):
    server, c = client
    _as(server, monkeypatch, CUSTOMER_EMAIL)
    _seed(au, book=["ZZZA", "ZZZB"], watch=[])
    weak = dict(_fund("ZZZA"), revenue_growth=1.0, earnings_growth=0.0,
                analyst_upside=0.0, insider_signal="NEUTRAL")
    strong = _fund("ZZZB")
    _stub_legs(server, monkeypatch, {}, fund=[weak, strong])
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")

    rows = c.post("/api/mybook/intelligence", json={}).json()["data"]["fundamentals"][
        "results"
    ]
    assert [r["symbol"] for r in rows] == ["ZZZB", "ZZZA"]


def test_one_unknown_ticker_does_not_blank_the_panel(client, au, monkeypatch):
    server, c = client
    _as(server, monkeypatch, CUSTOMER_EMAIL)
    _seed(au, book=["ZZZA", "ZZZB"], watch=[])
    _stub_legs(
        server, monkeypatch, {},
        fund=[_fund("ZZZA"),
              {"symbol": "ZZZB", "error": "HTTPError at https://query2.finance.yahoo.com/x"}],
    )
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")

    r = c.post("/api/mybook/intelligence", json={})
    assert r.status_code == 200, r.text
    f = r.json()["data"]["fundamentals"]
    assert [x["symbol"] for x in f["results"]] == ["ZZZA"]
    assert [x["symbol"] for x in f["errors"]] == ["ZZZB"]
    assert "query2.finance.yahoo.com" not in r.text, "raw upstream error echoed"


# ─── leg isolation ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("leg", ["sec", "news", "fundamentals"])
def test_a_dead_leg_does_not_kill_the_others(client, au, monkeypatch, leg):
    import fundamental_agent

    server, c = client
    _as(server, monkeypatch, CUSTOMER_EMAIL)
    _seed(au, book=["ZZZA"], watch=[])
    _stub_legs(server, monkeypatch, {})

    def _boom(*a, **k):
        raise RuntimeError("upstream down")

    target = {
        "sec": (server, "get_filing_history"),
        "news": (server, "get_news_history"),
        "fundamentals": (fundamental_agent, "_fetch_fundamentals_batch"),
    }[leg]
    monkeypatch.setattr(target[0], target[1], _boom, raising=True)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")

    r = c.post("/api/mybook/intelligence", json={})
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d[leg].get("error"), f"{leg} should report its own failure"
    assert "upstream down" not in r.text
    for other in ({"sec", "news", "fundamentals"} - {leg}):
        assert not d[other].get("error"), f"{leg} failing took {other} with it"


# ─── caching + spend ─────────────────────────────────────────────────────────

def test_repeat_calls_are_served_from_cache(client, au, monkeypatch):
    server, c = client
    _as(server, monkeypatch, CUSTOMER_EMAIL)
    _seed(au)
    seen = _stub_legs(server, monkeypatch, {})
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")

    c.post("/api/mybook/intelligence", json={})
    c.post("/api/mybook/intelligence", json={})
    assert len(seen["fund_calls"]) == 1


def test_cache_is_keyed_per_user(client, au, monkeypatch):
    server, c = client
    _seed(au, OTHER_CUSTOMER, book=["ZZZX"], watch=[])
    _as(server, monkeypatch, CUSTOMER_EMAIL)
    _seed(au)
    _stub_legs(server, monkeypatch, {})
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")

    c.post("/api/mybook/intelligence", json={})
    _as(server, monkeypatch, OTHER_CUSTOMER)
    d = c.post("/api/mybook/intelligence", json={}).json()["data"]
    assert d["symbols"] == ["ZZZX"]


def test_intelligence_spends_no_llm(client, au, monkeypatch):
    """The LIVE agent entry points all call Claude. This surface must reach for
    the cached/mechanical readers only."""
    import fundamental_agent
    import news_agent
    import sec_agent

    server, c = client
    _as(server, monkeypatch, CUSTOMER_EMAIL)
    _seed(au)
    _stub_legs(server, monkeypatch, {})

    def _boom(*a, **k):
        raise AssertionError("intelligence spent an LLM call")

    for mod, name in (
        (sec_agent, "watch_filings"), (news_agent, "scan_news"),
        (news_agent, "analyze_news_sentiment"),
        (fundamental_agent, "run_fundamental_scan"),
        (server, "watch_filings"), (server, "scan_news"),
        (server, "run_fundamental_scan"),
    ):
        monkeypatch.setattr(mod, name, _boom, raising=True)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")

    assert c.post("/api/mybook/intelligence", json={}).status_code == 200


def test_intelligence_never_serves_the_owners_universe_scan(client, au, monkeypatch):
    """`get_latest_fundamental_scan` returns the owner's ~95-name run. It is the
    obvious shortcut for a fundamentals panel and it is the wrong one."""
    import fundamental_agent

    server, c = client
    _as(server, monkeypatch, CUSTOMER_EMAIL)
    _seed(au)
    _stub_legs(server, monkeypatch, {})

    def _boom(*a, **k):
        raise AssertionError("served the owner's universe-wide fundamental scan")

    monkeypatch.setattr(fundamental_agent, "get_latest_fundamental_scan", _boom,
                        raising=True)
    monkeypatch.setattr(server, "get_latest_fundamental_scan", _boom, raising=True)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")

    assert c.post("/api/mybook/intelligence", json={}).status_code == 200
