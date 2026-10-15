"""
Offline unit tests for kelly.py — Kelly Criterion position sizer.

Pure-math money logic: every assertion uses exact numbers (or pytest.approx for
floats). No network — `returns` is injected directly, so `_fetch_returns`
(yfinance) is never reached.

Covers:
  * _continuous_kelly  : f* = (mu - r) / sigma^2
  * _discrete_kelly    : f* = W - (1-W)/R, win-rate + payoff ratio
  * half-Kelly cap     : final = clamp(blend * 0.5, 0, MAX_POSITION_PCT)
  * AI-penalty branch   : AVOID/SELL -> 0.5, pct_diff<-15 -> 0.6, pct_diff<-5 -> 0.8
  * action outputs      : AVOID / TRIM / ADD / HOLD
  * error path          : insufficient history
"""

from __future__ import annotations

import numpy as np
import pytest

from kelly import (
    HALF_KELLY_FACTOR,
    MAX_POSITION_PCT,
    _continuous_kelly,
    _discrete_kelly,
    kelly_size_holding,
)

pytestmark = pytest.mark.unit


# ─── Deterministic return fixtures ────────────────────────────────────────────

# 30 wins of +1% and 30 losses of -1%: mean 0, symmetric -> clean discrete math.
SYMMETRIC = np.array([0.01] * 30 + [-0.01] * 30, dtype=float)

# Strongly positive drift -> continuous Kelly large, gets capped at MAX_POSITION_PCT.
POSITIVE_DRIFT = np.array([0.02] * 25 + [0.01] * 25, dtype=float)

# Net-negative drift -> blended Kelly negative -> final clamps to 0 -> AVOID.
NEGATIVE_DRIFT = np.array([-0.02] * 30 + [0.001] * 30, dtype=float)


# ─── _continuous_kelly ────────────────────────────────────────────────────────

def test_continuous_kelly_zero_mean_is_zero():
    """Symmetric returns have mean 0, so f* = (0 - 0)/sigma^2 = 0."""
    out = _continuous_kelly(SYMMETRIC)
    assert out["method"] == "continuous"
    assert out["kelly_pct"] == pytest.approx(0.0, abs=1e-9)


def test_continuous_kelly_zero_sigma_returns_zero():
    """Constant returns -> sigma 0 -> guard returns kelly_pct 0.0 (no div-by-zero)."""
    flat = np.array([0.005] * 40, dtype=float)
    out = _continuous_kelly(flat)
    assert out["kelly_pct"] == 0.0
    assert out["method"] == "continuous"


def test_continuous_kelly_formula_exact():
    """f* = (mu - r) / sigma^2 computed directly against numpy."""
    rng = np.array([0.03, -0.01, 0.02, -0.015, 0.025, -0.005] * 6, dtype=float)
    mu = float(np.mean(rng))
    sig = float(np.std(rng))
    expected = mu / (sig ** 2)
    out = _continuous_kelly(rng)
    assert out["kelly_pct"] == pytest.approx(expected, rel=1e-12)


# ─── _discrete_kelly ──────────────────────────────────────────────────────────

def test_discrete_kelly_symmetric_is_zero_edge():
    """50% win rate with payoff ratio 1.0 -> f* = 0.5 - 0.5/1 = 0.0."""
    out = _discrete_kelly(SYMMETRIC)
    assert out["win_rate"] == 50.0
    assert out["payoff_ratio"] == 1.0
    assert out["kelly_pct"] == pytest.approx(0.0, abs=1e-12)
    assert out["method"] == "discrete"


def test_discrete_kelly_win_rate_and_payoff_exact():
    """40 wins (+2%) / 10 losses (-1%): W=0.8, R=2.0, f*=0.8-0.2/2=0.7."""
    rng = np.array([0.02] * 40 + [-0.01] * 10, dtype=float)
    out = _discrete_kelly(rng)
    assert out["win_rate"] == 80.0
    assert out["payoff_ratio"] == 2.0
    assert out["kelly_pct"] == pytest.approx(0.7, rel=1e-12)


# ─── half-Kelly cap ───────────────────────────────────────────────────────────

def test_half_kelly_factor_and_hard_cap():
    """Large positive Kelly must be halved then clamped to MAX_POSITION_PCT."""
    res = kelly_size_holding(
        "X", current_equity=1_000, total_portfolio=100_000,
        avg_cost=10, current_price=12, returns=POSITIVE_DRIFT,
    )
    # POSITIVE_DRIFT blends to a huge positive Kelly; after *0.5 it still
    # exceeds the 25% cap, so optimal_pct == MAX_POSITION_PCT * 100.
    assert res["optimal_pct"] == pytest.approx(MAX_POSITION_PCT * 100)
    assert HALF_KELLY_FACTOR == 0.5
    assert MAX_POSITION_PCT == 0.25


def test_recommended_usd_and_delta_exact():
    """recommended_usd = optimal_fraction * total; delta = recommended - equity."""
    res = kelly_size_holding(
        "X", current_equity=1_000, total_portfolio=100_000,
        avg_cost=10, current_price=12, returns=POSITIVE_DRIFT,
    )
    assert res["recommended_usd"] == pytest.approx(25_000.0)   # 0.25 * 100k
    assert res["delta_usd"] == pytest.approx(24_000.0)         # 25k - 1k
    assert res["pnl_pct"] == pytest.approx(20.0)               # (12-10)/10*100


# ─── AI-penalty branch ────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "ai_eval, expected_penalty",
    [
        ({"verdict": "AVOID"}, 0.5),
        ({"verdict": "SELL"}, 0.5),
        ({"pct_diff": -20.0}, 0.6),   # severe overvaluation (< -15)
        ({"pct_diff": -8.0}, 0.8),    # slight overvaluation (< -5)
        ({"verdict": "REDUCE"}, 0.8),
        ({"verdict": "BUY", "pct_diff": 5.0}, 1.0),  # no penalty
        (None, 1.0),                  # no AI eval -> neutral
    ],
)
def test_ai_penalty_branches(ai_eval, expected_penalty):
    res = kelly_size_holding(
        "X", current_equity=1_000, total_portfolio=100_000,
        avg_cost=10, current_price=12, returns=POSITIVE_DRIFT, ai_eval=ai_eval,
    )
    assert res["ai_penalty"] == expected_penalty


def test_ai_penalty_shrinks_position():
    """An AVOID verdict must strictly shrink the recommended size (gesh-core
    4f48962: penalty applies after the MAX_POSITION_PCT clamp and AVOID sizes
    to zero, so `<=` would have passed on the old pre-clamp bug)."""
    base = kelly_size_holding(
        "X", 1_000, 100_000, 10, 12, returns=POSITIVE_DRIFT,
    )
    penalised = kelly_size_holding(
        "X", 1_000, 100_000, 10, 12, returns=POSITIVE_DRIFT,
        ai_eval={"verdict": "AVOID"},
    )
    assert base["optimal_pct"] > 0
    assert penalised["optimal_pct"] < base["optimal_pct"]
    assert penalised["optimal_pct"] == 0.0
    assert penalised["action"] != "ADD"


# ─── action outputs: AVOID / TRIM / ADD / HOLD ────────────────────────────────

def test_action_avoid_on_zero_position():
    """Net-negative returns -> final_pct 0 -> action AVOID."""
    res = kelly_size_holding(
        "X", 1_000, 100_000, 10, 9, returns=NEGATIVE_DRIFT,
    )
    assert res["optimal_pct"] == 0.0
    assert res["action"] == "AVOID"
    assert res["action_color"] == "red"


def test_action_add_when_underweight():
    """current_pct (1%) < 0.6 * optimal (25%) -> ADD."""
    res = kelly_size_holding(
        "X", current_equity=1_000, total_portfolio=100_000,
        avg_cost=10, current_price=12, returns=POSITIVE_DRIFT,
    )
    assert res["current_pct"] == pytest.approx(1.0)
    assert res["optimal_pct"] == pytest.approx(25.0)
    assert res["action"] == "ADD"
    assert res["action_color"] == "green"


def test_action_hold_when_near_optimal():
    """current_pct (20%) within [0.6*25, 1.3*25] = [15%, 32.5%] -> HOLD."""
    res = kelly_size_holding(
        "X", current_equity=20_000, total_portfolio=100_000,
        avg_cost=10, current_price=12, returns=POSITIVE_DRIFT,
    )
    assert res["current_pct"] == pytest.approx(20.0)
    assert res["action"] == "HOLD"
    assert res["action_color"] == "cyan"


def test_action_trim_when_overweight():
    """current_pct (40%) > 1.3 * optimal (25%) -> TRIM."""
    res = kelly_size_holding(
        "X", current_equity=40_000, total_portfolio=100_000,
        avg_cost=10, current_price=12, returns=POSITIVE_DRIFT,
    )
    assert res["current_pct"] == pytest.approx(40.0)
    assert res["action"] == "TRIM"
    assert res["action_color"] == "yellow"


# ─── error path ───────────────────────────────────────────────────────────────

def test_insufficient_history_returns_error():
    """< MIN_HISTORY_DAYS rows -> structured error, no sizing."""
    res = kelly_size_holding(
        "X", 1_000, 100_000, 10, 12, returns=np.array([0.01] * 10, dtype=float),
    )
    assert res["error"] == "Insufficient price history"
    assert res["recommended_pct"] is None
    assert res["recommended_usd"] is None
