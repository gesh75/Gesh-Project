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
    ./venv/bin/python -m pytest trading/tests/test_autopilot_open_window.py -q
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

# Make the repo root importable (tests live two levels deep).
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# ─── Stub heavy top-level modules BEFORE importing trading.autopilot ──────────
# autopilot.py lazy-loads consensus via _load_consensus() (which does
# `from consensus import run_consensus_holding`); consensus imports yfinance.
# Stubbing it keeps the test fully offline even if the lazy path runs.
def _install_stub_modules() -> None:
    if "consensus" not in sys.modules:
        consensus_stub = types.ModuleType("consensus")
        consensus_stub.run_consensus_holding = lambda *a, **k: {}  # patched per-test
        sys.modules["consensus"] = consensus_stub

    if "data_fetcher" not in sys.modules:
        data_stub = types.ModuleType("data_fetcher")
        data_stub.build_data_payload = lambda *a, **k: {}
        sys.modules["data_fetcher"] = data_stub

    if "auth" not in sys.modules:
        auth_stub = types.ModuleType("auth")
        auth_stub.login = lambda *a, **k: None
        sys.modules["auth"] = auth_stub


_install_stub_modules()

from trading import autopilot  # noqa: E402
from trading.contract import TradeIntent  # noqa: E402


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
def _reset_candidates():
    autopilot._CANDIDATES.clear()
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
