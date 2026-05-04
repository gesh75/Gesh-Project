"""
monte_carlo.py — Monte Carlo portfolio stress-test engine.

Runs N simulations of portfolio returns over a horizon using per-symbol
historical volatility fetched from yfinance.

Enhancements (2026-05):
  • EWMA volatility (λ=0.94, RiskMetrics standard) — recent price action
    gets more weight; earnings spikes stop polluting vol estimates for months.
  • Regime-conditional drift — pulls live regime from regime.py and applies
    drift multipliers so Monte Carlo medians track reality better.
  • GARCH(1,1) preview via volatility_models.py (optional, falls back to EWMA).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
from typing import Any


# ─── Config ───────────────────────────────────────────────────────────────────

N_SIMS: int   = 1000      # number of Monte Carlo paths
PERIOD_DAYS   = 252        # 1 year of history for vol estimation
TRADING_DAYS  = 21         # forecast horizon (~1 month)

# Regime → drift multiplier (applied to the historical mean return)
REGIME_DRIFT: dict[str, float] = {
    "TRENDING":       1.15,   # strong uptrend → boost expected return
    "MOMENTUM-HIGH":  1.10,   # overbought but momentum intact
    "BREAKOUT-READY": 1.20,   # coiling for a move — upside bias
    "RANGING":        1.00,   # neutral
    "MOMENTUM-LOW":   0.70,   # oversold / weak
    "BEAR":           0.60,   # macro bear — pull back drift
    "CRISIS":         0.20,   # extreme stress
    "NEUTRAL":        1.00,   # default
}


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _ewma_vol(returns: pd.Series, span: int = 30) -> float:
    """EWMA variance → annualised daily vol. λ=0.94 (RiskMetrics standard).

    span ≈ 30 corresponds to λ = 1 - 2/(span+1) ≈ 0.935, close to the
    canonical 0.94. Recent shocks get exponentially more weight, so a
    one-off earnings gap decays away in ~3 weeks instead of dragging the
    rolling std for the full window.
    """
    if len(returns) < 5:
        return 0.018
    ewma_var = float(returns.ewm(span=span).var().iloc[-1])
    if ewma_var <= 0 or np.isnan(ewma_var):
        return float(returns.std()) or 0.018
    return float(np.sqrt(ewma_var))


def _get_portfolio_regime(symbols: list[str]) -> str:
    """Best-effort regime for the whole portfolio. Returns most-common regime."""
    try:
        from regime import detect_regimes
        result = detect_regimes(symbols[:5])  # cap at 5 to stay fast
        regimes = [r.get("regime", "NEUTRAL") for r in result.get("regimes", [])]
        if not regimes:
            return "NEUTRAL"
        # Return mode
        from collections import Counter
        return Counter(regimes).most_common(1)[0][0]
    except Exception:
        return "NEUTRAL"


def _fetch_data(symbols: list[str]) -> tuple[dict[str, float], dict[str, float], pd.DataFrame]:
    """Fetch EWMA vols, historical drifts, and log-returns DataFrame."""
    end   = datetime.today()
    start = end - timedelta(days=PERIOD_DAYS + 30)
    ewma_vols: dict[str, float] = {}
    hist_drifts: dict[str, float] = {}
    log_returns = pd.DataFrame()
    try:
        data = yf.download(
            symbols,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            auto_adjust=True,
            progress=False,
        )["Close"]
        rets_dict: dict[str, pd.Series] = {}
        if isinstance(data, pd.DataFrame):
            for sym in symbols:
                if sym in data.columns:
                    s = data[sym].dropna()
                    if len(s) > 5:
                        ret = np.log(s / s.shift(1)).dropna()
                        ewma_vols[sym]   = _ewma_vol(ret)
                        hist_drifts[sym] = float(ret.mean())   # daily historical drift
                        rets_dict[sym]   = ret
        else:  # single-ticker Series
            s = data.dropna()
            if len(s) > 5:
                ret = np.log(s / s.shift(1)).dropna()
                sym = symbols[0]
                ewma_vols[sym]   = _ewma_vol(ret)
                hist_drifts[sym] = float(ret.mean())
                rets_dict[sym]   = ret
        if rets_dict:
            log_returns = pd.DataFrame(rets_dict)
    except Exception:
        pass
    return ewma_vols, hist_drifts, log_returns


def _weighted_portfolio_vol(
    holdings: list[dict],
    vols: dict[str, float],
    total_equity: float,
) -> float:
    weighted = 0.0
    for h in holdings:
        wt = h["equity"] / total_equity if total_equity else 0.0
        weighted += wt * vols.get(h["symbol"], 0.018)
    return weighted or 0.018


def _correlated_portfolio_vol(
    holdings: list[dict],
    total_equity: float,
    log_returns_df: pd.DataFrame,
    vols: dict[str, float],
) -> float:
    """True correlated portfolio vol using the EWMA covariance matrix."""
    if total_equity <= 0:
        return 0.018
    valid = [h["symbol"] for h in holdings if h["symbol"] in log_returns_df.columns]
    if len(valid) < 2:
        return _weighted_portfolio_vol(holdings, vols, total_equity)
    weights = np.array([h["equity"] / total_equity for h in holdings if h["symbol"] in valid])
    if weights.sum() <= 0:
        return _weighted_portfolio_vol(holdings, vols, total_equity)
    weights /= weights.sum()
    # Use EWMA covariance (span=30) instead of simple covariance
    df = log_returns_df[valid].dropna()
    try:
        cov = df.ewm(span=30).cov().iloc[-len(valid):]
        cov_matrix = cov.values
    except Exception:
        cov_matrix = df.cov().values
    port_var = float(weights.T @ cov_matrix @ weights)
    if port_var <= 0 or np.isnan(port_var):
        return _weighted_portfolio_vol(holdings, vols, total_equity)
    return float(np.sqrt(port_var))


def _regime_adjusted_drift(
    holdings: list[dict],
    hist_drifts: dict[str, float],
    total_equity: float,
    regime: str,
) -> float:
    """Weighted portfolio historical drift × regime multiplier."""
    if total_equity <= 0:
        return 0.0
    # Weighted average historical daily drift
    weighted_drift = sum(
        (h["equity"] / total_equity) * hist_drifts.get(h["symbol"], 0.0)
        for h in holdings
    )
    multiplier = REGIME_DRIFT.get(regime, 1.0)
    return weighted_drift * multiplier


# ─── Main entry ──────────────────────────────────────────────────────────────

def run_monte_carlo(
    holdings: list[dict],
    equity_summary: dict[str, float],
    n_sims: int = N_SIMS,
    horizon_days: int = TRADING_DAYS,
    use_regime_drift: bool = True,
) -> dict[str, Any]:
    """
    Simulate portfolio value over `horizon_days` using GBM.

    Volatility:  EWMA (λ≈0.94) per symbol → EWMA covariance matrix → portfolio σ.
    Drift:       Historical weighted drift × regime multiplier (if use_regime_drift).

    Returns dict with keys:
      current_equity, horizon_days, n_sims, vol_method,
      regime, regime_multiplier,
      median, mean, p5, p25, p75, p95,
      var_95, cvar_95, var_95_pct,
      prob_profit_pct, prob_loss_10pct, prob_loss_20pct,
      buckets: [{label, center, pct}],
      per_symbol: [{symbol, ewma_vol_daily_pct, weight_pct}]
    """
    total_equity = equity_summary.get("portfolio_equity", 0.0)
    if total_equity <= 0:
        return {"error": "Portfolio equity is zero or missing"}

    symbols = [h["symbol"] for h in holdings]
    ewma_vols, hist_drifts, log_returns_df = _fetch_data(symbols)

    # ── Regime detection ─────────────────────────────────────────────────────
    regime = _get_portfolio_regime(symbols) if use_regime_drift else "NEUTRAL"
    regime_multiplier = REGIME_DRIFT.get(regime, 1.0)

    # ── Drift ─────────────────────────────────────────────────────────────────
    if use_regime_drift:
        daily_mu = _regime_adjusted_drift(holdings, hist_drifts, total_equity, regime)
    else:
        daily_mu = 0.0   # pure stress-test mode

    # ── Volatility (EWMA covariance) ─────────────────────────────────────────
    daily_sig = _correlated_portfolio_vol(holdings, total_equity, log_returns_df, ewma_vols)
    if daily_sig <= 0 or np.isnan(daily_sig):
        daily_sig = 0.018

    # ── GARCH(1,1) override (optional — if volatility_models installed) ───────
    vol_method = "EWMA-Corr"
    try:
        from volatility_models import garch_portfolio_vol
        garch_sig = garch_portfolio_vol(holdings, log_returns_df, total_equity)
        if garch_sig and garch_sig > 0:
            daily_sig  = garch_sig
            vol_method = "GARCH(1,1)"
    except (ImportError, Exception):
        pass

    # ── Simulation ────────────────────────────────────────────────────────────
    rng = np.random.default_rng(seed=42)
    daily_returns = rng.normal(
        loc   = daily_mu - 0.5 * daily_sig ** 2,
        scale = daily_sig,
        size  = (n_sims, horizon_days),
    )
    final_values = total_equity * np.exp(daily_returns.sum(axis=1))
    losses = total_equity - final_values

    p5   = float(np.percentile(final_values, 5))
    p25  = float(np.percentile(final_values, 25))
    p50  = float(np.percentile(final_values, 50))
    p75  = float(np.percentile(final_values, 75))
    p95  = float(np.percentile(final_values, 95))
    mean = float(np.mean(final_values))
    var95 = float(np.percentile(losses, 95))
    cvar_mask = losses[losses >= var95]
    cvar95 = float(cvar_mask.mean()) if len(cvar_mask) else var95

    prob_profit  = float((final_values > total_equity).mean())
    prob_loss_10 = float((final_values < total_equity * 0.90).mean())
    prob_loss_20 = float((final_values < total_equity * 0.80).mean())

    lo, hi = np.percentile(final_values, 1), np.percentile(final_values, 99)
    edges  = np.linspace(lo, hi, 11)
    counts, _ = np.histogram(final_values, bins=edges)
    total_count = counts.sum() or 1
    buckets = [
        {
            "label":  f"${edges[i]:,.0f}–${edges[i+1]:,.0f}",
            "center": float((edges[i] + edges[i+1]) / 2),
            "pct":    round(float(counts[i] / total_count * 100), 1),
        }
        for i in range(len(counts))
    ]

    per_symbol = [
        {
            "symbol":           h["symbol"],
            "ewma_vol_daily_pct": round(ewma_vols.get(h["symbol"], 0.018) * 100, 2),
            "weight_pct":       round(h["equity"] / total_equity * 100, 1) if total_equity else 0,
        }
        for h in holdings
    ]

    return {
        "current_equity":    round(total_equity, 2),
        "horizon_days":      horizon_days,
        "n_sims":            n_sims,
        "vol_method":        vol_method,
        "regime":            regime,
        "regime_multiplier": regime_multiplier,
        "daily_vol_pct":     round(daily_sig * 100, 3),
        "daily_drift_pct":   round(daily_mu * 100, 4),
        "mean":              round(mean, 2),
        "median":            round(p50, 2),
        "p5":                round(p5, 2),
        "p25":               round(p25, 2),
        "p75":               round(p75, 2),
        "p95":               round(p95, 2),
        "var_95":            round(var95, 2),
        "cvar_95":           round(cvar95, 2),
        "var_95_pct":        round(var95 / total_equity * 100, 2),
        "prob_profit_pct":   round(prob_profit * 100, 1),
        "prob_loss_10pct":   round(prob_loss_10 * 100, 1),
        "prob_loss_20pct":   round(prob_loss_20 * 100, 1),
        "buckets":           buckets,
        "per_symbol":        per_symbol,
    }
