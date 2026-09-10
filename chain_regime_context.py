"""Honest regime-prompt + Rule 5 helpers for the daily AI step.

``run_ai2.py`` used to coerce missing ADX/RSI/BB/%B with ``or 0``. A failed
OHLCV fetch then injected ``RSI=0.0`` (maximally oversold) into Claude and
made Rule 5 (``RSI >= 70``) silently unable to fire. These helpers keep the
None-safe crash guard without inventing a measured zero.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any


def finite_regime_metric(value: Any) -> float | None:
    """A usable ADX/RSI/BB/%B print. Bool/NaN/inf/blank stay unknown."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _fmt_metric(value: float | None, *, digits: int, suffix: str = "") -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}{suffix}"


def format_regime_line(row: Mapping[str, Any] | None) -> str:
    """One Claude-prompt line. Missing metrics render as n/a, never 0.0."""
    data = row if isinstance(row, Mapping) else {}
    symbol = str(data.get("symbol") or "?").strip() or "?"
    regime = str(data.get("regime") or "UNKNOWN")
    adx = finite_regime_metric(data.get("adx"))
    rsi = finite_regime_metric(data.get("rsi"))
    bb_width = finite_regime_metric(data.get("bb_width"))
    pct_b = finite_regime_metric(data.get("pct_b"))
    return (
        f"  {symbol}: {regime} | ADX={_fmt_metric(adx, digits=1)} "
        f"RSI={_fmt_metric(rsi, digits=1)} "
        f"BB-Width={_fmt_metric(bb_width, digits=1, suffix='%')} "
        f"%B={_fmt_metric(pct_b, digits=0, suffix='%')}"
    )


def build_regime_summary(
    regimes: Any,
    macro_summary: Any = "",
) -> str:
    """Full MARKET REGIME CONTEXT block injected into deep analysis."""
    rows = [row for row in (regimes or []) if isinstance(row, Mapping)]
    body = "\n".join(format_regime_line(row) for row in rows) or "  No per-symbol regime data"
    macro = str(macro_summary or "").strip() or "FRED macro: unavailable (use yfinance proxies)"
    return "MARKET REGIME CONTEXT (ADX/BB/RSI per symbol):\n" + body + f"\n\nMACRO: {macro}"


def collect_rsi_add_overrides(
    regimes: Any,
    kelly: Mapping[str, Any] | None,
) -> dict[str, str]:
    """Rule 5: finite RSI >= 70 turns a Kelly ADD into HOLD.

    Missing/bool/NaN RSI does not fire — that used to become 0.0, which never
    overrode but still printed as a measured oversold print in the prompt.
    """
    kelly = kelly if isinstance(kelly, Mapping) else {}
    overrides: dict[str, str] = {}
    for row in regimes or []:
        if not isinstance(row, Mapping):
            continue
        symbol = str(row.get("symbol") or "").strip()
        if not symbol:
            continue
        rsi = finite_regime_metric(row.get("rsi"))
        if rsi is None or rsi < 70:
            continue
        action = str((kelly.get(symbol) or {}).get("action") or "HOLD").upper()
        if action == "ADD":
            overrides[symbol] = f"RSI={rsi:.0f}≥70 → Kelly ADD overridden to HOLD"
    return overrides
