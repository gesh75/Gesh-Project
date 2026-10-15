"""
Offline unit tests for trading/risk_gate.py — the 8-gate risk gate.

This is the only path between a TradeIntent and execution, so each gate is
exercised independently and the documented rejection reason is asserted, plus
one all-pass APPROVED case.

Determinism / isolation strategy (no network, no real DB, no broker):
  * DB seams (`log_intent`, `log_decision`, `count_orders_today`) are monkeypatched
    in the risk_gate module namespace -> no sqlite writes.
  * The wall clock is frozen by replacing `risk_gate.datetime` with a fixed
    subclass (Wednesday 16:00 UTC == 12:00 ET, inside market hours) so the
    market-hours + freshness gates are reproducible on any day/timezone.
  * Each gate is driven via a tailored `RiskConfig` passed through `cfg=`.

Gates, in priority order:
  1. KILL_SWITCH  2. MARKET_HOURS  3. CONFIDENCE_FLOOR  4. POSITION_CAP
  5. ORDER_RATE   6. CASH_FLOOR    7. DAILY_LOSS        8. DATA_FRESH
"""

from __future__ import annotations

import datetime as _dt

import pytest

from trading import risk_gate as rg
from trading.config import RiskConfig
from trading.contract import TradeIntent

pytestmark = pytest.mark.unit


# A weekday, mid-session instant: 2026-06-03 is a Wednesday; 16:00 UTC == 12:00 EDT.
FROZEN_NOW = _dt.datetime(2026, 6, 3, 16, 0, 0, tzinfo=_dt.timezone.utc)


class _FrozenDateTime(_dt.datetime):
    """datetime whose now() is pinned to FROZEN_NOW (tz-aware or naive)."""

    @classmethod
    def now(cls, tz=None):
        return FROZEN_NOW.astimezone(tz) if tz else FROZEN_NOW.replace(tzinfo=None)


def _base_config(**overrides) -> RiskConfig:
    """All-pass-friendly config; override one field to trip a specific gate.

    paper_mode is pinned False so CASH_FLOOR / DAILY_LOSS evaluate against the
    snapshot (live-account behavior these tests document). In PAPER mode those
    two gates read cfg.paper_cash_usd / the paper book's realized P&L (rules
    #32/#36), which is covered separately by test_risk_gate_paper_mode.py.
    """
    defaults = dict(
        kill_switch_path="/tmp/__gesh_kill_does_not_exist__",
        market_open_hhmm="09:30",
        market_close_hhmm="16:00",
        market_timezone="America/New_York",
        min_confidence=0.5,
        max_position_pct=0.05,
        max_orders_per_day=10,
        min_cash_buffer_usd=100.0,
        max_daily_loss_pct=0.05,
        max_price_age_seconds=600,
        paper_mode=False,
    )
    defaults.update(overrides)
    return RiskConfig(**defaults)


def _fresh_snapshot(**eq_overrides) -> dict:
    eq = {
        "cash": 50_000.0,
        "portfolio_equity": 100_000.0,
        "adjusted_equity_previous_close": 100_000.0,
    }
    eq.update(eq_overrides)
    return {"as_of": FROZEN_NOW.isoformat(), "equity_summary": eq}


def _buy_intent(confidence: float = 0.9, target_pct: float = 0.02) -> TradeIntent:
    return TradeIntent(
        symbol="MSTR", action="BUY", confidence=confidence,
        source="consensus", target_position_pct=target_pct,
    )


@pytest.fixture(autouse=True)
def _patch_seams(monkeypatch):
    """Freeze the clock and stub every DB write so evaluate() is pure + offline."""
    monkeypatch.setattr(rg, "datetime", _FrozenDateTime)
    monkeypatch.setattr(rg, "log_intent", lambda intent: 101)
    monkeypatch.setattr(rg, "log_decision", lambda *a, **k: 202)
    monkeypatch.setattr(rg, "count_orders_today", lambda *a, **k: 0)


# ─── 0. all-pass ──────────────────────────────────────────────────────────────

def test_all_gates_pass_approved():
    verdict = rg.evaluate(_buy_intent(), _fresh_snapshot(), _base_config())
    assert verdict.approved is True
    assert verdict.reason is None
    assert verdict.intent_id == 101
    assert verdict.decision_id == 202
    # Every real gate recorded a pass.
    for gate in ("INTENT_VALID", "KILL_SWITCH", "MARKET_HOURS", "CONFIDENCE_FLOOR",
                 "POSITION_CAP", "ORDER_RATE", "CASH_FLOOR", "DAILY_LOSS", "DATA_FRESH"):
        assert verdict.gates[gate] is True


# ─── 1. kill switch ───────────────────────────────────────────────────────────

def test_gate_kill_switch(tmp_path, monkeypatch):
    kill = tmp_path / "KILL"
    kill.write_text("stop")
    verdict = rg.evaluate(_buy_intent(), _fresh_snapshot(),
                          _base_config(kill_switch_path=str(kill)))
    assert verdict.approved is False
    assert "Kill switch active" in verdict.reason
    assert verdict.gates["KILL_SWITCH"] is False


# ─── 2. market hours ──────────────────────────────────────────────────────────

def test_gate_market_hours_closed():
    """Window 09:30-09:31 ET excludes the frozen 12:00 ET instant."""
    verdict = rg.evaluate(_buy_intent(), _fresh_snapshot(),
                          _base_config(market_open_hhmm="09:30",
                                       market_close_hhmm="09:31"))
    assert verdict.approved is False
    assert verdict.reason == "Outside market hours"
    assert verdict.gates["MARKET_HOURS"] is False


# ─── 3. confidence floor ──────────────────────────────────────────────────────

def test_gate_confidence_floor():
    verdict = rg.evaluate(_buy_intent(confidence=0.40), _fresh_snapshot(),
                          _base_config(min_confidence=0.75))
    assert verdict.approved is False
    assert "Confidence 0.40 below floor 0.75" in verdict.reason
    assert verdict.gates["CONFIDENCE_FLOOR"] is False


# ─── 4. position cap ──────────────────────────────────────────────────────────

def test_gate_position_cap():
    verdict = rg.evaluate(_buy_intent(target_pct=0.10), _fresh_snapshot(),
                          _base_config(max_position_pct=0.02))
    assert verdict.approved is False
    assert "exceeds cap" in verdict.reason
    assert verdict.gates["POSITION_CAP"] is False


# ─── 5. order rate ────────────────────────────────────────────────────────────

def test_gate_order_rate(monkeypatch):
    monkeypatch.setattr(rg, "count_orders_today", lambda *a, **k: 3)
    verdict = rg.evaluate(_buy_intent(), _fresh_snapshot(),
                          _base_config(max_orders_per_day=3))
    assert verdict.approved is False
    assert "Daily order cap reached (3/3)" in verdict.reason
    assert verdict.gates["ORDER_RATE"] is False


# ─── 6. cash floor ────────────────────────────────────────────────────────────

def test_gate_cash_floor():
    """BUY of 2% of $100k equity from $1,000 cash -> post-trade cash -$1,000."""
    snap = _fresh_snapshot(cash=1_000.0)
    verdict = rg.evaluate(_buy_intent(target_pct=0.02), snap,
                          _base_config(min_cash_buffer_usd=500.0))
    assert verdict.approved is False
    assert "below floor" in verdict.reason
    assert verdict.gates["CASH_FLOOR"] is False


# ─── 7. daily loss ────────────────────────────────────────────────────────────

def test_gate_daily_loss():
    """Equity down 3% vs previous close trips the 1% daily-loss limit."""
    snap = _fresh_snapshot(portfolio_equity=97_000.0,
                           adjusted_equity_previous_close=100_000.0)
    verdict = rg.evaluate(_buy_intent(), snap,
                          _base_config(max_daily_loss_pct=0.01))
    assert verdict.approved is False
    assert "exceeds max loss" in verdict.reason
    assert verdict.gates["DAILY_LOSS"] is False


# ─── 8. data freshness ────────────────────────────────────────────────────────

def test_gate_data_fresh_stale_quote():
    """A snapshot timestamped 1 hour before the frozen now -> stale."""
    stale_ts = (FROZEN_NOW - _dt.timedelta(hours=1)).isoformat()
    snap = _fresh_snapshot()
    snap["as_of"] = stale_ts
    verdict = rg.evaluate(_buy_intent(), snap,
                          _base_config(max_price_age_seconds=300))
    assert verdict.approved is False
    assert "exceeds max" in verdict.reason
    assert verdict.gates["DATA_FRESH"] is False


def test_gate_data_fresh_missing_timestamp():
    """No 'as_of' -> cannot prove freshness -> fail closed."""
    snap = {"equity_summary": {"cash": 50_000.0, "portfolio_equity": 100_000.0,
                               "adjusted_equity_previous_close": 100_000.0}}
    verdict = rg.evaluate(_buy_intent(), snap, _base_config())
    assert verdict.approved is False
    assert "Quote age unknown" in verdict.reason
    assert verdict.gates["DATA_FRESH"] is False


# ─── extra: HOLD shortcut + malformed intent ──────────────────────────────────

def test_hold_intent_short_circuits_approved():
    hold = TradeIntent(symbol="MSTR", action="HOLD", confidence=0.9, source="consensus")
    verdict = rg.evaluate(hold, _fresh_snapshot(), _base_config())
    assert verdict.approved is True
    assert verdict.gates == {"HOLD_SHORTCUT": True}


def test_malformed_intent_rejected_before_gates():
    """BUY without target_position_pct fails validation, never reaching a gate."""
    bad = TradeIntent(symbol="MSTR", action="BUY", confidence=0.9,
                      source="consensus", target_position_pct=None)
    verdict = rg.evaluate(bad, _fresh_snapshot(), _base_config())
    assert verdict.approved is False
    assert "Malformed intent" in verdict.reason
    assert verdict.gates["INTENT_VALID"] is False


def test_malformed_hold_is_rejected_not_shortcut():
    """A HOLD with an out-of-range confidence is malformed. It must not ride the
    HOLD shortcut into the audit ledger as APPROVED (Argon audit, WP1-1)."""
    bad_hold = TradeIntent(symbol="MSTR", action="HOLD", confidence=1.7, source="consensus")
    verdict = rg.evaluate(bad_hold, _fresh_snapshot(), _base_config())
    assert verdict.approved is False
    assert "Malformed intent" in verdict.reason
    assert verdict.gates == {"INTENT_VALID": False}


# ─── extra: skipped gates are named, not silently recorded as failures ────────

def test_skipped_gates_listed_when_an_early_gate_rejects(tmp_path, monkeypatch):
    """After KILL_SWITCH rejects, gates 2-8 are not evaluated. The decision row
    keeps the boolean map shape (False for unevaluated gates) but names them in
    config_snapshot['gates_skipped'] so gate analytics can exclude them."""
    captured: dict = {}

    def _capture(intent_id, verdict, reason, gates, config_snapshot):
        captured.update(gates=dict(gates), cfg=dict(config_snapshot))
        return 202

    monkeypatch.setattr(rg, "log_decision", _capture)
    kill = tmp_path / "KILL"
    kill.write_text("stop")
    verdict = rg.evaluate(_buy_intent(), _fresh_snapshot(),
                          _base_config(kill_switch_path=str(kill)))
    assert verdict.approved is False
    skipped = captured["cfg"]["gates_skipped"]
    for gate in ("MARKET_HOURS", "MARKET_REGIME", "CONFIDENCE_FLOOR", "POSITION_CAP",
                 "ORDER_RATE", "CASH_FLOOR", "DAILY_LOSS", "DATA_FRESH"):
        assert gate in skipped
        assert captured["gates"][gate] is False
    assert "KILL_SWITCH" not in skipped


def test_no_gates_skipped_on_full_pass(monkeypatch):
    captured: dict = {}

    def _capture(intent_id, verdict, reason, gates, config_snapshot):
        captured.update(cfg=dict(config_snapshot))
        return 202

    monkeypatch.setattr(rg, "log_decision", _capture)
    verdict = rg.evaluate(_buy_intent(), _fresh_snapshot(), _base_config())
    assert verdict.approved is True
    assert captured["cfg"]["gates_skipped"] == []


# ─── extra: MARKET_HOURS honours the NYSE calendar ────────────────────────────

def _frozen_at(monkeypatch, instant: _dt.datetime) -> None:
    class _At(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)

    monkeypatch.setattr(rg, "datetime", _At)


def test_market_hours_closed_on_nyse_holiday(monkeypatch):
    # Friday 2026-04-03 is Good Friday: a weekday, mid-session clock, exchange closed.
    _frozen_at(monkeypatch, _dt.datetime(2026, 4, 3, 16, 0, tzinfo=_dt.timezone.utc))
    assert rg._within_market_hours(_base_config()) is False
    verdict = rg.evaluate(_buy_intent(), {"as_of": "2026-04-03T16:00:00+00:00",
                                          "equity_summary": _fresh_snapshot()["equity_summary"]},
                          _base_config())
    assert verdict.approved is False
    assert verdict.gates["MARKET_HOURS"] is False
    assert "market hours" in (verdict.reason or "").lower()


def test_market_hours_early_close_day_after_thanksgiving(monkeypatch):
    # 2026-11-27 14:00 ET (19:00 UTC, EST): inside the regular window, after the 13:00 close.
    _frozen_at(monkeypatch, _dt.datetime(2026, 11, 27, 19, 0, tzinfo=_dt.timezone.utc))
    assert rg._within_market_hours(_base_config()) is False
    # 2026-11-27 12:30 ET (17:30 UTC): before the early close → open.
    _frozen_at(monkeypatch, _dt.datetime(2026, 11, 27, 17, 30, tzinfo=_dt.timezone.utc))
    assert rg._within_market_hours(_base_config()) is True


def test_market_hours_ordinary_weekday_unchanged(monkeypatch):
    _frozen_at(monkeypatch, _dt.datetime(2026, 6, 3, 19, 0, tzinfo=_dt.timezone.utc))  # 15:00 EDT
    assert rg._within_market_hours(_base_config()) is True
