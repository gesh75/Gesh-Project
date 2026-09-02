"""Edge-case guards for the PR #239 MCP / broker-preflight surface.

The allowlist + status-taxonomy tests live in test_rh_mcp_integration.py.
This file covers the remaining fail-soft / parsing / session edges that
would otherwise regress into a false-clear, a fabricated 0.0 price, or a
SystemExit taking down every caller of the daily chain.

No network: every test stubs the MCP session.
"""

from __future__ import annotations

import json
import sys
import time

import pytest
from test_rh_mcp_integration import FakeMcp, _bars, _chain_harness, _review_with

import rh_mcp_data
import rh_order_review


@pytest.fixture(autouse=True)
def _isolate():
    rh_mcp_data.clear_caches()
    rh_order_review.reset()
    yield
    rh_mcp_data.clear_caches()
    rh_order_review.reset()


# ── SystemExit-safe import (CLAUDE.md: GESH_RH_ACCOUNT) ──────────────────────
class _SystemExitFinder:
    """Import finder that raises SystemExit for fetch_snapshot_direct.

    That module calls SystemExit at import time when GESH_RH_ACCOUNT is unset.
    SystemExit is a BaseException, so `except Exception` would let it take
    down every rh_mcp_data / rh_order_review caller.
    """

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "fetch_snapshot_direct":
            raise SystemExit("GESH_RH_ACCOUNT unset")
        return None


def _force_systemexit_on_direct_import(monkeypatch):
    monkeypatch.delitem(sys.modules, "fetch_snapshot_direct", raising=False)
    monkeypatch.setattr(sys, "meta_path", [_SystemExitFinder(), *sys.meta_path])


def test_rh_mcp_direct_swallows_systemexit(monkeypatch):
    _force_systemexit_on_direct_import(monkeypatch)
    assert rh_mcp_data._direct() is None
    assert rh_mcp_data.account_number() is None


def test_order_review_direct_swallows_systemexit(monkeypatch):
    _force_systemexit_on_direct_import(monkeypatch)
    assert rh_order_review._direct() is None


# ── Session / transport fail-soft ────────────────────────────────────────────
def test_session_retry_cooldown_does_not_reimport(monkeypatch):
    rh_mcp_data._session_failed_at = time.time()

    def boom():
        raise AssertionError("cooldown must not re-enter _direct")

    monkeypatch.setattr(rh_mcp_data, "_direct", boom)
    assert rh_mcp_data._get_session() is None
    assert rh_mcp_data.available() is False


def test_call_clears_session_on_transport_error(monkeypatch):
    fake = FakeMcp(RuntimeError("VPN reset"))
    rh_mcp_data._session = fake
    assert rh_mcp_data._call("get_equity_quotes", {"symbols": ["NVDA"]}) is None
    assert rh_mcp_data._session is None


def test_f_empty_and_garbage_stay_none_not_zero():
    """A missing RH number must stay None — never become a 0.0 price (D-07)."""
    assert rh_mcp_data._f(None) is None
    assert rh_mcp_data._f("") is None
    assert rh_mcp_data._f("n/a") is None
    assert rh_mcp_data._f("214.61") == pytest.approx(214.61)


# ── quotes / historicals cache / tax lots without an account ─────────────────
def test_quotes_keyed_by_symbol(monkeypatch):
    payload = {
        "data": {
            "results": [
                {"symbol": "NVDA", "last_trade_price": "214.61"},
                {"symbol": "BAC", "last_trade_price": "48.12"},
                {"last_trade_price": "1.00"},  # no symbol — dropped
            ]
        }
    }
    monkeypatch.setattr(rh_mcp_data, "_get_session", lambda: FakeMcp(payload))
    q = rh_mcp_data.quotes(["NVDA", "BAC"])
    assert set(q) == {"NVDA", "BAC"}
    assert q["NVDA"]["last_trade_price"] == "214.61"


def test_quotes_empty_on_failure_or_empty_input(monkeypatch):
    monkeypatch.setattr(rh_mcp_data, "_get_session", lambda: None)
    assert rh_mcp_data.quotes("NVDA") == {}
    assert rh_mcp_data.quotes([]) == {}


def test_historicals_cache_returns_isolated_copy(monkeypatch):
    fake = FakeMcp(_bars())
    monkeypatch.setattr(rh_mcp_data, "_get_session", lambda: fake)
    df1 = rh_mcp_data.historicals("NVDA", days=5)
    original = float(df1["Close"].iloc[0])
    df1.iloc[0, df1.columns.get_loc("Close")] = 999.0
    df2 = rh_mcp_data.historicals("NVDA", days=5)
    assert df2["Close"].iloc[0] == pytest.approx(original)
    assert df2.attrs["gesh_price_source"] == "robinhood_mcp"
    assert len(fake.calls) == 1, "second read must be a cache hit"


def test_historicals_empty_symbols_never_calls_mcp(monkeypatch):
    fake = FakeMcp(_bars())
    monkeypatch.setattr(rh_mcp_data, "_get_session", lambda: fake)
    df = rh_mcp_data.historicals([])
    assert df.empty
    assert fake.calls == []


def test_tax_lots_without_account_is_unknown_not_empty_held(monkeypatch):
    """[] means unknown. Never ask the MCP without an account number."""
    fake = FakeMcp(
        {
            "data": {
                "tax_lots": [
                    {
                        "open_lot_id": "a",
                        "quantity_available": "1",
                        "cost_per_share": "10",
                        "open_date": "2026-01-01",
                        "term": "st",
                        "is_selectable": True,
                    },
                ]
            }
        }
    )
    monkeypatch.setattr(rh_mcp_data, "_get_session", lambda: fake)
    monkeypatch.setattr(rh_mcp_data, "account_number", lambda: None)
    assert rh_mcp_data.tax_lots("NVDA") == []
    assert fake.calls == []


def test_unrealized_by_lot_zero_cost_and_bad_date(monkeypatch):
    payload = {
        "data": {
            "tax_lots": [
                {
                    "open_lot_id": "zero",
                    "quantity_available": "2",
                    "cost_per_share": "0",
                    "tax_cost_basis": "0",
                    "open_date": "2026-01-01",
                    "term": "st",
                    "is_selectable": True,
                },
                {
                    "open_lot_id": "bad",
                    "quantity_available": "2",
                    "cost_per_share": "100",
                    "tax_cost_basis": "200",
                    "open_date": "not-a-date",
                    "term": "st",
                    "is_selectable": True,
                },
            ]
        }
    }
    monkeypatch.setattr(rh_mcp_data, "_get_session", lambda: FakeMcp(payload))
    monkeypatch.setattr(rh_mcp_data, "account_number", lambda: "123")
    rows = {r["lot_id"]: r for r in rh_mcp_data.unrealized_by_lot("NVDA", 110.0)}
    assert rows["zero"]["unrealized_pct"] is None
    assert rows["bad"]["days_held"] is None
    assert rows["bad"]["unrealized_usd"] == pytest.approx(20.0)


# ── review() validation + JSON-text OK envelope ──────────────────────────────
def test_review_bad_side_and_missing_symbol_are_errors():
    bad_side = rh_order_review.review("NVDA", "short")
    assert bad_side.status == rh_order_review.ERROR
    assert bad_side.checked is False and bad_side.executable is False

    missing = rh_order_review.review("", "buy")
    assert missing.status == rh_order_review.ERROR
    assert missing.checked is False


def test_review_no_account_is_unavailable_not_ok(monkeypatch):
    monkeypatch.setattr(rh_order_review, "_direct", lambda: type("F", (), {"ACCT": None}))
    r = rh_order_review.review("NVDA", "sell", quantity=1)
    assert r.status == rh_order_review.UNAVAILABLE
    assert r.checked is False and r.executable is False


def test_ok_review_parses_json_text_envelope(monkeypatch):
    body = json.dumps(
        {
            "data": {
                "alerts": [{"text": "PDT warning"}],
                "quote": {"last_trade_price": "10.00"},
            }
        }
    )
    _review_with(monkeypatch, {"content": [{"type": "text", "text": body}]})
    r = rh_order_review.review("NVDA", "sell", quantity=1)
    assert r.status == rh_order_review.OK
    assert r.executable is True and r.checked is True
    assert r.alerts == ("PDT warning",)
    dumped = r.as_dict()
    assert dumped["status"] == "OK"
    assert "quote" not in dumped


def test_review_call_exception_is_error_not_rejected(monkeypatch):
    class Boom:
        def call(self, method, params=None):
            raise RuntimeError("socket reset")

    monkeypatch.setattr(rh_order_review, "_get_session", lambda: Boom())
    monkeypatch.setattr(rh_order_review, "_direct", lambda: type("F", (), {"ACCT": "123"}))
    r = rh_order_review.review("NVDA", "sell", quantity=1)
    assert r.status == rh_order_review.ERROR
    assert r.checked is False
    assert "socket reset" in (r.message or "")


# ── gate wiring: rejected 9-gate verdict must not call the reviewer ──────────
def test_reviewer_not_called_when_nine_gates_reject():
    G, intent_builder, _ = _chain_harness()

    class Reject:
        approved, reason, gates = False, "POSITION_CAP", {"POSITION_CAP": False}

    called: list[str] = []
    gr, created = G.gate_and_execute(
        [{"symbol": "NVDA", "action": "TRIM", "shares": 4, "current_price": 214.9}],
        [],
        36000.0,
        {},
        {"NVDA": 0.9},
        intent_builder=intent_builder,
        gate_evaluate=lambda i, p: Reject(),
        recorder=lambda rows, h, t, **k: called.append("recorded") or rows,
        reviewer=lambda a: (
            called.append("reviewed")
            or rh_order_review.ReviewResult(
                a["symbol"], "sell", "market", rh_order_review.REJECTED, message="halted"
            )
        ),
        review_blocks=True,
    )
    assert gr[0]["approved"] is False
    assert called == []
    assert created == []
    assert "broker_review" not in gr[0]
