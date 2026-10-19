"""Advisory notes from a book. These notes do not approve or block an order.

Thresholds are arguments. A missing input is skipped: unknown is not a warning
and not a pass. Nothing here places, cancels, or resizes an order, and a
narrative from another module cannot waive a note.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any


@dataclass(frozen=True)
class LessonNote:
    lesson_id: str
    severity: str
    symbol: str | None
    text: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def lesson_notes(
    *,
    positions: Sequence[Mapping[str, Any]] | None = None,
    closed_lots: Sequence[Mapping[str, Any]] | None = None,
    as_of: date | str | None = None,
    total_nav: float | None = None,
    order: Mapping[str, Any] | None = None,
    settled_cash: float | None = None,
    cash_usd: float | None = None,
    cash_band_pct: tuple[float, float] | None = None,
    min_notional: float | None = None,
    cluster_map: Mapping[str, Sequence[str]] | None = None,
    cluster_cap_pct: Mapping[str, float] | None = None,
    combined_caps: Sequence[Mapping[str, Any]] | None = None,
    trim_multiple: float = 2.0,
    trail_from_peak: float = 0.20,
    peak_arm_multiple: float = 1.5,
    reentry_days: int = 30,
    stub_weight_pct: float = 1.0,
    short_hold_days: int = 10,
) -> list[LessonNote]:
    """Return lesson notes for the supplied book. Empty inputs return no notes."""
    rows = list(positions or [])
    lots = list(closed_lots or [])
    today = _parse_date(as_of)
    nav = _positive(total_nav)
    notes: list[LessonNote] = []

    notes.extend(_size_notes(order, settled_cash, min_notional))
    notes.extend(
        _reentry_notes(
            order=order,
            positions=rows,
            lots=lots,
            as_of=today,
            reentry_days=reentry_days,
        )
    )
    for pos in rows:
        notes.extend(
            _position_notes(
                pos,
                as_of=today,
                nav=nav,
                trim_multiple=trim_multiple,
                trail_from_peak=trail_from_peak,
                peak_arm_multiple=peak_arm_multiple,
                stub_weight_pct=stub_weight_pct,
                short_hold_days=short_hold_days,
            )
        )
    notes.extend(_cash_band_note(cash_usd, nav, cash_band_pct))
    notes.extend(
        _cluster_notes(
            positions=rows,
            order=order,
            nav=nav,
            cluster_map=cluster_map,
            cluster_cap_pct=cluster_cap_pct,
            combined_caps=combined_caps,
        )
    )
    return notes


def _size_notes(
    order: Mapping[str, Any] | None,
    settled_cash: float | None,
    min_notional: float | None,
) -> list[LessonNote]:
    if not order:
        return []
    order_usd = _finite(order.get("order_usd"))
    if order_usd is None:
        return []
    symbol = _symbol(order) or None
    notes: list[LessonNote] = []
    settled = _finite(settled_cash)
    if settled is not None and order_usd > settled:
        notes.append(
            LessonNote(
                lesson_id="settled_cash",
                severity="watch",
                symbol=symbol,
                text=(
                    f"Order ${order_usd:,.2f} is above settled cash ${settled:,.2f}. "
                    "Pending deposits are not treated as spendable here."
                ),
            )
        )
    floor = _positive(min_notional)
    if floor is not None and order_usd < floor:
        notes.append(
            LessonNote(
                lesson_id="min_notional",
                severity="watch",
                symbol=symbol,
                text=(
                    f"Order ${order_usd:,.2f} is below the caller minimum ${floor:,.2f}."
                ),
            )
        )
    return notes


def _reentry_notes(
    *,
    order: Mapping[str, Any] | None,
    positions: Sequence[Mapping[str, Any]],
    lots: Sequence[Mapping[str, Any]],
    as_of: date | None,
    reentry_days: int,
) -> list[LessonNote]:
    events: list[tuple[str, float, date]] = []
    if order and as_of is not None:
        price = _finite(order.get("current_price"))
        symbol = _symbol(order)
        if symbol and price is not None and price > 0:
            events.append((symbol, price, as_of))
    for pos in positions:
        entered = _parse_date(pos.get("entry_date"))
        price = _finite(pos.get("avg_cost"))
        symbol = _symbol(pos)
        if symbol and entered is not None and price is not None and price > 0:
            events.append((symbol, price, entered))

    notes: list[LessonNote] = []
    seen: set[tuple[str, str]] = set()
    for symbol, price, when in events:
        for lot in lots:
            if _symbol(lot) != symbol:
                continue
            pnl = _finite(lot.get("realized_pnl"))
            exit_px = _finite(lot.get("exit_price"))
            exited = _parse_date(lot.get("exit_date"))
            if pnl is None or exit_px is None or exited is None:
                continue
            if pnl >= 0 or exit_px <= 0 or price <= exit_px:
                continue
            days = (when - exited).days
            if days < 0 or days > reentry_days:
                continue
            key = (symbol, exited.isoformat())
            if key in seen:
                continue
            seen.add(key)
            notes.append(
                LessonNote(
                    lesson_id="reentry",
                    severity="watch",
                    symbol=symbol,
                    text=(
                        f"{symbol} was sold at ${exit_px:,.2f} for a ${pnl:,.2f} loss "
                        f"{days} days before this price ${price:,.2f}."
                    ),
                )
            )
    return notes


def _position_notes(
    pos: Mapping[str, Any],
    *,
    as_of: date | None,
    nav: float | None,
    trim_multiple: float,
    trail_from_peak: float,
    peak_arm_multiple: float,
    stub_weight_pct: float,
    short_hold_days: int,
) -> list[LessonNote]:
    symbol = _symbol(pos) or None
    if symbol is None:
        return []
    notes: list[LessonNote] = []
    cost = _positive(pos.get("avg_cost"))
    current = _positive(pos.get("current_price"))
    if cost is not None and current is not None and current / cost >= trim_multiple:
        notes.append(
            LessonNote(
                lesson_id="round_trip",
                severity="watch",
                symbol=symbol,
                text=(
                    f"{symbol} is {current / cost:.2f}x cost "
                    f"(${current:,.2f} vs ${cost:,.2f}). "
                    "A partial sale or a trail was the pre-written option at 2x. "
                    "This note does not size a sale."
                ),
            )
        )
    if "high_water_price" in pos and cost is not None and current is not None:
        peak = _positive(pos.get("high_water_price"))
        if (
            peak is not None
            and peak >= cost * peak_arm_multiple
            and (1.0 - (current / peak)) >= trail_from_peak - 1e-9
        ):
            giveback = 1.0 - (current / peak)
            notes.append(
                LessonNote(
                    lesson_id="trail",
                    severity="watch",
                    symbol=symbol,
                    text=(
                        f"{symbol} is {giveback * 100:.1f}% under its high-water "
                        f"${peak:,.2f}. The {trail_from_peak * 100:.0f}% trail level "
                        "is reached. This note does not place a stop."
                    ),
                )
            )
    if "shares_held_for_sells" in pos:
        reserved = _finite(pos.get("shares_held_for_sells"))
        qty = _positive(pos.get("qty"))
        if reserved is not None and reserved <= 0 and qty is not None:
            notes.append(
                LessonNote(
                    lesson_id="protective_order",
                    severity="watch",
                    symbol=symbol,
                    text=(
                        f"{symbol}: shares_held_for_sells is 0 "
                        f"({qty:g} shares). No protective order is on the row."
                    ),
                )
            )
    mkt = _market_value(pos)
    if nav is not None and mkt is not None and mkt > 0:
        weight = mkt / nav * 100.0
        if 0 < weight < stub_weight_pct:
            notes.append(
                LessonNote(
                    lesson_id="stub",
                    severity="info",
                    symbol=symbol,
                    text=(
                        f"{symbol} is {weight:.2f}% of NAV (${mkt:,.2f}), "
                        f"under the {stub_weight_pct:.1f}% stub line."
                    ),
                )
            )
    entered = _parse_date(pos.get("entry_date"))
    if as_of is not None and entered is not None:
        held = (as_of - entered).days
        if 0 <= held < short_hold_days:
            notes.append(
                LessonNote(
                    lesson_id="short_hold",
                    severity="info",
                    symbol=symbol,
                    text=f"{symbol} has been held {held} days (under {short_hold_days}).",
                )
            )
    return notes


def _cash_band_note(
    cash_usd: float | None,
    nav: float | None,
    band: tuple[float, float] | None,
) -> list[LessonNote]:
    cash = _finite(cash_usd)
    if cash is None or nav is None or band is None or len(band) != 2:
        return []
    low, high = band
    if not math.isfinite(low) or not math.isfinite(high) or high < low:
        return []
    pct = cash / nav * 100.0
    if low <= pct <= high:
        return []
    return [
        LessonNote(
            lesson_id="cash_band",
            severity="info",
            symbol=None,
            text=(
                f"Cash is {pct:.1f}% of NAV. The caller band is {low:.1f}%–{high:.1f}%."
            ),
        )
    ]


def _cluster_notes(
    *,
    positions: Sequence[Mapping[str, Any]],
    order: Mapping[str, Any] | None,
    nav: float | None,
    cluster_map: Mapping[str, Sequence[str]] | None,
    cluster_cap_pct: Mapping[str, float] | None,
    combined_caps: Sequence[Mapping[str, Any]] | None,
) -> list[LessonNote]:
    if nav is None or not cluster_map:
        return []
    values = _values_by_symbol(positions, order)
    weights = {
        name: sum(values.get(str(t).upper(), 0.0) for t in tickers) / nav * 100.0
        for name, tickers in cluster_map.items()
    }
    proposed = _symbol(order) if order else ""
    notes: list[LessonNote] = []
    for name, weight in weights.items():
        cap = _finite((cluster_cap_pct or {}).get(name))
        if cap is None or weight <= cap:
            continue
        in_cluster = proposed in {str(t).upper() for t in cluster_map.get(name, ())}
        notes.append(
            LessonNote(
                lesson_id="cluster",
                severity="watch",
                symbol=proposed if in_cluster and proposed else None,
                text=(
                    f"Cluster {name} is {weight:.2f}% of NAV. The caller cap is {cap:.1f}%."
                ),
            )
        )
    for spec in combined_caps or ():
        names = spec.get("clusters")
        cap = _finite(spec.get("cap_pct"))
        if not isinstance(names, (list, tuple)) or cap is None:
            continue
        present = [str(n) for n in names if str(n) in weights]
        if len(present) != len(list(names)):
            continue
        weight = sum(weights[n] for n in present)
        if weight <= cap:
            continue
        tickers: set[str] = set()
        for name in present:
            tickers.update(str(t).upper() for t in cluster_map.get(name, ()))
        notes.append(
            LessonNote(
                lesson_id="cluster",
                severity="watch",
                symbol=proposed if proposed in tickers else None,
                text=(
                    f"Combined {' + '.join(present)} is {weight:.2f}% of NAV. "
                    f"The caller cap is {cap:.1f}%."
                ),
            )
        )
    return notes


def _values_by_symbol(
    positions: Sequence[Mapping[str, Any]],
    order: Mapping[str, Any] | None,
) -> dict[str, float]:
    values: dict[str, float] = {}
    for pos in positions:
        symbol = _symbol(pos)
        mkt = _market_value(pos)
        if symbol and mkt is not None and mkt > 0:
            values[symbol] = values.get(symbol, 0.0) + mkt
    if order:
        add = _finite(order.get("order_usd"))
        symbol = _symbol(order)
        if symbol and add is not None and add > 0:
            values[symbol] = values.get(symbol, 0.0) + add
    return values


def _market_value(pos: Mapping[str, Any]) -> float | None:
    if "market_value" in pos:
        return _finite(pos.get("market_value"))
    qty = _finite(pos.get("qty"))
    px = _finite(pos.get("current_price"))
    if qty is None or px is None:
        return None
    return qty * px


def _symbol(row: Mapping[str, Any]) -> str:
    raw = row.get("ticker") if row.get("ticker") else row.get("symbol")
    return str(raw or "").upper()


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _positive(value: Any) -> float | None:
    number = _finite(value)
    return number if number is not None and number > 0 else None


def _parse_date(value: Any) -> date | None:
    if value is None or value is True or value is False:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None
