"""
tax_harvester.py — Tax-Loss Harvesting & Wash-Sale Tracker

Scans Robinhood positions for unrealised losses above a configurable
threshold and flags them as TAX-LOSS ELIGIBLE.  Tracks 30-day wash-sale
windows per ticker in SQLite so you don't accidentally rebuy too soon.

Key functions
─────────────
  scan_tax_loss_candidates(positions, cost_basis_map)
      → list of {ticker, loss_usd, loss_pct, wash_safe_date, days_until_safe, status}

  record_harvest(ticker, sale_price, loss_usd)
      → writes to wash_sale_log; starts 30-day window

  get_wash_status(ticker)
      → {in_wash_window: bool, wash_safe_date: str, days_remaining: int}

  get_harvest_summary()
      → {total_harvestable_loss, candidates, wash_active}

DB table (auto-created in gesh_financial.db)
─────────────────────────────────────────────
  wash_sale_log: id, ticker, sale_date, sale_price, loss_usd, wash_safe_date, created_at
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


# ─── Config ───────────────────────────────────────────────────────────────────

MIN_LOSS_USD: float = 500.0   # minimum unrealised loss to flag
WASH_SALE_DAYS: int = 30      # IRS wash-sale window

# DB path — same resolution logic as db.py (VirtioFS fallback)
_PRIMARY_DB = Path("/Users/georgigaydarov/Projects/gesh-project-trading/gesh_financial.db")
_FALLBACK_DB = Path("/tmp/gesh_trades.db")


def _db_path() -> Path:
    try:
        _PRIMARY_DB.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(_PRIMARY_DB), timeout=5)
        conn.execute("CREATE TABLE IF NOT EXISTS _probe (x INTEGER)")
        conn.close()
        return _PRIMARY_DB
    except Exception:
        return _FALLBACK_DB


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path()), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


# ─── Schema ───────────────────────────────────────────────────────────────────

def init_wash_sale_table() -> None:
    """Create wash_sale_log table if it doesn't exist."""
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS wash_sale_log (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker        TEXT NOT NULL,
                sale_date     TEXT NOT NULL,
                sale_price    REAL,
                loss_usd      REAL,
                wash_safe_date TEXT NOT NULL,
                created_at    TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_wsl_ticker ON wash_sale_log(ticker)")
        conn.commit()


# ─── Core helpers ─────────────────────────────────────────────────────────────

def get_wash_status(ticker: str) -> dict[str, Any]:
    """Return wash-sale window status for a ticker."""
    init_wash_sale_table()
    today_str = date.today().isoformat()
    with _get_conn() as conn:
        row = conn.execute(
            """SELECT wash_safe_date, sale_date
               FROM wash_sale_log
               WHERE ticker = ?
               ORDER BY created_at DESC LIMIT 1""",
            (ticker.upper(),),
        ).fetchone()
    if row is None:
        return {"in_wash_window": False, "wash_safe_date": None, "days_remaining": 0}
    wash_safe = row["wash_safe_date"]
    in_window  = wash_safe > today_str
    days_rem   = (date.fromisoformat(wash_safe) - date.today()).days if in_window else 0
    return {
        "in_wash_window":  in_window,
        "wash_safe_date":  wash_safe,
        "days_remaining":  max(0, days_rem),
    }


def record_harvest(ticker: str, sale_price: float, loss_usd: float) -> None:
    """Log a tax-loss harvest event and start the 30-day wash-sale clock."""
    init_wash_sale_table()
    sale_date  = date.today().isoformat()
    safe_date  = (date.today() + timedelta(days=WASH_SALE_DAYS)).isoformat()
    with _get_conn() as conn:
        conn.execute(
            """INSERT INTO wash_sale_log (ticker, sale_date, sale_price, loss_usd, wash_safe_date)
               VALUES (?, ?, ?, ?, ?)""",
            (ticker.upper(), sale_date, sale_price, loss_usd, safe_date),
        )
        conn.commit()


# ─── Main scanner ─────────────────────────────────────────────────────────────

def scan_tax_loss_candidates(
    positions: dict[str, dict],
    cost_basis_map: dict[str, float] | None = None,
    min_loss_usd: float = MIN_LOSS_USD,
) -> list[dict[str, Any]]:
    """
    Scan positions for tax-loss harvesting opportunities.

    Parameters
    ----------
    positions : {ticker: {price, quantity, equity, ...}} from data_fetcher
    cost_basis_map : {ticker: avg_cost_per_share} from Robinhood
                     Falls back to `average_buy_price` key inside position dict.
    min_loss_usd : minimum unrealised loss (default $500)

    Returns
    -------
    List sorted by loss ascending (biggest losses first), each dict:
        ticker, current_price, cost_basis, shares,
        unrealised_loss_usd, unrealised_loss_pct,
        in_wash_window, wash_safe_date, days_until_safe,
        status  (ELIGIBLE | IN_WASH_WINDOW | PROFIT)
    """
    init_wash_sale_table()
    candidates: list[dict] = []
    cbm = cost_basis_map or {}

    for ticker, pos in positions.items():
        ticker = ticker.upper()
        price  = float(pos.get("price", 0) or 0)
        shares = float(pos.get("quantity", pos.get("shares", 0)) or 0)
        if price <= 0 or shares <= 0:
            continue

        # Cost basis: from map → from position dict → fallback to price (no loss)
        cb = float(cbm.get(ticker) or pos.get("average_buy_price") or price)
        unrealised = (price - cb) * shares
        unrealised_pct = ((price - cb) / cb * 100) if cb > 0 else 0.0

        if unrealised >= 0:
            continue   # profitable position — skip

        wash = get_wash_status(ticker)
        loss_abs = abs(unrealised)

        record: dict[str, Any] = {
            "ticker":               ticker,
            "current_price":        round(price, 4),
            "cost_basis":           round(cb, 4),
            "shares":               round(shares, 4),
            "unrealised_loss_usd":  round(-loss_abs, 2),
            "unrealised_loss_pct":  round(unrealised_pct, 2),
            "in_wash_window":       wash["in_wash_window"],
            "wash_safe_date":       wash["wash_safe_date"],
            "days_until_safe":      wash["days_remaining"],
        }

        if wash["in_wash_window"]:
            record["status"] = "IN_WASH_WINDOW"
        elif loss_abs >= min_loss_usd:
            record["status"] = "ELIGIBLE"
        else:
            record["status"] = "BELOW_THRESHOLD"

        candidates.append(record)

    return sorted(candidates, key=lambda x: x["unrealised_loss_usd"])


def get_harvest_summary(
    positions: dict[str, dict],
    cost_basis_map: dict[str, float] | None = None,
) -> dict[str, Any]:
    """High-level harvest summary for dashboard injection."""
    candidates = scan_tax_loss_candidates(positions, cost_basis_map)
    eligible   = [c for c in candidates if c["status"] == "ELIGIBLE"]
    in_window  = [c for c in candidates if c["status"] == "IN_WASH_WINDOW"]
    total_harvestable = sum(abs(c["unrealised_loss_usd"]) for c in eligible)
    return {
        "total_harvestable_loss_usd": round(total_harvestable, 2),
        "eligible_count":            len(eligible),
        "in_wash_window_count":      len(in_window),
        "eligible":                  eligible,
        "in_wash_window":            in_window,
        "generated_at":              datetime.utcnow().isoformat(),
        "summary": (
            f"Tax harvest: ${total_harvestable:,.0f} eligible across "
            f"{len(eligible)} position(s). "
            f"{len(in_window)} in 30-day wash-sale window."
        ),
    }


# ─── CLI test ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Quick smoke test with fake positions
    fake_positions = {
        "PLTR": {"price": 18.50, "quantity": 200, "average_buy_price": 24.00},
        "NVDA": {"price": 875.00, "quantity": 5,  "average_buy_price": 820.00},
        "MSTR": {"price": 320.00, "quantity": 10, "average_buy_price": 480.00},
    }
    result = get_harvest_summary(fake_positions)
    print(result["summary"])
    for c in result["eligible"]:
        print(f"  {c['ticker']:6s}  loss=${abs(c['unrealised_loss_usd']):,.0f}  "
              f"({c['unrealised_loss_pct']:.1f}%)  status={c['status']}")
