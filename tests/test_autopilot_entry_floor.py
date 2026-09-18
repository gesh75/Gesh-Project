"""P2 entry-conviction floor, product-repo copy (audit PR4 — reconciled fix).

confidence from from_consensus is participation (data coverage), so
pre_market_scan must ALSO gate new BUYs on the consensus raw_score
(cfg.min_entry_score, default 0.50). Mirrors the engine repo's
test_entry_conviction_floor.py so the two autopilot copies can't drift apart.
Fully offline: login, fetcher, consensus, alerts, and the DB are all stubbed.
"""

from __future__ import annotations

import pytest

from trading import autopilot, state
from trading.contract import TradeIntent


@pytest.fixture
def sandbox_db(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "DB_PATH", tmp_path / "test_state.db")
    state.init_trading_tables()
    return state


def _row(symbol: str, verdict: str, raw_score: float, confidence: float = 89.0) -> dict:
    return {
        "symbol": symbol,
        "verdict": verdict,
        "raw_score": raw_score,
        "confidence": confidence,
        "active_nodes": 8,
        "total_nodes": 9,
    }


def _drive(monkeypatch, rows_by_symbol: dict[str, dict]) -> dict:
    symbols = list(rows_by_symbol)
    snapshot = {
        "holdings": [],
        "watchlist": [{"symbol": s, "last_trade_price": 100.0} for s in symbols],
        "equity_summary": {"portfolio_equity": 100_000.0},
    }
    monkeypatch.setattr(autopilot, "_ensure_logged_in", lambda *a, **k: True)
    monkeypatch.setattr(autopilot, "_load_data_fetcher", lambda: lambda _syms: snapshot)
    monkeypatch.setattr(
        autopilot,
        "_load_consensus",
        lambda: lambda sym, **kw: rows_by_symbol[sym],
    )
    monkeypatch.setattr(autopilot, "alert_scan_summary", lambda *a, **k: None)
    autopilot.pre_market_scan()
    out = dict(autopilot._CANDIDATES)
    autopilot._CANDIDATES.clear()
    return out


def test_weak_buy_below_floor_is_dropped(sandbox_db, monkeypatch):
    cands = _drive(monkeypatch, {"WEAK": _row("WEAK", "BUY", 0.26)})
    assert "WEAK" not in cands  # 0.26 < 0.50 floor — near-coin-flip, no entry


def test_solid_buy_at_floor_is_kept(sandbox_db, monkeypatch):
    cands = _drive(monkeypatch, {"SOLID": _row("SOLID", "BUY", 0.50)})
    assert "SOLID" in cands


def test_sell_is_never_gated_by_the_floor(sandbox_db, monkeypatch):
    cands = _drive(monkeypatch, {"EXIT": _row("EXIT", "SELL", -0.26)})
    assert "EXIT" in cands  # managed exit — conviction floor applies to entries only


def _must_not_run(label: str):
    """Return a *run* callback that must never be invoked.

    ``pre_market_scan`` calls ``_load_data_fetcher()`` / ``_load_consensus()``
    (the import loaders) *before* the auth and empty-watchlist early returns.
    The loaders themselves are therefore expected to run; only the returned
    fetch / consensus functions must stay unused. Stubbing the loader to throw
    is a false failure — GHA 35333370555.
    """

    def _run(*_a, **_k):
        raise RuntimeError(label)

    return _run


def test_empty_watchlist_does_not_wipe_prior_candidates(sandbox_db, monkeypatch):
    """A blank watchlist returns before the atomic swap — yesterday's book stays."""
    prior = TradeIntent(
        symbol="HOOD", action="BUY", confidence=0.9, source="consensus", target_position_pct=0.02
    )
    autopilot._CANDIDATES["HOOD"] = (prior, {"verdict": "BUY"})
    monkeypatch.setattr(autopilot, "_ensure_logged_in", lambda *a, **k: True)
    monkeypatch.setattr(
        autopilot,
        "_load_data_fetcher",
        lambda: lambda _s: {"holdings": [], "watchlist": [], "equity_summary": {}},
    )
    monkeypatch.setattr(autopilot, "_load_consensus", lambda: _must_not_run("unused"))
    monkeypatch.setattr(autopilot, "alert_scan_summary", lambda *a, **k: None)

    autopilot.pre_market_scan()
    assert "HOOD" in autopilot._CANDIDATES
    autopilot._CANDIDATES.clear()


def test_auth_failure_does_not_wipe_prior_candidates(sandbox_db, monkeypatch):
    prior = TradeIntent(
        symbol="HOOD", action="BUY", confidence=0.9, source="consensus", target_position_pct=0.02
    )
    autopilot._CANDIDATES["HOOD"] = (prior, {"verdict": "BUY"})
    monkeypatch.setattr(autopilot, "_ensure_logged_in", lambda *a, **k: False)
    monkeypatch.setattr(
        autopilot,
        "_load_data_fetcher",
        lambda: _must_not_run("must not fetch after auth fail"),
    )

    autopilot.pre_market_scan()
    assert "HOOD" in autopilot._CANDIDATES
    autopilot._CANDIDATES.clear()


def test_completed_hold_scan_replaces_prior_candidates(sandbox_db, monkeypatch):
    """A finished scan with no actionable names IS the new book — wipe, don't keep ghosts."""
    prior = TradeIntent(
        symbol="OLD", action="BUY", confidence=0.9, source="consensus", target_position_pct=0.02
    )
    autopilot._CANDIDATES["OLD"] = (prior, {"verdict": "BUY"})
    cands = _drive(monkeypatch, {"FLAT": _row("FLAT", "HOLD", 0.10)})
    assert cands == {}
    assert "OLD" not in cands


def test_precision_overlay_blocks_buy_even_above_floor(sandbox_db, monkeypatch):
    class _Block:
        blocked = True
        reason = "Rel.Strength bearish"

    monkeypatch.setattr(autopilot, "precision_overlay_blocks", lambda action, row=None: _Block())
    cands = _drive(monkeypatch, {"NVDA": _row("NVDA", "STRONG BUY", 0.70)})
    assert "NVDA" not in cands
