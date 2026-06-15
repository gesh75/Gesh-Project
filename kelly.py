"""
Kelly Criterion Position Sizer
Calculates optimal position sizes using:
  - Full Kelly: f* = (μ - r) / σ²  (continuous, log-normal approximation)
  - Half-Kelly: f*/2               (practical, reduces ruin risk)
  - Win-rate Kelly: f* = W - (1-W)/R  (discrete, trade history based)
  - Volatility-adjusted cap: never exceed 25% per position
Inputs per holding: price history (90d), analyst upside, current allocation
"""

import yfinance as yf
import price_source  # Yahoo primary, Robinhood fallback (price_source.py)
import numpy as np
from datetime import datetime

HALF_KELLY_FACTOR = 0.5   # conservative multiplier
MAX_POSITION_PCT  = 0.25  # hard cap per position
MIN_HISTORY_DAYS  = 30    # minimum trading days needed

def _fetch_returns(symbol: str, days: int = 90) -> np.ndarray | None:
    try:
        raw = price_source.yf_download(symbol, period=f"{days}d", auto_adjust=True, progress=False)
        if raw.empty or len(raw) < MIN_HISTORY_DAYS:
            return None
        closes = raw["Close"]
        if hasattr(closes, "squeeze"):
            closes = closes.squeeze()
        returns = closes.pct_change().dropna().values.astype(float)
        return returns
    except Exception:
        return None

def _continuous_kelly(returns: np.ndarray, risk_free_daily: float = 0.0) -> dict:
    """Log-normal Kelly: f* = (μ - r) / σ²"""
    mu  = float(np.mean(returns))
    sig = float(np.std(returns))
    if sig == 0:
        return {"kelly_pct": 0.0, "method": "continuous"}
    excess = mu - risk_free_daily
    raw_kelly = excess / (sig ** 2)
    return {
        "kelly_pct": raw_kelly,
        "mu_daily":  round(mu * 100, 4),
        "sigma_daily": round(sig * 100, 4),
        "sharpe_annualized": round((mu / sig) * np.sqrt(252), 2) if sig > 0 else 0,
        "method": "continuous"
    }

def _discrete_kelly(returns: np.ndarray, threshold: float = 0.0) -> dict:
    """Discrete Kelly: f* = W - (1-W)/R using daily return wins/losses"""
    wins   = returns[returns > threshold]
    losses = returns[returns <= threshold]
    W = len(wins) / len(returns) if len(returns) > 0 else 0.5
    avg_win  = float(np.mean(np.abs(wins)))  if len(wins)   > 0 else 0.01
    avg_loss = float(np.mean(np.abs(losses))) if len(losses) > 0 else 0.01
    R = avg_win / avg_loss if avg_loss > 0 else 1.0
    raw_kelly = W - (1 - W) / R
    return {
        "kelly_pct":  raw_kelly,
        "win_rate":   round(W * 100, 1),
        "avg_win_pct": round(avg_win * 100, 3),
        "avg_loss_pct": round(avg_loss * 100, 3),
        "payoff_ratio": round(R, 2),
        "method": "discrete"
    }

def _volatility_score(returns: np.ndarray) -> str:
    ann_vol = float(np.std(returns)) * np.sqrt(252) * 100
    if ann_vol < 30:  return "LOW"
    if ann_vol < 60:  return "MEDIUM"
    if ann_vol < 100: return "HIGH"
    return "EXTREME"

def _max_drawdown(returns: np.ndarray) -> float:
    cumulative = np.cumprod(1 + returns)
    running_max = np.maximum.accumulate(cumulative)
    drawdown = (cumulative - running_max) / running_max
    return float(np.min(drawdown)) * 100  # negative %

def kelly_size_holding(
    symbol: str,
    current_equity: float,
    total_portfolio: float,
    avg_cost: float,
    current_price: float,
    analyst_upside_pct: float | None = None,
    ai_eval: dict | None = None,
    returns: np.ndarray | None = None,
) -> dict:
    """Full Kelly analysis for one holding."""
    if returns is None:
        returns = _fetch_returns(symbol, days=90)
        
    if returns is None or len(returns) < MIN_HISTORY_DAYS:
        return {
            "symbol": symbol,
            "error": "Insufficient price history",
            "recommended_pct": None,
            "recommended_usd": None,
        }

    cont   = _continuous_kelly(returns)
    disc   = _discrete_kelly(returns)
    ann_vol = float(np.std(returns)) * np.sqrt(252) * 100
    mdd     = _max_drawdown(returns)

    # Blend: 60% continuous + 40% discrete (more stable)
    raw_blend = 0.6 * cont["kelly_pct"] + 0.4 * disc["kelly_pct"]

    # Analyst upside boost: scale by upside confidence (cap +20% adjustment)
    if analyst_upside_pct is not None and analyst_upside_pct > 0:
        boost = min(analyst_upside_pct / 100.0 * 0.2, 0.2)
        raw_blend = raw_blend * (1 + boost)

    # Master Signal Valuation Penalty (from AI deep analysis)
    ai_penalty = 1.0
    if ai_eval:
        verdict = str(ai_eval.get("verdict", "")).upper()
        # "pct_diff" is negative if overvalued
        pct_diff = float(ai_eval.get("pct_diff", 0) or 0)
        
        if verdict == "AVOID" or verdict == "SELL":
            ai_penalty = 0.5  # Heavy penalty
        elif pct_diff < -15.0:
            ai_penalty = 0.6  # Severe overvaluation penalty
        elif pct_diff < -5.0 or verdict == "REDUCE":
            ai_penalty = 0.8  # Slight overvaluation penalty
            
        raw_blend = raw_blend * ai_penalty

    # Apply half-Kelly and hard cap
    half_kelly = raw_blend * HALF_KELLY_FACTOR
    final_pct  = max(0.0, min(half_kelly, MAX_POSITION_PCT))

    # Current vs optimal
    current_pct = current_equity / total_portfolio if total_portfolio > 0 else 0
    recommended_usd = final_pct * total_portfolio
    delta_usd = recommended_usd - current_equity
    delta_pct = (final_pct - current_pct) * 100

    # Signal
    if final_pct == 0:
        action = "AVOID"
        action_color = "red"
    elif current_pct > final_pct * 1.3:
        action = "TRIM"
        action_color = "yellow"
    elif current_pct < final_pct * 0.6:
        action = "ADD"
        action_color = "green"
    else:
        action = "HOLD"
        action_color = "cyan"

    # PnL
    pnl_pct = ((current_price - avg_cost) / avg_cost * 100) if avg_cost > 0 else 0

    return {
        "symbol":          symbol,
        "current_pct":     round(current_pct * 100, 1),
        "optimal_pct":     round(final_pct * 100, 1),
        "recommended_usd": round(recommended_usd, 2),
        "delta_usd":       round(delta_usd, 2),
        "delta_pct":       round(delta_pct, 1),
        "action":          action,
        "action_color":    action_color,
        "win_rate":        disc["win_rate"],
        "payoff_ratio":    disc["payoff_ratio"],
        "sharpe":          cont.get("sharpe_annualized", 0),
        "ann_volatility":  round(ann_vol, 1),
        "volatility_tier": _volatility_score(returns),
        "max_drawdown":    round(mdd, 1),
        "pnl_pct":         round(pnl_pct, 1),
        "raw_kelly":       round(raw_blend * 100, 1),
        "half_kelly":      round(final_pct * 100, 1),
        "analyst_upside":  analyst_upside_pct,
        "ai_penalty":      ai_penalty,
        "mu_daily":        cont.get("mu_daily", 0),
        "sigma_daily":     cont.get("sigma_daily", 0),
    }

def run_kelly_analysis(holdings: list[dict], total_portfolio: float,
                       analyst_targets: dict | None = None,
                       ai_verdicts: dict | None = None) -> dict:
    """Run Kelly on all holdings, return ranked results + portfolio summary."""
    results = []
    for h in holdings:
        sym   = h.get("symbol", "")
        eq    = float(h.get("equity", 0))
        cost  = float(h.get("average_buy_price", 0))
        price = float(h.get("current_price", 0))
        upside = None
        if analyst_targets and sym in analyst_targets:
            upside = analyst_targets[sym].get("upside_pct")
            
        eval_dict = None
        if ai_verdicts and sym in ai_verdicts:
            eval_dict = ai_verdicts[sym]
            
        r = kelly_size_holding(sym, eq, total_portfolio, cost, price, upside, eval_dict)
        results.append(r)

    # Portfolio-level stats
    allocated_pct   = sum(r["optimal_pct"] for r in results if r.get("optimal_pct"))
    over_positions  = [r for r in results if r.get("action") == "TRIM"]
    under_positions = [r for r in results if r.get("action") == "ADD"]
    avoid_positions = [r for r in results if r.get("action") == "AVOID"]

    return {
        "holdings":        sorted(results, key=lambda x: x.get("optimal_pct", 0), reverse=True),
        "total_optimal_allocated_pct": round(allocated_pct, 1),
        "cash_recommended_pct": round(max(0, 100 - allocated_pct), 1),
        "trim_list":  [r["symbol"] for r in over_positions],
        "add_list":   [r["symbol"] for r in under_positions],
        "avoid_list": [r["symbol"] for r in avoid_positions],
        "portfolio_kelly_score": round(
            np.mean([r["half_kelly"] for r in results if r.get("half_kelly", 0) > 0]) if results else 0, 1
        ),
    }
