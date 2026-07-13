"""Monte Carlo calibration honesty (Task C).

(a) The result and the /api/monte-carlo response expose the TRUE horizon
    (21 trading days) — no served surface may claim "1yr" for a 1-month sim.
(b) When per-symbol history can't be fetched the result says so
    (calibration:"degraded" + the affected symbols) instead of silently
    substituting the 1.8% fallback daily vol.
(c) GBM sanity: with constant arithmetic mu/sigma the mean log-return of the
    simulated paths ≈ (mu − 0.5σ²)·horizon, AND the calibrated drift is the
    ARITHMETIC daily drift (log-mean + ½·var), not the raw log-return mean —
    feeding the log-mean into `loc = mu − 0.5σ²` applied the variance
    adjustment twice and biased every path low by ½σ² per day.

All tests are offline (conftest blocks sockets); yfinance/regime/GARCH seams
are monkeypatched.
"""

from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd
import pytest
from starlette.testclient import TestClient

import monte_carlo

_HOLDINGS = [
    {"symbol": "NVDA", "quantity": 1.0, "equity": 60_000.0},
    {"symbol": "MSTR", "quantity": 2.0, "equity": 40_000.0},
]
_EQ = {"portfolio_equity": 100_000.0}
_H = {"X-API-Key": "testkey"}


@pytest.fixture
def _stub_regime_and_garch(monkeypatch):
    """Deterministic engine: NEUTRAL regime (drift ×1.0), GARCH override off."""
    monkeypatch.setattr(monte_carlo, "_get_portfolio_regime", lambda syms: "NEUTRAL")
    monkeypatch.setitem(sys.modules, "volatility_models", None)  # import → ImportError


def _fetch_stub(vols: dict, drifts: dict):
    return lambda syms: (dict(vols), dict(drifts), pd.DataFrame())


# ─── (a) horizon honesty ─────────────────────────────────────────────────────

def test_result_exposes_true_horizon(monkeypatch, _stub_regime_and_garch):
    monkeypatch.setattr(
        monte_carlo, "_fetch_data",
        _fetch_stub({"NVDA": 0.02, "MSTR": 0.03}, {"NVDA": 0.001, "MSTR": 0.001}))
    res = monte_carlo.run_monte_carlo(_HOLDINGS, _EQ, n_sims=500)
    assert res["horizon_days"] == 21
    assert res["horizon_label"] == "21 trading days"
    assert "1yr" not in json.dumps(res).lower()


def test_no_stale_1yr_label_in_served_surfaces(repo_root):
    """The engine simulates 21 trading days; any '1yr' Monte-Carlo label is a lie."""
    for rel in ("server.py", "templates/dashboard.html"):
        src = (repo_root / rel).read_text()
        assert "1yr" not in src.lower(), f"stale '1yr' Monte-Carlo label in {rel}"


# ─── (b) degraded calibration is never silent ────────────────────────────────

def test_total_fetch_failure_marks_calibration_degraded(monkeypatch, _stub_regime_and_garch):
    monkeypatch.setattr(
        monte_carlo.price_source, "yf_download",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("yahoo down")))
    res = monte_carlo.run_monte_carlo(_HOLDINGS, _EQ, n_sims=200)
    assert res["calibration"] == "degraded"
    assert set(res["calibration_degraded_symbols"]) == {"NVDA", "MSTR"}


def test_partial_history_marks_only_missing_symbols(monkeypatch, _stub_regime_and_garch):
    monkeypatch.setattr(
        monte_carlo, "_fetch_data", _fetch_stub({"NVDA": 0.02}, {"NVDA": 0.001}))
    res = monte_carlo.run_monte_carlo(_HOLDINGS, _EQ, n_sims=200)
    assert res["calibration"] == "degraded"
    assert res["calibration_degraded_symbols"] == ["MSTR"]


def test_full_history_marks_calibration_ok(monkeypatch, _stub_regime_and_garch):
    monkeypatch.setattr(
        monte_carlo, "_fetch_data",
        _fetch_stub({"NVDA": 0.02, "MSTR": 0.03}, {"NVDA": 0.001, "MSTR": 0.001}))
    res = monte_carlo.run_monte_carlo(_HOLDINGS, _EQ, n_sims=200)
    assert res["calibration"] == "ok"
    assert res["calibration_degraded_symbols"] == []


# ─── (c) GBM drift applied exactly once ──────────────────────────────────────

def test_sim_mean_log_return_matches_gbm_drift(monkeypatch, _stub_regime_and_garch):
    """With constant ARITHMETIC daily drift mu and vol sigma, GBM log-return
    paths must center on (mu − 0.5σ²)·H. A second variance adjustment would
    shift the center to (mu − σ²)·H ≈ 9.4e-3 lower — far outside tolerance.
    RNG is seeded inside run_monte_carlo (seed=42) → deterministic."""
    mu, sigma, horizon = 0.002, 0.03, 21
    one = [{"symbol": "NVDA", "quantity": 1.0, "equity": 100_000.0}]
    monkeypatch.setattr(monte_carlo, "_fetch_data", _fetch_stub({"NVDA": sigma}, {"NVDA": mu}))
    res = monte_carlo.run_monte_carlo(one, {"portfolio_equity": 100_000.0}, n_sims=120_000)

    # median(final) ≈ exp(median log-path) → log(median/equity) ≈ (mu − ½σ²)·H
    med_log = float(np.log(res["median"] / 100_000.0))
    assert med_log == pytest.approx((mu - 0.5 * sigma**2) * horizon, abs=2e-3)
    # mean(final) ≈ equity·exp(mu·H) — the lognormal mean recovers raw mu,
    # so a double adjustment also shows up here as exp((mu − ½σ²)·H).
    assert res["mean"] == pytest.approx(100_000.0 * np.exp(mu * horizon), rel=5e-3)


def test_calibrated_drift_is_arithmetic_not_log_mean(monkeypatch):
    """_fetch_data must store μ = E[log r] + ½·Var[log r] (arithmetic daily
    drift). Storing the raw log-return mean — itself already μ − ½σ² — makes
    the simulation's explicit −½σ² a DOUBLE variance adjustment."""
    rng = np.random.default_rng(7)
    log_rets = rng.normal(0.001, 0.03, 300)
    prices = np.concatenate([[100.0], 100.0 * np.exp(np.cumsum(log_rets))])
    idx = pd.bdate_range("2025-01-01", periods=len(prices))
    frame = pd.DataFrame({"NVDA": prices}, index=idx)
    monkeypatch.setattr(
        monte_carlo.price_source, "yf_download", lambda *a, **k: {"Close": frame})

    vols, drifts, _lr = monte_carlo._fetch_data(["NVDA"])

    s = pd.Series(prices, index=idx)
    ret = np.log(s / s.shift(1)).dropna()
    arithmetic = float(ret.mean()) + 0.5 * float(ret.var())
    assert drifts["NVDA"] == pytest.approx(arithmetic, abs=1e-12)
    # tripwire: the raw log-mean (the double-adjustment source) must NOT be stored
    assert abs(drifts["NVDA"] - float(ret.mean())) > 1e-5


# ─── (a)+(b) API passthrough ─────────────────────────────────────────────────

@pytest.fixture
def owner_client(monkeypatch):
    import server
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: False)
    monkeypatch.setenv("GESH_API_KEY", "testkey")
    monkeypatch.setattr(
        server, "build_data_payload",
        lambda *a, **k: {"holdings": [dict(h) for h in _HOLDINGS],
                         "equity_summary": dict(_EQ)})
    monkeypatch.setattr(server, "_save_engine_result", lambda *a, **k: None)
    return TestClient(server.app, raise_server_exceptions=False)


def test_api_monte_carlo_carries_horizon_and_degraded(
        monkeypatch, _stub_regime_and_garch, owner_client):
    # per-symbol history unavailable → the API must say so, not silently
    # substitute the 1.8% fallback vol
    monkeypatch.setattr(monte_carlo, "_fetch_data", _fetch_stub({}, {}))
    r = owner_client.post("/api/monte-carlo", headers=_H)
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["horizon_days"] == 21
    assert data["horizon_label"] == "21 trading days"
    assert data["calibration"] == "degraded"
    assert set(data["calibration_degraded_symbols"]) == {"NVDA", "MSTR"}
    assert "1yr" not in json.dumps(data).lower()
