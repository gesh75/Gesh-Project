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

from starlette.testclient import TestClient

import ai_analyzer
import server

# ─── Fakes (mirror tests/test_ai_analyzer_caching.py) ────────────────────────

class _ToolBlock:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.type = "tool_use"
        self.input = payload


class _FakeMessage:
    def __init__(self, content: list[Any]) -> None:
        self.content = content


class _FakeMessages:
    def __init__(self, response: _FakeMessage, sink: dict[str, Any]) -> None:
        self._response, self._sink = response, sink

    def create(self, **kwargs: Any) -> _FakeMessage:
        self._sink.clear()
        self._sink.update(kwargs)
        return self._response


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


# ─── Route: POST /api/analyze-ticker ─────────────────────────────────────────

def _customer_client(monkeypatch, card=None, boom=None):
    """A TestClient with the auth wall ON and the session bound to a CUSTOMER
    (non-owner) email, with deep_analyze_ticker stubbed (no LLM)."""
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: True)
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: "cust@x.com")
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda p, c: "allow", raising=False)

    def _fake(sym, regime_context=None):
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
