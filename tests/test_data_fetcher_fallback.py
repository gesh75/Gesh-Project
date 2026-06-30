"""
tests/test_data_fetcher_fallback.py — MCP-snapshot fallback for the live dashboard.

When the robin_stocks session is offline, data_fetcher.load_snapshot_payload() maps the
MCP-built live_portfolio_snapshot.json into the exact shape build_data_payload() returns, so
/api/portfolio degrades to last-known data instead of blanking. No network.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import data_fetcher as df


def _write_snap(tmp_path: Path) -> Path:
    snap = {
        "fetched_at": "2026-06-29T14:31:01.238569+00:00",
        "source": "robinhood_mcp",
        "holdings": [
            {"symbol": "MSTR", "quantity": 10, "price": 400.0,
             "average_buy_price": 350.0, "equity": 4000.0, "percent_change": 1.5},
            {"symbol": "SPY", "quantity": 5, "price": 600.0,
             "average_buy_price": 580.0, "equity": 3000.0, "percent_change": -0.3},
        ],
        "equity_summary": {"portfolio_equity": 7000.0, "cash": 500.0, "deployable": 500.0,
                           "buying_power": 500.0, "adjusted_equity_previous_close": 7100.0},
    }
    p = tmp_path / "live_portfolio_snapshot.json"
    p.write_text(json.dumps(snap))
    return p


def test_snapshot_maps_to_payload_shape(tmp_path):
    payload = df.load_snapshot_payload(_write_snap(tmp_path))
    # same top-level shape as build_data_payload + offline markers
    assert set(payload) >= {"as_of", "equity_summary", "holdings", "watchlist", "offline", "source"}
    assert payload["offline"] is True and payload["source"] == "robinhood_mcp"
    eq = payload["equity_summary"]
    assert eq["portfolio_equity"] == 7000.0 and eq["cash"] == 500.0
    assert eq["adjusted_equity_previous_close"] == 7100.0


def test_snapshot_holdings_are_enriched(tmp_path):
    payload = df.load_snapshot_payload(_write_snap(tmp_path))
    h = {x["symbol"]: x for x in payload["holdings"]}
    assert h["MSTR"]["current_price"] == 400.0                 # price -> current_price
    assert h["MSTR"]["equity_change"] == 500.0                 # 4000 - 350*10
    # portfolio_percentage recomputed off combined equity (4000/7000)
    assert h["MSTR"]["portfolio_percentage"] == pytest.approx(57.1, abs=0.1)
    assert h["MSTR"]["type"] == "stock" and h["MSTR"]["pe_ratio"] is None


def test_snapshot_age_is_reported(tmp_path):
    payload = df.load_snapshot_payload(_write_snap(tmp_path))
    assert isinstance(payload["snapshot_age_seconds"], int) and payload["snapshot_age_seconds"] > 0


def test_missing_snapshot_raises_offline(tmp_path):
    with pytest.raises(df.RobinhoodOfflineError):
        df.load_snapshot_payload(tmp_path / "does_not_exist.json")


def test_corrupt_snapshot_raises_offline(tmp_path):
    bad = tmp_path / "live_portfolio_snapshot.json"
    bad.write_text("{not json")
    with pytest.raises(df.RobinhoodOfflineError):
        df.load_snapshot_payload(bad)
