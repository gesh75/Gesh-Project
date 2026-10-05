"""Paper-book lifecycle registry (audit item 14).

A paper book can be *retired*: it stops opening new simulated positions, but its
ledger stays readable and on the public proof page forever (no deletion, no
survivorship bias), and positions it already holds are still closed by their
stops/targets (an exit is never trapped).

Retirement is a deliberate, reviewed code change: there is no env var or runtime
toggle that revives a book. Relaunching means a NEW pre-registered experiment
(see ``relaunch_requirements``), not flipping this entry back.

Pure and total: no I/O, never raises.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

DAILY_ANALYSIS = "daily-analysis"


@dataclass(frozen=True)
class RetiredBook:
    source: str
    retired_on: str
    reason: str
    relaunch_requirements: tuple[str, ...]


RETIRED_BOOKS: Mapping[str, RetiredBook] = {
    DAILY_ANALYSIS: RetiredBook(
        source=DAILY_ANALYSIS,
        retired_on="2026-10-04",
        reason=(
            "Retired after an implementation defect was found: its sizing signal "
            "(Kelly) published an equal-weight allocation as an optimum, so its "
            "ADD/TRIM trades were distance-from-equal-weight rebalances, not "
            "per-name edge estimates. The record is kept in full."
        ),
        relaunch_requirements=(
            "the Kelly equal-weight root cause is fixed upstream",
            "a hypothesis and a sample-size target are written down before the first trade",
            "a sequential kill rule (CUSUM/SPRT) is pre-registered",
            "the relaunch is counted as one additional trial in the DSR registry",
        ),
    ),
}

RETIRED_HOLD_PREFIX = "Book retired"


def retired_book(source: object) -> RetiredBook | None:
    return RETIRED_BOOKS.get(source) if isinstance(source, str) else None


def is_retired(source: object) -> bool:
    return retired_book(source) is not None


def hold_if_retired(action_plan: Iterable[Mapping[str, Any]], source: str) -> list[dict]:
    """Turn every position-opening action into a HOLD when ``source`` is retired.

    Only the plan is rewritten. Auto-close of already-open positions runs before the
    plan is built and is untouched, so a retired book can still exit. Inputs are
    never mutated. A non-retired source returns an equal copy.
    """
    book = retired_book(source)
    out: list[dict] = []
    for row in action_plan:
        item = dict(row)
        if book is not None and str(item.get("action") or "").upper() in ("BUY", "ADD", "TRIM", "SELL"):
            item["action"] = "HOLD"
            item["shares"] = 0
            item["signal_reason"] = (
                f"{RETIRED_HOLD_PREFIX} ({book.retired_on}): no new simulated entries; "
                f"the ledger stays public on the track-record page"
            )
        out.append(item)
    return out
