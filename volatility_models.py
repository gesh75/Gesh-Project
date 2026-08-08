"""
volatility_models.py — Advanced Volatility Models for Monte Carlo

Provides GARCH(1,1) and EWMA volatility estimators that plug into
monte_carlo.py.  monte_carlo.py tries to import garch_portfolio_vol()
first and falls back to EWMA if this module is unavailable or arch
is not installed.

Models
──────
  EWMA (λ=0.94)       — quick, no additional dependencies
  GARCH(1,1)          — requires 'arch' package (pip install arch)
                        More accurate forecast of near-term volatility
                        because it models volatility clustering.

Key functions
─────────────
  ewma_vol(returns, span)                → annualised daily σ
  garch_vol(returns)                     → GARCH(1,1) 1-day-ahead σ forecast
  garch_portfolio_vol(holdings, df, eq)  → weighted portfolio σ via GARCH

Installation (optional — for GARCH)
─────────────────────────────────────
  pip install arch --break-system-packages
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Any
import price_source  # locked yf.download adapter (thread-safety)


# ─── EWMA ─────────────────────────────────────────────────────────────────────

def ewma_vol(returns: pd.Series, span: int = 30) -> float:
    """
    Exponentially Weighted Moving Average volatility.

    λ = 1 - 2/(span+1).  span=30 → λ≈0.935 (near RiskMetrics 0.94).
    Returns annualised daily σ.
    """
    if len(returns) < 5:
        return 0.018
    ewma_var = float(returns.ewm(span=span).var().iloc[-1])
    if ewma_var <= 0 or np.isnan(ewma_var):
        # NaN-safe fallback: returns.std() can itself be NaN, and `nan or 0.018`
        # returns nan (nan is truthy), leaking NaN into downstream risk math.
        fallback = float(returns.std())
        return fallback if (fallback > 0 and not np.isnan(fallback)) else 0.018
    return float(np.sqrt(ewma_var))


# ─── GARCH(1,1) ───────────────────────────────────────────────────────────────

#: value returned by garch_vol_detail()[1] when the GARCH fit genuinely ran.
GARCH_ESTIMATOR = "garch"


def garch_vol_detail(returns: pd.Series, scale: float = 100.0) -> tuple[float, str]:
    """GARCH(1,1) 1-step-ahead σ **and the estimator that actually produced it**.

    Returns ``(daily_sigma, estimator)``. ``estimator`` is ``"garch"`` only when the
    fit genuinely ran; otherwise it is ``"ewma:<reason>"`` naming which of the four
    fallback paths was taken — short sample, `arch` not installed, fit failure, or a
    result outside the plausible band.

    Why the estimator is returned at all (2026-08-07): `garch_vol()` returns a bare
    float, so no caller could distinguish a real GARCH fit from the EWMA fallback —
    and `arch` is not installed in either project venv, so in practice EVERY "GARCH"
    figure this module has produced is EWMA wearing a GARCH label. The number was
    never wrong; the label was. A mislabelled figure that reaches a report as fact is
    exactly what the provenance rules (#45/#51/#59) exist to prevent.

    Installing `arch` is a separate decision. This makes the substitution legible
    either way, which is the part that must not depend on an install.
    """
    if len(returns) < 60:
        return ewma_vol(returns), "ewma:short_sample"
    try:
        from arch import arch_model  # type: ignore[import]
    except ImportError:
        # Distinguished from a fit failure on purpose: "not installed" is an
        # environment fact the operator can act on, "fit failed" is a data problem.
        return ewma_vol(returns), "ewma:no_arch"
    try:
        scaled = returns.dropna() * scale
        am = arch_model(scaled, vol="Garch", p=1, q=1, dist="normal", rescale=False)
        res = am.fit(disp="off", show_warning=False, options={"maxiter": 200})
        # 1-step forecast variance (in scaled units²)
        forecast = res.forecast(horizon=1)
        var_scaled = float(forecast.variance.iloc[-1, 0])
        # Convert back to original return units
        daily_sig = float(np.sqrt(max(var_scaled, 0))) / scale
    except Exception:
        return ewma_vol(returns), "ewma:fit_failed"
    if not 0 < daily_sig < 0.20:
        # A fitted-but-implausible σ is still not a GARCH answer we can stand behind.
        return ewma_vol(returns), "ewma:out_of_range"
    return daily_sig, GARCH_ESTIMATOR


def garch_vol(returns: pd.Series, scale: float = 100.0) -> float:
    """
    Fit a GARCH(1,1) model and return the 1-step-ahead conditional volatility.

    Parameters
    ----------
    returns : daily log-returns as pd.Series
    scale   : arch library works better with returns scaled to percentage points.

    Returns
    -------
    Daily σ (NOT annualised) — same units as ewma_vol().
    Falls back to EWMA if arch is not installed or fitting fails.

    Thin wrapper over :func:`garch_vol_detail` kept for existing float callers.
    Prefer the detail form anywhere the result is REPORTED rather than just consumed
    numerically — this one cannot tell you which estimator you actually got.
    """
    return garch_vol_detail(returns, scale)[0]


def garch_portfolio_vol(
    holdings: list[dict],
    log_returns_df: pd.DataFrame,
    total_equity: float,
) -> float | None:
    """
    Weighted portfolio volatility using GARCH(1,1) per-symbol forecasts.

    Returns None if arch is not installed (monte_carlo.py will fall back to EWMA).
    Returns the weighted average of per-symbol GARCH σ forecasts.
    Note: ignores cross-correlations (conservative approximation).
    """
    try:
        from arch import arch_model  # noqa: F401 — just check import
    except ImportError:
        return None

    if log_returns_df.empty or total_equity <= 0:
        return None

    valid_holdings = [h for h in holdings if h["symbol"] in log_returns_df.columns]
    if not valid_holdings:
        return None

    weighted_vol = 0.0
    total_weight = 0.0
    for h in valid_holdings:
        sym = h["symbol"]
        wt  = h["equity"] / total_equity
        ret = log_returns_df[sym].dropna()
        sig = garch_vol(ret)
        weighted_vol += wt * sig
        total_weight += wt

    if total_weight <= 0:
        return None
    return weighted_vol / total_weight


# ─── Model comparison ─────────────────────────────────────────────────────────

def compare_vol_models(
    returns: pd.Series,
    symbol: str = "?",
) -> dict[str, Any]:
    """
    Compare EWMA vs GARCH(1,1) volatility estimates for a single symbol.
    Useful for the dashboard "Vol Model" debug panel.
    """
    ewma_sig = ewma_vol(returns)
    garch_sig, estimator = garch_vol_detail(returns)
    available = estimator == GARCH_ESTIMATOR

    # When GARCH did not run, garch_sig IS ewma_sig — so the old code reported the
    # EWMA number under a `garch_daily_pct` key and a difference of exactly 0.0,
    # which reads as "the two models agree perfectly". They had not been compared at
    # all. Emit None rather than a figure that cannot mean what its key says.
    out: dict[str, Any] = {
        "symbol":           symbol,
        "ewma_daily_pct":   round(ewma_sig * 100, 3),
        "ewma_annual_pct":  round(ewma_sig * np.sqrt(252) * 100, 2),
        "garch_available":  available,
        "estimator":        estimator,
    }
    if available:
        diff_bp = round((garch_sig - ewma_sig) * 10_000, 1)
        out.update({
            "garch_daily_pct":  round(garch_sig * 100, 3),
            "garch_annual_pct": round(garch_sig * np.sqrt(252) * 100, 2),
            "diff_basis_pts":   diff_bp,
            "preferred":        "GARCH" if abs(diff_bp) > 20 else "EWMA",
        })
    else:
        out.update({
            "garch_daily_pct":  None,
            "garch_annual_pct": None,
            "diff_basis_pts":   None,
            "preferred":        f"EWMA (GARCH unavailable: {estimator})",
        })
    return out


# ─── CLI test ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    for ticker in ["NVDA", "MSTR", "SPY"]:
        data   = price_source.locked_yf_download(ticker, period="1y", auto_adjust=True, progress=False)["Close"]
        rets   = np.log(data / data.shift(1)).dropna()
        result = compare_vol_models(rets, ticker)
        print(
            f"{ticker}: EWMA={result['ewma_annual_pct']:.1f}%  "
            f"GARCH={result['garch_annual_pct']:.1f}%  "
            f"Δ={result['diff_basis_pts']:+.0f}bp  "
            f"→ {result['preferred']}"
        )
