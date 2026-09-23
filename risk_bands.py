#!/usr/bin/env python3
"""risk_bands.py — canonical, broker-agnostic exit-risk math for the GESH stack.

This is the single source of truth for the vol-adaptive exit logic that the
agentic Robinhood trader (``agentic-rh-test``) pioneered. It is pure math:

  * ``exit_bands``   — vol-adaptive take-profit / stop-loss percentages.
  * ``trailing_stop``— peak-anchored trailing stop that engages only AFTER the
                       take-profit band is reached (locks gains, never loosens
                       the initial fixed stop).
  * ``exit_analytics``— exit-reason counts + run-sampled MFE/MAE across closed
                       trades, used for the post-30-trade band review.

Design contract (mirrors the agentic safety contract):
  * No broker / MCP / network imports — importable from any GESH module or test.
  * Every leg is clamped so a corrupt config can never yield a 0% or runaway band.
  * The trailing stop can only ever TIGHTEN risk relative to the fixed stop.
"""
from __future__ import annotations

import math
from typing import Iterable, Mapping

# --- vol-adaptive bands ----------------------------------------------------
# Scale the base TP/SL by a per-name volatility tier, keeping risk/reward ratio
# constant (MEDIUM = the base anchor). Calm names exit tighter; volatile names
# get more room so normal swings don't stop them out prematurely. Absolute
# dollar risk stays bounded upstream by the per-trade / per-day caps.
BAND_MULT: dict[str, float] = {"LOW": 0.6, "MEDIUM": 1.0, "HIGH": 1.5, "EXTREME": 2.0}
BAND_TP_MIN, BAND_TP_MAX = 4.0, 15.0
BAND_SL_MIN, BAND_SL_MAX = 3.0, 10.0
DEFAULT_BASE_TP_PCT = 8.0
DEFAULT_BASE_SL_PCT = 5.0

# Trailing-stop give-back from peak, by tier. Wider for volatile names. Clamped
# so a bad tier can't produce a 0% (instant exit) or runaway trail.
TRAIL_MULT: dict[str, float] = {"LOW": 0.6, "MEDIUM": 1.0, "HIGH": 1.5, "EXTREME": 2.0}
TRAIL_MIN, TRAIL_MAX = 2.0, 8.0
DEFAULT_BASE_TRAIL_PCT = 4.0

VALID_EXIT_REASONS = {"take_profit", "stop", "trailing_stop", "gesh_bearish", "manual"}


def _f(x: object, default: float | None = None) -> float | None:
    # bool is an int subclass: True must not become $1.00 entry/peak.
    if isinstance(x, bool):
        return default
    try:
        v = float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return default
    return v if math.isfinite(v) else default


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def exit_bands(
    volatility_tier: object,
    *,
    base_tp: float = DEFAULT_BASE_TP_PCT,
    base_sl: float = DEFAULT_BASE_SL_PCT,
) -> dict[str, float]:
    """Vol-adaptive take-profit / stop-loss percentages (positive magnitudes).

    Unknown/missing tier -> MEDIUM -> the base bands (safe no-op). Both legs are
    clamped so a bad config can never yield a 0% or runaway stop.
    """
    mult = BAND_MULT.get(str(volatility_tier).upper(), 1.0)
    tp = _clamp(round(base_tp * mult, 2), BAND_TP_MIN, BAND_TP_MAX)
    sl = _clamp(round(base_sl * mult, 2), BAND_SL_MIN, BAND_SL_MAX)
    return {"take_profit_pct": tp, "stop_loss_pct": sl}


def trailing_stop(
    entry: float,
    peak: float,
    volatility_tier: object,
    *,
    base_tp: float = DEFAULT_BASE_TP_PCT,
    base_sl: float = DEFAULT_BASE_SL_PCT,
    base_trail: float = DEFAULT_BASE_TRAIL_PCT,
) -> dict[str, object]:
    """Peak-anchored trailing stop that engages only AFTER the TP band is hit.

    ``peak`` is the highest price observed since entry (run-sampled high-water
    mark). The trail activates once ``peak`` has reached the vol-adaptive
    take-profit band; until then the position rides its fixed vol-adaptive stop
    and this returns ``active=False`` with the fixed-stop level as ``stop_price``.

    Once active, the stop is ``peak * (1 - trail_pct/100)`` but is floored at the
    fixed-stop level so the trail can only ever TIGHTEN risk, never loosen it.
    """
    entry_f = _f(entry)
    peak_f = _f(peak)
    bands = exit_bands(volatility_tier, base_tp=base_tp, base_sl=base_sl)
    fixed_stop_pct = bands["stop_loss_pct"]

    if not entry_f or entry_f <= 0 or peak_f is None:
        # Degenerate input -> fall back to the fixed stop, inactive trail.
        fixed_price = round(max(0.0, (entry_f or 0.0)) * (1 - fixed_stop_pct / 100), 4)
        return {
            "active": False,
            "stop_price": fixed_price,
            "trail_pct": None,
            "tp_band_pct": bands["take_profit_pct"],
            "reason": "invalid_entry_or_peak",
        }

    tp_pct = bands["take_profit_pct"]
    tp_trigger_price = entry_f * (1 + tp_pct / 100)
    fixed_stop_price = round(entry_f * (1 - fixed_stop_pct / 100), 4)

    # Compare rounded money values so a float-representation wart (e.g.
    # 100*1.12 = 112.00000000000001) can't keep an at-the-band peak inactive.
    if round(peak_f, 6) < round(tp_trigger_price, 6):
        return {
            "active": False,
            "stop_price": fixed_stop_price,
            "trail_pct": None,
            "tp_band_pct": tp_pct,
            "reason": f"peak {peak_f:.4f} < TP trigger {tp_trigger_price:.4f}",
        }

    mult = TRAIL_MULT.get(str(volatility_tier).upper(), 1.0)
    trail_pct = _clamp(round(base_trail * mult, 2), TRAIL_MIN, TRAIL_MAX)
    trail_price = peak_f * (1 - trail_pct / 100)
    # Floor at the fixed stop so the trail only ever tightens risk.
    stop_price = round(max(trail_price, fixed_stop_price), 4)
    return {
        "active": True,
        "stop_price": stop_price,
        "trail_pct": trail_pct,
        "tp_band_pct": tp_pct,
        "reason": f"trailing {trail_pct:.2f}% below peak {peak_f:.4f}",
    }


def _num(value: object) -> float | None:
    s = str(value or "").strip().replace("$", "").replace(",", "")
    if not s:
        return None
    try:
        v = float(s)
    except (TypeError, ValueError, OverflowError):
        return None
    return v if math.isfinite(v) else None


def _norm_reason(raw: object) -> str:
    """Normalize exit_reason to the valid set. Blank -> 'unspecified'; anything
    outside the set -> 'invalid' so typos can't fragment the stats."""
    s = str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not s:
        return "unspecified"
    return s if s in VALID_EXIT_REASONS else "invalid"


def exit_analytics(rows: Iterable[Mapping[str, object]]) -> dict[str, object]:
    """Exit-reason counts + mean run-sampled MFE/MAE across CLOSED trades.

    MFE/MAE are run-sampled (observed once per scheduled run), not true intraday
    excursions. Averages include ONLY fully-tracked rows (non-blank
    volatility_tier + tp_pct + sl_pct) so partially-sampled legacy rows can't
    contaminate the review.
    """
    reasons: dict[str, int] = {}
    mfe: list[float] = []
    mae: list[float] = []
    n_untracked = 0
    for r in rows:
        if _num(r.get("agent_pnl", "")) is None:
            continue  # still open
        reason = _norm_reason(r.get("exit_reason", ""))
        reasons[reason] = reasons.get(reason, 0) + 1
        tracked = all(str(r.get(k) or "").strip() for k in ("volatility_tier", "tp_pct", "sl_pct"))
        if not tracked:
            n_untracked += 1
            continue
        f, a = _num(r.get("mfe_pct", "")), _num(r.get("mae_pct", ""))
        if f is not None:
            mfe.append(f)
        if a is not None:
            mae.append(a)
    return {
        "exit_reasons": reasons,
        "avg_mfe_pct": round(sum(mfe) / len(mfe), 2) if mfe else None,
        "avg_mae_pct": round(sum(mae) / len(mae), 2) if mae else None,
        "n_excursion": len(mfe),
        "n_untracked_closed": n_untracked,
    }
