#!/usr/bin/env python3
"""gesh_exit_advice.py — advisory exit-band table for GESH holdings.

Read-only bridge between GESH's engine (``engine_results.json``) and the
canonical vol-adaptive exit math in :mod:`risk_bands`. For every holding it
emits the take-profit / stop-loss band scaled to that name's Kelly volatility
tier, plus the trailing-stop give-back that would engage once the TP band is
reached.

This is ADVISORY ONLY — it surfaces exit levels in GESH reports/screens for the
MAIN book. It places no orders and is not wired to any broker. Live execution of
these bands is ring-fenced to the agentic experiment account in
``agentic-rh-test``.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from risk_bands import TRAIL_MULT, TRAIL_MAX, TRAIL_MIN, exit_bands

ENGINE = Path(__file__).with_name("engine_results.json")


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def exit_advice(
    holdings: list[str],
    kelly: Mapping[str, Mapping[str, object]],
    *,
    base_tp: float = 8.0,
    base_sl: float = 5.0,
    base_trail: float = 4.0,
) -> list[dict[str, object]]:
    """Per-holding advisory exit bands. Names without a Kelly tier fall back to
    MEDIUM (the base bands), so every holding is covered."""
    # Normalize Kelly keys once so lookup and the tracked-flag agree even if the
    # engine emits a lower-case symbol key.
    kelly_by_symbol = {str(k).upper(): v for k, v in kelly.items()}
    out: list[dict[str, object]] = []
    for sym in holdings:
        sym = str(sym).upper()
        row = kelly_by_symbol.get(sym, {}) or {}
        tier = str(row.get("volatility_tier", "MEDIUM")).upper()
        bands = exit_bands(tier, base_tp=base_tp, base_sl=base_sl)
        trail_pct = _clamp(round(base_trail * TRAIL_MULT.get(tier, 1.0), 2), TRAIL_MIN, TRAIL_MAX)
        out.append({
            "symbol": sym,
            "volatility_tier": tier,
            "take_profit_pct": bands["take_profit_pct"],
            "stop_loss_pct": bands["stop_loss_pct"],
            "trailing_pct": trail_pct,
            "tracked_by_kelly": sym in kelly_by_symbol,
        })
    return out


def exit_advice_with_lessons(
    holdings: list[str],
    kelly: Mapping[str, Mapping[str, object]],
    positions: Sequence[Mapping[str, Any]] | None = None,
    **lesson_kwargs: Any,
) -> dict[str, object]:
    """Exit bands plus lesson notes. ``exit_advice`` itself is unchanged.

    Imported inside the function: ``trading``'s package init loads gesh-core
    state, and the band table must stay importable without that.
    """
    from trading.portfolio_lessons import lesson_notes

    bands = exit_advice(holdings, kelly)
    notes = [] if not positions else lesson_notes(positions=positions, **lesson_kwargs)
    return {
        "bands": bands,
        "lesson_notes": [note.as_dict() for note in notes],
    }


def _holdings_from_engine(engine: Mapping[str, object]) -> list[str]:
    rows = engine.get("holdings", []) or []
    syms: list[str] = []
    for r in rows:
        if isinstance(r, Mapping):
            s = r.get("symbol") or r.get("ticker")
            if s:
                syms.append(str(s).upper())
    return syms


def main() -> None:
    if not ENGINE.exists():
        raise SystemExit(f"missing {ENGINE}")
    engine = json.loads(ENGINE.read_text())
    holdings = _holdings_from_engine(engine)
    kelly = engine.get("kelly", {}) or {}
    table = exit_advice(holdings, kelly)
    print(f"\nGESH advisory exit bands — {len(table)} holdings (ADVISORY ONLY, no execution)\n")
    print(f"{'sym':<8}{'tier':<8}{'+TP%':>7}{'-SL%':>7}{'trail%':>8}  kelly")
    print("-" * 46)
    for r in table:
        print(f"{r['symbol']:<8}{r['volatility_tier']:<8}"
              f"{r['take_profit_pct']:>7}{r['stop_loss_pct']:>7}{r['trailing_pct']:>8}"
              f"  {'yes' if r['tracked_by_kelly'] else 'fallback'}")


if __name__ == "__main__":
    main()
