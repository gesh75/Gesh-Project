"""Paper simulate-session + cached consensus helpers."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

import server


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture(autouse=True)
def _no_auth_wall(monkeypatch):
    monkeypatch.delenv("GESH_AUTH_ENABLED", raising=False)
    monkeypatch.setenv("GESH_API_KEY", "test-gate-key")


@pytest.fixture()
def engine_file(tmp_path, monkeypatch):
    eng = tmp_path / "engine_results.json"
    monkeypatch.setattr(server, "ENGINE_RESULTS", eng)

    def _load():
        return json.loads(eng.read_text()) if eng.exists() else {}

    monkeypatch.setattr(server, "_load_engine_results", _load)
    return eng


def test_cached_consensus_row_from_post_shape(engine_file):
    engine_file.write_text(json.dumps({
        "consensus": {
            "status": "ok",
            "data": {
                "holdings": [
                    {"symbol": "NVDA", "verdict": "BUY", "raw_score": 0.5, "confidence": 80},
                ],
            },
        },
        "consensus_ran_at": _iso_now(),
    }))
    row = server._cached_consensus_row("NVDA")
    assert row is not None
    assert row["verdict"] == "BUY"
    assert row["symbol"] == "NVDA"


def test_holding_consensus_args_from_holdings():
    payload = {
        "equity_summary": {"portfolio_equity": 50000},
        "holdings": [
            {
                "symbol": "MU",
                "equity": 2500,
                "average_buy_price": 90,
                "current_price": 95,
            },
        ],
    }
    args = server._holding_consensus_args("MU", payload)
    assert args["equity"] == 2500
    assert args["total_portfolio"] == 50000
    assert args["avg_cost"] == 90
    assert args["current_price"] == 95


def test_upstream_unavailable_maps_to_503():
    try:
        import anthropic
        err = anthropic.APIConnectionError(request=None)  # type: ignore[arg-type]
    except Exception:
        pytest.skip("anthropic SDK not installed")
    resp = server._upstream_unavailable_error(err)
    assert resp is not None
    assert resp.status_code == 503
    assert "Claude API unreachable" in resp.detail


def test_gate_status_endpoint(monkeypatch):
    from fastapi.testclient import TestClient

    class _Cfg:
        max_orders_per_day = 3

    monkeypatch.setattr("trading.config.load_risk_config", lambda: _Cfg())
    monkeypatch.setattr("trading.state.count_orders_today", lambda: 6)

    tc = TestClient(server.app)
    r = tc.get("/api/trading/gate-status", headers={"X-API-Key": "test-gate-key"})
    assert r.status_code == 200
    body = r.json()
    assert body["orders_today"] == 6
    assert body["max_orders_per_day"] == 3
    assert body["cap_reached"] is True

# ── M-05: an unknown portfolio total must not become a 1.0-dollar portfolio ──
import pytest as _pytest  # noqa: E402


@_pytest.mark.parametrize(
    "summary",
    [{}, {"portfolio_equity": 0}, {"portfolio_equity": None}, {"portfolio_equity": float("nan")},
     {"portfolio_equity": -5}],
)
def test_holding_consensus_args_unknown_total_is_zero_for_a_held_position(summary):
    payload = {
        "equity_summary": summary,
        "holdings": [{"symbol": "MU", "equity": 2500, "average_buy_price": 90, "current_price": 95}],
    }
    args = server._holding_consensus_args("MU", payload)
    assert args["total_portfolio"] == 0.0  # never the old 1.0 substitute
    assert args["equity"] == 2500


def test_holding_consensus_args_unknown_total_keeps_watchlist_symbols_usable():
    payload = {"equity_summary": {}, "holdings": [],
               "watchlist": [{"symbol": "NVDA", "last_trade_price": "123.4"}]}
    args = server._holding_consensus_args("NVDA", payload)
    assert args["equity"] == 0.0
    assert args["current_price"] == 123.4


# ── M-12: Kelly delta with a missing side is unknown, not "trim to zero" ─────
@_pytest.mark.parametrize(
    ("row", "expected"),
    [
        ({"optimal_pct": 12.0, "current_pct": 9.1}, 2.9),
        ({"optimal_pct": 0, "current_pct": 5.0}, -5.0),          # a real 0% target
        ({"optimal_pct": None, "current_pct": 9.1}, None),       # was -9.1 (phantom TRIM)
        ({"current_pct": 9.1}, None),
        ({"optimal_pct": 12.0}, None),                           # was +12.0 (phantom ADD)
        ({"optimal_pct": "n/a", "current_pct": 1.0}, None),
        ({"optimal_pct": float("nan"), "current_pct": 1.0}, None),
        ({"optimal_pct": True, "current_pct": 1.0}, None),
    ],
)
def test_kelly_delta_pp_is_none_unless_both_sides_are_known(row, expected):
    assert server._kelly_delta_pp(row) == expected
