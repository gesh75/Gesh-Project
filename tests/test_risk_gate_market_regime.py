"""MARKET_REGIME (SPY vs 200d MA) is BUY-only and fail-open.

Config loading for MARKET_REGIME_GATE is covered in
``test_strict_config_integration.py``. This file covers the gate's *behavior*:
a downtrend blocks new longs, unknown SPY data does not, and SELL/HOLD are
never blocked. Failures are not cached so a Yahoo blip cannot pin the gate
blind for six hours.
"""

from __future__ import annotations

import datetime as _dt
import sys
import time

import pytest

pytest.importorskip("gesh_core", reason="gesh-core not installed in this environment")

from trading import risk_gate as rg
from trading.config import RiskConfig
from trading.contract import TradeIntent

pytestmark = pytest.mark.unit

FROZEN_NOW = _dt.datetime(2026, 6, 3, 16, 0, 0, tzinfo=_dt.timezone.utc)


class _FrozenDateTime(_dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return FROZEN_NOW.astimezone(tz) if tz else FROZEN_NOW.replace(tzinfo=None)


def _cfg(**overrides) -> RiskConfig:
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
        market_regime_gate=True,
    )
    defaults.update(overrides)
    return RiskConfig(**defaults)


def _snap() -> dict:
    return {
        "as_of": FROZEN_NOW.isoformat(),
        "equity_summary": {
            "cash": 50_000.0,
            "portfolio_equity": 100_000.0,
            "adjusted_equity_previous_close": 100_000.0,
        },
    }


def _buy() -> TradeIntent:
    return TradeIntent(
        symbol="MSTR",
        action="BUY",
        confidence=0.9,
        source="consensus",
        target_position_pct=0.02,
    )


def _sell() -> TradeIntent:
    return TradeIntent(
        symbol="MSTR",
        action="SELL",
        confidence=0.9,
        source="consensus",
        target_position_pct=0.0,
    )


@pytest.fixture(autouse=True)
def _patch_seams(monkeypatch):
    monkeypatch.setattr(rg, "datetime", _FrozenDateTime)
    monkeypatch.setattr(rg, "log_intent", lambda intent: 101)
    monkeypatch.setattr(rg, "log_decision", lambda *a, **k: 202)
    monkeypatch.setattr(rg, "count_orders_today", lambda *a, **k: 0)
    rg._SPY_TREND_CACHE.clear()
    yield
    rg._SPY_TREND_CACHE.clear()


def test_disabled_gate_never_blocks_buy(monkeypatch):
    monkeypatch.setattr(rg, "_spy_above_ma", lambda *a, **k: False)
    verdict = rg.evaluate(_buy(), _snap(), _cfg(market_regime_gate=False))
    assert verdict.approved is True
    assert verdict.gates["MARKET_REGIME"] is True


def test_spy_below_ma_rejects_buy(monkeypatch):
    monkeypatch.setattr(rg, "_spy_above_ma", lambda *a, **k: False)
    verdict = rg.evaluate(_buy(), _snap(), _cfg())
    assert verdict.approved is False
    assert "SPY below its 200d MA" in verdict.reason
    assert verdict.gates["MARKET_REGIME"] is False


def test_unknown_spy_trend_fails_open(monkeypatch):
    monkeypatch.setattr(rg, "_spy_above_ma", lambda *a, **k: None)
    verdict = rg.evaluate(_buy(), _snap(), _cfg())
    assert verdict.approved is True
    assert verdict.gates["MARKET_REGIME"] is True


def test_spy_above_ma_allows_buy(monkeypatch):
    monkeypatch.setattr(rg, "_spy_above_ma", lambda *a, **k: True)
    verdict = rg.evaluate(_buy(), _snap(), _cfg())
    assert verdict.approved is True
    assert verdict.gates["MARKET_REGIME"] is True


def test_sell_is_never_blocked_by_downtrend(monkeypatch):
    monkeypatch.setattr(rg, "_spy_above_ma", lambda *a, **k: False)
    verdict = rg.evaluate(_sell(), _snap(), _cfg())
    assert verdict.approved is True
    assert verdict.gates["MARKET_REGIME"] is True


def test_helper_only_blocks_buy_when_enabled_and_below():
    buy = _buy()
    sell = _sell()
    # Pin the downtrend via cache so this helper never touches Yahoo.
    rg._SPY_TREND_CACHE["v"] = (time.time(), False)
    blocked, why = rg._market_regime_blocks_buy(buy, _cfg())
    assert blocked is True
    assert "no new longs" in why
    assert rg._market_regime_blocks_buy(sell, _cfg()) == (False, "")
    assert rg._market_regime_blocks_buy(buy, _cfg(market_regime_gate=False)) == (False, "")


def test_spy_failure_is_not_cached(monkeypatch):
    calls = {"n": 0}

    def boom(*_a, **_k):
        calls["n"] += 1
        raise RuntimeError("yahoo down")

    fake_ps = type("PS", (), {"yf_download": staticmethod(boom)})
    monkeypatch.setitem(sys.modules, "price_source", fake_ps)
    assert rg._spy_above_ma() is None
    assert rg._spy_above_ma() is None
    assert calls["n"] == 2
    assert "v" not in rg._SPY_TREND_CACHE


def test_successful_spy_result_is_cached(monkeypatch):
    rg._SPY_TREND_CACHE["v"] = (time.time(), True)
    calls = {"n": 0}

    def boom(*_a, **_k):
        calls["n"] += 1
        raise RuntimeError("should not fetch while cached")

    fake_ps = type("PS", (), {"yf_download": staticmethod(boom)})
    monkeypatch.setitem(sys.modules, "price_source", fake_ps)
    assert rg._spy_above_ma() is True
    assert calls["n"] == 0


# ─── snapshot-first: a caller-supplied regime makes the gate pure ─────────────

def _never_fetch(*_a, **_k):
    raise AssertionError("gate must not fetch SPY when the snapshot carries the regime")


def test_snapshot_regime_below_ma_blocks_without_fetch(monkeypatch):
    monkeypatch.setattr(rg, "_spy_above_ma", _never_fetch)
    snap = _snap()
    snap["market_regime"] = {"spy_above_ma": False}
    verdict = rg.evaluate(_buy(), snap, _cfg())
    assert verdict.approved is False
    assert verdict.gates["MARKET_REGIME"] is False


def test_snapshot_regime_above_ma_allows_without_fetch(monkeypatch):
    monkeypatch.setattr(rg, "_spy_above_ma", _never_fetch)
    snap = _snap()
    snap["market_regime"] = {"spy_above_ma": True}
    verdict = rg.evaluate(_buy(), snap, _cfg())
    assert verdict.approved is True
    assert verdict.gates["MARKET_REGIME"] is True


def test_snapshot_regime_unknown_falls_back_to_fetch(monkeypatch):
    calls = {"n": 0}

    def _fetch(*_a, **_k):
        calls["n"] += 1
        return False

    monkeypatch.setattr(rg, "_spy_above_ma", _fetch)
    for snap in (_snap(), {**_snap(), "market_regime": {}},
                 {**_snap(), "market_regime": {"spy_above_ma": None}},
                 {**_snap(), "market_regime": {"spy_above_ma": "yes"}}):
        verdict = rg.evaluate(_buy(), snap, _cfg())
        assert verdict.approved is False
    assert calls["n"] == 4


def test_snapshot_regime_helper_reads_only_real_booleans():
    assert rg._snapshot_spy_above_ma({"market_regime": {"spy_above_ma": True}}) is True
    assert rg._snapshot_spy_above_ma({"market_regime": {"spy_above_ma": False}}) is False
    assert rg._snapshot_spy_above_ma({"market_regime": {"spy_above_ma": 1}}) is None
    assert rg._snapshot_spy_above_ma({"market_regime": "below"}) is None
    assert rg._snapshot_spy_above_ma({}) is None
