"""Deep analysis + Kelly must degrade to MCP snapshot when Robinhood is offline."""
from __future__ import annotations

from starlette.testclient import TestClient

import data_fetcher
import server


def _owner_session(monkeypatch):
    monkeypatch.setenv("GESH_AUTH_ENABLED", "1")
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: "georgy75@gmail.com")
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda path, cookie: "allow")


_SNAPSHOT = {
    "holdings": [
        {"symbol": "NVDA", "quantity": 10.0, "price": 100.0, "equity": 1000.0,
         "average_buy_price": 90.0, "percent_change": 1.0, "type": "stock"},
    ],
    "equity_summary": {
        "portfolio_equity": 10_000.0,
        "adjusted_equity_previous_close": 9_900.0,
        "cash": 500.0,
    },
    "offline": True,
    "snapshot_age_s": 3600,
}


def test_deep_analysis_uses_snapshot_when_robinhood_offline(monkeypatch):
    _owner_session(monkeypatch)
    monkeypatch.setattr(
        server, "build_data_payload",
        lambda *a, **k: (_ for _ in ()).throw(data_fetcher.RobinhoodOfflineError("offline")),
    )
    monkeypatch.setattr(server, "load_snapshot_payload", lambda *a, **k: dict(_SNAPSHOT))
    monkeypatch.setattr(server, "_regime_for_symbols", lambda s, **k: {"regimes": [], "regime_summary": "cached"})
    monkeypatch.setattr(server, "get_all_market_data", lambda s: {})
    monkeypatch.setattr(
        server, "deep_analyze_portfolio",
        lambda p, **k: {"holdings_analysis": [], "portfolio_score": 70},
    )
    monkeypatch.setattr(server, "log_analysis", lambda *a, **k: 42)
    monkeypatch.setattr(server, "log_equity_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(server, "_save_engine_result", lambda *a, **k: None)

    client = TestClient(server.app, raise_server_exceptions=False)
    r = client.post("/api/deep-analysis")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body.get("offline") is True
    assert body["run_id"] == 42


def test_kelly_uses_snapshot_when_robinhood_offline(monkeypatch):
    _owner_session(monkeypatch)
    monkeypatch.setattr(
        server, "build_data_payload",
        lambda *a, **k: (_ for _ in ()).throw(data_fetcher.RobinhoodOfflineError("offline")),
    )
    monkeypatch.setattr(server, "load_snapshot_payload", lambda *a, **k: dict(_SNAPSHOT))
    monkeypatch.setattr(server, "get_analyst_targets", lambda s: {})
    monkeypatch.setattr(
        server, "run_kelly_analysis",
        lambda h, eq, au, av: {"allocations": [], "score": 80},
    )
    monkeypatch.setattr(server, "_save_engine_result", lambda *a, **k: None)
    monkeypatch.setattr(server, "get_analysis_history", lambda limit=1: [])
    monkeypatch.setattr(server, "get_analysis_by_id", lambda rid: None)

    client = TestClient(server.app, raise_server_exceptions=False)
    r = client.post("/api/kelly")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body.get("offline") is True
    assert body["total_equity"] == 10_000.0

def test_kelly_endpoint_serializes_numpy_scalars(monkeypatch):
    _owner_session(monkeypatch)
    """QA 2026-07-17: POST /api/kelly 500'd with "'numpy.bool' object is not
    iterable" — run_kelly_analysis can emit numpy scalars, and the OWNER endpoint
    returned engine output raw while the customer path wraps it in _jsonable.
    The endpoint must coerce engine output to JSON-native types."""
    import numpy as np

    import data_fetcher
    import server

    monkeypatch.setattr(
        server, "build_data_payload",
        lambda *a, **k: (_ for _ in ()).throw(data_fetcher.RobinhoodOfflineError("offline")),
    )
    monkeypatch.setattr(server, "load_snapshot_payload", lambda *a, **k: dict(_SNAPSHOT))
    monkeypatch.setattr(server, "get_analyst_targets", lambda s: {})
    monkeypatch.setattr(
        server, "run_kelly_analysis",
        lambda h, eq, au, av: {"holdings": [{"symbol": "NVDA", "action": "ADD",
                                             "overweight": np.True_,
                                             "optimal_pct": np.float64(5.4)}],
                               "capped": np.bool_(False)},
    )
    monkeypatch.setattr(server, "_save_engine_result", lambda *a, **k: None)
    monkeypatch.setattr(server, "get_analysis_history", lambda limit=1: [])
    monkeypatch.setattr(server, "get_analysis_by_id", lambda rid: None)

    client = TestClient(server.app, raise_server_exceptions=False)
    r = client.post("/api/kelly")
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["capped"] is False                       # native bool, not numpy repr
    assert d["holdings"][0]["overweight"] is True
    assert d["holdings"][0]["optimal_pct"] == 5.4


def test_monte_carlo_endpoint_serializes_numpy_scalars(monkeypatch):
    _owner_session(monkeypatch)
    """Same hazard class as /api/kelly: run_monte_carlo is numpy-heavy and the
    owner endpoint returns its output raw."""
    import numpy as np

    import data_fetcher
    import server

    monkeypatch.setattr(
        server, "build_data_payload",
        lambda *a, **k: (_ for _ in ()).throw(data_fetcher.RobinhoodOfflineError("offline")),
    )
    monkeypatch.setattr(server, "load_snapshot_payload", lambda *a, **k: dict(_SNAPSHOT))
    monkeypatch.setattr(server, "run_monte_carlo",
                        lambda h, eq, **k: {"median": np.float64(10500.0),
                                            "breached": np.bool_(True)})
    monkeypatch.setattr(server, "_save_engine_result", lambda *a, **k: None)

    client = TestClient(server.app, raise_server_exceptions=False)
    r = client.post("/api/monte-carlo")
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["median"] == 10500.0
    assert d["breached"] is True
