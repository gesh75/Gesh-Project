"""GATE 2 — unified unknown / unavailable-symbol semantics, every engine × 3 cases.

CASE A  clearly nonexistent symbol (syntactically valid, no market identity)
CASE B  valid symbol, one provider unavailable
CASE C  valid symbol, genuinely neutral evidence

The backend must distinguish all three. Expectations are computed independently
here — never via the production helper under test.
"""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

import server

UNKNOWN = "ZQXZ9"          # passes _safe_symbol, has no market identity
REAL = "AAPL"

# Two acceptable explicit shapes, enumerated so the test — not the code — owns
# the contract:
#   404                       -> not-found / no coverage
#   200 + status "unavailable" -> well-formed request, no data to compute
NOT_FOUND_404 = {"analyst", "fundamentals", "chart"}
UNAVAILABLE_200 = {"consensus", "regime", "momentum", "fft-cycles",
                   "earnings", "levels", "news"}

# Fabrication tells that must NEVER appear in a CASE-A response body.
_FORBIDDEN = ('"status": "ok"', '"status":"ok"', '"verdict": "hold"',
              '"verdict":"hold"', '"recommendation": "hold"', '"regime": "falling"',
              'just now')


def _route(engine: str, sym: str) -> str:
    return f"/api/chart/{sym}?range=1mo" if engine == "chart" else f"/api/{engine}/{sym}"


@pytest.fixture
def unknown_client(monkeypatch):
    """Every upstream source returns nothing → the symbol has no market identity."""
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: False)
    import earnings_viz
    import fundamental_agent
    import market_data
    import news_agent
    import price_source
    monkeypatch.setattr(server, "_latest_close", lambda s: None, raising=True)
    monkeypatch.setattr(server, "_cached_close", lambda s: None, raising=True)
    monkeypatch.setattr(server, "_latest_close_meta",
                        lambda s: (None, {"source": None, "substituted": False}), raising=True)
    monkeypatch.setattr(price_source, "yf_download", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(price_source, "intraday_5m", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(market_data, "rh_analyst", lambda s: None, raising=False)
    monkeypatch.setattr(market_data, "get_analyst_targets",
                        lambda syms: {s: {"error": "no data"} for s in syms}, raising=False)
    monkeypatch.setattr(market_data, "rh_fundamentals", lambda s: None, raising=False)
    monkeypatch.setattr(fundamental_agent, "_fetch_fundamentals_batch",
                        lambda syms: [{"symbol": s, "error": "no data"} for s in syms], raising=False)
    monkeypatch.setattr(earnings_viz, "_from_finnhub", lambda s: None, raising=False)
    monkeypatch.setattr(earnings_viz, "_from_yfinance", lambda s: None, raising=False)
    monkeypatch.setattr(news_agent, "_fetch_alpaca_news", lambda s: [], raising=False)
    monkeypatch.setattr(news_agent, "_fetch_all_news", lambda s: [], raising=False)
    monkeypatch.setattr(news_agent, "get_cached_articles", lambda s: [], raising=False)
    return TestClient(server.app, raise_server_exceptions=False)


# ─── CASE A ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("engine", sorted(NOT_FOUND_404 | UNAVAILABLE_200))
def test_caseA_unknown_symbol_is_explicitly_unavailable(unknown_client, engine):
    r = unknown_client.get(_route(engine, UNKNOWN))
    if engine in NOT_FOUND_404:
        assert r.status_code == 404, f"{engine}: {r.status_code} {r.text[:120]}"
        return
    assert r.status_code == 200, f"{engine}: {r.status_code}"
    body = r.json()
    assert body["status"] == "unavailable", f"{engine} status={body.get('status')}"
    assert body["reason_code"] == "symbol_not_found", f"{engine} reason={body.get('reason_code')}"
    assert body["symbol"] == UNKNOWN
    assert body["data_as_of"] is None, f"{engine} stamped a timestamp on nonexistent data"
    assert body["source"] is None


@pytest.mark.parametrize("engine", sorted(UNAVAILABLE_200))
def test_caseA_no_fabricated_success_tell(unknown_client, engine):
    blob = unknown_client.get(_route(engine, UNKNOWN)).text.lower()
    for tell in _FORBIDDEN:
        assert tell not in blob, f"{engine} leaked fabrication tell {tell!r}"


def test_caseA_no_synthetic_indicators_or_levels(unknown_client):
    """regime must not invent an RSI; levels must not invent support/resistance."""
    reg = unknown_client.get(_route("regime", UNKNOWN)).json()
    assert reg["status"] == "unavailable"
    # any retained data must carry nulls, never a synthetic reading
    assert (reg.get("data") or {}).get("rsi") in (None, "null")
    lvl = unknown_client.get(_route("levels", UNKNOWN)).json()
    assert lvl["status"] == "unavailable"
    assert (lvl.get("data") or {}).get("available") is False


# ─── CASE B — valid symbol, one provider down ────────────────────────────────

def test_caseB_valid_symbol_missing_provider_is_data_unavailable(monkeypatch):
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: False)
    # Symbol HAS market identity (a price), but the earnings provider is down.
    monkeypatch.setattr(server, "_cached_close", lambda s: 213.45, raising=True)
    import earnings_viz
    monkeypatch.setattr(earnings_viz, "_from_finnhub", lambda s: None, raising=False)
    monkeypatch.setattr(earnings_viz, "_from_yfinance", lambda s: None, raising=False)
    body = TestClient(server.app, raise_server_exceptions=False).get(f"/api/earnings/{REAL}").json()
    assert body["status"] == "unavailable"
    assert body["reason_code"] == "data_unavailable", (
        "a real symbol with a downed provider must be data_unavailable, not symbol_not_found")


def test_caseB_one_engine_down_does_not_make_the_symbol_nonexistent(monkeypatch):
    """A real symbol whose PRICE resolves is not 'not found' just because earnings
    is empty — the other engines must not inherit symbol_not_found."""
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: False)
    monkeypatch.setattr(server, "_cached_close", lambda s: 213.45, raising=True)
    import earnings_viz
    monkeypatch.setattr(earnings_viz, "_from_finnhub", lambda s: None, raising=False)
    monkeypatch.setattr(earnings_viz, "_from_yfinance", lambda s: None, raising=False)
    body = TestClient(server.app, raise_server_exceptions=False).get(f"/api/earnings/{REAL}").json()
    assert body["reason_code"] != "symbol_not_found"


# ─── CASE C — valid symbol, genuinely neutral ────────────────────────────────

def test_caseC_neutral_consensus_stays_a_real_success(monkeypatch):
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: False)
    monkeypatch.setattr(server, "_cached_close", lambda s: 100.0, raising=True)
    import consensus
    monkeypatch.setattr(consensus, "run_consensus_holding",
                        lambda *a, **k: {"symbol": "KO", "verdict": "HOLD",
                                         "confidence": 0.5, "active_nodes": 8,
                                         "bullish": 3, "bearish": 3, "neutral": 2,
                                         "votes": {"Momentum": 0}}, raising=False)
    body = TestClient(server.app, raise_server_exceptions=False).get("/api/consensus/KO").json()
    assert body["status"] == "ok", "a genuine neutral result must not be flattened to unavailable"
    assert body["data"]["verdict"] == "HOLD"
    assert body["data"]["active_nodes"] == 8


def test_caseC_neutral_regime_stays_a_real_success(monkeypatch):
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: False)
    monkeypatch.setattr(server, "_cached_close", lambda s: 100.0, raising=True)
    import regime
    monkeypatch.setattr(regime, "detect_regimes",
                        lambda syms: {"regimes": [{"symbol": "KO", "regime": "RANGING",
                                                   "adx": 18.2, "rsi": 51.0,
                                                   "description": "Range-bound"}]}, raising=False)
    body = TestClient(server.app, raise_server_exceptions=False).get("/api/regime/KO").json()
    assert body["status"] == "ok"
    assert body["data"]["regime"] == "RANGING"
    assert body["data"]["rsi"] == 51.0        # real reading preserved, not nulled


def test_caseC_empty_news_for_a_real_symbol_is_ok_not_unavailable(monkeypatch):
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: False)
    monkeypatch.setattr(server, "_cached_close", lambda s: 500.0, raising=True)
    import news_agent
    for fn in ("_fetch_alpaca_news", "_fetch_all_news", "get_cached_articles"):
        monkeypatch.setattr(news_agent, fn, lambda s: [], raising=False)
    body = TestClient(server.app, raise_server_exceptions=False).get(f"/api/news/{REAL}").json()
    assert body["status"] == "ok"
    assert body["data"] == []


# ─── the reason-code vocabulary is closed ────────────────────────────────────

def test_reason_codes_are_from_the_documented_set(unknown_client):
    allowed = {"symbol_not_found", "data_unavailable"}
    for engine in UNAVAILABLE_200:
        body = unknown_client.get(_route(engine, UNKNOWN)).json()
        assert body["reason_code"] in allowed, f"{engine}: {body['reason_code']}"
