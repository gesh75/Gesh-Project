"""Owner analysis endpoints must DEGRADE to the MCP snapshot when the (permanently
dead) robin_stocks session raises RobinhoodOfflineError — not hard-500 the
Monte-Carlo / AI-Deep-Analysis / Daily panels.

Regression for the live dashboard error:
    {"detail":"Robinhood session not logged in (build_holdings) — run reauth.py."}
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

import server

_SNAP = {
    "holdings": [{"symbol": "NVDA", "quantity": 1, "equity": 100.0,
                  "average_buy_price": 90.0, "current_price": 100.0, "price": 100.0}],
    "equity_summary": {"portfolio_equity": 100.0, "total": 200.0,
                       "adjusted_equity_previous_close": 95.0},
    "offline": True,
}
_H = {"X-API-Key": "testkey"}


@pytest.fixture
def owner_client(monkeypatch):
    # wall off + GESH_API_KEY set → require_api_key admits the X-API-Key header
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: False)
    monkeypatch.setenv("GESH_API_KEY", "testkey")
    # robin_stocks is offline; _resolved_payload must fall back to the snapshot
    monkeypatch.setattr(server, "build_data_payload",
                        lambda *a, **k: (_ for _ in ()).throw(server.RobinhoodOfflineError("dead")))
    monkeypatch.setattr(server, "load_snapshot_payload", lambda *a, **k: dict(_SNAP))
    monkeypatch.setattr(server, "_save_engine_result", lambda *a, **k: None)
    return TestClient(server.app, raise_server_exceptions=False)


def test_monte_carlo_falls_back_to_snapshot(monkeypatch, owner_client):
    monkeypatch.setattr(server, "run_monte_carlo", lambda h, e: {"var_95": 5.0})
    r = owner_client.post("/api/monte-carlo", headers=_H)
    assert r.status_code == 200                       # NOT 500 "Robinhood session not logged in"
    body = r.json()
    assert body["status"] == "ok"
    assert body.get("offline") is True                # honestly flagged as degraded


def test_deep_analysis_falls_back_and_skips_equity_log(monkeypatch, owner_client):
    monkeypatch.setattr(server, "_regime_for_symbols",
                        lambda syms, offline=False: {"regime_summary": "x", "regimes": []})
    monkeypatch.setattr(server, "get_all_market_data", lambda syms: {})
    monkeypatch.setattr(server, "deep_analyze_portfolio",
                        lambda p, regime_context=None: {"portfolio_score": 60})
    monkeypatch.setattr(server, "log_analysis", lambda *a, **k: 1)
    logged = {"equity": False}
    monkeypatch.setattr(server, "log_equity_snapshot", lambda h: logged.__setitem__("equity", True))
    r = owner_client.post("/api/deep-analysis", headers=_H)
    assert r.status_code == 200                        # NOT 500
    assert r.json().get("offline") is True
    assert logged["equity"] is False                   # never persist a stale snapshot as a live tick


def test_monte_carlo_still_500s_when_no_snapshot(monkeypatch, owner_client):
    # If even the snapshot is unavailable, load_snapshot_payload re-raises → honest 5xx,
    # NOT a silent wrong-data success.
    monkeypatch.setattr(server, "load_snapshot_payload",
                        lambda *a, **k: (_ for _ in ()).throw(server.RobinhoodOfflineError("no snapshot")))
    r = owner_client.post("/api/monte-carlo", headers=_H)
    assert r.status_code in (500, 503)
