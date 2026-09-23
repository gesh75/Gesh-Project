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

import copy
import math


def canonical_pct_diff(fair_value: dict | None) -> float:
    """(estimate - current) / |current| * 100, rounded 2dp.

    +% = estimate ABOVE price (undervalued / upside), matching the dashboard and the
    fvb-under/over badges. Returns 0.0 (neutral — no tilt) when estimate/current are
    missing, boolean, non-finite, or current == 0, rather than trusting the model's own value.
    """
    if not isinstance(fair_value, dict):
        return 0.0
    est_raw = fair_value.get("estimate")
    cur_raw = fair_value.get("current")
    # True is not $1 of estimate/price — same honesty class as optional_float.
    if isinstance(est_raw, bool) or isinstance(cur_raw, bool):
        return 0.0
    try:
        est = float(est_raw)
        cur = float(cur_raw)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(est) or not math.isfinite(cur) or cur == 0:
        return 0.0
    return round((est - cur) / abs(cur) * 100, 2)


def canonicalize_analysis(result: dict) -> dict:
    """Return a COPY of a deep-analysis result with every holding's
    `fair_value.pct_diff` rewritten to the canonical convention.

    Call this at the WRITE boundary (before persisting to engine_results.json, the
    analysis-history DB, or a report). Codex #2 canonicalized only the read sites that
    feed Kelly, so stored results kept the model's own convention and reports/history
    disagreed with the trading decision (Codex #4). Canonicalizing on write makes the
    persisted value identical to what the read sites recompute.

    Never raises and never mutates the input — it sits on the analysis write path, so a
    malformed model payload must degrade to "unchanged", not break the run.
    """
    if not isinstance(result, dict):
        return result
    holdings = result.get("holdings_analysis")
    if not isinstance(holdings, list):
        return result
    out = copy.deepcopy(result)
    for item in out.get("holdings_analysis") or []:
        if isinstance(item, dict) and isinstance(item.get("fair_value"), dict):
            item["fair_value"]["pct_diff"] = canonical_pct_diff(item["fair_value"])
    return out
