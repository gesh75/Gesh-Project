"""trading/market_regime.py — stamp the SPY-vs-200d regime on a snapshot once per run.

risk_gate.evaluate() reads ``snapshot["market_regime"]["spy_above_ma"]`` before
its own network fallback. The builders (autopilot jobs, run_paper_gate2) call
``stamp_market_regime`` so normal operation never fetches inside the gate.
"""

from __future__ import annotations

import datetime as _dt

import pytest

pytest.importorskip("gesh_core", reason="gesh-core not installed in this environment")

from trading import market_regime as mr
from trading import risk_gate as rg
from trading.config import RiskConfig
from trading.contract import TradeIntent

pytestmark = pytest.mark.unit


def _cfg(**overrides) -> RiskConfig:
    defaults = dict(market_regime_gate=True, market_regime_ma_days=200)
    defaults.update(overrides)
    return RiskConfig(**defaults)


def test_stamps_bool_from_probe():
    snap: dict = {"holdings": []}
    out = mr.stamp_market_regime(snap, _cfg(), probe=lambda days: False)
    assert out is snap
    assert snap["market_regime"]["spy_above_ma"] is False
    assert snap["market_regime"]["ma_days"] == 200
    assert snap["market_regime"]["source"] == "spy_sma_probe"


def test_probe_receives_configured_ma_days():
    seen: list[int] = []

    def _probe(days: int) -> bool:
        seen.append(days)
        return True

    mr.stamp_market_regime({}, _cfg(market_regime_ma_days=150), probe=_probe)
    assert seen == [150]


def test_unknown_probe_result_leaves_key_absent():
    snap: dict = {}
    mr.stamp_market_regime(snap, _cfg(), probe=lambda days: None)
    assert "market_regime" not in snap


def test_probe_exception_is_swallowed():
    def _boom(days: int) -> bool:
        raise RuntimeError("yahoo down")

    snap: dict = {}
    assert mr.stamp_market_regime(snap, _cfg(), probe=_boom) is snap
    assert "market_regime" not in snap


def test_disabled_gate_does_not_probe():
    def _never(days: int) -> bool:
        raise AssertionError("gate disabled — must not probe")

    snap: dict = {}
    mr.stamp_market_regime(snap, _cfg(market_regime_gate=False), probe=_never)
    assert "market_regime" not in snap


def test_existing_bool_is_not_overwritten():
    def _never(days: int) -> bool:
        raise AssertionError("caller already stamped — must not probe")

    snap = {"market_regime": {"spy_above_ma": True, "source": "caller"}}
    mr.stamp_market_regime(snap, _cfg(), probe=_never)
    assert snap["market_regime"] == {"spy_above_ma": True, "source": "caller"}


def test_non_bool_existing_value_is_replaced():
    snap = {"market_regime": {"spy_above_ma": "yes"}}
    mr.stamp_market_regime(snap, _cfg(), probe=lambda days: False)
    assert snap["market_regime"]["spy_above_ma"] is False


def test_non_dict_snapshot_is_returned_untouched():
    assert mr.stamp_market_regime(None, _cfg(), probe=lambda days: True) is None  # type: ignore[arg-type]


def test_default_probe_is_the_gate_helper():
    assert mr.stamp_market_regime.__defaults__ is None  # keyword-only, no positional default
    assert mr._DEFAULT_PROBE is rg._spy_above_ma


def test_stamped_snapshot_keeps_evaluate_network_free(monkeypatch):
    """End to end: once stamped, evaluate() never calls _spy_above_ma."""
    frozen = _dt.datetime(2026, 6, 3, 16, 0, 0, tzinfo=_dt.timezone.utc)

    class _At(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen.astimezone(tz) if tz else frozen.replace(tzinfo=None)

    monkeypatch.setattr(rg, "datetime", _At)
    monkeypatch.setattr(rg, "log_intent", lambda intent: 1)
    monkeypatch.setattr(rg, "log_decision", lambda *a, **k: 1)
    monkeypatch.setattr(rg, "count_orders_today", lambda *a, **k: 0)

    def _never(*_a, **_k):
        raise AssertionError("evaluate() must not fetch when the snapshot is stamped")

    monkeypatch.setattr(rg, "_spy_above_ma", _never)

    snap = {
        "as_of": frozen.isoformat(),
        "equity_summary": {
            "cash": 50_000.0,
            "portfolio_equity": 100_000.0,
            "adjusted_equity_previous_close": 100_000.0,
        },
    }
    mr.stamp_market_regime(snap, _cfg(), probe=lambda days: False)
    cfg = RiskConfig(
        kill_switch_path="/tmp/__gesh_kill_does_not_exist__",
        min_confidence=0.5, max_position_pct=0.05, max_orders_per_day=10,
        min_cash_buffer_usd=100.0, max_daily_loss_pct=0.05, max_price_age_seconds=600,
        paper_mode=False, market_regime_gate=True,
    )
    buy = TradeIntent(symbol="MSTR", action="BUY", confidence=0.9,
                      source="consensus", target_position_pct=0.02)
    verdict = rg.evaluate(buy, snap, cfg)
    assert verdict.approved is False
    assert verdict.gates["MARKET_REGIME"] is False


# ─── wiring: every snapshot builder stamps before the gate runs ───────────────

def test_every_autopilot_snapshot_is_stamped():
    """Each `snapshot = build_data_payload([])` in the four autopilot jobs is
    followed by `stamp_market_regime(snapshot, cfg)` before any evaluate()."""
    import re
    from pathlib import Path

    src = Path(__file__).resolve().parents[1].joinpath("trading", "autopilot.py").read_text()
    assert "from .market_regime import stamp_market_regime" in src
    builds = [m.end() for m in re.finditer(r"snapshot = build_data_payload\(\[\]\)", src)]
    assert len(builds) == 4, "expected the four scheduled jobs to build a snapshot"
    for end in builds:
        window = src[end:end + 220]
        assert "stamp_market_regime(snapshot, cfg)" in window, window
        assert "evaluate(" not in window.split("stamp_market_regime")[0]


def test_paper_gate_stamps_the_session_payload():
    from pathlib import Path

    src = Path(__file__).resolve().parents[1].joinpath("run_paper_gate2.py").read_text()
    stamp = src.index("stamp_market_regime(gate_payload")
    gate = src.index("gate_results, paper_created = gate_and_execute(")
    assert stamp < gate, "regime must be stamped before the intents are gated"
