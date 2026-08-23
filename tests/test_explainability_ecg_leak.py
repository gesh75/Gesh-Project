"""GET /api/explainability/{symbol} must not vote the OWNER's equity curve.

Same leak class as /api/consensus/{symbol} (fixed in 1ad42d7, guarded by
test_consensus_symbol_ecg_leak.py). That route was patched; this one was
missed. It calls `run_consensus_holding(symbol)` with allow_ecg defaulting
to True, so analysing any ticker the owner also holds returned the owner's
real drawdown:

    node_ecg('MU')  -> (-1, 'ECG=SUSPENDED DD=-71.9% Streak=1')

`explain_consensus` embeds `details["ECG"]` verbatim in `node_bullets` and
`plain_english`, and the vote can flip the explained verdict. The route is
customer-reachable (heavy bucket, not owner-only). Money-masking does not
scrub drawdown percentages.

WHY THE EXISTING GUARDS MISSED IT — same lessons as the consensus-symbol
file, preserved here because they still apply:

  * `test_customer_leak_sweep` matches owner DOLLAR sentinels and stubs
    `build_transparency_report`, so ECG prose never reaches the wire check.
  * conftest's `_isolate_equity_snapshots_db` empties the owner table, so
    `node_ecg` abstains on its own unless a test seeds a real curve.

This file SEEDS a falling owner curve first. The control test proves the
seed genuinely produces a vote; only then is its absence meaningful.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from starlette.testclient import TestClient

# Held by the owner in live_portfolio_snapshot.json — the collision that makes
# the leak reachable at all.
OWNER_HELD_SYMBOL = "NVDA"


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


@pytest.fixture
def customer_client(monkeypatch):
    """A signed-in CUSTOMER — the population the leak is reachable by."""
    import server

    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: True)
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: "cust@x.com")
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda p, c: "allow", raising=False)
    monkeypatch.setattr(server.auth_users, "is_owner", lambda e: False, raising=False)
    c = TestClient(server.app, raise_server_exceptions=False)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    return c


def test_control_seeded_owner_curve_really_does_vote(falling_owner_curve):
    """Proves the fixture is genuine. If this fails, every assertion below is vacuous."""
    from consensus import ABSTAIN, node_ecg

    vote, detail = node_ecg(OWNER_HELD_SYMBOL)

    assert vote is not ABSTAIN, f"seed produced no vote: {detail}"
    assert "DD=" in detail, f"expected a drawdown reading, got {detail!r}"


def test_route_explicitly_disables_the_ecg_node(monkeypatch, falling_owner_curve, customer_client):
    """Assert on the ARGUMENT the route passes, not on downstream behaviour.

    Spying on `node_ecg` can pass against an unfixed engine: the offline harness
    has no price data, so `run_consensus_holding` may bail before ECG. The
    route's contract is `allow_ecg=False`; assert that directly.
    """
    import consensus

    seen: dict = {}

    def _spy(symbol, *args, **kwargs):
        seen["kwargs"] = kwargs
        return {
            "verdict": "INSUFFICIENT DATA",
            "active_nodes": 0,
            "regime": "NEUTRAL",
            "votes": {},
            "details": {},
        }

    monkeypatch.setattr(consensus, "run_consensus_holding", _spy)
    customer_client.get(f"/api/explainability/{OWNER_HELD_SYMBOL}")

    assert seen, "the route never reached run_consensus_holding"
    assert seen["kwargs"].get("allow_ecg") is False, (
        "explainability did not disable the ECG node; it would read the "
        f"owner's equity_snapshots. kwargs={seen['kwargs']!r}"
    )


def test_owner_drawdown_never_appears_in_the_response_body(falling_owner_curve, customer_client):
    """ECG detail ships in node_bullets / plain_english. Assert on the wire."""
    r = customer_client.get(f"/api/explainability/{OWNER_HELD_SYMBOL}")

    body = r.text
    assert "ECG=SUSPENDED" not in body
    assert "DD=-" not in body, "an owner drawdown reading reached the response body"
    assert "Streak=" not in body
