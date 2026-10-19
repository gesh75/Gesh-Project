"""Read-only history paging for a caller-supplied fetch.

The fetch function is injected. This module does not open a network connection
and does not add tools to ``rh_mcp_data``'s allowlist.

A page with a cursor is followed. A page marked truncated, or at or above the
byte ceiling, is discarded and the date window is split. A single day that is
still truncated becomes a gap instead of another request.
"""
from __future__ import annotations

import csv
import json
import math
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

MCP_SAFE_BYTE_CEILING = 18_500
_OVERRIDE_FIELDS = ("date", "type", "amount", "source")

FetchPage = Callable[[str, str, str | None, str | None], Mapping[str, Any]]


@dataclass(frozen=True)
class HistoryGap:
    start: date
    end: date
    symbol: str | None
    reason: str


@dataclass
class HistoryCollect:
    orders: list[dict[str, Any]] = field(default_factory=list)
    gaps: list[HistoryGap] = field(default_factory=list)


def collect_pages(
    fetch: FetchPage,
    start: date | str,
    end: date | str,
    symbols: Sequence[str] | None = None,
    *,
    ceiling: int = MCP_SAFE_BYTE_CEILING,
) -> HistoryCollect:
    """Collect orders from ``start`` through ``end`` inclusive."""
    start_d = _to_date(start)
    end_d = _to_date(end)
    if end_d < start_d:
        raise ValueError("end is before start")
    collected = HistoryCollect()
    seen: set[str] = set()
    targets: list[str | None] = [s.upper() for s in symbols] if symbols else [None]
    for symbol in targets:
        _pull_window(fetch, start_d, end_d, symbol, collected, seen, ceiling, set())
    collected.orders.sort(key=lambda row: str(row.get("executed_at") or row.get("created_at") or ""))
    return collected


def load_statement_overrides(path: str | Path | None = None) -> list[dict[str, Any]]:
    """Load a local statement supplement. Unset env and no path yields an empty list.

    The file is not created here and must not be committed. A path that is set
    but missing raises, so a typo is not a silent empty history.
    """
    if path is None:
        env = os.environ.get("GESH_STATEMENT_OVERRIDES", "").strip()
        if not env:
            return []
        file_path = Path(env)
    else:
        file_path = Path(path)
    if not file_path.is_file():
        raise FileNotFoundError(file_path)
    if file_path.suffix.lower() == ".csv":
        with file_path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    else:
        parsed = json.loads(file_path.read_text(encoding="utf-8"))
        if not isinstance(parsed, list):
            raise ValueError("statement overrides must be a list")
        rows = parsed
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("each statement override must be an object")
        missing = [key for key in _OVERRIDE_FIELDS if key not in row or row[key] in (None, "")]
        if missing:
            raise ValueError("statement override missing " + ", ".join(missing))
        amount = float(row["amount"])
        if not math.isfinite(amount):
            raise ValueError("statement override amount must be finite")
        date.fromisoformat(str(row["date"])[:10])
        out.append(
            {
                "date": str(row["date"])[:10],
                "type": str(row["type"]),
                "amount": amount,
                "source": str(row["source"]),
            }
        )
    return out


def _pull_window(
    fetch: FetchPage,
    start: date,
    end: date,
    symbol: str | None,
    collected: HistoryCollect,
    seen: set[str],
    ceiling: int,
    cursors_seen: set[str],
    cursor: str | None = None,
) -> None:
    payload = fetch(start.isoformat(), end.isoformat(), symbol, cursor)
    if _truncated(payload, ceiling):
        if start < end:
            mid = start + timedelta(days=(end - start).days // 2)
            _pull_window(fetch, start, mid, symbol, collected, seen, ceiling, set())
            _pull_window(fetch, mid + timedelta(days=1), end, symbol, collected, seen, ceiling, set())
            return
        collected.gaps.append(
            HistoryGap(
                start=start,
                end=end,
                symbol=symbol,
                reason="truncated",
            )
        )
        return
    _take_results(payload.get("results") or [], seen, collected)
    next_cursor = payload.get("next_cursor")
    if not isinstance(next_cursor, str) or not next_cursor:
        return
    if next_cursor in cursors_seen:
        collected.gaps.append(
            HistoryGap(start=start, end=end, symbol=symbol, reason="cursor_loop")
        )
        return
    cursors_seen.add(next_cursor)
    _pull_window(
        fetch, start, end, symbol, collected, seen, ceiling, cursors_seen, cursor=next_cursor
    )


def _truncated(payload: Mapping[str, Any], ceiling: int) -> bool:
    if payload.get("truncated") is True:
        return True
    raw = payload.get("raw_bytes")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return False
    return int(raw) >= ceiling


def _take_results(
    results: Any,
    seen: set[str],
    collected: HistoryCollect,
) -> None:
    if not isinstance(results, list):
        return
    for order in results:
        if not isinstance(order, dict):
            continue
        oid = _order_id(order)
        if oid in seen:
            continue
        seen.add(oid)
        collected.orders.append(order)


def _order_id(order: Mapping[str, Any]) -> str:
    oid = order.get("id")
    if oid not in (None, ""):
        return str(oid)
    return "|".join(
        [
            str(order.get("symbol") or ""),
            str(order.get("executed_at") or order.get("created_at") or ""),
            str(order.get("quantity") or ""),
            str(order.get("side") or ""),
        ]
    )


def _to_date(value: date | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])
