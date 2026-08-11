"""GET /api/consensus/{symbol} must not vote the OWNER's equity curve.

Found in production, 2026-08-10. `run_consensus_for_customer` was written to stop
`node_ecg` reading the symbol-keyed owner `equity_snapshots` table on the BOOK
path — but the per-symbol route was missed. It calls `run_consensus_holding()`
without `allow_ecg=False`, and that parameter defaults to True, so analysing any
ticker the owner also holds returned the owner's real drawdown:

    node_ecg('MU')  -> (-1, 'ECG=SUSPENDED DD=-71.9% Streak=1')

That vote counted toward the verdict and confidence shown to the customer, and the
raw string shipped in the response body under `details`. The route has no auth gate
at all, and the customer Analyze tab fires it on every ticker.

WHY THE EXISTING GUARDS MISSED IT — both are worth preserving as lessons:

  * `test_customer_leak_sweep` matches owner DOLLAR sentinels. A drawdown
    percentage is not a dollar figure, so the sweep was structurally blind.
  * conftest's `_isolate_equity_snapshots_db` repoints `db.DB_PATH` at a throwaway,
    so under test `node_ecg` finds no history and abstains on its own. Any test that
    merely asserts "ECG abstained" therefore passes against a completely unfixed
    engine.

So this file SEEDS a real owner curve into the isolated DB first. The control test
proves the seed genuinely produces a vote when the node is allowed to run; only
then does asserting its absence mean anything.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from starlette.testclient import TestClient

# Held by the owner in live_portfolio_snapshot.json — the collision that makes the
# leak reachable at all.
OWNER_HELD_SYMBOL = "NVDA"


def _seed_owner_equity_history(symbol: str, equities: list[float]) -> None:
    """Write a real owner-keyed curve into the per-test throwaway DB.

    A FALLING curve produces a drawdown and a loss streak, so `ecg._classify`
    returns SUSPENDED and the node votes BEARISH — the node has something loud to
    say, which is what makes its absence below a meaningful assertion.
    """
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
    """5000 -> 1000: a 80% drawdown with a long loss streak."""
    _seed_owner_equity_history(OWNER_HELD_SYMBOL, [5000.0, 4000.0, 3000.0, 2000.0, 1000.0])


@pytest.fixture
def customer_client(monkeypatch):
    """A signed-in CUSTOMER — the population the leak was reachable by.

    The route carries no `dependencies=` gate; the auth-wall middleware returns
    401 to anonymous callers, which is why an unauthenticated probe looks safe.
    A paying subscriber on the Analyze tab clears that wall, so the session must
    be present for this file to be testing the real exposure.
    """
    import server

    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: True)
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: "cust@x.com")
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda p, c: "allow", raising=False)
    monkeypatch.setattr(server.auth_users, "is_owner", lambda e: False, raising=False)
    c = TestClient(server.app, raise_server_exceptions=False)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    return c


def test_control_seeded_owner_curve_really_does_vote(falling_owner_curve):
    """Proves the fixture is genuine. If this ever fails, every assertion below is
    vacuous and this file is no longer protecting anything."""
    from consensus import ABSTAIN, node_ecg

    vote, detail = node_ecg(OWNER_HELD_SYMBOL)

    assert vote is not ABSTAIN, f"seed produced no vote: {detail}"
    assert "DD=" in detail, f"expected a drawdown reading, got {detail!r}"


def test_route_explicitly_disables_the_ecg_node(monkeypatch, falling_owner_curve, customer_client):
    """Assert on the ARGUMENT the route passes, not on downstream behaviour.

    A first version of this test spied on `node_ecg` and asserted it was never
    called — and it PASSED against the unfixed engine, because the offline test
    harness has no price data, so `run_consensus_holding` bails long before it
    reaches the ECG node. The route's contract is `allow_ecg=False`; assert that
    directly and the test cannot be satisfied by an unrelated early return.
    """
    import consensus

    seen: dict = {}

    def _spy(symbol, *args, **kwargs):
        seen["kwargs"] = kwargs
        return {"verdict": "INSUFFICIENT DATA", "active_nodes": 0}

    monkeypatch.setattr(consensus, "run_consensus_holding", _spy)
    customer_client.get(f"/api/consensus/{OWNER_HELD_SYMBOL}")

    assert seen, "the route never reached run_consensus_holding"
    assert seen["kwargs"].get("allow_ecg") is False, (
        "the per-symbol route did not disable the ECG node; it would read the "
        f"owner's equity_snapshots. kwargs={seen['kwargs']!r}"
    )


def test_ecg_node_abstains_when_the_engine_actually_runs(monkeypatch, falling_owner_curve):
    """Complements the argument check: with the other nodes stubbed so the engine
    reaches the end, the ECG node must abstain rather than vote the owner's curve."""
    import consensus

    monkeypatch.setattr(consensus.price_source, "yf_download", lambda *a, **k: None)
    for name in ("node_momentum", "node_trend", "node_volume"):
        monkeypatch.setattr(consensus, name, lambda *a, **k: (consensus.VOTE_BULLISH, "stub"))
    for name in ("node_relative_strength", "node_fundamentals",
                 "node_analyst", "node_news", "node_kelly"):
        monkeypatch.setattr(consensus, name, lambda *a, **k: (consensus.ABSTAIN, "offline"))

    row = consensus.run_consensus_holding(
        OWNER_HELD_SYMBOL, equity=0.0, total_portfolio=1.0,
        avg_cost=0.0, current_price=0.0, allow_ecg=False,
    )

    assert row["votes"]["ECG"] is None, f"ECG voted {row['votes']['ECG']!r} from owner data"
    assert "DD=-" not in str(row.get("details", {}).get("ECG", ""))


def test_owner_drawdown_never_appears_in_the_response_body(falling_owner_curve, customer_client):
    """The raw ECG string shipped under `details`. Assert on the wire format, not
    on internals — that is what a customer actually receives."""
    r = customer_client.get(f"/api/consensus/{OWNER_HELD_SYMBOL}")

    body = r.text
    assert "ECG=SUSPENDED" not in body
    assert "DD=-" not in body, "an owner drawdown reading reached the response body"
    assert "Streak=" not in body


def test_ecg_is_reported_as_excluded_not_silently_missing(falling_owner_curve, customer_client):
    """A node that vanishes reads to a caller as agreement. If ECG appears at all it
    must be an explicit abstain, never a vote."""
    r = customer_client.get(f"/api/consensus/{OWNER_HELD_SYMBOL}")
    if r.status_code != 200:
        pytest.skip("engine returned no consensus in this environment")

    votes = (r.json() or {}).get("votes") or {}
    if "ECG" in votes:
        assert votes["ECG"] is None, f"ECG voted {votes['ECG']!r} from owner data"
