"""Execution tests for run_paper_gate2.build_action_plan.

The daily chain's share counts and override rules live here: Rule 8 ($500
cash buffer), Rule 5 (RSI >= 70 ADD→HOLD), skip-re-entry on an already-open
daily-analysis paper position, floor() on ADD, ceil()+quantity-clamp on TRIM.
Wrong math here writes the wrong paper fills and poisons the win-rate book.

No network, no DB — build_action_plan is pure.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "run_paper_gate2.py"


def _module_is_import_safe() -> bool:
    tree = ast.parse(SCRIPT.read_text())
    return any(
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "__name__"
        for node in tree.body
    )


@pytest.fixture(scope="module")
def gate_mod():
    if not _module_is_import_safe():
        pytest.fail(
            "run_paper_gate2.py executes its script body at import time "
            "(no __main__ guard) — build_action_plan cannot be imported safely"
        )
    return importlib.import_module("run_paper_gate2")


def _holding(symbol="NVDA", price=100.0, equity=1000.0, quantity=10.0, **extra):
    row = {"symbol": symbol, "price": price, "equity": equity, "quantity": quantity}
    row.update(extra)
    return row


def _plan(gate_mod, holdings, kelly, *, total_eq=10_000.0, cash=2_000.0,
          rsi=None, open_syms=None):
    return gate_mod.build_action_plan(
        holdings,
        kelly,
        total_eq=total_eq,
        cash=cash,
        rsi_by_sym=rsi,
        open_paper_symbols=open_syms,
    )


def _row(plan, symbol):
    matches = [r for r in plan if r["symbol"] == symbol]
    assert len(matches) == 1, f"expected one row for {symbol}, got {plan}"
    return matches[0]


# ── Rule 8: cash buffer ──────────────────────────────────────────────────────
def test_action_plan_cash_below_buffer_blocks_add(gate_mod):
    plan = _plan(
        gate_mod,
        [_holding()],
        {"NVDA": {"action": "ADD", "optimal_pct": 25}},
        cash=400.0,
    )
    row = _row(plan, "NVDA")
    assert row["action"] == "HOLD"
    assert row["shares"] == 0
    assert "Rule 8" in row["signal_reason"]
    assert "$500" in row["signal_reason"]


def test_action_plan_cash_at_buffer_sizes_zero_not_a_buy(gate_mod):
    """cash == $500 is not Rule-8-blocked, but the buffer leaves $0 deployable."""
    plan = _plan(
        gate_mod,
        [_holding()],
        {"NVDA": {"action": "ADD", "optimal_pct": 25}},
        cash=500.0,
    )
    assert plan == [], (
        "cash at the $500 floor must not emit a BUY (buffer leaves $0) "
        "and must not fire the Rule 8 HOLD (cash < 500 is false)"
    )


# ── Rule 5: RSI override ─────────────────────────────────────────────────────
def test_action_plan_rsi_overbought_overrides_add(gate_mod):
    plan = _plan(
        gate_mod,
        [_holding()],
        {"NVDA": {"action": "ADD", "optimal_pct": 25}},
        rsi={"NVDA": 72.0},
    )
    row = _row(plan, "NVDA")
    assert row["action"] == "HOLD"
    assert row["shares"] == 0
    assert "Rule 5" in row["signal_reason"]
    assert "72" in row["signal_reason"]


def test_action_plan_rsi_at_70_overrides_add(gate_mod):
    plan = _plan(
        gate_mod,
        [_holding()],
        {"NVDA": {"action": "BUY", "optimal_pct": 25}},
        rsi={"NVDA": 70.0},
    )
    assert _row(plan, "NVDA")["action"] == "HOLD"


def test_action_plan_rsi_69_does_not_override(gate_mod):
    plan = _plan(
        gate_mod,
        [_holding()],
        {"NVDA": {"action": "ADD", "optimal_pct": 25}},
        rsi={"NVDA": 69.0},
        cash=5_000.0,
    )
    assert _row(plan, "NVDA")["action"] == "BUY"


# ── skip re-entry ────────────────────────────────────────────────────────────
def test_action_plan_open_paper_skips_reentry(gate_mod):
    plan = _plan(
        gate_mod,
        [_holding()],
        {"NVDA": {"action": "ADD", "optimal_pct": 25}},
        open_syms={"NVDA"},
        cash=5_000.0,
    )
    row = _row(plan, "NVDA")
    assert row["action"] == "HOLD"
    assert row["shares"] == 0
    assert "already holding" in row["signal_reason"]


def test_action_plan_open_paper_match_is_casefold(gate_mod):
    plan = _plan(
        gate_mod,
        [_holding(symbol="nvda")],
        {"nvda": {"action": "ADD", "optimal_pct": 25}},
        open_syms={"NVDA"},
    )
    assert _row(plan, "nvda")["action"] == "HOLD"


def test_action_plan_open_skip_beats_cash_and_rsi(gate_mod):
    """Re-entry skip is first: the reason must not be Rule 8 / Rule 5."""
    plan = _plan(
        gate_mod,
        [_holding()],
        {"NVDA": {"action": "ADD", "optimal_pct": 25}},
        open_syms={"NVDA"},
        cash=100.0,
        rsi={"NVDA": 80.0},
    )
    assert "already holding" in _row(plan, "NVDA")["signal_reason"]


# ── ADD / BUY sizing ─────────────────────────────────────────────────────────
def test_action_plan_add_sizes_floor_after_cash_buffer(gate_mod):
    # need 25% of $10k = $2500; held $1000 → $1500; cash $2000 − $500 = $1500
    # floor(1500 / 100) = 15
    plan = _plan(
        gate_mod,
        [_holding(equity=1000.0, price=100.0)],
        {"NVDA": {"action": "ADD", "optimal_pct": 25}},
        total_eq=10_000.0,
        cash=2_000.0,
    )
    row = _row(plan, "NVDA")
    assert row["action"] == "BUY"
    assert row["shares"] == 15
    assert row["current_price"] == 100.0
    assert "Kelly ADD" in row["signal_reason"]


def test_action_plan_add_and_buy_both_emit_buy(gate_mod):
    holdings = [
        _holding("AAA", equity=0.0, price=50.0, quantity=0),
        _holding("BBB", equity=0.0, price=50.0, quantity=0),
    ]
    kelly = {
        "AAA": {"action": "ADD", "optimal_pct": 10},
        "BBB": {"action": "BUY", "optimal_pct": 10},
    }
    plan = _plan(gate_mod, holdings, kelly, total_eq=10_000.0, cash=5_000.0)
    assert {r["symbol"]: r["action"] for r in plan} == {"AAA": "BUY", "BBB": "BUY"}
    # 10% of 10k = 1000; floor(1000/50) = 20; cash buffer leaves 4500
    assert all(r["shares"] == 20 for r in plan)


def test_action_plan_zero_price_is_skipped(gate_mod):
    plan = _plan(
        gate_mod,
        [_holding(price=0.0)],
        {"NVDA": {"action": "ADD", "optimal_pct": 25}},
    )
    assert plan == []


def test_action_plan_missing_symbol_is_skipped(gate_mod):
    plan = _plan(
        gate_mod,
        [{"symbol": "", "price": 10.0, "equity": 100.0, "quantity": 10}],
        {"": {"action": "ADD", "optimal_pct": 25}},
    )
    assert plan == []


def test_action_plan_hold_emits_nothing(gate_mod):
    plan = _plan(
        gate_mod,
        [_holding()],
        {"NVDA": {"action": "HOLD", "optimal_pct": 10}},
    )
    assert plan == []


# ── TRIM sizing + fractional clamp ───────────────────────────────────────────
def test_action_plan_fractional_trim_clamped_to_quantity(gate_mod):
    # equity $40 on $10k, opt 0% → usd=40; ceil(40/100)=1; qty=0.4 → clamp 0.4
    plan = _plan(
        gate_mod,
        [_holding(equity=40.0, price=100.0, quantity=0.4)],
        {"NVDA": {"action": "TRIM", "optimal_pct": 0}},
        total_eq=10_000.0,
    )
    row = _row(plan, "NVDA")
    assert row["action"] == "TRIM"
    assert row["shares"] == pytest.approx(0.4)
    assert "Kelly TRIM" in row["signal_reason"]


def test_action_plan_trim_ceil_then_whole_share(gate_mod):
    # equity $1500, opt 10% of $10k = $1000 → usd=500; ceil(500/50)=10
    plan = _plan(
        gate_mod,
        [_holding("MU", price=50.0, equity=1500.0, quantity=30)],
        {"MU": {"action": "TRIM", "optimal_pct": 10}},
        total_eq=10_000.0,
    )
    row = _row(plan, "MU")
    assert row["action"] == "TRIM"
    assert row["shares"] == 10


def test_action_plan_trim_zero_quantity_emits_nothing(gate_mod):
    plan = _plan(
        gate_mod,
        [_holding(equity=1500.0, quantity=0)],
        {"NVDA": {"action": "TRIM", "optimal_pct": 0}},
    )
    assert plan == []
