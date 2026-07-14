"""The ONE canonical fair-value convention, server-side.

The model fills `fair_value.pct_diff` with a non-deterministic formula (sometimes
(est-cur)/cur, sometimes (cur-est)/est), so the dashboard recomputes it as
(estimate - current)/|current| * 100 (see daFvPct in dashboard.html). Kelly sizing,
however, was still consuming the raw model `pct_diff` — so the UI and the trading
decision could disagree on whether a name is over/undervalued (Codex finding #2).

This helper is the single source of truth on the Python side; the JS `daFvPct`
mirrors the exact same formula. Use it anywhere a fair-value gap feeds a decision.
"""
from __future__ import annotations

import math


def canonical_pct_diff(fair_value: dict | None) -> float:
    """(estimate - current) / |current| * 100, rounded 2dp.

    +% = estimate ABOVE price (undervalued / upside), matching the dashboard and the
    fvb-under/over badges. Returns 0.0 (neutral — no tilt) when estimate/current are
    missing, non-finite, or current == 0, rather than trusting the model's own value.
    """
    if not isinstance(fair_value, dict):
        return 0.0
    try:
        est = float(fair_value.get("estimate"))
        cur = float(fair_value.get("current"))
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(est) or not math.isfinite(cur) or cur == 0:
        return 0.0
    return round((est - cur) / abs(cur) * 100, 2)
