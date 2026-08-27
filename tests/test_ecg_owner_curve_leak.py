"""GET /api/ecg must not serve the OWNER's equity curve to a customer.

Same leak class as /api/consensus/{symbol} (1ad42d7) and the explainability/
radar holes. `equity_snapshots` is keyed by SYMBOL with no user column, so
`get_equity_history(symbol)` returns the owner's personal drawdown / loss
streak / SUSPENDED state. The published model book excludes ECG on purpose
(`model_portfolio.py`); customer book ECG lives at /api/mybook/ecg.

WHY EXISTING GUARDS MISSED IT

  * `test_customer_leak_sweep` matches owner DOLLAR sentinels. drawdown_pct
    is a percentage, so the sweep is structurally blind (same lesson as
    tests/test_consensus_symbol_ecg_leak.py).
  * conftest's `_isolate_equity_snapshots_db` points `db.DB_PATH` at a
    throwaway, so under test `compute_ecg` finds no history and reports
    HEALTHY / insufficient history. Seeding the isolated DB first is what
    makes the absence of SUSPENDED a real assertion.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from starlette.testclient import TestClient

OWNER_HELD_SYMBOL = "NVDA"
OWNER_EMAIL = "georgy75@gmail.com"
CUSTOMER_EMAIL = "cust@x.com"


def _seed_owner_equity_history(symbol: str, equities: list[float]) -> None:
    """Write a real owner-keyed curve into the per-test throwaway DB."""
    import db

    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with db._conn() as con:
        con.executemany(
            "INSERT INTO equity_snapshots "
            "(snapshot_at, symbol, equity, percent_change, current_price) "
            "VALUES (?,?,?,?,?)",
            [
                ((start + timedelta(days=i)).isoformat(), symbol, eq, 0.0, 10.0)
                for i, eq in enumerate(equities)
            ],
        )


@pytest.fixture
def falling_owner_curve():
    """5000 -> 1000: an 80% drawdown with a long loss streak."""
    _seed_owner_equity_history(OWNER_HELD_SYMBOL, [5000.0, 4000.0, 3000.0, 2000.0, 1000.0])


def _nvda_payload() -> dict:
    return {
        "holdings": [
            {
                "symbol": OWNER_HELD_SYMBOL,
                "quantity": 10.0,
                "current_price": 10.0,
                "equity": 1000.0,
                "portfolio_percentage": 100.0,
                "type": "stock",
            }
        ],
        "equity_summary": {
            "portfolio_equity": 1000.0,
            "cash": 0.0,
            "total": 1000.0,
        },
        "watchlist": [],
    }


@pytest.fixture
def env(monkeypatch):
    import server

    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: True)
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda p, c: "allow", raising=False)
    monkeypatch.setattr(server, "_resolved_payload", lambda *a, **k: _nvda_payload())
    monkeypatch.setattr(server, "build_data_payload", lambda *a, **k: _nvda_payload())
    monkeypatch.setattr(server, "load_snapshot_payload", lambda: _nvda_payload())
    return server


def _client(server, monkeypatch, email: str, *, owner: bool) -> TestClient:
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: email)
    monkeypatch.setattr(server.auth_users, "is_owner", lambda e: owner)
    c = TestClient(server.app, raise_server_exceptions=False)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    return c


def test_control_seeded_owner_curve_trips_the_gate(env, monkeypatch, falling_owner_curve):
    """Proves the fixture is genuine. If this fails, the customer assertions
    below are vacuous — the seed never produced a loud owner ECG state."""
    server = env
    client = _client(server, monkeypatch, OWNER_EMAIL, owner=True)
    r = client.get("/api/ecg")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("status") == "ok"
    states = (body.get("data") or {}).get("states") or []
    nvda = next((s for s in states if s.get("symbol") == OWNER_HELD_SYMBOL), None)
    assert nvda is not None, f"owner ECG did not score {OWNER_HELD_SYMBOL}: {body}"
    assert nvda.get("state") in ("COOLING", "SUSPENDED"), f"seed produced no drawdown state: {nvda}"
    assert float(nvda.get("drawdown_pct") or 0) < -30, f"expected a deep drawdown, got {nvda}"


def test_customer_ecg_never_reads_owner_equity_snapshots(env, monkeypatch, falling_owner_curve):
    """The contract: a customer GET must not touch get_equity_history at all."""
    server = env
    called: list[str] = []

    def _spy(symbol, days=30):
        called.append(symbol)
        raise AssertionError(f"customer /api/ecg read owner history for {symbol}")

    monkeypatch.setattr(server, "get_equity_history", _spy)
    client = _client(server, monkeypatch, CUSTOMER_EMAIL, owner=False)
    r = client.get("/api/ecg")
    assert r.status_code == 200, r.text
    assert called == [], f"owner history was read for {called}"


def test_owner_drawdown_never_appears_in_the_customer_body(env, monkeypatch, falling_owner_curve):
    """Assert on the wire format — that is what a paying subscriber receives."""
    server = env
    client = _client(server, monkeypatch, CUSTOMER_EMAIL, owner=False)
    r = client.get("/api/ecg")
    assert r.status_code == 200, r.text
    body = r.text
    assert "SUSPENDED" not in body
    assert "COOLING" not in body
    assert "DD=-" not in body
    data = r.json()["data"]
    assert data.get("ecg_enabled") is False
    assert data.get("states") == []
    assert data.get("portfolio_gate") == "UNKNOWN"
    for state in data.get("states") or []:
        assert state.get("drawdown_pct") in (None, 0, 0.0)


def test_unauthenticated_ecg_is_treated_as_customer(env, monkeypatch, falling_owner_curve):
    """Fails closed: wall on + no session must never mean owner."""
    server = env
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: None)
    monkeypatch.setattr(server.auth_users, "is_owner", lambda e: False)
    c = TestClient(server.app, raise_server_exceptions=False)
    r = c.get("/api/ecg")
    assert r.status_code == 200, r.text
    assert "SUSPENDED" not in r.text
    assert r.json()["data"].get("ecg_enabled") is False
