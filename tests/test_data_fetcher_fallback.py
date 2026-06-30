"""
tests/test_data_fetcher_fallback.py — MCP-snapshot fallback for the live dashboard.

When the robin_stocks session is offline, data_fetcher.load_snapshot_payload() maps the
MCP-built live_portfolio_snapshot.json into the exact shape build_data_payload() returns, so
/api/portfolio degrades to last-known data instead of blanking. No network.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import data_fetcher as df


def _fresh_ts(age_seconds: int = 3600) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=age_seconds)).isoformat()


def _write_snap(tmp_path: Path, *, equity_summary: dict | None = None,
                fetched_at: str | None = None) -> Path:
    snap = {
        "fetched_at": fetched_at if fetched_at is not None else _fresh_ts(),
        "source": "robinhood_mcp",
        "holdings": [
            {"symbol": "MSTR", "quantity": 10, "price": 400.0,
             "average_buy_price": 350.0, "equity": 4000.0, "percent_change": 1.5},
            {"symbol": "SPY", "quantity": 5, "price": 600.0,
             "average_buy_price": 580.0, "equity": 3000.0, "percent_change": -0.3},
        ],
        "equity_summary": equity_summary if equity_summary is not None else {
            "portfolio_equity": 7000.0, "cash": 500.0, "deployable": 500.0,
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


# ── Codex HIGH-2: headline equity must be ACCOUNT-level (positions + cash) ─────

def test_headline_equity_is_account_level(tmp_path):
    """portfolio_equity must map from account_equity (positions + cash), not positions-only —
    otherwise day_change compares positions-only equity against an account-level previous close
    and overstates the loss."""
    es = {"portfolio_equity": 38491.91,        # positions only — must NOT be the headline
          "account_equity": 38920.51,          # positions + cash — the correct headline
          "total": 38920.51, "cash": 509.70, "buying_power": 509.70, "deployable": 509.70,
          "adjusted_equity_previous_close": 39009.30}
    payload = df.load_snapshot_payload(_write_snap(tmp_path, equity_summary=es))
    assert payload["equity_summary"]["portfolio_equity"] == 38920.51       # account-level
    # day-change is now account-vs-account (computed in server.py, but verify the input is right)
    prev = payload["equity_summary"]["adjusted_equity_previous_close"]
    assert round(payload["equity_summary"]["portfolio_equity"] - prev, 2) == -88.79


def test_account_equity_falls_back_to_total_then_portfolio(tmp_path):
    # no account_equity → use total
    p1 = df.load_snapshot_payload(_write_snap(tmp_path, equity_summary={
        "total": 5000.0, "portfolio_equity": 4500.0, "adjusted_equity_previous_close": 5000.0}))
    assert p1["equity_summary"]["portfolio_equity"] == 5000.0


# ── Codex MEDIUM-3: stale / empty / timestamp-less snapshots must NOT pose as live ──

def test_empty_snapshot_rejected(tmp_path):
    p = tmp_path / "live_portfolio_snapshot.json"
    p.write_text("{}")
    with pytest.raises(df.RobinhoodOfflineError):       # not a fresh $0 portfolio
        df.load_snapshot_payload(p)


def test_zero_equity_snapshot_rejected(tmp_path):
    with pytest.raises(df.RobinhoodOfflineError):
        df.load_snapshot_payload(_write_snap(tmp_path, equity_summary={
            "account_equity": 0.0, "adjusted_equity_previous_close": 0.0}))


def test_stale_snapshot_beyond_max_age_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(df, "_SNAPSHOT_MAX_AGE_SECONDS", 3600.0)   # 1h cap
    old = _fresh_ts(age_seconds=365 * 86400)                       # a year old
    with pytest.raises(df.RobinhoodOfflineError):
        df.load_snapshot_payload(_write_snap(tmp_path, fetched_at=old))


def test_timestampless_snapshot_rejected(tmp_path):
    with pytest.raises(df.RobinhoodOfflineError):
        df.load_snapshot_payload(_write_snap(tmp_path, fetched_at=""))


# ── Codex MEDIUM-4: build_data_payload raises typed offline error (not a bare Exception) ──

def test_build_data_payload_offline_raises_robinhood_offline(monkeypatch):
    """robin_stocks raises a bare Exception when the session is dead; get_portfolio_holdings
    must normalize it to RobinhoodOfflineError so the API falls back ONLY on offline, not on a
    real bug."""
    def _not_logged_in():
        raise Exception("build_holdings can only be called when logged in")
    monkeypatch.setattr(df.rh, "build_holdings", _not_logged_in)
    with pytest.raises(df.RobinhoodOfflineError):
        df.get_portfolio_holdings()


def test_real_bug_in_holdings_is_not_swallowed(monkeypatch):
    """A non-offline error must propagate as-is (so the endpoint 500s, not silently serves
    stale data)."""
    def _boom():
        raise RuntimeError("simulated programming defect")
    monkeypatch.setattr(df.rh, "build_holdings", _boom)
    with pytest.raises(RuntimeError):
        df.get_portfolio_holdings()


# ── Codex round-2 LOW-2: non-object JSON must be a clean 503, not an AttributeError 500 ──

@pytest.mark.parametrize("body", ["[]", "42", '"a string"', "null"])
def test_non_object_snapshot_rejected(tmp_path, body):
    p = tmp_path / "live_portfolio_snapshot.json"
    p.write_text(body)
    with pytest.raises(df.RobinhoodOfflineError):       # not AttributeError
        df.load_snapshot_payload(p)


# ── Codex round-2 MEDIUM-1: BOTH portfolio loaders must render the offline/cached badge ──

def test_dashboard_loaders_are_offline_aware():
    """Guard against the regression Codex found: the interval loader (loadPortfolioWithECG)
    showed cached data as green 'Live'. Both loaders must route the badge through the single
    offline-aware helper, and the unconditional pattern must not reappear."""
    html = (Path(__file__).resolve().parents[1] / "templates" / "dashboard.html").read_text()
    assert "setConn(true);setTs()" not in html            # the buggy unconditional pattern is gone
    assert "function applyPortfolioConn(" in html          # single source of truth exists
    # both loaders delegate to it
    for fn in ("loadPortfolio", "loadPortfolioWithECG"):
        seg = html.split(f"async function {fn}(", 1)[1].split("async function", 1)[0]
        assert "applyPortfolioConn(d)" in seg, f"{fn} does not use applyPortfolioConn"
