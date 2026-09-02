"""Plain-language translation of consensus vs Kelly for the customer desk.

These strings are the product. They tell a retail user what to do without
naming engines. Callers pass measured fields; this module never invents a
number or a verdict.
"""

from __future__ import annotations

from typing import Any

_BUYISH = frozenset({"BUY", "STRONG BUY"})
_SELLISH = frozenset({"SELL", "STRONG SELL"})


def _norm(value: object) -> str:
    return str(value or "").strip().upper()


def plain_line(
    *,
    consensus: str | None,
    kelly_action: str | None,
    delta_pp: float | None = None,
) -> str:
    """One sentence for a holding. `delta_pp` is accepted and ignored on
    purpose — the UI must not leak model percentages into the headline."""
    del delta_pp
    cons, kelly = _norm(consensus), _norm(kelly_action)
    if not cons and not kelly:
        return "No read yet."
    if kelly == "TRIM" and cons in _BUYISH:
        return "Like the company, don't add — it's already large."
    if kelly == "AVOID" and cons in _BUYISH:
        return "Like the company, size says skip."
    if kelly == "ADD" and cons in _SELLISH:
        return "Size wants more; the signal does not. Wait."
    if kelly == "ADD" and cons in _BUYISH:
        return "Both lean add — still a small slice of the book."
    if kelly in {"AVOID", "TRIM"} and cons in _SELLISH:
        return "Both want less of this."
    if kelly == "HOLD" and cons in _BUYISH:
        return "Keep the name; size looks about right."
    if kelly == "HOLD" and cons in _SELLISH:
        return "Size is fine; the signal is cautious."
    if kelly == "HOLD":
        return "Size looks about right."
    if kelly == "ADD":
        return "Model says this is small vs the rest of the book."
    if kelly == "TRIM":
        return "Model says this is large vs the rest of the book."
    if kelly == "AVOID":
        return "Model would not hold this size."
    if cons in _BUYISH:
        return "Signal leans buy."
    if cons in _SELLISH:
        return "Signal leans sell."
    return "No clear action."


def _kind(cons: str, kelly: str) -> str | None:
    if not cons and not kelly:
        return None
    if (kelly in {"TRIM", "AVOID"} and cons in _BUYISH) or (kelly == "ADD" and cons in _SELLISH):
        return "conflict"
    if kelly in {"TRIM", "AVOID"}:
        return "trim"
    if kelly == "ADD":
        return "add"
    if kelly == "HOLD" or cons:
        return "hold"
    return None


def _priority(kind: str, delta_pp: float) -> tuple[int, float]:
    rank = {"conflict": 0, "trim": 1, "add": 2, "hold": 3, "flip": 4}
    return (rank.get(kind, 9), -abs(delta_pp))


def book_actions(
    rows: list[dict[str, Any]],
    *,
    top_changes: list[dict[str, Any]] | None = None,
    limit: int = 3,
) -> list[dict[str, str]]:
    """Up to `limit` advisory tickets, conflict first.

    Each item: {symbol, kind, headline}. Flips fill remaining slots when the
    symbol is not already on the list.
    """
    tickets: list[dict[str, Any]] = []
    for row in rows:
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        cons = _norm(row.get("consensus"))
        kelly = _norm(row.get("kelly_action"))
        kind = _kind(cons, kelly)
        if kind is None:
            continue
        try:
            delta = float(row.get("delta_pp") or 0)
        except (TypeError, ValueError):
            delta = 0.0
        tickets.append(
            {
                "symbol": symbol,
                "kind": kind,
                "headline": f"{symbol} — {plain_line(consensus=cons, kelly_action=kelly)}",
                "_delta": delta,
            }
        )
    tickets.sort(key=lambda t: _priority(t["kind"], t["_delta"]))
    out: list[dict[str, str]] = [
        {"symbol": t["symbol"], "kind": t["kind"], "headline": t["headline"]} for t in tickets
    ]
    flipped: set[str] = set()
    for flip in top_changes or []:
        if len(out) >= limit:
            break
        symbol = str(flip.get("symbol") or "").strip().upper()
        prior = str(flip.get("prior_verdict") or "").strip()
        new = str(flip.get("new_verdict") or "").strip()
        if not symbol or not prior or not new or symbol in flipped:
            continue
        out.append(
            {
                "symbol": symbol,
                "kind": "flip",
                "headline": f"{symbol} — {prior} → {new}.",
            }
        )
        flipped.add(symbol)
    return out[:limit]
