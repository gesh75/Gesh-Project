#!/usr/bin/env python3
"""
build_snapshot_from_mcp.py
Deterministic snapshot builder for the GESH daily chain — the MCP-sourced
replacement for fetch_live_snapshot.py's robin_stocks pickle login (which breaks
on every Robinhood password change).

Reads a raw MCP dump (written by fetch_snapshot_mcp.sh via `claude -p`) and writes
live_portfolio_snapshot.json in the EXACT schema run_engines2.py expects.

Raw dump schema (path supplied by fetch_snapshot_mcp.sh):
  {"fetched_account": "...",
   "portfolio":  <get_portfolio data object>,
   "positions":  <get_equity_positions data.positions array>,
   "quotes":     <get_equity_quotes data.results array>}

All money math lives HERE (tested, deterministic) — never in the LLM. The critical
rule (CLAUDE.md): size against `deployable` = settled, UNLEVERAGED buying power =
the RH MCP's buying_power.buying_power. The MCP exposes no Gold margin figure, so
buying_power_leveraged is kept == deployable (sizing can never inflate on margin).
"""

import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rh_history_pages import MCP_SAFE_BYTE_CEILING, collect_pages, load_statement_overrides

BASE_DIR = Path(__file__).parent
OUTPUT = BASE_DIR / "live_portfolio_snapshot.json"


def _finite_money(value: object) -> float | None:
    """A finite dollar figure. Bool / NaN / inf / overflow stay unknown.

    A measured ``0`` is kept. ``or``-chaining money fields would treat $0
    settled buying power as missing and size against ``portfolio.cash``,
    which this builder's own tests mark as the wrong (non-settled) figure.
    """
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        num = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return num if math.isfinite(num) else None


def _f(x, default=0.0):
    """Finite number or ``default``. Bool / NaN / inf / overflow are unknown.

    Used for quantity, average cost, pending deposits, and headline total —
    never for a last mark (see ``_positive_price``).
    """
    num = _finite_money(x)
    return default if num is None else num


def _settled_cash(portfolio: dict[str, Any]) -> float:
    """Settled unleveraged cash from the MCP portfolio object.

    Prefer ``buying_power.buying_power`` (including a measured $0). Fall
    back to ``cash`` only when that field is absent or unusable — never
    when it is legitimately zero. A scalar ``buying_power`` is accepted
    so a flat MCP shape cannot AttributeError the daily snapshot.
    """
    raw_bp = portfolio.get("buying_power")
    if isinstance(raw_bp, dict):
        settled = _finite_money(raw_bp.get("buying_power"))
    else:
        settled = _finite_money(raw_bp)
    if settled is None:
        settled = _finite_money(portfolio.get("cash"))
    return 0.0 if settled is None else settled


def _positive_price(x) -> float | None:
    """A usable last mark. Missing / blank / non-numeric / non-finite / <=0 stay None.

    `_f(..., default=0.0)` must NEVER be used for a holding's last trade price:
    a partial MCP quote response (symbol present in positions, absent or empty
    in quotes) used to write `price: 0.0`, `equity: 0.0`, `percent_change: -100`
    and then fold that $0 into portfolio_equity — understating the book and
    skewing every Kelly / Monte Carlo weight. Unknown is not zero.
    """
    value = _finite_money(x)
    if value is None or value <= 0:
        return None
    return value


def quote_provenance(item: dict[str, Any], session: str) -> dict[str, Any]:
    """Return provider-owned quote time; never substitute transport time."""
    quote = item.get("quote", item) if isinstance(item, dict) else {}
    for field in ("venue_last_trade_time", "updated_at", "last_trade_time"):
        if quote.get(field):
            return {
                "quote_as_of": quote[field],
                "provider": "robinhood_mcp",
                "session": session,
                "timestamp_basis": field,
            }
    return {
        "quote_as_of": None,
        "provider": "robinhood_mcp",
        "session": session,
        "timestamp_basis": "unavailable",
    }


def _history_metadata(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Gaps and statement-override counts. Equity fields stay untouched.

    Recorded pages are replayed through the pager. A truncated single day
    becomes a gap. Order rows are counted and not copied into the snapshot.
    """
    pages = raw.get("history_pages")
    meta: dict[str, Any] = {}
    if isinstance(pages, list) and pages:
        gaps, count = _recorded_history(pages)
        meta["gaps"] = gaps
        meta["order_count"] = count
    meta.update(_statement_meta())
    return meta or None


def _recorded_history(pages: list[object]) -> tuple[list[dict[str, Any]], int]:
    clean = [page for page in pages if isinstance(page, dict)]
    gaps: list[dict[str, Any]] = []
    count = 0
    plain = [page for page in clean if not page.get("symbol")]
    scoped = [page for page in clean if page.get("symbol")]
    if plain:
        group_gaps, group_count = _collect_group(plain, None)
        gaps.extend(group_gaps)
        count += group_count
    if scoped:
        symbols = sorted(
            {str(page.get("symbol") or "").strip().upper() for page in scoped if page.get("symbol")}
        )
        group_gaps, group_count = _collect_group(scoped, symbols or None)
        gaps.extend(group_gaps)
        count += group_count
    return gaps, count


def _collect_group(
    pages: list[dict[str, Any]],
    symbols: list[str] | None,
) -> tuple[list[dict[str, Any]], int]:
    window = _history_window(pages)
    if window is None:
        return [], 0
    start, end = window

    def fetch(
        start_iso: str,
        end_iso: str,
        symbol: str | None,
        cursor: str | None,
    ) -> dict[str, Any]:
        for page in pages:
            if _page_matches(page, start_iso, end_iso, symbol, cursor):
                return page
        for page in pages:
            if _covers_truncated(page, start_iso, end_iso, symbol):
                raw_bytes = page.get("raw_bytes")
                size = raw_bytes if isinstance(raw_bytes, int) and not isinstance(raw_bytes, bool) else 20_000
                return {"results": [], "truncated": True, "raw_bytes": size}
        return {"results": [], "raw_bytes": 0, "truncated": False}

    collected = collect_pages(fetch, start, end, symbols)
    gaps = [
        {
            "start": gap.start.isoformat(),
            "end": gap.end.isoformat(),
            "symbol": gap.symbol,
            "reason": gap.reason,
        }
        for gap in collected.gaps
    ]
    return gaps, len(collected.orders)


def _history_window(pages: list[dict[str, Any]]) -> tuple[str, str] | None:
    starts: list[str] = []
    ends: list[str] = []
    for page in pages:
        start = str(page.get("start") or "")[:10]
        end = str(page.get("end") or "")[:10]
        if len(start) == 10 and len(end) == 10 and end >= start:
            starts.append(start)
            ends.append(end)
    if not starts:
        return None
    return min(starts), max(ends)


def _page_cursor(page: dict[str, Any]) -> str | None:
    cursor = page.get("cursor")
    if isinstance(cursor, str) and cursor:
        return cursor
    return None


def _page_symbol(page: dict[str, Any]) -> str | None:
    symbol = page.get("symbol")
    if symbol in (None, ""):
        return None
    return str(symbol).strip().upper() or None


def _page_matches(
    page: dict[str, Any],
    start: str,
    end: str,
    symbol: str | None,
    cursor: str | None,
) -> bool:
    return (
        str(page.get("start") or "")[:10] == start
        and str(page.get("end") or "")[:10] == end
        and _page_symbol(page) == symbol
        and _page_cursor(page) == cursor
    )


def _covers_truncated(page: dict[str, Any], start: str, end: str, symbol: str | None) -> bool:
    raw_bytes = page.get("raw_bytes")
    over_ceiling = (
        isinstance(raw_bytes, int)
        and not isinstance(raw_bytes, bool)
        and raw_bytes >= MCP_SAFE_BYTE_CEILING
    )
    if page.get("truncated") is not True and not over_ceiling:
        return False
    page_start = str(page.get("start") or "")[:10]
    page_end = str(page.get("end") or "")[:10]
    if not page_start or not page_end or page_start > start or page_end < end:
        return False
    return _page_symbol(page) == symbol


def _statement_meta() -> dict[str, Any]:
    if not os.environ.get("GESH_STATEMENT_OVERRIDES", "").strip():
        return {}
    try:
        rows = load_statement_overrides()
    except FileNotFoundError:
        return {"statement_overrides_error": "missing"}
    except Exception as exc:
        print(
            f"[build_snapshot] statement overrides not loaded: {type(exc).__name__}",
            flush=True,
        )
        return {"statement_overrides_error": "invalid"}
    return {
        "statement_override_count": len(rows),
        "statement_override_dates": [str(row.get("date") or "") for row in rows],
    }


def build(
    *,
    raw_data: dict[str, Any] | None = None,
    raw_path: Path | None = None,
    output: Path = OUTPUT,
    now: datetime | None = None,
) -> dict[str, Any]:
    if raw_data is None:
        if raw_path is None:
            raise ValueError("raw_path is required when raw_data is not provided")
        raw_data = json.loads(raw_path.read_text())
    raw = raw_data
    pf = raw["portfolio"]
    positions = raw["positions"]
    # get_equity_quotes returns results[] as {"quote": {...}, "close": {...}};
    # unwrap the inner "quote" (tolerate a flat shape too).
    quotes = {}
    quote_meta = {}
    session = str(raw.get("session_id") or raw.get("request_id") or "mcp-snapshot")
    for item in raw["quotes"]:
        q = item.get("quote", item) if isinstance(item, dict) else {}
        sym = q.get("symbol")
        if sym:
            quotes[sym] = q
            quote_meta[sym] = quote_provenance(item, session)

    # ── Cash / buying power ──
    # MCP buying_power.buying_power is settled + unleveraged. A measured $0
    # (fully invested) must stay $0 — ``or pf.cash`` used to invent dry powder.
    if not isinstance(pf, dict):
        pf = {}
    settled_cash = _settled_cash(pf)
    pending_deposits = _f(pf.get("pending_deposits"))
    total_value = _f(pf.get("total_value"))
    deployable = settled_cash

    holdings = []
    unpriced: list[str] = []
    tot_eq = 0.0
    prev_eq_positions = 0.0
    for pos in positions:
        qty = _f(pos.get("quantity"))
        if qty <= 0:
            continue
        sym = pos.get("symbol")
        q = quotes.get(sym, {})
        price = _positive_price(q.get("last_trade_price") or q.get("last_non_reg_trade_price"))
        if price is None:
            unpriced.append(str(sym or "?"))
            continue
        avg = _f(pos.get("average_buy_price"))
        prev_close = (
            _positive_price(q.get("previous_close") or q.get("adjusted_previous_close")) or price
        )
        eq = price * qty
        tot_eq += eq
        prev_eq_positions += prev_close * qty
        holdings.append(
            {
                "symbol": sym,
                "quantity": qty,
                "price": round(price, 4),
                "average_buy_price": round(avg, 4),
                "equity": round(eq, 4),
                "percent_change": round((price / avg - 1) * 100, 2) if avg > 0 else 0,
            }
        )

    if unpriced:
        print(
            "[build_snapshot] ERROR: unpriced holdings (missing/zero MCP quote): "
            + ", ".join(unpriced)
            + " — refusing to write a $0 mark (fallback tiers exist)",
            flush=True,
        )
        sys.exit(1)

    portfolio_total = round(tot_eq + deployable, 2)
    snapshot = {
        "fetched_at": (now or datetime.now(timezone.utc)).isoformat(),
        "source": "robinhood_mcp",
        "quote_provenance": quote_meta,
        "holdings": holdings,
        "equity_summary": {
            "portfolio_equity": round(tot_eq, 2),
            "cash": round(deployable, 2),
            "deployable": round(deployable, 2),
            "settled_cash": round(deployable, 2),
            "pending_deposits": round(pending_deposits, 2),
            "buying_power": round(deployable, 2),
            "buying_power_leveraged": round(
                deployable, 2
            ),  # MCP has no margin BP -> = deployable (sizing-safe)
            "margin_buying_power": round(deployable, 2),
            "withdrawable_amount": round(deployable, 2),
            "uncleared_deposits": round(pending_deposits, 2),
            "total": round(total_value, 2) if total_value else portfolio_total,
            "account_equity": round(total_value, 2) if total_value else portfolio_total,
            "adjusted_equity_previous_close": round(prev_eq_positions + deployable, 2),
        },
    }
    history = _history_metadata(raw)
    if history is not None:
        snapshot["history"] = history

    if not holdings:
        print("[build_snapshot] ERROR: 0 holdings parsed from MCP dump", flush=True)
        sys.exit(1)

    output.write_text(json.dumps(snapshot, indent=2))
    es = snapshot["equity_summary"]
    print(
        f"[build_snapshot] {len(holdings)} holdings  total=${es['total']:,.2f}  "
        f"pos_equity=${es['portfolio_equity']:,.2f}  deployable=${es['deployable']:,.2f}  "
        f"src=robinhood_mcp",
        flush=True,
    )
    return snapshot


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"usage: {Path(sys.argv[0]).name} RAW_PATH", file=sys.stderr)
        sys.exit(2)
    raw_path = Path(sys.argv[1])
    if not raw_path.is_file():
        print(f"[build_snapshot] FATAL: raw MCP dump not found at {raw_path}", flush=True)
        sys.exit(1)
    build(raw_path=raw_path)
