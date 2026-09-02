"""Guard tests for the read-only Robinhood MCP integration (Stages 1 + 2).

The load-bearing tests here are the allowlist ones. Everything else in this
integration is convenience; `test_allowlist_*` is the reason the design is safe
to run unattended from launchd. If someone ever adds an order-placing tool to
`rh_mcp_data._ALLOWED_TOOLS`, these must fail loudly.

No network: every test stubs the MCP session.
"""

from __future__ import annotations

import pandas as pd
import pytest

import rh_mcp_data
import rh_order_review


# ── Fakes ────────────────────────────────────────────────────────────────────
class FakeMcp:
    """Stand-in for fetch_snapshot_direct.Mcp. Records every tool name sent."""

    def __init__(self, tool_payload=None, call_result=None):
        self.calls: list[tuple[str, dict]] = []
        self._tool_payload = tool_payload or {}
        self._call_result = call_result or {}

    def tool(self, name, arguments):
        self.calls.append((name, arguments))
        if isinstance(self._tool_payload, Exception):
            raise self._tool_payload
        return self._tool_payload

    def call(self, method, params=None):
        self.calls.append((method, params or {}))
        return self._call_result


@pytest.fixture(autouse=True)
def _isolate():
    rh_mcp_data.clear_caches()
    rh_order_review.reset()
    yield
    rh_mcp_data.clear_caches()
    rh_order_review.reset()


def _bars(symbol="NVDA"):
    return {
        "data": {
            "results": [
                {
                    "symbol": symbol,
                    "bars": [
                        {
                            "begins_at": "2026-08-18T00:00:00Z",
                            "open_price": "220.45",
                            "high_price": "221.64",
                            "low_price": "218.69",
                            "close_price": "219.74",
                            "volume": 103128247,
                        },
                        {
                            "begins_at": "2026-08-19T00:00:00Z",
                            "open_price": "221.67",
                            "high_price": "222.87",
                            "low_price": "216.76",
                            "close_price": "217.56",
                            "volume": 96797314,
                        },
                    ],
                }
            ]
        }
    }


# ── The safety invariant ─────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "tool",
    [
        "place_equity_order",
        "place_option_order",
        "cancel_equity_order",
        "cancel_option_order",
        "exercise_option",
    ],
)
def test_allowlist_blocks_every_mutating_tool(tool, monkeypatch):
    """rh_mcp_data must refuse to send any order-mutating tool, loudly."""
    fake = FakeMcp()
    monkeypatch.setattr(rh_mcp_data, "_get_session", lambda: fake)
    with pytest.raises(ValueError, match="read-only allowlist"):
        rh_mcp_data._call(tool, {})
    assert fake.calls == [], "a blocked tool must never reach the transport"


def test_allowlist_contains_no_mutating_verbs():
    """Belt and braces: no allowlisted name may start with a mutating verb."""
    banned = ("place_", "cancel_", "exercise_", "create_", "update_", "add_", "remove_")
    offenders = [t for t in rh_mcp_data._ALLOWED_TOOLS if t.startswith(banned)]
    assert offenders == [], f"mutating tools on the read-only allowlist: {offenders}"


def test_order_review_only_ever_sends_review_tool(monkeypatch):
    fake = FakeMcp(
        call_result={
            "isError": True,
            "_meta": {"rh_error_category": "unauthorized"},
            "content": [{"type": "text", "text": "error: FORBIDDEN"}],
        }
    )
    monkeypatch.setattr(rh_order_review, "_get_session", lambda: fake)
    monkeypatch.setattr(rh_order_review, "_direct", lambda: type("F", (), {"ACCT": "123"}))
    rh_order_review.review("NVDA", "sell", quantity=1)
    sent = [p.get("name") for m, p in fake.calls if m == "tools/call"]
    assert sent == ["review_equity_order"]


# ── Stage 1: shape + fail-soft ───────────────────────────────────────────────
def test_historicals_matches_yfinance_shape(monkeypatch):
    monkeypatch.setattr(rh_mcp_data, "_get_session", lambda: FakeMcp(_bars()))
    df = rh_mcp_data.historicals("NVDA", days=5)
    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert isinstance(df.index, pd.DatetimeIndex)
    assert df["Close"].iloc[-1] == pytest.approx(217.56)
    assert df.attrs["gesh_price_source"] == "robinhood_mcp"


def test_historicals_multi_symbol_is_multiindex(monkeypatch):
    payload = {
        "data": {
            "results": [
                _bars("NVDA")["data"]["results"][0],
                {**_bars("BAC")["data"]["results"][0], "symbol": "BAC"},
            ]
        }
    }
    monkeypatch.setattr(rh_mcp_data, "_get_session", lambda: FakeMcp(payload))
    df = rh_mcp_data.historicals(["NVDA", "BAC"], days=5)
    assert isinstance(df.columns, pd.MultiIndex)
    assert set(df["Close"].columns) == {"NVDA", "BAC"}  # yfinance group_by="column"


def test_historicals_returns_empty_when_mcp_down(monkeypatch):
    monkeypatch.setattr(rh_mcp_data, "_get_session", lambda: None)
    df = rh_mcp_data.historicals("NVDA")
    assert df.empty and isinstance(df, pd.DataFrame)


def test_call_never_raises_on_transport_error(monkeypatch):
    monkeypatch.setattr(rh_mcp_data, "_get_session", lambda: FakeMcp(RuntimeError("VPN reset")))
    assert rh_mcp_data._call("get_equity_quotes", {"symbols": ["NVDA"]}) is None


def test_bar_without_close_is_dropped_not_zeroed(monkeypatch):
    """A missing close must remove the bar — never become a 0.0 price (D-07)."""
    payload = {
        "data": {
            "results": [
                {
                    "symbol": "NVDA",
                    "bars": [
                        {
                            "begins_at": "2026-08-18T00:00:00Z",
                            "open_price": "220",
                            "high_price": "221",
                            "low_price": "219",
                            "close_price": "219.74",
                            "volume": 1,
                        },
                        {
                            "begins_at": "2026-08-19T00:00:00Z",
                            "open_price": "221",
                            "high_price": "222",
                            "low_price": "216",
                            "close_price": None,
                            "volume": 2,
                        },
                    ],
                }
            ]
        }
    }
    monkeypatch.setattr(rh_mcp_data, "_get_session", lambda: FakeMcp(payload))
    df = rh_mcp_data.historicals("NVDA")
    assert len(df) == 1
    assert (df["Close"] > 0).all()


def test_indicator_parses_series(monkeypatch):
    payload = {
        "data": {
            "indicators": [
                {
                    "type": "atr",
                    "series": [
                        {"begins_at": "2026-08-18T00:00:00Z", "value": 6.51},
                        {"begins_at": "2026-08-19T00:00:00Z", "value": 6.42},
                    ],
                }
            ]
        }
    }
    monkeypatch.setattr(rh_mcp_data, "_get_session", lambda: FakeMcp(payload))
    assert rh_mcp_data.atr("NVDA") == pytest.approx(6.42)


def test_indicator_none_when_unavailable(monkeypatch):
    monkeypatch.setattr(rh_mcp_data, "_get_session", lambda: None)
    assert rh_mcp_data.atr("NVDA") is None


def test_tax_lots_normalized_and_zero_qty_dropped(monkeypatch):
    payload = {
        "data": {
            "tax_lots": [
                {
                    "open_lot_id": "a",
                    "quantity_available": "4.0",
                    "cost_per_share": "214.07",
                    "tax_cost_basis": "856.28",
                    "open_date": "2026-05-26",
                    "term": "st",
                    "is_selectable": True,
                },
                {
                    "open_lot_id": "b",
                    "quantity_available": "0",
                    "cost_per_share": "1.0",
                    "open_date": "2026-01-01",
                    "term": "lt",
                    "is_selectable": True,
                },
            ]
        }
    }
    monkeypatch.setattr(rh_mcp_data, "_get_session", lambda: FakeMcp(payload))
    monkeypatch.setattr(rh_mcp_data, "account_number", lambda: "123")
    lots = rh_mcp_data.tax_lots("NVDA")
    assert [x["lot_id"] for x in lots] == ["a"]
    assert lots[0]["cost_per_share"] == pytest.approx(214.07)


def test_unrealized_by_lot_computes_pnl_and_holding_period(monkeypatch):
    payload = {
        "data": {
            "tax_lots": [
                {
                    "open_lot_id": "a",
                    "quantity_available": "4.0",
                    "cost_per_share": "200.0",
                    "tax_cost_basis": "800.0",
                    "open_date": "2026-05-26",
                    "term": "st",
                    "is_selectable": True,
                },
            ]
        }
    }
    monkeypatch.setattr(rh_mcp_data, "_get_session", lambda: FakeMcp(payload))
    monkeypatch.setattr(rh_mcp_data, "account_number", lambda: "123")
    rows = rh_mcp_data.unrealized_by_lot("NVDA", 214.61)
    assert rows[0]["unrealized_usd"] == pytest.approx(58.44, abs=0.01)
    assert rows[0]["days_held"] > 0


def test_empty_tax_lots_means_unknown_not_none_held(monkeypatch):
    """[] is 'we could not ask' — the docstring contract downstream relies on."""
    monkeypatch.setattr(rh_mcp_data, "_get_session", lambda: None)
    monkeypatch.setattr(rh_mcp_data, "account_number", lambda: "123")
    assert rh_mcp_data.tax_lots("NVDA") == []


# ── Stage 2: status taxonomy ─────────────────────────────────────────────────
def _review_with(monkeypatch, result):
    monkeypatch.setattr(rh_order_review, "_get_session", lambda: FakeMcp(call_result=result))
    monkeypatch.setattr(rh_order_review, "_direct", lambda: type("F", (), {"ACCT": "123"}))


def test_unauthorized_is_not_executable(monkeypatch):
    _review_with(
        monkeypatch,
        {
            "isError": True,
            "_meta": {"rh_error_category": "unauthorized"},
            "content": [{"type": "text", "text": "error: FORBIDDEN: agent not authorized"}],
        },
    )
    r = rh_order_review.review("NVDA", "sell", quantity=1)
    assert r.status == rh_order_review.UNAUTHORIZED
    assert r.executable is False and r.checked is False


def test_unavailable_is_not_executable(monkeypatch):
    monkeypatch.setattr(rh_order_review, "_get_session", lambda: None)
    monkeypatch.setattr(rh_order_review, "_direct", lambda: type("F", (), {"ACCT": "123"}))
    r = rh_order_review.review("NVDA", "sell", quantity=1)
    assert r.status == rh_order_review.UNAVAILABLE
    assert r.executable is False and r.checked is False


def test_ok_is_executable_and_parses_alerts(monkeypatch):
    _review_with(
        monkeypatch,
        {
            "structuredContent": {
                "data": {
                    "alerts": [{"message": "Collared limit price applied"}, "Extended hours"],
                    "quote": {"last_trade_price": "214.61"},
                }
            }
        },
    )
    r = rh_order_review.review("NVDA", "sell", quantity=1)
    assert r.status == rh_order_review.OK
    assert r.executable is True and r.checked is True
    assert r.alerts == ("Collared limit price applied", "Extended hours")


def test_rejected_is_checked_but_not_executable(monkeypatch):
    _review_with(
        monkeypatch,
        {
            "isError": True,
            "_meta": {"rh_error_category": "validation"},
            "content": [{"type": "text", "text": "insufficient shares"}],
        },
    )
    r = rh_order_review.review("NVDA", "sell", quantity=999)
    assert r.status == rh_order_review.REJECTED
    assert r.checked is True and r.executable is False


def test_unauthorized_short_circuits_the_rest_of_the_batch(monkeypatch):
    fake = FakeMcp(
        call_result={
            "isError": True,
            "_meta": {"rh_error_category": "unauthorized"},
            "content": [{"type": "text", "text": "FORBIDDEN"}],
        }
    )
    monkeypatch.setattr(rh_order_review, "_get_session", lambda: fake)
    monkeypatch.setattr(rh_order_review, "_direct", lambda: type("F", (), {"ACCT": "123"}))
    for sym in ("NVDA", "BAC", "MPC"):
        assert rh_order_review.review(sym, "sell", quantity=1).status == "UNAUTHORIZED"
    assert len([c for c in fake.calls if c[0] == "tools/call"]) == 1, "should not re-ask per symbol"


def test_review_action_maps_trim_to_sell(monkeypatch):
    seen = {}

    def fake_review(symbol, side, **kw):
        seen.update(symbol=symbol, side=side, quantity=kw.get("quantity"))
        return rh_order_review.ReviewResult(symbol, side, "market", rh_order_review.OK)

    monkeypatch.setattr(rh_order_review, "review", fake_review)
    rh_order_review.review_action({"symbol": "NVDA", "action": "TRIM", "shares": 4})
    assert seen == {"symbol": "NVDA", "side": "sell", "quantity": 4}


def test_review_action_rejects_hold(monkeypatch):
    r = rh_order_review.review_action({"symbol": "ZS", "action": "HOLD", "shares": 0})
    assert r.status == rh_order_review.ERROR


@pytest.mark.parametrize("action,side", [("BUY", "buy"), ("ADD", "buy"), ("SELL", "sell")])
def test_review_action_maps_buy_add_sell(monkeypatch, action, side):
    seen = {}

    def fake_review(symbol, mapped_side, **kw):
        seen.update(symbol=symbol, side=mapped_side, quantity=kw.get("quantity"))
        return rh_order_review.ReviewResult(symbol, mapped_side, "market", rh_order_review.OK)

    monkeypatch.setattr(rh_order_review, "review", fake_review)
    rh_order_review.review_action({"symbol": "HOOD", "action": action, "shares": 3})
    assert seen == {"symbol": "HOOD", "side": side, "quantity": 3}


def test_blocking_defaults_off(monkeypatch):
    monkeypatch.delenv("GESH_REVIEW_BLOCKING", raising=False)
    assert rh_order_review.blocking() is False
    monkeypatch.setenv("GESH_REVIEW_BLOCKING", "1")
    assert rh_order_review.blocking() is True


def test_review_enabled_defaults_on(monkeypatch):
    """Default ON is load-bearing: a missing env var must not silently disable preflight."""
    monkeypatch.delenv("GESH_BROKER_REVIEW", raising=False)
    assert rh_order_review.enabled() is True
    for off in ("0", "false", "no"):
        monkeypatch.setenv("GESH_BROKER_REVIEW", off)
        assert rh_order_review.enabled() is False, off


# ── Stage 2 wiring into the gate chain ───────────────────────────────────────
def _chain_harness():
    import run_paper_gate2 as G

    class Verdict:
        approved, reason, gates = True, None, {"KILL_SWITCH": True}

    class Intent:
        intent_uuid, confidence, target_position_pct = "uuid-1", 0.9, 0.04

    return G, (lambda *a, **k: Intent()), (lambda i, p: Verdict())


def test_reviewer_annotates_without_blocking_by_default():
    G, intent_builder, gate_evaluate = _chain_harness()
    recorded = []
    gr, created = G.gate_and_execute(
        [{"symbol": "NVDA", "action": "TRIM", "shares": 4, "current_price": 214.9}],
        [],
        36000.0,
        {},
        {"NVDA": 0.9},
        intent_builder=intent_builder,
        gate_evaluate=gate_evaluate,
        recorder=lambda rows, h, t, **k: recorded.extend(rows) or rows,
        reviewer=lambda a: rh_order_review.ReviewResult(
            a["symbol"], "sell", "market", rh_order_review.REJECTED, message="halted"
        ),
        review_blocks=False,
    )
    assert gr[0]["approved"] is True
    assert gr[0]["broker_review"]["status"] == "REJECTED"
    assert len(recorded) == 1, "advisory mode must still record the paper trade"


def test_reviewer_vetoes_when_blocking_enabled():
    G, intent_builder, gate_evaluate = _chain_harness()
    recorded = []
    gr, created = G.gate_and_execute(
        [{"symbol": "NVDA", "action": "TRIM", "shares": 4, "current_price": 214.9}],
        [],
        36000.0,
        {},
        {"NVDA": 0.9},
        intent_builder=intent_builder,
        gate_evaluate=gate_evaluate,
        recorder=lambda rows, h, t, **k: recorded.extend(rows) or rows,
        reviewer=lambda a: rh_order_review.ReviewResult(
            a["symbol"], "sell", "market", rh_order_review.REJECTED, message="halted"
        ),
        review_blocks=True,
    )
    assert gr[0]["approved"] is False
    assert "BROKER_REVIEW" in gr[0]["reason"]
    assert recorded == [], "a vetoed intent must leave no paper record"


@pytest.mark.parametrize("status", ["UNAUTHORIZED", "UNAVAILABLE", "ERROR"])
def test_inconclusive_review_never_vetoes_even_when_blocking(status):
    """'Not checked' must not silently become 'refused' — that would blank the book."""
    G, intent_builder, gate_evaluate = _chain_harness()
    recorded = []
    gr, _ = G.gate_and_execute(
        [{"symbol": "NVDA", "action": "TRIM", "shares": 4, "current_price": 214.9}],
        [],
        36000.0,
        {},
        {"NVDA": 0.9},
        intent_builder=intent_builder,
        gate_evaluate=gate_evaluate,
        recorder=lambda rows, h, t, **k: recorded.extend(rows) or rows,
        reviewer=lambda a: rh_order_review.ReviewResult(a["symbol"], "sell", "market", status),
        review_blocks=True,
    )
    assert gr[0]["approved"] is True
    assert len(recorded) == 1


def test_reviewer_exception_is_non_fatal():
    G, intent_builder, gate_evaluate = _chain_harness()
    recorded = []
    gr, _ = G.gate_and_execute(
        [{"symbol": "NVDA", "action": "TRIM", "shares": 4, "current_price": 214.9}],
        [],
        36000.0,
        {},
        {"NVDA": 0.9},
        intent_builder=intent_builder,
        gate_evaluate=gate_evaluate,
        recorder=lambda rows, h, t, **k: recorded.extend(rows) or rows,
        reviewer=lambda a: (_ for _ in ()).throw(RuntimeError("boom")),
        review_blocks=True,
    )
    assert gr[0]["approved"] is True
    assert "boom" in gr[0]["broker_review_error"]
    assert len(recorded) == 1
