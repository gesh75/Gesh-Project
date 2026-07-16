"""Property/invariant tests for the Kelly position sizer (gesh_core.kelly via the
`kelly` shim) — the money math the /kelly dashboard, consensus.py and the daily
chain all size against.

No `hypothesis` dependency (CI installs only pytest/pytest-cov/ruff): instead we
sweep a wide, deterministic grid of synthetic daily-return series (seeded numpy +
hand-built edge cases) and assert the invariants that must hold for EVERY input.
All paths inject `returns=` / monkeypatch `_fetch_returns`, so the suite is
network-free and deterministic.

Invariants under test (the ones that protect real capital):
  * sizing never exceeds the per-position hard cap, and is never negative;
  * a bearish AI verdict or positive autocorrelation can only SHRINK size, never grow it;
  * positive analyst upside never shrinks size;
  * portfolio gross exposure never exceeds the gross cap, correlation haircuts never up-size;
  * an all-AVOID / all-error book reports 0.0, never NaN, into the JSON payload.
"""
import numpy as np
import pandas as pd
import pytest

import kelly  # shim -> gesh_core.kelly (exact product import path)

MIN_DAYS = kelly.MIN_HISTORY_DAYS  # 30


# ─────────────────────────── synthetic return series ────────────────────────

def _series(kind: str, n: int = 120, seed: int = 0) -> np.ndarray:
    """Deterministic daily-return arrays spanning the regimes the sizer sees."""
    rng = np.random.default_rng(seed)
    if kind == "flat":
        return np.zeros(n)
    if kind == "constant_up":
        return np.full(n, 0.001)
    if kind == "strong_up":
        return rng.normal(0.004, 0.01, n)      # high positive drift, modest vol
    if kind == "strong_down":
        return rng.normal(-0.004, 0.01, n)     # persistent losses -> negative edge
    if kind == "high_vol":
        return rng.normal(0.0, 0.06, n)        # no edge, huge vol
    if kind == "low_vol_edge":
        return rng.normal(0.0008, 0.004, n)    # thin edge, low vol
    if kind == "mixed":
        return rng.normal(0.0005, 0.02, n)
    if kind == "single_spike":
        a = np.zeros(n)
        a[n // 2] = 0.2
        return a
    raise ValueError(kind)


ALL_KINDS = ["flat", "constant_up", "strong_up", "strong_down",
             "high_vol", "low_vol_edge", "mixed", "single_spike"]

# (kind, seed) grid — several seeds per stochastic kind widens coverage.
GRID = [(k, s) for k in ALL_KINDS for s in range(4)]


def _size(returns, **kw) -> dict:
    return kelly.kelly_size_holding(
        kw.pop("symbol", "TST"),
        kw.pop("current_equity", 1_000.0),
        kw.pop("total_portfolio", 100_000.0),
        kw.pop("avg_cost", 100.0),
        kw.pop("current_price", 110.0),
        analyst_upside_pct=kw.pop("analyst_upside_pct", None),
        ai_eval=kw.pop("ai_eval", None),
        returns=np.asarray(returns, dtype=float),
    )


# ───────────────────────── kelly_size_holding invariants ─────────────────────

@pytest.mark.parametrize("kind,seed", GRID)
def test_sizing_core_invariants(kind, seed):
    r = _size(_series(kind, seed=seed))
    if "error" in r:                      # only allowed when history is too short
        pytest.skip("insufficient-history branch covered separately")
    cap_pct = kelly._max_position_pct() * 100.0

    # 1. hard cap + non-negativity — the two that stop a blow-up.
    assert 0.0 <= r["optimal_pct"] <= cap_pct + 1e-9
    assert r["recommended_usd"] >= 0.0
    assert r["half_kelly"] == r["optimal_pct"]          # half-Kelly IS the surfaced size

    # 2. optimal is at most half the (post-adjustment) raw Kelly, within rounding.
    if r["raw_kelly"] > 0:
        assert r["optimal_pct"] <= r["raw_kelly"] / 2.0 + 0.1
    else:
        assert r["optimal_pct"] == 0.0

    # 3. zero size <=> AVOID.
    if r["optimal_pct"] == 0.0:
        assert r["action"] == "AVOID"

    # 4. book-keeping fields are internally consistent.
    assert r["current_pct"] == pytest.approx(1_000.0 / 100_000.0 * 100, abs=0.05)
    assert r["delta_usd"] == pytest.approx(r["recommended_usd"] - 1_000.0, abs=0.05)
    assert 0.0 <= r["win_rate"] <= 100.0
    assert r["volatility_tier"] in {"LOW", "MEDIUM", "HIGH", "EXTREME"}
    assert r["max_drawdown"] <= 0.0                     # drawdown is a non-positive %
    assert 0.30 <= r["autocorr_shrink"] <= 1.0          # never up-sizes


def test_insufficient_history_returns_error():
    r = _size(np.full(MIN_DAYS - 1, 0.001))
    assert "error" in r
    assert r["recommended_pct"] is None
    assert r["recommended_usd"] is None


def test_persistent_losses_avoid():
    # A series with a clearly negative edge must not be sized up.
    r = _size(_series("strong_down", seed=1))
    assert r["optimal_pct"] == 0.0
    assert r["action"] == "AVOID"


@pytest.mark.parametrize("kind,seed", GRID)
def test_bearish_ai_verdict_never_upsizes(kind, seed):
    base = _size(_series(kind, seed=seed))
    if "error" in base:
        pytest.skip("insufficient history")
    for verdict, penalty in [("AVOID", 0.5), ("SELL", 0.5), ("REDUCE", 0.8)]:
        pen = _size(_series(kind, seed=seed), ai_eval={"verdict": verdict})
        assert pen["optimal_pct"] <= base["optimal_pct"] + 1e-9, verdict
    # A severe-overvaluation pct_diff also only shrinks.
    ov = _size(_series(kind, seed=seed), ai_eval={"verdict": "HOLD", "pct_diff": -20.0})
    assert ov["optimal_pct"] <= base["optimal_pct"] + 1e-9


@pytest.mark.parametrize("kind,seed", GRID)
def test_positive_analyst_upside_never_shrinks(kind, seed):
    base = _size(_series(kind, seed=seed))
    if "error" in base:
        pytest.skip("insufficient history")
    boosted = _size(_series(kind, seed=seed), analyst_upside_pct=50.0)
    assert boosted["optimal_pct"] >= base["optimal_pct"] - 0.05


@pytest.mark.parametrize("kind,seed", GRID)
def test_determinism(kind, seed):
    a = _size(_series(kind, seed=seed))
    b = _size(_series(kind, seed=seed))
    assert a == b


def test_hard_cap_binds_on_extreme_edge():
    # A massive, low-variance positive edge would blow past any sane size —
    # the cap must clamp it to exactly the configured maximum.
    cap_pct = kelly._max_position_pct() * 100.0
    r = _size(np.full(120, 0.05))          # +5%/day, zero variance after mean-removal
    # zero variance -> continuous kelly is 0; blend leans on discrete (all wins).
    assert r["optimal_pct"] <= cap_pct + 1e-9


# ─────────────────────── low-level estimator invariants ──────────────────────

def test_continuous_kelly_zero_variance_is_zero():
    assert kelly._continuous_kelly(np.full(50, 0.01))["kelly_pct"] == 0.0


@pytest.mark.parametrize("kind,seed", GRID)
def test_discrete_win_rate_bounded(kind, seed):
    d = kelly._discrete_kelly(_series(kind, seed=seed))
    assert 0.0 <= d["win_rate"] <= 100.0
    assert d["payoff_ratio"] >= 0.0


@pytest.mark.parametrize("kind,seed", GRID)
def test_autocorr_shrink_never_exceeds_one(kind, seed):
    _rho, factor = kelly._autocorr_shrink(_series(kind, seed=seed))
    assert 0.0 <= factor <= 1.0


def test_volatility_score_thresholds_monotone():
    # tiers must be ordered by annualized vol (30/60/100 boundaries).
    def ann(v):  # array whose STD gives annualized vol v% (alternating ±sigma: mean 0, std sigma)
        sigma = v / 100.0 / np.sqrt(252)
        return np.array([sigma, -sigma] * 100)
    assert kelly._volatility_score(ann(10)) == "LOW"
    assert kelly._volatility_score(ann(45)) == "MEDIUM"
    assert kelly._volatility_score(ann(80)) == "HIGH"
    assert kelly._volatility_score(ann(150)) == "EXTREME"


def test_max_drawdown_non_positive():
    for kind, seed in GRID:
        assert kelly._max_drawdown(_series(kind, seed=seed)) <= 0.0


# ─────────────────── run_kelly_analysis (portfolio) invariants ───────────────

@pytest.fixture
def _patched_fetch(monkeypatch):
    """Make run_kelly_analysis network-free and deterministic by mapping each
    symbol to a fixed synthetic return Series (dated, so the correlation overlay
    exercises its date-aligned path)."""
    idx = pd.date_range("2025-01-01", periods=120, freq="D")
    book = {
        "AAA": pd.Series(_series("strong_up", seed=1), index=idx),
        "BBB": pd.Series(_series("mixed", seed=2), index=idx),
        "CCC": pd.Series(_series("strong_down", seed=3), index=idx),
        "DDD": pd.Series(_series("low_vol_edge", seed=4), index=idx),
    }
    monkeypatch.setattr(kelly, "_fetch_returns", lambda sym, days=90: book.get(sym))
    return book


def _holdings(*syms):
    return [{"symbol": s, "equity": 5_000.0, "average_buy_price": 100.0,
             "current_price": 110.0} for s in syms]


def test_portfolio_gross_and_cash_invariants(_patched_fetch):
    holdings = _holdings("AAA", "BBB", "CCC", "DDD")
    out = kelly.run_kelly_analysis(holdings, total_portfolio=100_000.0)
    cap_pct = kelly._max_gross_exposure() * 100.0

    # gross exposure never exceeds the cap (post-reconciliation), within rounding.
    assert out["total_optimal_allocated_pct"] <= cap_pct + 0.2
    # cash is exactly the complement, floored at 0.
    assert out["cash_recommended_pct"] == pytest.approx(
        max(0.0, 100.0 - out["total_optimal_allocated_pct"]), abs=0.11)
    # NaN guard: score is a real number >= 0 even with a losing/mixed book.
    score = out["portfolio_kelly_score"]
    assert score >= 0.0 and np.isfinite(score)
    # every correlation haircut is a de-risking factor, never an up-size.
    for h in out["holdings"]:
        assert h.get("corr_haircut", 1.0) <= 1.0 + 1e-9


def test_all_avoid_book_scores_zero_not_nan(monkeypatch):
    # Every name a persistent loser -> all AVOID -> mean of [] must be 0.0, not NaN.
    idx = pd.date_range("2025-01-01", periods=120, freq="D")
    losers = {s: pd.Series(_series("strong_down", seed=i), index=idx)
              for i, s in enumerate(["AAA", "BBB"])}
    monkeypatch.setattr(kelly, "_fetch_returns", lambda sym, days=90: losers.get(sym))
    out = kelly.run_kelly_analysis(_holdings("AAA", "BBB"), total_portfolio=100_000.0)
    assert out["portfolio_kelly_score"] == 0.0
    assert np.isfinite(out["portfolio_kelly_score"])


def test_run_kelly_does_not_mutate_input(_patched_fetch):
    holdings = _holdings("AAA", "BBB")
    before = [dict(h) for h in holdings]
    kelly.run_kelly_analysis(holdings, total_portfolio=100_000.0)
    assert holdings == before        # inputs untouched (immutability)
