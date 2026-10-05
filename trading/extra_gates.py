"""Entry-risk gates the engine enforced but the product gate did not (audit M-02).

SECTOR_CAP, LOSS_STREAK, CORRELATION_HEAT and EARNINGS_PROXIMITY. Rule #53 exists
because a correlated cohort once caused a 21.4% drawdown; the product config
documented these limits while ``evaluate()`` never enforced them.

Design rules (same as the rest of the gate):
  * PURE. Every input is data already stamped into the snapshot; there is no network
    call here (the engine's yfinance earnings lookup inside the gate is NOT ported).
  * BUY only. These are entry-risk gates; an exit must never be trapped by them.
  * Unknown is not safe and not unsafe. Missing or garbage input (NaN, bool, str) makes a
    gate PASS, but the gate is listed by :func:`unchecked_gates` and written to the decision
    log, so an inert gate can never be mistaken for protection.
"""

from __future__ import annotations

import logging
import math
import os
from typing import Any

from benchmarks import SECTOR_BENCHMARKS

from .config import RiskConfig
from .contract import TradeIntent

_log = logging.getLogger(__name__)

GATE_NAMES = ("EARNINGS_PROXIMITY", "SECTOR_CAP", "LOSS_STREAK", "CORRELATION_HEAT")
_DEFAULT_MAX_CORRELATED = 3


def _num(value: Any) -> float | None:
    """A finite real number, else None. bool/str/NaN/inf are all 'unknown'."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _positive(value: Any) -> float | None:
    number = _num(value)
    return number if number is not None and number > 0 else None


def hours_to_earnings(snapshot: dict[str, Any], symbol: str) -> float | None:
    table = snapshot.get("earnings_hours")
    return _num(table.get(symbol)) if isinstance(table, dict) else None


def cluster_size(snapshot: dict[str, Any], symbol: str) -> int | None:
    table = snapshot.get("correlation")
    number = _num(table.get(symbol)) if isinstance(table, dict) else None
    return None if number is None else int(number)


def max_correlated_positions() -> int:
    raw = os.getenv("MAX_CORRELATED_POSITIONS", str(_DEFAULT_MAX_CORRELATED))
    try:
        return int(raw)
    except ValueError:
        # A typo'd env value must not throw mid-evaluate() (after log_intent, before the
        # decision log); degrade to the shipped default.
        _log.warning("MAX_CORRELATED_POSITIONS is not an int — using %d", _DEFAULT_MAX_CORRELATED)
        return _DEFAULT_MAX_CORRELATED


def loss_streak(history: Any) -> int:
    """Consecutive most-recent days with negative P&L.

    A day whose ``pnl_pct`` is missing or unusable ENDS the count: nothing proves it was a
    loss, and it is not invented as a win either.
    """
    if not isinstance(history, list):
        return 0
    streak = 0
    for day in reversed(history):
        pnl = _num(day.get("pnl_pct")) if isinstance(day, dict) else None
        if pnl is None or pnl >= 0:
            break
        streak += 1
    return streak


def sector_weight_after_buy(snapshot: dict[str, Any], symbol: str, target_pct: float) -> float | None:
    """Sector weight AFTER the buy, or None when it cannot be known.

    The symbol's own position is REPLACED by ``max(target, current weight)`` — the engine
    added the target on top of an existing holding and so double-counted it.
    """
    sector = SECTOR_BENCHMARKS.get(symbol.upper())
    total = _positive((snapshot.get("equity_summary") or {}).get("portfolio_equity"))
    if sector is None or total is None:
        return None
    own = 0.0
    others = 0.0
    for holding in snapshot.get("holdings") or []:
        sym = str(holding.get("symbol") or "").upper()
        if SECTOR_BENCHMARKS.get(sym) != sector:
            continue
        equity = _num(holding.get("equity"))
        if equity is None or equity < 0:
            return None  # a same-sector position of unknown size: the weight is unknowable
        if sym == symbol.upper():
            own += equity
        else:
            others += equity
    return others / total + max(_num(target_pct) or 0.0, own / total)


def unchecked_gates(intent: TradeIntent, snapshot: dict[str, Any]) -> list[str]:
    """Entry gates that had no usable input for this intent (BUY only)."""
    if intent.action != "BUY":
        return []
    out: list[str] = []
    if hours_to_earnings(snapshot, intent.symbol) is None:
        out.append("EARNINGS_PROXIMITY")
    if sector_weight_after_buy(snapshot, intent.symbol, intent.target_position_pct or 0.0) is None:
        out.append("SECTOR_CAP")
    history = snapshot.get("daily_pnl_history")
    if not isinstance(history, list) or not history:
        out.append("LOSS_STREAK")
    if cluster_size(snapshot, intent.symbol) is None:
        out.append("CORRELATION_HEAT")
    return out


def check_entry_gates(
    intent: TradeIntent,
    snapshot: dict[str, Any],
    cfg: RiskConfig,
    *,
    blocked: bool,
) -> tuple[dict[str, bool], str | None]:
    """Evaluate the four gates for a BUY. Returns (gate map, first rejection reason).

    ``blocked`` means an earlier gate already rejected: nothing is evaluated and every gate
    reads False, matching how the other gates report after a rejection.
    """
    if blocked:
        return {name: False for name in GATE_NAMES}, None

    gates = {name: True for name in GATE_NAMES}
    reason: str | None = None
    symbol = intent.symbol

    hours = hours_to_earnings(snapshot, symbol)
    if hours is not None and 0 < hours < cfg.earnings_proximity_hours:
        gates["EARNINGS_PROXIMITY"] = False
        reason = f"Earnings in {hours:.0f}h — BUY blocked within {cfg.earnings_proximity_hours}h window"

    if reason is None:
        weight = sector_weight_after_buy(snapshot, symbol, intent.target_position_pct or 0.0)
        if weight is not None and weight > cfg.max_sector_pct:
            gates["SECTOR_CAP"] = False
            reason = f"Sector exposure {weight:.0%} exceeds cap {cfg.max_sector_pct:.0%}"

    if reason is None:
        streak = loss_streak(snapshot.get("daily_pnl_history"))
        if streak >= cfg.max_consecutive_loss_days:
            gates["LOSS_STREAK"] = False
            reason = (
                f"Auto-suspended: {streak} consecutive loss days "
                f"(limit={cfg.max_consecutive_loss_days})"
            )

    if reason is None:
        cluster = cluster_size(snapshot, symbol)
        cap = max_correlated_positions()
        if cluster is not None and cluster > cap:
            gates["CORRELATION_HEAT"] = False
            reason = (
                f"Correlation cluster {cluster} exceeds cap {cap} — "
                f"{symbol} adds correlated beta exposure"
            )

    # Gates after the first rejection were not evaluated: report them False like the rest.
    if reason is not None:
        failed_at = next(i for i, name in enumerate(GATE_NAMES) if not gates[name])
        for name in GATE_NAMES[failed_at + 1:]:
            gates[name] = False
    return gates, reason
