"""Property harness for the Monte-Carlo money math (first slice of the coverage theme).

Instead of a few hand-picked cases, these assert INVARIANTS that must hold for ANY
input across hundreds of seeded-random draws — the kind of test that catches a sign
flip, an ordering bug, or a broken variance adjustment that example-based tests miss.

Fully offline & deterministic: pure helpers take arrays directly; run_monte_carlo has
its ONE network call (_fetch_data) monkeypatched and runs in pure stress mode
(use_regime_drift=False) so no regime/GARCH fetch fires. No `hypothesis` dependency —
seeded numpy loops keep it runnable in CI with zero new packages.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import monte_carlo as mc

_RNG = np.random.default_rng(20260714)


def _holdings(vols):
    # equal-ish random weights; returns (holdings, vols_dict, total_equity)
    syms = [f"S{i}" for i in range(len(vols))]
    eqs = _RNG.uniform(100, 10_000, size=len(vols))
    holdings = [{"symbol": s, "equity": float(e)} for s, e in zip(syms, eqs)]
    return holdings, {s: float(v) for s, v in zip(syms, vols)}, float(eqs.sum())


# ── pure helpers ──────────────────────────────────────────────────────────────

def test_weighted_vol_is_bounded_by_member_vols():
    for _ in range(300):
        vols = _RNG.uniform(0.001, 0.2, size=int(_RNG.integers(1, 8)))
        holdings, vd, te = _holdings(vols)
        out = mc._weighted_portfolio_vol(holdings, vd, te)
        assert out >= 0.0
        # a weighted average never leaves the [min, max] envelope of its members
        assert min(vols) - 1e-9 <= out <= max(vols) + 1e-9


def test_weighted_vol_single_asset_identity():
    for _ in range(100):
        v = float(_RNG.uniform(0.001, 0.3))
        out = mc._weighted_portfolio_vol([{"symbol": "X", "equity": 1000.0}], {"X": v}, 1000.0)
        assert out == pytest.approx(v)


def test_weighted_vol_zero_equity_falls_back_positive():
    out = mc._weighted_portfolio_vol([{"symbol": "X", "equity": 0.0}], {"X": 0.02}, 0.0)
    assert out == mc.FALLBACK_DAILY_VOL and out > 0


def test_regime_drift_scales_linearly_with_multiplier():
    for _ in range(200):
        vols = _RNG.uniform(0.01, 0.05, size=3)
        holdings, _vd, te = _holdings(vols)
        drifts = {h["symbol"]: float(_RNG.normal(0, 0.001)) for h in holdings}
        base = sum((h["equity"] / te) * drifts[h["symbol"]] for h in holdings)
        for regime, mult in mc.REGIME_DRIFT.items():
            got = mc._regime_adjusted_drift(holdings, drifts, te, regime)
            assert got == pytest.approx(base * mult, abs=1e-12)


def test_regime_drift_zero_equity_is_zero():
    assert mc._regime_adjusted_drift([{"symbol": "X", "equity": 5.0}], {"X": 0.01}, 0.0, "BULL") == 0.0


def test_ewma_vol_is_always_positive_and_finite():
    # Contract: EWMA vol is a FLOORED estimate — never zero/negative/NaN. A random
    # series gives a real number; degenerate inputs fall back to FALLBACK_DAILY_VOL.
    for _ in range(100):
        s = pd.Series(_RNG.normal(0, 0.01, size=120))
        v = mc._ewma_vol(s)
        assert v > 0.0 and np.isfinite(v)
    assert mc._ewma_vol(pd.Series([0.01, 0.02])) == mc.FALLBACK_DAILY_VOL   # len < 5
    assert mc._ewma_vol(pd.Series([0.0] * 60)) == mc.FALLBACK_DAILY_VOL     # flat → floor


# ── run_monte_carlo end-to-end (offline via mocked _fetch_data) ────────────────

def _run(monkeypatch, vol, drift=0.0, n_holdings=3, equity=100_000.0, n_sims=2000):
    syms = [f"S{i}" for i in range(n_holdings)]
    holdings = [{"symbol": s, "equity": equity / n_holdings} for s in syms]
    vols = {s: vol for s in syms}
    drifts = {s: drift for s in syms}
    # empty returns df → _correlated_portfolio_vol falls back to the weighted vol,
    # so portfolio σ == `vol` deterministically.
    monkeypatch.setattr(mc, "_fetch_data", lambda symbols: (vols, drifts, pd.DataFrame()))
    return mc.run_monte_carlo(holdings, {"portfolio_equity": equity},
                              n_sims=n_sims, horizon_days=21, use_regime_drift=False)


def test_percentiles_are_ordered(monkeypatch):
    r = _run(monkeypatch, vol=0.02)
    assert r["p5"] <= r["p25"] <= r["median"] <= r["p75"] <= r["p95"]


def test_cvar_at_least_var_and_probs_monotone(monkeypatch):
    r = _run(monkeypatch, vol=0.03)
    assert r["cvar_95"] >= r["var_95"]                     # tail mean ≥ its threshold
    for k in ("prob_profit_pct", "prob_loss_10pct", "prob_loss_20pct"):
        assert 0.0 <= r[k] <= 100.0
    assert r["prob_loss_20pct"] <= r["prob_loss_10pct"]    # a 20% loss implies a 10% loss
    assert r["median"] > 0 and r["mean"] > 0               # GBM values are strictly positive
    assert r["var_95_pct"] == pytest.approx(r["var_95"] / r["current_equity"] * 100, abs=0.01)


def test_higher_vol_widens_the_distribution(monkeypatch):
    lo = _run(monkeypatch, vol=0.01)
    hi = _run(monkeypatch, vol=0.05)
    assert (hi["p95"] - hi["p5"]) > (lo["p95"] - lo["p5"])   # more vol → wider band


def test_zero_equity_returns_error(monkeypatch):
    monkeypatch.setattr(mc, "_fetch_data", lambda symbols: ({}, {}, pd.DataFrame()))
    out = mc.run_monte_carlo([{"symbol": "X", "equity": 0.0}], {"portfolio_equity": 0.0})
    assert "error" in out
