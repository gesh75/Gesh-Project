"""The paper chain must feed LOSS_STREAK the closes it already stored."""

from __future__ import annotations

import datetime as dt

import pytest

from trading import extra_gates, risk_gate, state
from trading.config import RiskConfig
from trading.contract import TradeIntent
from trading.entry_inputs import loss_days_from_closes, stamp_entry_inputs

TOTAL = 100_000.0


def _cfg(**kw) -> RiskConfig:
    base = dict(
        paper_mode=True, market_regime_gate=False, market_open_hhmm="00:00",
        market_close_hhmm="23:59", min_confidence=0.0, max_position_pct=1.0,
        max_orders_per_day=9999, min_cash_buffer_usd=0.0, max_daily_loss_pct=9.0,
        max_price_age_seconds=10**9, max_price_age_seconds_paper=10**9,
        max_sector_pct=0.40, max_consecutive_loss_days=3, earnings_proximity_hours=48,
    )
    base.update(kw)
    return RiskConfig(**base)


def _snap(holdings=None, **extra) -> dict:
    snapshot = {
        "equity_summary": {
            "portfolio_equity": TOTAL, "cash": TOTAL,
            "adjusted_equity_previous_close": TOTAL,
        },
        "as_of": dt.datetime.now(dt.timezone.utc).isoformat(),
        "holdings": holdings or [],
    }
    snapshot.update(extra)
    return snapshot


def _buy(sym: str = "NVDA", target: float = 0.05) -> TradeIntent:
    return TradeIntent(sym, "BUY", confidence=0.9, source="consensus", target_position_pct=target)


@pytest.fixture(autouse=True)
def _sandbox(monkeypatch, tmp_path):
    monkeypatch.setattr(state, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(risk_gate, "_within_market_hours", lambda cfg: True)
    monkeypatch.setattr(risk_gate, "paper_realized_pnl_usd_today", lambda: 0.0)


def test_loss_days_sum_a_day_and_skip_open_and_undated():
    rows = [
        {"outcome": "LOSS", "exit_time": "2026-10-01T20:00:00+00:00", "realized_pnl": -4.0},
        {"outcome": "WIN", "exit_time": "2026-10-01T21:00:00+00:00", "realized_pnl": 1.0},
        {"outcome": "LOSS", "exit_time": "2026-10-02T20:00:00+00:00", "realized_pnl": -2.0},
        {"outcome": "OPEN", "exit_time": None, "realized_pnl": None},
        {"outcome": "LOSS", "exit_time": "", "realized_pnl": -9.0},
    ]
    days = loss_days_from_closes(rows)
    assert days == [
        {"date": "2026-10-01", "pnl_pct": -3.0},
        {"date": "2026-10-02", "pnl_pct": -2.0},
    ]


def test_three_losing_days_block_a_buy_once_stamped():
    closes = [
        {"outcome": "LOSS", "exit_time": f"2026-10-0{n}T20:00:00+00:00", "realized_pnl": -1.0}
        for n in (1, 2, 3)
    ]
    snapshot = stamp_entry_inputs(_snap(), closes=closes)
    assert "LOSS_STREAK" not in extra_gates.unchecked_gates(_buy(), snapshot)
    assert "EARNINGS_PROXIMITY" in extra_gates.unchecked_gates(_buy(), snapshot)
    assert "CORRELATION_HEAT" in extra_gates.unchecked_gates(_buy(), snapshot)
    verdict = risk_gate.evaluate(_buy(), snapshot, _cfg())
    assert verdict.gates["LOSS_STREAK"] is False
    assert verdict.approved is False


def test_stamp_does_not_invent_earnings_or_correlation():
    snapshot = stamp_entry_inputs(_snap(), closes=[], engine={"holdings": []})
    assert "earnings_hours" not in snapshot
    assert "correlation" not in snapshot
    assert "daily_pnl_history" not in snapshot


def test_stamp_passes_through_engine_maps_without_overwriting():
    given = {"earnings_hours": {"NVDA": 10.0}}
    snapshot = stamp_entry_inputs(
        _snap(**given),
        engine={"earnings_hours": {"NVDA": 1.0}, "correlation": {"NVDA": 4}},
    )
    assert snapshot["earnings_hours"] == {"NVDA": 10.0}
    assert snapshot["correlation"] == {"NVDA": 4}
