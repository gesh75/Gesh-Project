"""P2 entry-conviction floor, product-repo copy (audit PR4 — reconciled fix).

confidence from from_consensus is participation (data coverage), so
pre_market_scan must ALSO gate new BUYs on the consensus raw_score
(cfg.min_entry_score, default 0.35). Mirrors the engine repo's
test_entry_conviction_floor.py so the two autopilot copies can't drift apart.
Fully offline: login, fetcher, consensus, alerts, and the DB are all stubbed.
"""
from __future__ import annotations

import pytest

from trading import autopilot, state


@pytest.fixture
def sandbox_db(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "DB_PATH", tmp_path / "test_state.db")
    state.init_trading_tables()
    return state


def _row(symbol: str, verdict: str, raw_score: float, confidence: float = 89.0) -> dict:
    return {
        "symbol": symbol, "verdict": verdict, "raw_score": raw_score,
        "confidence": confidence, "active_nodes": 8, "total_nodes": 9,
    }


def _drive(monkeypatch, rows_by_symbol: dict[str, dict]) -> dict:
    symbols = list(rows_by_symbol)
    snapshot = {
        "holdings": [],
        "watchlist": [{"symbol": s, "last_trade_price": 100.0} for s in symbols],
        "equity_summary": {"portfolio_equity": 100_000.0},
    }
    monkeypatch.setattr(autopilot, "_ensure_logged_in", lambda *a, **k: True)
    monkeypatch.setattr(autopilot, "_load_data_fetcher", lambda: (lambda _syms: snapshot))
    monkeypatch.setattr(
        autopilot, "_load_consensus",
        lambda: (lambda sym, **kw: rows_by_symbol[sym]),
    )
    monkeypatch.setattr(autopilot, "alert_scan_summary", lambda *a, **k: None)
    autopilot.pre_market_scan()
    out = dict(autopilot._CANDIDATES)
    autopilot._CANDIDATES.clear()
    return out


def test_weak_buy_below_floor_is_dropped(sandbox_db, monkeypatch):
    cands = _drive(monkeypatch, {"WEAK": _row("WEAK", "BUY", 0.26)})
    assert "WEAK" not in cands  # 0.26 < 0.35 floor — near-coin-flip, no entry


def test_solid_buy_at_floor_is_kept(sandbox_db, monkeypatch):
    cands = _drive(monkeypatch, {"SOLID": _row("SOLID", "BUY", 0.40)})
    assert "SOLID" in cands


def test_sell_is_never_gated_by_the_floor(sandbox_db, monkeypatch):
    cands = _drive(monkeypatch, {"EXIT": _row("EXIT", "SELL", -0.26)})
    assert "EXIT" in cands  # managed exit — conviction floor applies to entries only
