"""close_risk_check stop-loss sweep (audit PR2).

The old sweep was dead twice: data_fetcher holdings never carry a stop_loss
field, and the SELL intent it built omitted target_position_pct so the gate
rejected it as malformed. The fixed sweep reads the PAPER book + stops recorded
at fill time and emits a VALID full-close SELL.
"""
from __future__ import annotations

import types

import pytest

from trading import autopilot, state


@pytest.fixture
def sandbox_db(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "DB_PATH", tmp_path / "test_state.db")
    state.init_trading_tables()
    return state


def _wire(monkeypatch, snapshot, captured):
    monkeypatch.setattr(autopilot, "_ensure_logged_in", lambda: True)
    monkeypatch.setattr(autopilot, "_load_data_fetcher", lambda: (lambda syms: snapshot))
    monkeypatch.setattr(autopilot, "alert_daily_pnl", lambda *a, **k: None)
    monkeypatch.setattr(
        autopilot, "evaluate",
        lambda intent, snap, cfg=None: types.SimpleNamespace(
            approved=True, reason=None, gates={}, intent_id=1, decision_id=1),
    )
    monkeypatch.setattr(
        autopilot, "place",
        lambda intent, verdict, snap, cfg=None: captured.append(intent),
    )


def _snapshot(price: float) -> dict:
    return {
        "as_of": "2026-07-02T15:55:00+00:00",
        "equity_summary": {
            "portfolio_equity": 10_000.0,
            "adjusted_equity_previous_close": 10_000.0,
        },
        "holdings": [],
        "watchlist": [{"symbol": "HOOD", "last_trade_price": price}],
    }


def test_breached_paper_stop_emits_valid_full_close_sell(sandbox_db, monkeypatch):
    sandbox_db.log_order(
        intent_id=1, decision_id=None, mode="paper", client_order_id="t-buy",
        status="SIMULATED", symbol="HOOD", action="BUY",
        qty=5.0, target_price=100.0, fill_price=100.0, fill_qty=5.0,
    )
    sandbox_db.record_stop(symbol="HOOD", entry_price=100.0, stop_price=95.0)

    captured: list = []
    _wire(monkeypatch, _snapshot(price=90.0), captured)  # 90 < 95 → breach
    monkeypatch.setenv("PAPER_MODE", "true")

    autopilot.close_risk_check()

    assert len(captured) == 1
    intent = captured[0]
    assert intent.action == "SELL"
    assert intent.target_position_pct == 0.0
    assert intent.source == "stop_sweep"
    assert intent.validate() == []  # must survive the gate's INTENT_VALID check


def test_no_breach_no_sell(sandbox_db, monkeypatch):
    sandbox_db.log_order(
        intent_id=1, decision_id=None, mode="paper", client_order_id="t-buy2",
        status="SIMULATED", symbol="HOOD", action="BUY",
        qty=5.0, target_price=100.0, fill_price=100.0, fill_qty=5.0,
    )
    sandbox_db.record_stop(symbol="HOOD", entry_price=100.0, stop_price=95.0)

    captured: list = []
    _wire(monkeypatch, _snapshot(price=97.0), captured)  # above stop
    monkeypatch.setenv("PAPER_MODE", "true")

    autopilot.close_risk_check()
    assert captured == []


def test_paper_mode_skips_live_holdings_sweep(sandbox_db, monkeypatch):
    """Audit fix: in PAPER mode the legacy real-holdings sweep must NOT run — a broker
    holding that DID carry a stop_loss must not emit a 'live'-book intent from a paper run.
    The paper-book sweep above is authoritative in paper mode (here it's empty)."""
    snap = {
        "as_of": "2026-07-02T15:55:00+00:00",
        "equity_summary": {"portfolio_equity": 10_000.0,
                           "adjusted_equity_previous_close": 10_000.0},
        "holdings": [{"symbol": "AAPL", "current_price": 90.0, "stop_loss": 95.0}],  # breached
        "watchlist": [],
    }
    captured: list = []
    _wire(monkeypatch, snap, captured)
    monkeypatch.setenv("PAPER_MODE", "true")

    autopilot.close_risk_check()
    assert captured == []          # live sweep skipped in paper mode; no paper position for AAPL


def test_restored_candidates_survive_restart(sandbox_db, monkeypatch):
    """pre_market_scan persists candidate symbols; open_window restores them
    after a process restart (the in-memory _CANDIDATES dict is wiped)."""
    import json
    from datetime import datetime
    from zoneinfo import ZoneInfo

    today = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    state.set_marker("premarket_candidates", json.dumps({"date": today, "symbols": ["HOOD", "NVDA"]}))

    autopilot._CANDIDATES.clear()
    autopilot._restore_candidates()
    assert set(autopilot._CANDIDATES) == {"HOOD", "NVDA"}
    autopilot._CANDIDATES.clear()


def test_paper_book_read_failure_skips_sweep_and_pages(sandbox_db, monkeypatch):
    """A book-read exception must not silently skip stops without an alert."""
    sandbox_db.log_order(
        intent_id=1, decision_id=None, mode="paper", client_order_id="t-buy-fail",
        status="SIMULATED", symbol="HOOD", action="BUY",
        qty=5.0, target_price=100.0, fill_price=100.0, fill_qty=5.0,
    )
    sandbox_db.record_stop(symbol="HOOD", entry_price=100.0, stop_price=95.0)

    captured: list = []
    alerts: list = []
    _wire(monkeypatch, _snapshot(price=90.0), captured)
    monkeypatch.setenv("PAPER_MODE", "true")
    monkeypatch.setattr(
        autopilot,
        "paper_open_positions",
        lambda: (_ for _ in ()).throw(RuntimeError("db locked")),
    )
    monkeypatch.setattr(
        autopilot, "alert_error", lambda job, msg: alerts.append((job, msg))
    )

    autopilot.close_risk_check()

    assert captured == []
    assert len(alerts) == 1
    assert alerts[0][0] == "close_risk_check"
    assert "stop sweep skipped" in alerts[0][1]


def test_stale_candidates_are_not_restored(sandbox_db):
    import json

    state.set_marker("premarket_candidates", json.dumps({"date": "2020-01-01", "symbols": ["OLD"]}))
    autopilot._CANDIDATES.clear()
    autopilot._restore_candidates()
    assert autopilot._CANDIDATES == {}
