"""Kelly quality check (audit M-14).

When most names land on one identical ``optimal_pct`` (every name hit the
per-name cap, then the book was rescaled to a common gross factor), Kelly is
publishing *equal weight* as an optimum. Every ADD/TRIM derived from it is
merely "distance from equal weight", not an edge estimate.

The producing code lives in gesh-core (SHA-pinned, shared by both production
services), so the product detects the condition from the result instead of
changing that body. Pure and total: junk in => ``degenerate=False``, never raises.
"""
from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping

MIN_SIZED_NAMES = 5      # fewer names than this: identical values are coincidence
PINNED_SHARE = 0.60      # >= 60% of sized names on one value => uninformative
CLUSTER_STEP_PCT = 0.1   # values are bucketed to 0.1 percentage points

KELLY_UNINFORMATIVE_NOTE = (
    "Kelly is uninformative on this book: most names share one identical target "
    "weight (equal weight), so ADD/TRIM here reflect distance from equal weight, "
    "not a per-name edge."
)


def _sized_values(kelly: object) -> list[float]:
    if not isinstance(kelly, Mapping):
        return []
    inner = kelly.get("data")
    if isinstance(inner, Mapping):  # the server's {"status", "data": {...}} envelope
        kelly = inner
    holdings = kelly.get("holdings")
    if isinstance(holdings, list):  # run_kelly_analysis / POST shape: {"holdings": [rows]}
        rows: list[object] = list(holdings)
    else:  # daily-chain shape: {symbol: row}
        rows = list(kelly.values())
    out: list[float] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        v = row.get("optimal_pct")
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        f = float(v)
        if math.isfinite(f) and f > 0:
            out.append(f)
    return out


def quality_block(kelly: object) -> dict:
    """Degeneracy result plus the customer-facing note (``None`` when healthy)."""
    q = kelly_degeneracy(kelly)
    return {**q, "note": KELLY_UNINFORMATIVE_NOTE if q["degenerate"] else None}


def kelly_degeneracy(kelly: object) -> dict:
    """Return ``{degenerate, modal_pct, pinned, sized}`` for a symbol->row Kelly map."""
    values = _sized_values(kelly)
    sized = len(values)
    if sized == 0:
        return {"degenerate": False, "modal_pct": None, "pinned": 0, "sized": 0}
    buckets = Counter(round(v / CLUSTER_STEP_PCT) for v in values)
    bucket, pinned = buckets.most_common(1)[0]
    modal = sum(v for v in values if round(v / CLUSTER_STEP_PCT) == bucket) / pinned
    degenerate = sized >= MIN_SIZED_NAMES and pinned / sized >= PINNED_SHARE
    return {"degenerate": degenerate, "modal_pct": modal, "pinned": pinned, "sized": sized}
