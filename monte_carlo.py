"""
monte_carlo.py — Monte Carlo portfolio stress-test engine.

Runs N simulations of portfolio returns over a horizon using per-symbol
historical volatility fetched from yfinance. Produces VaR, CVaR, and a
probability distribution over outcome buckets.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
from typing import Any


# ─── Config ───────────────────────────────────────────────────────────────────

N_SIMS: int   = 1000      # number of Monte Carlo paths
PERIOD_DAYS   = 252        # 1 year of history for volatility estimation
TRADING_DAYS  = 21         # forecast horizon (~1 month)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _fetch_volatilities(symbols: list[str]) -> tuple[dict[str, float], pd.DataFrame]:
    """Return annualised daily log-return std for each symbol, and the log returns dataframe."""
    end   = datetime.today()
    start = end - timedelta(days=PERIOD_DAYS + 30)  # buffer for weekends
    vols: dict[str, float] = {}
    log_returns = pd.DataFrame()
    try:
        data = yf.download(
            symbols,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            auto_adjust=True,
            progress=False,
        )["Close"]
        if hasattr(data, "columns"):             # multi-ticker DataFrame
            rets_dict = {}
            for sym in symbols:
                if sym in data.columns:
                    s = data[sym].dropna()
                    if len(s) > 5:
                        ret = np.log(s / s.shift(1)).dropna()
                        vols[sym] = float(ret.std())
                        rets_dict[sym] = ret
            if rets_dict:
                log_returns = pd.DataFrame(rets_dict)
        else:                                    # single ticker → Series
            s = data.dropna()
            if len(s) > 5:
                ret = np.log(s / s.shift(1)).dropna()
                vols[symbols[0]] = float(ret.std())
                log_returns = pd.DataFrame({symbols[0]: ret})
    except Exception:
        pass
    return vols, log_returns


def _weighted_portfolio_vol(
    holdings: list[dict],
    vols: dict[str, float],
    total_equity: float,
) -> float:
    """Approx portfolio daily vol as weighted average (ignoring correlation)."""
    weighted = 0.0
    for h in holdings:
        sym   = h["symbol"]
        wt    = h["equity"] / total_equity if total_equity else 0.0
        vol   = vols.get(sym, 0.018)          # fallback ~1.8% daily vol
        weighted += wt * vol
    return weighted

def _correlated_portfolio_vol(
    holdings: list[dict],
    total_equity: float,
    log_returns_df: pd.DataFrame,
    vols: dict[str, float]
) -> float:
    """Calculate true correlated portfolio daily volatility using the covariance matrix."""
    if total_equity <= 0:
        return 0.018

    # Extract symbols that actually have returns data
    valid_symbols = [h["symbol"] for h in holdings if h["symbol"] in log_returns_df.columns]
    
    if len(valid_symbols) < 2:
        return _weighted_portfolio_vol(holdings, vols, total_equity)
        
    # Align weights to valid symbols
    weights = np.array([h["equity"] / total_equity for h in holdings if h["symbol"] in valid_symbols])
    
    # Normalize weights to 1 just in case
    if weights.sum() > 0:
        weights = weights / weights.sum()
    else:
        return _weighted_portfolio_vol(holdings, vols, total_equity)
    
    # Get covariance matrix of log returns
    cov_matrix = log_returns_df[valid_symbols].cov().values
    
    # portfolio var = w^T * Cov * w
    port_var = weights.T @ cov_matrix @ weights
    
    if port_var <= 0:
        return _weighted_portfolio_vol(holdings, vols, total_equity)
        
    return float(np.sqrt(port_var))


# ─── Main entry ──────────────────────────────────────────────────────────────

def run_monte_carlo(
    holdings: list[dict],
    equity_summary: dict[str, float],
    n_sims: int = N_SIMS,
    horizon_days: int = TRADING_DAYS,
) -> dict[str, Any]:
    """
    Simulate portfolio value over `horizon_days` using GBM.

    Returns
    -------
    dict with keys:
      current_equity, horizon_days, n_sims,
      median, mean, p5, p95,           ← final portfolio value percentiles
      var_95, cvar_95,                  ← Value at Risk / Conditional VaR (loss $)
      prob_profit, prob_loss_10pct,     ← probabilities
      buckets: [{label, pct}]           ← histogram data for charting
      per_symbol: [{symbol, vol_pct, weight_pct}]
    """
    total_equity = equity_summary.get("portfolio_equity", 0.0)
    if total_equity <= 0:
        return {"error": "Portfolio equity is zero or missing"}

    symbols = [h["symbol"] for h in holdings]
    vols, log_returns_df = _fetch_volatilities(symbols)

    # Daily drift: conservative assumption = 0 excess return (stress test)
    daily_mu  = 0.0
    
    # Calculate True Multivariate Covariance Volatility
    daily_sig = _correlated_portfolio_vol(holdings, total_equity, log_returns_df, vols)

    if daily_sig <= 0:
        daily_sig = 0.018

    rng = np.random.default_rng(seed=42)
    # GBM: S_T = S_0 * exp(sum of N(mu - 0.5σ², σ) shocks)
    daily_returns = rng.normal(
        loc   = daily_mu - 0.5 * daily_sig ** 2,
        scale = daily_sig,
        size  = (n_sims, horizon_days),
    )
    final_values = total_equity * np.exp(daily_returns.sum(axis=1))

    losses = total_equity - final_values      # positive = loss

    p5    = float(np.percentile(final_values, 5))
    p25   = float(np.percentile(final_values, 25))
    p50   = float(np.percentile(final_values, 50))
    p75   = float(np.percentile(final_values, 75))
    p95   = float(np.percentile(final_values, 95))
    mean  = float(np.mean(final_values))
    var95 = float(np.percentile(losses, 95))      # 95th-percentile loss
    cvar95_mask = losses[losses >= var95]
    cvar95 = float(cvar95_mask.mean()) if len(cvar95_mask) else var95

    prob_profit       = float((final_values > total_equity).mean())
    prob_loss_10      = float((final_values < total_equity * 0.90).mean())
    prob_loss_20      = float((final_values < total_equity * 0.80).mean())

    # Histogram — 10 equal-width buckets from p1 to p99
    lo, hi = np.percentile(final_values, 1), np.percentile(final_values, 99)
    edges  = np.linspace(lo, hi, 11)
    counts, _ = np.histogram(final_values, bins=edges)
    total_count = counts.sum() or 1
    buckets = [
        {
            "label": f"${edges[i]:,.0f}–${edges[i+1]:,.0f}",
            "center": float((edges[i] + edges[i+1]) / 2),
            "pct":    round(float(counts[i] / total_count * 100), 1),
        }
        for i in range(len(counts))
    ]

    per_symbol = [
        {
            "symbol":     h["symbol"],
            "vol_daily_pct": round(vols.get(h["symbol"], 0.018) * 100, 2),
            "weight_pct": round(h["equity"] / total_equity * 100, 1) if total_equity else 0,
        }
        for h in holdings
    ]

    return {
        "current_equity":    round(total_equity, 2),
        "horizon_days":      horizon_days,
        "n_sims":            n_sims,
        "daily_vol_pct":     round(daily_sig * 100, 3),
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
