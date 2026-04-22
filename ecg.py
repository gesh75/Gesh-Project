"""
ecg.py — Equity Curve Gate (ECG) state machine.

Evaluates per-holding health based on stored equity snapshots in SQLite.
Classifies each holding into one of three states:
  🟢 HEALTHY    — equity curve is positive or recovering
  🟡 COOLING    — drawdown between threshold1 and threshold2
  🔴 SUSPENDED  — severe drawdown or sustained loss streak

Also calculates a portfolio-level gate status.
"""

from __future__ import annotations

from typing import Any
import numpy as np

# ─── Thresholds ───────────────────────────────────────────────────────────────
COOLING_DRAWDOWN_PCT   = -8.0    # % change from peak → enters COOLING
SUSPENDED_DRAWDOWN_PCT = -20.0   # % change from peak → enters SUSPENDED
LOSS_STREAK_COOLING    = 3       # consecutive negative sessions for COOLING
LOSS_STREAK_SUSPENDED  = 6       # consecutive negative sessions for SUSPENDED
MIN_SNAPSHOTS          = 2       # min data points needed for meaningful ECG


def _compute_drawdown_from_peak(equities: list[float]) -> float:
    """Max drawdown from running peak, as a percentage."""
    if not equities or len(equities) < 2:
        return 0.0
    peak = equities[0]
    current = equities[-1]
    if peak <= 0:
        return 0.0
    return (current - peak) / peak * 100.0


def _loss_streak(pct_changes: list[float]) -> int:
    """Count the most recent consecutive negative sessions."""
    streak = 0
    for chg in reversed(pct_changes):
        if chg < 0:
            streak += 1
        else:
            break
    return streak


def _classify(drawdown_pct: float, streak: int) -> str:
    if drawdown_pct <= SUSPENDED_DRAWDOWN_PCT or streak >= LOSS_STREAK_SUSPENDED:
        return "SUSPENDED"
    if drawdown_pct <= COOLING_DRAWDOWN_PCT or streak >= LOSS_STREAK_COOLING:
        return "COOLING"
    return "HEALTHY"


def compute_ecg(holdings: list[dict], history_fn) -> dict[str, Any]:
    """
    Compute ECG state for each holding.

    Parameters
    ----------
    holdings    : list of current holding dicts (from data_fetcher)
    history_fn  : callable(symbol) -> list[dict] with keys
                  {snapshot_at, equity, percent_change, current_price}
                  (use db.get_equity_history)

    Returns
    -------
    {
      "states": [
        {symbol, state, drawdown_pct, loss_streak, snapshots_available,
         equity_history: [float], message}
      ],
      "portfolio_gate": "HEALTHY" | "COOLING" | "SUSPENDED",
      "portfolio_message": str
    }
    """
    states: list[dict] = []

    for h in holdings:
        symbol = h["symbol"]
        history = history_fn(symbol)   # newest-first list

        if len(history) < MIN_SNAPSHOTS:
            # Not enough data yet — optimistically mark as HEALTHY
            states.append({
                "symbol":              symbol,
                "state":               "HEALTHY",
                "drawdown_pct":        0.0,
                "loss_streak":         0,
                "snapshots_available": len(history),
                "equity_history":      [h.get("equity", 0)],
                "message":             f"Insufficient history ({len(history)} snapshot{'s' if len(history)!=1 else ''}). Accumulating data.",
            })
            continue

        # Oldest → newest for correct running-peak computation
        eq_list  = [r["equity"]         for r in reversed(history)]
        pct_list = [r["percent_change"] for r in reversed(history)]

        drawdown   = _compute_drawdown_from_peak(eq_list)
        streak     = _loss_streak(pct_list)
        state      = _classify(drawdown, streak)

        msg_map = {
            "HEALTHY":   f"Equity curve healthy. Drawdown from peak: {drawdown:.1f}%",
            "COOLING":   f"⚠ Cooling — drawdown {drawdown:.1f}%, {streak}-session loss streak. Consider reducing size.",
            "SUSPENDED": f"🔴 Suspended — severe drawdown {drawdown:.1f}% or {streak}-session streak. Consider exiting.",
        }

        states.append({
            "symbol":              symbol,
            "state":               state,
            "drawdown_pct":        round(drawdown, 2),
            "loss_streak":         streak,
            "snapshots_available": len(history),
            "equity_history":      [round(e, 2) for e in eq_list[-30:]],  # last 30 pts
            "message":             msg_map[state],
        })

    # Portfolio-level gate: worst state wins
    all_states = [s["state"] for s in states]
    if "SUSPENDED" in all_states:
        port_gate = "SUSPENDED"
        port_msg  = f"{all_states.count('SUSPENDED')} holding(s) in SUSPENDED state. Review immediately."
    elif "COOLING" in all_states:
        port_gate = "COOLING"
        port_msg  = f"{all_states.count('COOLING')} holding(s) cooling. Monitor closely."
    else:
        port_gate = "HEALTHY"
        port_msg  = "All holdings within healthy equity curve bounds."

    return {
        "states":            states,
        "portfolio_gate":    port_gate,
        "portfolio_message": port_msg,
    }
