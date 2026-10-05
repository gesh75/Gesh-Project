"""Stamp the entry-gate inputs the paper chain can prove locally (audit M-02).

The four gates in ``trading.extra_gates`` are pure: a missing input passes and is
recorded as unchecked. The daily chain built a snapshot with holdings and equity
only, so LOSS_STREAK, CORRELATION_HEAT and EARNINGS_PROXIMITY never saw data.

This module fills only what is already known:

* ``daily_pnl_history`` from closed paper rows. A day's ``pnl_pct`` is the sign
  carrier for that day's summed realized dollars (the streak gate reads the sign,
  not the percent). Days are oldest-first.
* ``earnings_hours`` and ``correlation`` only when the engine payload already
  carries those dicts. Nothing here calls the network, and nothing is invented.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def loss_days_from_closes(rows: list[dict] | None) -> list[dict[str, Any]]:
    """One row per exit date, oldest first. Open rows and undated closes are skipped."""
    if not isinstance(rows, list):
        return []
    by_day: dict[str, float] = defaultdict(float)
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("outcome") or "").upper() == "OPEN":
            continue
        exit_time = str(row.get("exit_time") or "").strip()
        if len(exit_time) < 10:
            continue
        pnl = _finite(row.get("realized_pnl"))
        if pnl is None:
            continue
        by_day[exit_time[:10]] += pnl
    return [
        {"date": day, "pnl_pct": by_day[day]}
        for day in sorted(by_day)
    ]


def stamp_entry_inputs(
    snapshot: dict[str, Any],
    *,
    closes: list[dict] | None = None,
    engine: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fill missing gate inputs. Never overwrites a value the caller already set."""
    if "daily_pnl_history" not in snapshot:
        history = loss_days_from_closes(closes)
        if history:
            snapshot["daily_pnl_history"] = history
    source = engine if isinstance(engine, dict) else {}
    for key in ("earnings_hours", "correlation"):
        if key not in snapshot and isinstance(source.get(key), dict):
            snapshot[key] = source[key]
    return snapshot
