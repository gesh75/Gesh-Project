"""Customer 'analyze any ticker' v1 — the RICH Claude research card.

`deep_analyze_ticker(symbol)` is the money-free, per-ticker subset of the internal
`deep_analyze_portfolio`: VAL/GRO/HLT/MOM/DIV scores, fair-value pill, 3M/12M
prediction, risks/rewards, AI verdict — for ONE public ticker, with no portfolio,
no positions, and no owner dollars. It powers `POST /api/analyze-ticker`, which a
CUSTOMER may call (not owner-gated) and whose public per-share prose must reach the
customer RAW (exempt from money-masking, which would wrongly redact "$180" -> "$•••").

These tests never touch the network (conftest `_no_network` hard-fails real sockets);
a fake Anthropic client is injected to inspect the request shape and feed a
deterministic response.
"""

from __future__ import annotations

from typing import Any

import pytest
from starlette.testclient import TestClient

import ai_analyzer
import server


@pytest.fixture(autouse=True)
def _clear_ticker_cache():
    """The per-symbol card cache is process-wide — clear it so one test's stub
    result never leaks into the next."""
    server._ticker_card_cache.clear()
    yield
    server._ticker_card_cache.clear()


# ─── Fakes (mirror tests/test_ai_analyzer_caching.py) ────────────────────────

class _ToolBlock:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.type = "tool_use"
        self.input = payload


class _FakeMessage:
    def __init__(self, content: list[Any]) -> None:
        self.content = content


class _FakeStreamContext:
    """Mimics the context manager anthropic.messages.stream() returns."""

    def __init__(self, response: _FakeMessage) -> None:
        self._response = response

    def __enter__(self) -> "_FakeStreamContext":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        return None

    def get_final_message(self) -> _FakeMessage:
        return self._response


class _FakeMessages:
    def __init__(self, response: _FakeMessage, sink: dict[str, Any]) -> None:
        self._response, self._sink = response, sink

    def create(self, **kwargs: Any) -> _FakeMessage:
        self._sink.clear()
        self._sink.update(kwargs)
        return self._response

    def stream(self, **kwargs: Any) -> _FakeStreamContext:
        # ai_analyzer._create_streaming() drives deep_analyze_portfolio through
        # client.messages.stream(...).get_final_message() (2026-07-27, dodges a
        # wall-clock timeout on long generations) — mirror create()'s capture.
        self._sink.clear()
        self._sink.update(kwargs)
        return _FakeStreamContext(self._response)


class _FakeClient:
    def __init__(self, response: _FakeMessage, sink: dict[str, Any]) -> None:
        self.messages = _FakeMessages(response, sink)


# A full, realistic per-ticker card — note the "$180" in prose (public per-share
# price talk) that must survive to the customer, and the absence of ANY portfolio
# or dollar-position field.
_CARD = {
    "symbol": "AAPL",
    "company_type": "Mega-cap consumer tech",
    "scores": {"valuation": 45, "growth": 62, "health": 90, "momentum": 55, "dividend": 30},
    "snowflake_summary": "Healthy balance sheet, fair value, moderate growth.",
    "fair_value": {"estimate": 180.0, "current": 195.0, "status": "OVERVALUED", "pct_diff": -7.7},
    "risks": [{"text": "Trades ~8% above a $180 intrinsic estimate.", "severity": "MEDIUM"}],
    "rewards": [{"text": "Net-cash fortress with durable services growth."}],
    "prediction": {"3m": "$185-205", "12m": "$170-230", "rationale": "Range-bound near fair value."},
    "verdict": "HOLD",
    "analyst_note": "Wall St target ~$210; our fair value is $180 — wait for a pullback.",
    "bear_case": {
        "thesis": "Services growth decelerates below 8% YoY while the multiple "
                   "still prices in mid-teens growth, closing the gap to fair value "
                   "from the other side.",
        "invalidators": [
            "Services revenue growth prints below 8% YoY next quarter",
            "Gross margin falls below 44% for two consecutive quarters",
        ],
    },
}


def _install_fake_client(monkeypatch, response: _FakeMessage) -> dict[str, Any]:
    sink: dict[str, Any] = {}
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")
    monkeypatch.setattr(ai_analyzer.anthropic, "Anthropic",
                        lambda *a, **k: _FakeClient(response, sink))
    return sink


# ─── Unit: deep_analyze_ticker contract (money-free, per-ticker) ─────────────

def test_deep_analyze_ticker_returns_card(monkeypatch):
    _install_fake_client(monkeypatch, _FakeMessage([_ToolBlock(_CARD)]))
    out = ai_analyzer.deep_analyze_ticker("AAPL")
    # rich per-ticker fields present
    for k in ("scores", "fair_value", "prediction", "verdict", "risks", "rewards",
              "snowflake_summary", "analyst_note"):
        assert k in out, f"missing per-ticker field {k!r}"
    assert set(out["scores"]) == {"valuation", "growth", "health", "momentum", "dividend"}


def test_schema_is_money_free_no_portfolio_keys(monkeypatch):
    """The CONTRACT is structural: the tool schema exposes ONLY per-ticker fields
    — no portfolio_score / holdings_analysis / kelly / sector dollars can appear."""
    sink = _install_fake_client(monkeypatch, _FakeMessage([_ToolBlock(_CARD)]))
    ai_analyzer.deep_analyze_ticker("AAPL")
    schema = sink["tools"][0]["input_schema"]
    props = schema["properties"]
    assert {"scores", "fair_value", "prediction", "verdict", "risks", "rewards"} <= set(props)
    for banned in ("portfolio_score", "holdings_analysis", "sector_breakdown",
                   "portfolio_recommendations", "portfolio_risks", "kelly", "delta_usd"):
        assert banned not in props, f"portfolio/dollar key {banned!r} leaked into the ticker schema"
    assert sink["tool_choice"]["name"] == "return_ticker_analysis"


def test_prompt_carries_symbol_not_owner_book(monkeypatch):
    """Money-free by construction: the request names the ticker and dumps NO owner
    holdings / equity_summary payload."""
    sink = _install_fake_client(monkeypatch, _FakeMessage([_ToolBlock(_CARD)]))
    ai_analyzer.deep_analyze_ticker("AAPL")
    user_msg = sink["messages"][0]["content"]
    assert "AAPL" in user_msg
    for owner_marker in ("equity_summary", "portfolio_equity", "buying_power", "\"holdings\""):
        assert owner_marker not in user_msg


def test_ticker_schema_matches_internal_holding_card(monkeypatch):
    """The customer card MUST stay identical to the internal per-holding card so
    the shared daRender markup renders it — guard against drift."""
    item = ai_analyzer.DEEP_ANALYSIS_SCHEMA["properties"]["holdings_analysis"]["items"]
    assert ai_analyzer.TICKER_ANALYSIS_SCHEMA is item or \
        ai_analyzer.TICKER_ANALYSIS_SCHEMA == item


def test_ticker_schema_includes_bear_case(monkeypatch):
    """The tool schema must ask the model for a bear_case (thesis + observable
    invalidators) — this rides the SAME single LLM call, no new spend."""
    sink = _install_fake_client(monkeypatch, _FakeMessage([_ToolBlock(_CARD)]))
    ai_analyzer.deep_analyze_ticker("AAPL")
    schema = sink["tools"][0]["input_schema"]
    props = schema["properties"]
    assert "bear_case" in props
    bc_props = props["bear_case"]["properties"]
    assert bc_props["thesis"]["type"] == "string"
    assert bc_props["invalidators"]["type"] == "array"


def test_deep_analyze_ticker_defaults_bear_case_when_model_omits_it(monkeypatch):
    """Defensive default: a model response that omits bear_case must still reach
    the customer with an empty-but-present scaffold, never a KeyError."""
    card_without_bear_case = {k: v for k, v in _CARD.items() if k != "bear_case"}
    _install_fake_client(monkeypatch, _FakeMessage([_ToolBlock(card_without_bear_case)]))
    out = ai_analyzer.deep_analyze_ticker("AAPL")
    assert out["bear_case"] == {"thesis": "", "invalidators": []}


def test_portfolio_holdings_default_bear_case_when_model_omits_it(monkeypatch):
    """bear_case lives in the SHARED per-holding schema, so deep_analyze_portfolio
    (incl. the customer book analysis) is now also prompted for it — every holding
    card must carry the same defensive scaffold when the model omits the field."""
    holding_no_bc = {k: v for k, v in _CARD.items() if k != "bear_case"}
    parsed = {
        "portfolio_score": 60,
        "portfolio_verdict": "HOLD",
        "holdings_analysis": [holding_no_bc,
                              {**holding_no_bc, "symbol": "NVDA"}],
    }
    _install_fake_client(monkeypatch, _FakeMessage([_ToolBlock(parsed)]))
    out = ai_analyzer.deep_analyze_portfolio({"holdings": [{"symbol": "AAPL"},
                                                           {"symbol": "NVDA"}]})
    for h in out["holdings_analysis"]:
        assert h["bear_case"] == {"thesis": "", "invalidators": []}


def test_concise_portfolio_prompt_caps_bear_case(monkeypatch):
    """The customer book analysis runs concise + token-budget-constrained (12000
    max_tokens with a truncation retry ladder) — the brevity instruction must cap
    bear_case output so the new field can't blow that budget."""
    parsed = {"portfolio_score": 60, "portfolio_verdict": "HOLD",
              "holdings_analysis": []}
    sink = _install_fake_client(monkeypatch, _FakeMessage([_ToolBlock(parsed)]))
    ai_analyzer.deep_analyze_portfolio({"holdings": [{"symbol": "AAPL"}]},
                                       concise=True)
    user_msg = sink["messages"][0]["content"]
    assert "bear_case" in user_msg           # concise mode explicitly caps the field
    assert "BE CONCISE" in user_msg          # ...inside the existing brevity block


# ─── Route: POST /api/analyze-ticker ─────────────────────────────────────────

def _customer_client(monkeypatch, card=None, boom=None, price=195.0):
    """A TestClient with the auth wall ON and the session bound to a CUSTOMER
    (non-owner) email, with the live-price fetch and deep_analyze_ticker stubbed
    (no network, no LLM)."""
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: True)
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: "cust@x.com")
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda p, c: "allow", raising=False)
    # Patch the seam the handler ACTUALLY calls: /api/analyze-ticker resolves its
    # grounding quote via _latest_close_meta (Codex #6 added the provenance tuple),
    # and _latest_close is now only a thin wrapper over it. Stubbing the wrapper left
    # the endpoint fetching a LIVE price — invisible to conftest's `_no_network`,
    # because yfinance fetches through curl_cffi and never opens a Python socket.
    monkeypatch.setattr(server, "_latest_close_meta",
                        lambda sym: (price, {"source": "yahoo", "substituted": False}),
                        raising=True)

    def _fake(sym, current_price=None, regime_context=None):
        if boom:
            raise boom
        return {**(card or _CARD), "symbol": sym}
    monkeypatch.setattr(server, "deep_analyze_ticker", _fake, raising=True)
    c = TestClient(server.app, raise_server_exceptions=False)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    return c


def test_route_customer_allowed_returns_200(monkeypatch):
    """Contrast with /api/deep-analysis, which 403s customers — this one is open."""
    c = _customer_client(monkeypatch)
    r = c.post("/api/analyze-ticker", json={"symbol": "aapl"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["data"]["symbol"] == "AAPL"          # _safe_symbol uppercased


def test_analyze_ticker_reports_cached_and_generated_at(monkeypatch):
    """GESH-4: the AI card is stamped generated_at + a cached flag, so the UI can tag
    it 'AI-GENERATED · <time>' instead of hiding it under a global 'LIVE' banner."""
    from datetime import datetime
    c = _customer_client(monkeypatch)
    r1 = c.post("/api/analyze-ticker", json={"symbol": "AAPL"})
    b1 = r1.json()
    assert b1["cached"] is False
    datetime.fromisoformat(b1["data"]["generated_at"])      # parses as ISO 8601
    r2 = c.post("/api/analyze-ticker", json={"symbol": "AAPL"})   # cache hit, no spend
    b2 = r2.json()
    assert b2["cached"] is True
    assert b2["data"]["generated_at"] == b1["data"]["generated_at"]  # same cached card


def test_card_always_carries_provenance_block(monkeypatch):
    """Every card gets a provenance block — timestamp, model, price feed, and a
    suspect flag — so the UI can render trust signals on it (feature 2)."""
    c = _customer_client(monkeypatch)
    r = c.post("/api/analyze-ticker", json={"symbol": "AAPL"})
    card = r.json()["data"]
    p = card["provenance"]
    assert p["generated_at"] and p["model"]
    assert p["suspect"] in (True, False)
    assert "price_source" in p and "price_as_of" in p     # keys present even when None


def test_card_carries_bear_case_and_invalidators(monkeypatch):
    """Every card carries a bear_case section (feature 4) — the strongest
    argument against the verdict plus concrete, observable invalidators."""
    c = _customer_client(monkeypatch)
    r = c.post("/api/analyze-ticker", json={"symbol": "AAPL"})
    card = r.json()["data"]
    assert card["bear_case"]["thesis"]
    assert isinstance(card["bear_case"]["invalidators"], list)
    assert len(card["bear_case"]["invalidators"]) >= 2


def test_route_prose_not_masked_for_customer(monkeypatch):
    """The masking middleware must NOT redact this public per-share prose: a
    customer sees the real "$180", not "$•••"."""
    c = _customer_client(monkeypatch)
    body = c.post("/api/analyze-ticker", json={"symbol": "AAPL"}).json()
    assert "$180" in body["data"]["analyst_note"]
    assert "$•••" not in body["data"]["analyst_note"]
    assert body.get("view") != "model_book"          # exempt from model-book scaling


def test_route_bad_symbol_is_clean_4xx(monkeypatch):
    c = _customer_client(monkeypatch)
    r = c.post("/api/analyze-ticker", json={"symbol": "@@@"})
    assert r.status_code == 400                       # never 500


def test_route_llm_error_is_500_not_leak(monkeypatch):
    c = _customer_client(monkeypatch, boom=RuntimeError("anthropic exploded: sk-secret"))
    r = c.post("/api/analyze-ticker", json={"symbol": "AAPL"})
    assert r.status_code == 500
    assert "sk-secret" not in r.text                  # _server_error hides internals


def test_route_rejects_anonymous_when_wall_on(monkeypatch):
    """Never an open LLM-spend surface: wall ON + no session → 401, and the model
    is never invoked (no spend)."""
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: True)
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: None)
    spent = {"llm": False}
    monkeypatch.setattr(server, "deep_analyze_ticker",
                        lambda *a, **k: spent.__setitem__("llm", True) or _CARD, raising=True)
    c = TestClient(server.app, raise_server_exceptions=False)
    r = c.post("/api/analyze-ticker", json={"symbol": "AAPL"})
    assert r.status_code == 401
    assert spent["llm"] is False


def test_route_unknown_ticker_is_404_no_spend(monkeypatch):
    """No live price = unknown ticker/outage → 404 BEFORE any LLM call, so the
    model can't fabricate a card for a nonexistent symbol."""
    spent = {"llm": False}
    c = _customer_client(monkeypatch, price=None)
    monkeypatch.setattr(server, "deep_analyze_ticker",
                        lambda *a, **k: spent.__setitem__("llm", True) or _CARD, raising=True)
    r = c.post("/api/analyze-ticker", json={"symbol": "ZZZZ9"})
    assert r.status_code == 404
    assert spent["llm"] is False


def test_route_caches_repeat_symbol(monkeypatch):
    """A hot ticker is served from cache on the second call — one LLM spend, not two."""
    calls = {"n": 0}
    c = _customer_client(monkeypatch)

    def _counting(sym, current_price=None, regime_context=None):
        calls["n"] += 1
        return {**_CARD, "symbol": sym}
    monkeypatch.setattr(server, "deep_analyze_ticker", _counting, raising=True)
    c.post("/api/analyze-ticker", json={"symbol": "AAPL"})
    c.post("/api/analyze-ticker", json={"symbol": "AAPL"})
    assert calls["n"] == 1


def test_prompt_grounds_current_price(monkeypatch):
    """A caller-supplied price is injected so fair_value.current is never invented."""
    sink = _install_fake_client(monkeypatch, _FakeMessage([_ToolBlock(_CARD)]))
    ai_analyzer.deep_analyze_ticker("AAPL", 195.0)
    user_msg = sink["messages"][0]["content"]
    assert "195" in user_msg
    assert "fair_value.current" in user_msg


# ── inspection fixes 2026-07-08: cache eviction + single-flight dedup ─────────

def test_ticker_card_store_evicts_expired():
    """The card cache is process-wide and long-lived — expired entries must be
    purged on store, not accumulate for the life of the process."""
    import time as _time
    server._ticker_card_cache["OLD"] = (_time.monotonic() - server._TICKER_CARD_TTL - 1,
                                        {"symbol": "OLD"})
    server._ticker_card_store("NEW", {"symbol": "NEW"})
    assert "OLD" not in server._ticker_card_cache
    assert "NEW" in server._ticker_card_cache


def test_concurrent_same_symbol_spends_one_llm_call(monkeypatch):
    """Single-flight: the ~15s LLM call makes concurrent first requests the norm
    for a hot ticker — followers must wait for the leader and hit the cache, not
    spend a second Claude call."""
    import asyncio

    calls = {"n": 0}

    def _slow(sym, current_price=None, regime_context=None):
        calls["n"] += 1
        import time as _time
        _time.sleep(0.05)                     # long enough for the follower to queue
        return {**_CARD, "symbol": sym}
    monkeypatch.setattr(server, "deep_analyze_ticker", _slow, raising=True)
    monkeypatch.setattr(server, "_latest_close_meta",           # the seam the handler calls
                        lambda s: (195.0, {"source": "yahoo", "substituted": False}),
                        raising=True)

    # Call the endpoint function directly (its Depends auth gate is route-level and
    # covered by the route tests above) so both coroutines share one event loop.
    class _Req:                               # endpoint reads .json() + (usage-log) .cookies
        cookies: dict = {}                    # empty → _session_email resolves without error
        async def json(self):
            return {"symbol": "AAPL"}

    async def _go():
        return await asyncio.gather(server.analyze_ticker_endpoint(_Req()),
                                    server.analyze_ticker_endpoint(_Req()))

    r1, r2 = asyncio.run(_go())
    assert r1["data"]["symbol"] == "AAPL" and r2["data"]["symbol"] == "AAPL"
    assert calls["n"] == 1
