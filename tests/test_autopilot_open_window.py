"""
Offline regression test for trading.autopilot.open_window().

Guards the NameError + wrong-args bug: open_window() previously called
run_consensus_holding(sym, snapshot) without importing the symbol, raising a
NameError that the inner try/except swallowed — marking every candidate
rejected so the autopilot executed nothing.

This test mocks every external dependency (yfinance/consensus, Robinhood data
fetch, auth, broker execution) and asserts open_window:
  1. does NOT raise NameError,
  2. produces a valid TradeIntent via from_consensus,
  3. pushes that intent through the risk gate to place() (i.e. executes).

Run from the repo root:
    ./venv/bin/python -m pytest tests/test_autopilot_open_window.py -q
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

# Make the repo root importable (tests live one level deep, under tests/).
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# ─── Stub heavy top-level modules BEFORE importing trading.autopilot ──────────
# autopilot.py lazy-loads consensus via _load_consensus() (which does
# `from consensus import run_consensus_holding`); consensus imports yfinance.
# Stubbing it keeps the `from trading import autopilot` import below fully offline even if
# some transitive import touches these names.
def _install_stub_modules() -> set[str]:
    """Install throwaway stand-ins, returning only the names actually inserted (skips any
    already present) so the caller can remove exactly those right after the import below.
    The stubs must NOT linger in sys.modules for the rest of the pytest session — moving this
    file into tests/ (2026-07-01, Codex round-2 fix) proved that: a lingering fake `auth`
    module (missing `logout`) broke test_watch_endpoint.py's `from auth import login, logout`
    when both files ran in the same session. Safe to remove immediately: autopilot.py's
    loaders (_ensure_logged_in/_load_consensus/_load_data_fetcher) are plain functions, never
    invoked at import time, and every test below monkeypatches them directly."""
    installed: set[str] = set()
    if "consensus" not in sys.modules:
        consensus_stub = types.ModuleType("consensus")
        consensus_stub.run_consensus_holding = lambda *a, **k: {}  # patched per-test
        sys.modules["consensus"] = consensus_stub
        installed.add("consensus")

    if "data_fetcher" not in sys.modules:
        data_stub = types.ModuleType("data_fetcher")
        data_stub.build_data_payload = lambda *a, **k: {}
        sys.modules["data_fetcher"] = data_stub
        installed.add("data_fetcher")

    if "auth" not in sys.modules:
        auth_stub = types.ModuleType("auth")
        auth_stub.login = lambda *a, **k: None
        auth_stub.logout = lambda *a, **k: None
        sys.modules["auth"] = auth_stub
        installed.add("auth")

    return installed


_stubbed_modules = _install_stub_modules()

# try/finally so the stubs are removed even if the import below RAISES. A fileless
# stub left in sys.modules poisons every later test that imports the real module —
# e.g. server's `from data_fetcher import DEFAULT_WATCHLIST` then fails with
# "cannot import name ... (unknown location)". Observed in CI: a transitive
# gesh-core ImportError aborted this module mid-import, leaking the data_fetcher
# stub and cascading collection errors into test_chart/test_customer_view/test_watch.
try:
    from trading import autopilot  # noqa: E402
    from trading.contract import TradeIntent  # noqa: E402
finally:
    for _name in _stubbed_modules:
        sys.modules.pop(_name, None)
    del _stubbed_modules


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def snapshot() -> dict:
    """Representative open-window snapshot: one held + watchlist quote."""
    return {
        "as_of": "2026-05-29T13:35:00+00:00",
        "equity_summary": {"portfolio_equity": 100_000.0},
        "holdings": [
            {
                "symbol": "MSTR",
                "average_buy_price": 250.0,
                "equity": 5_000.0,
                "current_price": 300.0,
                "portfolio_percentage": 5.0,
            }
        ],
        "watchlist": [
            {"symbol": "MSTR", "last_trade_price": 300.0},
            {"symbol": "SPY", "last_trade_price": 500.0},
        ],
    }


@pytest.fixture(autouse=True)
def _reset_candidates(monkeypatch):
    autopilot._CANDIDATES.clear()
    monkeypatch.setattr(autopilot, "set_marker", lambda *args: None)
    yield
    autopilot._CANDIDATES.clear()


# ─── Helpers ─────────────────────────────────────────────────────────────────

class _Verdict:
    def __init__(self, approved: bool, reason: str = "ok") -> None:
        self.approved = approved
        self.reason = reason


class _Result:
    def __init__(self) -> None:
        self.qty = 10.0
        self.target_price = 300.0
        self.status = "SIMULATED"


# ─── Tests ───────────────────────────────────────────────────────────────────

def test_open_window_executes_candidate_without_nameerror(monkeypatch, snapshot):
    """A strong-buy candidate must survive open_window and reach place()."""
    captured: dict = {}

    def fake_consensus(symbol, equity=0, total_portfolio=1, avg_cost=0, current_price=0):
        # Record the args so we can assert correct positional/keyword usage.
        captured["call"] = {
            "symbol": symbol,
            "equity": equity,
            "total_portfolio": total_portfolio,
            "avg_cost": avg_cost,
            "current_price": current_price,
        }
        return {
            "symbol": symbol,
            "verdict": "STRONG BUY",
            "raw_score": 0.7,
            "confidence": 90.0,   # 0..100 → 0.9 after from_consensus
            "active_nodes": 8,
            "total_nodes": 9,
        }

    placed: list[TradeIntent] = []

    def fake_place(intent, verdict, snap, cfg):
        placed.append(intent)
        return _Result()

    # Pre-seed a candidate as pre_market_scan would have.
    seed_intent = TradeIntent(
        symbol="MSTR", action="BUY", confidence=0.9,
        source="consensus", target_position_pct=0.02,
    )
    autopilot._CANDIDATES["MSTR"] = (seed_intent, {"verdict": "STRONG BUY"})

    # open_window obtains run_consensus_holding via _load_consensus() (lazy),
    # exactly like pre_market_scan and midday_rebalance — patch that seam.
    monkeypatch.setattr(autopilot, "_load_consensus", lambda: fake_consensus)
    monkeypatch.setattr(autopilot, "_ensure_logged_in", lambda: True)
    monkeypatch.setattr(autopilot, "_load_data_fetcher", lambda: (lambda _: snapshot))
    monkeypatch.setattr(autopilot, "evaluate", lambda intent, snap, cfg: _Verdict(True))
    monkeypatch.setattr(autopilot, "place", fake_place)

    # Must not raise (previously raised NameError internally → all rejected).
    autopilot.open_window()

    # The candidate reached execution with a valid TradeIntent.
    assert len(placed) == 1, "open_window did not execute the candidate"
    executed = placed[0]
    assert isinstance(executed, TradeIntent)
    assert executed.validate() == [], f"invalid intent: {executed.validate()}"
    assert executed.action == "BUY"
    assert executed.symbol == "MSTR"

    # Consensus was called with the CORRECT signature, args drawn from snapshot.
    call = captured["call"]
    assert call["symbol"] == "MSTR"
    assert call["total_portfolio"] == 100_000.0
    assert call["equity"] == 5_000.0          # from the held position
    assert call["avg_cost"] == 250.0          # from average_buy_price
    assert call["current_price"] == 300.0


def test_consensus_args_unheld_symbol_uses_watchlist_quote(snapshot):
    """For a watchlist-only symbol, equity/avg_cost default to 0 and price
    comes from the watchlist quote — proves args derive from real fields."""
    args = autopilot._consensus_args("SPY", snapshot)
    assert args == {
        "equity": 0.0,
        "total_portfolio": 100_000.0,
        "avg_cost": 0.0,
        "current_price": 500.0,
    }


def test_open_window_no_candidates_is_noop(monkeypatch):
    """Empty candidate set returns immediately without touching execution."""
    called = {"place": False}
    monkeypatch.setattr(autopilot, "place", lambda *a, **k: called.__setitem__("place", True))
    autopilot._CANDIDATES.clear()
    autopilot.open_window()
    assert called["place"] is False


def test_open_window_swallows_consensus_error_and_rejects(monkeypatch, snapshot):
    """If re-consensus raises, the candidate is rejected (not executed) and the
    error is swallowed safely — open_window must not crash or place an order.

    This recreates the *behavioural* shape of the original bug (every candidate
    silently rejected) and proves the recovery path is intentional, not a crash.
    """
    def boom(*a, **k):
        raise RuntimeError("consensus exploded")

    placed: list = []
    seed_intent = TradeIntent(
        symbol="MSTR", action="BUY", confidence=0.9,
        source="consensus", target_position_pct=0.02,
    )
    autopilot._CANDIDATES["MSTR"] = (seed_intent, {"verdict": "STRONG BUY"})

    monkeypatch.setattr(autopilot, "_load_consensus", lambda: boom)
    monkeypatch.setattr(autopilot, "_ensure_logged_in", lambda: True)
    monkeypatch.setattr(autopilot, "_load_data_fetcher", lambda: (lambda _: snapshot))
    monkeypatch.setattr(autopilot, "place", lambda *a, **k: placed.append(a))

    autopilot.open_window()  # must not raise

    assert placed == [], "a failing re-consensus must not reach execution"


def test_open_window_consumes_marker_before_execution(monkeypatch, snapshot):
    """A completed handoff cannot be restored and executed again after restart."""
    marker = {
        "value": json.dumps({
            "date": autopilot._et_today(),
            "symbols": ["MSTR"],
        })
    }
    placed: list[TradeIntent] = []

    def fake_consensus(symbol, **kwargs):
        return {
            "symbol": symbol,
            "verdict": "STRONG BUY",
            "raw_score": 0.7,
            "confidence": 90.0,
            "active_nodes": 8,
            "total_nodes": 9,
        }

    monkeypatch.setattr(autopilot, "get_marker", lambda _name: marker["value"])
    monkeypatch.setattr(
        autopilot, "set_marker", lambda _name, value: marker.__setitem__("value", value),
    )
    monkeypatch.setattr(autopilot, "_load_consensus", lambda: fake_consensus)
    monkeypatch.setattr(autopilot, "_ensure_logged_in", lambda: True)
    monkeypatch.setattr(autopilot, "_load_data_fetcher", lambda: (lambda _: snapshot))
    monkeypatch.setattr(autopilot, "evaluate", lambda *args: _Verdict(True))
    monkeypatch.setattr(
        autopilot,
        "place",
        lambda intent, *args: placed.append(intent) or _Result(),
    )

    autopilot.open_window()
    assert len(placed) == 1
    assert json.loads(marker["value"])["symbols"] == []

    # Simulate a same-day process restart and a second scheduler invocation.
    autopilot._CANDIDATES.clear()
    autopilot.open_window()
    assert len(placed) == 1


def test_open_window_aborts_when_consume_cannot_persist(monkeypatch, snapshot):
    """Fail-closed: a marker write error must not place orders after a restart."""
    placed: list = []
    seed_intent = TradeIntent(
        symbol="MSTR", action="BUY", confidence=0.9,
        source="consensus", target_position_pct=0.02,
    )
    autopilot._CANDIDATES["MSTR"] = (seed_intent, {"verdict": "STRONG BUY"})

    def boom(*_a, **_k):
        raise RuntimeError("db locked")

    monkeypatch.setattr(autopilot, "set_marker", boom)
    monkeypatch.setattr(autopilot, "_ensure_logged_in", lambda: True)
    monkeypatch.setattr(autopilot, "_load_data_fetcher", lambda: (lambda _: snapshot))
    monkeypatch.setattr(autopilot, "_load_consensus", lambda: (lambda *a, **k: {}))
    monkeypatch.setattr(autopilot, "place", lambda *a, **k: placed.append(a))

    autopilot.open_window()

    assert placed == []
    assert "MSTR" in autopilot._CANDIDATES  # book left intact; job aborted before exec
