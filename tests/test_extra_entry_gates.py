"""M-02: SECTOR_CAP / LOSS_STREAK / CORRELATION_HEAT / EARNINGS_PROXIMITY in the PRODUCT gate.

The engine enforced these; the product config documented them but evaluate() never did.
Properties locked here:
  * BUY only — an exit is never trapped by an entry-risk gate.
  * Inputs are pure snapshot data (no network inside the gate).
  * A gate with NO input passes but is recorded in ``gates_unchecked`` so an inert gate can
    never be mistaken for protection.
  * Garbage input (NaN, bool, str) is "unknown", never a measured safe/unsafe value.
"""

from __future__ import annotations

import json
import math

import pytest

from trading import extra_gates, risk_gate, state
from trading.config import RiskConfig
from trading.contract import TradeIntent

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
    import datetime as dt

    s = {
        "equity_summary": {"portfolio_equity": TOTAL, "cash": TOTAL,
                           "adjusted_equity_previous_close": TOTAL},
        "as_of": dt.datetime.now(dt.timezone.utc).isoformat(),
        "holdings": holdings or [],
    }
    s.update(extra)
    return s


def _buy(sym="NVDA", target=0.05) -> TradeIntent:
    return TradeIntent(sym, "BUY", confidence=0.9, source="consensus", target_position_pct=target)


def _sell(sym="NVDA") -> TradeIntent:
    return TradeIntent(sym, "SELL", confidence=0.9, source="consensus", target_position_pct=0.0)


@pytest.fixture(autouse=True)
def _sandbox(monkeypatch, tmp_path):
    monkeypatch.setattr(state, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(risk_gate, "_within_market_hours", lambda cfg: True)
    monkeypatch.setattr(risk_gate, "paper_realized_pnl_usd_today", lambda: 0.0)


# ── SECTOR_CAP ────────────────────────────────────────────────────────────────
def _tech(equity):  # NVDA/AMD/AVGO are all in the SMH bucket of benchmarks.SECTOR_BENCHMARKS
    return {"symbol": "AMD", "equity": equity}


def test_sector_over_cap_after_buy_is_blocked():
    # 38% already in the semis bucket + a 5% NVDA target = 43% > 40%
    v = risk_gate.evaluate(_buy("NVDA", 0.05), _snap([_tech(38_000)]), _cfg())
    assert v.gates["SECTOR_CAP"] is False
    assert v.approved is False
    assert "Sector exposure" in v.reason


def test_sector_at_or_under_cap_passes():
    v = risk_gate.evaluate(_buy("NVDA", 0.05), _snap([_tech(35_000)]), _cfg())  # 40.0% == cap
    assert v.gates["SECTOR_CAP"] is True


def test_sector_weight_replaces_not_double_counts_the_symbols_own_position():
    """Engine added the target ON TOP of an existing holding. NVDA already 20% -> target 25%
    is a 25% sector weight, not 45%."""
    held = [{"symbol": "NVDA", "equity": 20_000}]
    v = risk_gate.evaluate(_buy("NVDA", 0.25), _snap(held), _cfg())
    assert v.gates["SECTOR_CAP"] is True


def test_unknown_sector_symbol_passes_but_is_recorded_unchecked():
    v = risk_gate.evaluate(_buy("ZZZZ", 0.30), _snap([_tech(38_000)]), _cfg())
    assert v.gates["SECTOR_CAP"] is True
    assert "SECTOR_CAP" in extra_gates.unchecked_gates(_buy("ZZZZ", 0.30), _snap([_tech(38_000)]))


@pytest.mark.parametrize("bad_total", [None, 0, -1, float("nan"), "n/a", True])
def test_sector_with_unusable_total_equity_is_unchecked_not_zero_weight(bad_total):
    snap = _snap([_tech(38_000)])
    snap["equity_summary"]["portfolio_equity"] = bad_total
    assert extra_gates.sector_weight_after_buy(snap, "NVDA", 0.05) is None


def test_sell_is_never_blocked_by_sector_cap():
    v = risk_gate.evaluate(_sell("AMD"), _snap([_tech(90_000)]), _cfg())
    assert v.gates["SECTOR_CAP"] is True


# ── LOSS_STREAK ───────────────────────────────────────────────────────────────
def _hist(*pcts):
    return [{"pnl_pct": p} for p in pcts]


def test_three_straight_loss_days_suspend_buys():
    v = risk_gate.evaluate(_buy(), _snap(daily_pnl_history=_hist(0.4, -0.5, -0.2, -1.0)), _cfg())
    assert v.gates["LOSS_STREAK"] is False
    assert v.approved is False
    assert "consecutive loss days" in v.reason


def test_streak_below_limit_passes():
    v = risk_gate.evaluate(_buy(), _snap(daily_pnl_history=_hist(-0.5, -0.2)), _cfg())
    assert v.gates["LOSS_STREAK"] is True


def test_a_green_day_resets_the_streak():
    v = risk_gate.evaluate(_buy(), _snap(daily_pnl_history=_hist(-1, -1, -1, 0.3, -0.1)), _cfg())
    assert v.gates["LOSS_STREAK"] is True


@pytest.mark.parametrize("bad", [None, "n/a", float("nan"), True])
def test_unknown_day_breaks_the_streak_it_is_not_counted_as_a_loss_or_a_win(bad):
    # The unknown day is the most recent: nothing proves a loss streak is running.
    assert extra_gates.loss_streak(_hist(-1, -1, -1) + [{"pnl_pct": bad}]) == 0


def test_streak_does_not_trap_a_sell():
    v = risk_gate.evaluate(_sell(), _snap(daily_pnl_history=_hist(-1, -1, -1, -1)), _cfg())
    assert v.gates["LOSS_STREAK"] is True


# ── CORRELATION_HEAT ──────────────────────────────────────────────────────────
def test_cluster_over_cap_blocks_buy():
    v = risk_gate.evaluate(_buy("AAPL"), _snap(correlation={"AAPL": 4}), _cfg())
    assert v.gates["CORRELATION_HEAT"] is False and not v.approved
    assert "Correlation cluster" in v.reason


def test_cluster_at_cap_passes():
    assert risk_gate.evaluate(_buy("AAPL"), _snap(correlation={"AAPL": 3}), _cfg()).gates["CORRELATION_HEAT"]


def test_env_override_and_bad_env_degrade_to_default(monkeypatch):
    monkeypatch.setenv("MAX_CORRELATED_POSITIONS", "5")
    assert risk_gate.evaluate(_buy("AAPL"), _snap(correlation={"AAPL": 5}), _cfg()).gates["CORRELATION_HEAT"]
    monkeypatch.setenv("MAX_CORRELATED_POSITIONS", "three")
    assert not risk_gate.evaluate(_buy("AAPL"), _snap(correlation={"AAPL": 4}), _cfg()).gates["CORRELATION_HEAT"]


@pytest.mark.parametrize("bad", ["x", float("nan"), True, None])
def test_garbage_cluster_value_is_unknown_and_unchecked(bad):
    snap = _snap(correlation={"AAPL": bad})
    assert risk_gate.evaluate(_buy("AAPL"), snap, _cfg()).gates["CORRELATION_HEAT"] is True
    assert "CORRELATION_HEAT" in extra_gates.unchecked_gates(_buy("AAPL"), snap)


def test_sell_not_blocked_by_correlation():
    assert risk_gate.evaluate(_sell("AAPL"), _snap(correlation={"AAPL": 9}), _cfg()).gates["CORRELATION_HEAT"]


# ── EARNINGS_PROXIMITY ────────────────────────────────────────────────────────
def test_buy_inside_earnings_window_is_blocked():
    v = risk_gate.evaluate(_buy("NVDA"), _snap(earnings_hours={"NVDA": 20.0}), _cfg())
    assert v.gates["EARNINGS_PROXIMITY"] is False and not v.approved
    assert "Earnings in 20h" in v.reason


@pytest.mark.parametrize("hours", [48.0, 120.0, -5.0, 0.0])  # at window edge, far, past, now
def test_outside_window_or_already_reported_passes(hours):
    assert risk_gate.evaluate(_buy("NVDA"), _snap(earnings_hours={"NVDA": hours}), _cfg()).gates["EARNINGS_PROXIMITY"]


def test_missing_earnings_data_passes_but_is_recorded_unchecked():
    snap = _snap()
    assert risk_gate.evaluate(_buy("NVDA"), snap, _cfg()).gates["EARNINGS_PROXIMITY"] is True
    assert "EARNINGS_PROXIMITY" in extra_gates.unchecked_gates(_buy("NVDA"), snap)


def test_sell_before_earnings_is_allowed():
    assert risk_gate.evaluate(_sell("NVDA"), _snap(earnings_hours={"NVDA": 2.0}), _cfg()).gates["EARNINGS_PROXIMITY"]


# ── audit trail ───────────────────────────────────────────────────────────────
def test_unchecked_gates_are_written_to_the_decision_log():
    v = risk_gate.evaluate(_buy("NVDA"), _snap(), _cfg())
    with state._conn() as con:
        cfg_json = con.execute(
            "SELECT config_json FROM risk_decisions WHERE id=?", (v.decision_id,)
        ).fetchone()[0]
    logged = json.loads(cfg_json)
    assert set(logged["gates_unchecked"]) >= {"CORRELATION_HEAT", "EARNINGS_PROXIMITY"}


def test_fully_annotated_buy_logs_nothing_unchecked():
    snap = _snap([_tech(5_000)], correlation={"NVDA": 1}, earnings_hours={"NVDA": 300.0},
                 daily_pnl_history=_hist(0.1, 0.2))
    assert extra_gates.unchecked_gates(_buy("NVDA"), snap) == []
    assert risk_gate.evaluate(_buy("NVDA"), snap, _cfg()).approved is True


def test_helpers_reject_nan_inf():
    assert math.isnan(float("nan"))  # sanity for the parametrized NaNs above
    assert extra_gates.loss_streak([{"pnl_pct": float("inf")}]) == 0
