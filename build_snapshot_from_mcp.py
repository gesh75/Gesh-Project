#!/usr/bin/env python3
"""
build_snapshot_from_mcp.py
Deterministic snapshot builder for the GESH daily chain — the MCP-sourced
replacement for fetch_live_snapshot.py's robin_stocks pickle login (which breaks
on every Robinhood password change).

Reads a raw MCP dump (written by fetch_snapshot_mcp.sh via `claude -p`) and writes
live_portfolio_snapshot.json in the EXACT schema run_engines2.py expects.

Raw dump schema (/tmp/gesh_mcp_raw.json):
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
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).parent
RAW = Path(os.environ.get("GESH_MCP_RAW", "/tmp/gesh_mcp_raw.json"))
OUTPUT = BASE_DIR / "live_portfolio_snapshot.json"


def _f(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


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


def build(
    *,
    raw_data: dict[str, Any] | None = None,
    output: Path = OUTPUT,
    now: datetime | None = None,
) -> dict[str, Any]:
    raw = raw_data if raw_data is not None else json.loads(RAW.read_text())
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

    # ── Cash / buying power (mirror fetch_live_snapshot.py exactly) ──
    bp = pf.get("buying_power") or {}
    settled_cash = _f(bp.get("buying_power") or pf.get("cash"))
    pending_deposits = _f(pf.get("pending_deposits"))
    total_value = _f(pf.get("total_value"))
    deployable = settled_cash  # MCP buying_power is already settled+unleveraged

    holdings = []
    tot_eq = 0.0
    prev_eq_positions = 0.0
    for pos in positions:
        qty = _f(pos.get("quantity"))
        if qty <= 0:
            continue
        sym = pos.get("symbol")
        q = quotes.get(sym, {})
        price = _f(q.get("last_trade_price") or q.get("last_non_reg_trade_price"))
        avg = _f(pos.get("average_buy_price"))
        prev_close = _f(q.get("previous_close") or q.get("adjusted_previous_close") or price)
        eq = price * qty
        tot_eq += eq
        prev_eq_positions += prev_close * qty
        holdings.append({
            "symbol": sym,
            "quantity": qty,
            "price": round(price, 4),
            "average_buy_price": round(avg, 4),
            "equity": round(eq, 4),
            "percent_change": round((price / avg - 1) * 100, 2) if avg > 0 else 0,
        })

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
            "buying_power_leveraged": round(deployable, 2),  # MCP has no margin BP -> = deployable (sizing-safe)
            "margin_buying_power": round(deployable, 2),
            "withdrawable_amount": round(deployable, 2),
            "uncleared_deposits": round(pending_deposits, 2),
            "total": round(total_value, 2) if total_value else portfolio_total,
            "account_equity": round(total_value, 2) if total_value else portfolio_total,
            "adjusted_equity_previous_close": round(prev_eq_positions + deployable, 2),
        },
    }

    if not holdings:
        print("[build_snapshot] ERROR: 0 holdings parsed from MCP dump", flush=True)
        sys.exit(1)

    output.write_text(json.dumps(snapshot, indent=2))
    es = snapshot["equity_summary"]
    print(f"[build_snapshot] {len(holdings)} holdings  total=${es['total']:,.2f}  "
          f"pos_equity=${es['portfolio_equity']:,.2f}  deployable=${es['deployable']:,.2f}  "
          f"src=robinhood_mcp", flush=True)
    return snapshot


if __name__ == "__main__":
    if not RAW.exists():
        print(f"[build_snapshot] FATAL: raw MCP dump not found at {RAW}", flush=True)
        sys.exit(1)
    build()
