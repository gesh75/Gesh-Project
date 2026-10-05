"""
Tests for the paper-mode risk-gate guards ported from gesh-project-trading
(rules #32 CASH_FLOOR and #36 DAILY_LOSS).

Tests the gate helpers directly — never evaluate() — so nothing is written to
the production gesh_financial.db (trading repo rule #39).
"""
from __future__ import annotations

import pytest

from trading import risk_gate
from trading.config import RiskConfig
from trading.contract import manual as manual_intent


def _cfg(paper: bool) -> RiskConfig:
    return RiskConfig(paper_mode=paper, paper_cash_usd=100_000.0)


# Snapshot shape produced by fetch_live_snapshot.py: portfolio_equity is
# positions-only; the real account is nearly fully invested.
PORTFOLIO = {
    "equity_summary": {
        "portfolio_equity": 36_000.0,
        "cash": 600.0,
        "adjusted_equity_previous_close": 37_000.0,
    },
}


# ── Rule #32 — CASH_FLOOR ─────────────────────────────────────────────────────

def test_paper_cash_floor_uses_simulated_cash():
    intent = manual_intent("NVDA", "BUY", target_position_pct=0.02)
    post = risk_gate._post_trade_cash_estimate(PORTFOLIO, intent, _cfg(paper=True))
    # 100_000 - 0.02 * 36_000 = 99_280 — the real $600 cash must be irrelevant
    assert post == pytest.approx(100_000.0 - 720.0)
    assert post >= 500.0  # would pass the default floor


def test_live_cash_floor_uses_real_cash():
    intent = manual_intent("NVDA", "BUY", target_position_pct=0.02)
    post = risk_gate._post_trade_cash_estimate(PORTFOLIO, intent, _cfg(paper=False))
    # 600 - 720 = -120 — live mode must still gate on actual capital
    assert post == pytest.approx(600.0 - 720.0)
    assert post < 500.0  # would (correctly) fail the default floor


def test_sell_returns_cash_unchanged():
    intent = manual_intent("NVDA", "SELL", target_position_pct=0.0)
    assert risk_gate._post_trade_cash_estimate(PORTFOLIO, intent, _cfg(paper=True)) == 100_000.0
    assert risk_gate._post_trade_cash_estimate(PORTFOLIO, intent, _cfg(paper=False)) == 600.0


# ── Rule #36 — DAILY_LOSS ─────────────────────────────────────────────────────

def test_paper_daily_loss_reads_paper_book_not_real_account(monkeypatch):
    """A real red day must NOT block paper entries — only the paper book's own
    realized loss counts. (The unfixed gate read the real account: a -2.7% real
    day, or the positions-vs-account baseline bias, rejected every paper BUY.)"""
    monkeypatch.setattr(risk_gate, "paper_realized_pnl_usd_today", lambda: 0.0)
    pnl = risk_gate._daily_pnl_pct(PORTFOLIO, _cfg(paper=True))
    assert pnl == 0.0  # flat paper day → gate passes regardless of the real account


def test_paper_daily_loss_trips_on_paper_losses(monkeypatch):
    monkeypatch.setattr(risk_gate, "paper_realized_pnl_usd_today", lambda: -1_500.0)
    pnl = risk_gate._daily_pnl_pct(PORTFOLIO, _cfg(paper=True))
    assert pnl == pytest.approx(-0.015)  # -1.5% of the $100k paper base → would trip the 1% gate


def test_live_daily_loss_uses_account_equity_change():
    pnl = risk_gate._daily_pnl_pct(PORTFOLIO, _cfg(paper=False))
    assert pnl == pytest.approx((36_000.0 - 37_000.0) / 37_000.0)


@pytest.mark.parametrize(
    "summary",
    [
        {"portfolio_equity": 36_000.0},  # key absent
        {"portfolio_equity": 36_000.0, "adjusted_equity_previous_close": 0.0},
        {"portfolio_equity": 36_000.0, "adjusted_equity_previous_close": None},
        {"portfolio_equity": 36_000.0, "adjusted_equity_previous_close": -5.0},
        {"portfolio_equity": 36_000.0, "adjusted_equity_previous_close": float("nan")},
        {"portfolio_equity": 36_000.0, "adjusted_equity_previous_close": float("inf")},
        {"portfolio_equity": 36_000.0, "adjusted_equity_previous_close": True},
        {"portfolio_equity": 36_000.0, "adjusted_equity_previous_close": "n/a"},
        {"portfolio_equity": 0.0, "adjusted_equity_previous_close": 37_000.0},
        {"portfolio_equity": None, "adjusted_equity_previous_close": 37_000.0},
    ],
)
def test_live_daily_loss_unknown_baseline_is_not_flat(summary):
    """A missing/zero/garbage baseline (or current equity) is UNKNOWN, never a 0% day.

    The unfixed gate returned 0.0 here, so DAILY_LOSS silently passed in LIVE mode
    exactly when the data feed was broken.
    """
    assert risk_gate._daily_pnl_pct({"equity_summary": summary}, _cfg(paper=False)) is None


def _live_gate_cfg() -> RiskConfig:
    return RiskConfig(paper_mode=False, paper_cash_usd=100_000.0)


def _wire_gate_to_daily_loss(monkeypatch, pnl):
    """Isolate the DAILY_LOSS branch of evaluate() — no DB writes, other gates open."""
    monkeypatch.setattr(risk_gate, "log_intent", lambda _d: 1)
    monkeypatch.setattr(risk_gate, "log_decision", lambda *a, **k: 1)
    monkeypatch.setattr(risk_gate, "_kill_switch_present", lambda _p: False)
    monkeypatch.setattr(risk_gate, "_within_market_hours", lambda _c: True)
    monkeypatch.setattr(risk_gate, "_market_regime_blocks_buy", lambda _i, _c: (False, ""))
    monkeypatch.setattr(risk_gate, "count_orders_today", lambda: 0)
    monkeypatch.setattr(risk_gate, "_post_trade_cash_estimate", lambda *_a: 1_000_000.0)
    monkeypatch.setattr(risk_gate, "_daily_pnl_pct", lambda _p, _c: pnl)
    monkeypatch.setattr(risk_gate, "_quote_age_seconds", lambda *_a, **_k: 1.0)


def test_live_unknown_baseline_rejects_buy_with_a_live_reason(monkeypatch):
    _wire_gate_to_daily_loss(monkeypatch, None)
    intent = manual_intent("NVDA", "BUY", target_position_pct=0.01)
    verdict = risk_gate.evaluate(intent, {"equity_summary": {}}, _live_gate_cfg())
    assert verdict.approved is False
    assert verdict.gates["DAILY_LOSS"] is False
    # The old message blamed the *paper ledger* even in LIVE mode.
    assert "paper" not in (verdict.reason or "").lower()
    assert "daily" in (verdict.reason or "").lower()


def test_live_unknown_baseline_does_not_trap_a_sell(monkeypatch):
    """An exit adds no risk; fail-closed must not stop the user closing a position."""
    _wire_gate_to_daily_loss(monkeypatch, None)
    intent = manual_intent("NVDA", "SELL", target_position_pct=0.0)
    verdict = risk_gate.evaluate(intent, {"equity_summary": {}}, _live_gate_cfg())
    assert verdict.gates["DAILY_LOSS"] is True
    assert verdict.approved is True


def test_paper_unavailable_ledger_still_rejects_and_keeps_paper_reason(monkeypatch):
    _wire_gate_to_daily_loss(monkeypatch, None)
    intent = manual_intent("NVDA", "BUY", target_position_pct=0.01)
    verdict = risk_gate.evaluate(
        intent, {"equity_summary": {}}, RiskConfig(paper_mode=True, paper_cash_usd=100_000.0)
    )
    assert verdict.approved is False
    assert "paper ledger unavailable" in (verdict.reason or "").lower()


def test_live_known_loss_still_trips_the_breaker(monkeypatch):
    _wire_gate_to_daily_loss(monkeypatch, -0.5)
    intent = manual_intent("NVDA", "BUY", target_position_pct=0.01)
    verdict = risk_gate.evaluate(intent, {"equity_summary": {}}, _live_gate_cfg())
    assert verdict.approved is False
    assert verdict.gates["DAILY_LOSS"] is False
    assert "Daily P&L" in (verdict.reason or "")


def test_paper_daily_loss_zero_base_fails_safe(monkeypatch):
    monkeypatch.setattr(risk_gate, "paper_realized_pnl_usd_today",
                        lambda: pytest.fail("must not query the book with zero base"))
    cfg = RiskConfig(paper_mode=True, paper_cash_usd=0.0)
    assert risk_gate._daily_pnl_pct(PORTFOLIO, cfg) == 0.0


def test_paper_daily_loss_unavailable_ledger_is_not_flat(monkeypatch):
    """Astra R3: a missing paper book must not look like a 0% healthy day."""
    from gesh_core.trading.state import LedgerUnavailable

    def _boom():
        raise LedgerUnavailable("neither paper book is readable")

    monkeypatch.setattr(risk_gate, "paper_realized_pnl_usd_today", _boom)
    assert risk_gate._daily_pnl_pct(PORTFOLIO, _cfg(paper=True)) is None
