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


def test_live_daily_loss_no_baseline_returns_zero():
    pnl = risk_gate._daily_pnl_pct({"equity_summary": {"portfolio_equity": 36_000.0}},
                                   _cfg(paper=False))
    assert pnl == 0.0


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
