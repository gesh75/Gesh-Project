"""
trade_log.py — Trade Logging Module for GESH Financial

Records every executed or paper trade with full context:
  - Scanner/signal source that generated the idea
  - Entry time, reason, stop loss, target price
  - Exit time, exit price, realized P&L
  - Win/loss outcome and notes

Tables (in gesh_financial.db):
  trade_log    — real executed trades
  paper_trades — paper/simulated trades (same schema)

Long-only P&L (GFD-008): close_trade always uses (exit − entry) × shares.
TRIM/SELL close longs; they are not short entries.

Usage:
    from trade_log import log_trade, close_trade, close_long_qty, get_win_rate, get_trade_history
"""

from __future__ import annotations
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from gesh_core.db_path import resolve_db_path as _resolve_db

try:
    from gesh_core.db_path import ensure_db_writable as _ensure_writable
except ImportError:  # pin lag vs editable checkout (Astra R7)
    def _ensure_writable(path=None):
        return _resolve_db() if path is None else path

# Same resolver as db.py / trading.state. GESH_DB_PATH → cwd/gesh_financial.db.
# Unusable paths fail closed; do not silently write /tmp/gesh_trades.db (Astra R3).
DB_PATH = _resolve_db()

# ─── Schema ───────────────────────────────────────────────────────────────────

_DDL_STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS trade_log (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol          TEXT    NOT NULL,
        action          TEXT    NOT NULL,
        shares          REAL    NOT NULL,
        entry_price     REAL    NOT NULL,
        entry_time      TEXT    NOT NULL,
        exit_price      REAL,
        exit_time       TEXT,
        stop_loss       REAL,
        target_price    REAL,
        scanner_source  TEXT,
        signal_reason   TEXT,
        realized_pnl    REAL,
        pnl_pct         REAL,
        outcome         TEXT    DEFAULT 'OPEN',
        notes           TEXT,
        is_paper        INTEGER DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS paper_trades (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol          TEXT    NOT NULL,
        action          TEXT    NOT NULL,
        shares          REAL    NOT NULL,
        entry_price     REAL    NOT NULL,
        entry_time      TEXT    NOT NULL,
        exit_price      REAL,
        exit_time       TEXT,
        stop_loss       REAL,
        target_price    REAL,
        scanner_source  TEXT,
        signal_reason   TEXT,
        realized_pnl    REAL,
        pnl_pct         REAL,
        outcome         TEXT    DEFAULT 'OPEN',
        notes           TEXT,
        simulated_portfolio_value REAL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_tl_symbol  ON trade_log(symbol, entry_time)",
    "CREATE INDEX IF NOT EXISTS idx_tl_outcome ON trade_log(outcome)",
    "CREATE INDEX IF NOT EXISTS idx_pt_symbol  ON paper_trades(symbol, entry_time)",
]


_schema_lock = threading.Lock()
_initialized_paths: set[str] = set()


def _ensure_schema(con: sqlite3.Connection, path) -> None:
    key = str(path)
    if key in _initialized_paths:
        return
    with _schema_lock:
        if key in _initialized_paths:
            return
        for stmt in _DDL_STATEMENTS:
            con.execute(stmt)
        con.commit()
        _initialized_paths.add(key)


@contextmanager
def _conn():
    _ensure_writable(DB_PATH)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        _ensure_schema(con, DB_PATH)
        yield con
        con.commit()
    finally:
        con.close()


# ─── Log a new trade entry ────────────────────────────────────────────────────

def log_trade(
    symbol: str,
    action: str,
    shares: float,
    entry_price: float,
    stop_loss: float | None = None,
    target_price: float | None = None,
    scanner_source: str | None = None,
    signal_reason: str | None = None,
    notes: str | None = None,
    is_paper: bool = False,
) -> int:
    """
    Log a new trade entry. Returns the trade id.

    Args:
        symbol:         Ticker symbol (e.g. "NVDA")
        action:         "BUY" / "ADD" / "TRIM" / "SELL"
        shares:         Number of shares
        entry_price:    Price at entry
        stop_loss:      Stop loss price (optional)
        target_price:   Target/take-profit price (optional)
        scanner_source: Source signal (e.g. "daily-analysis", "premarket", "1HR-scan")
        signal_reason:  Human-readable reason (e.g. "Consensus STRONG BUY + FFT trough")
        notes:          Any extra context
        is_paper:       True = write to paper_trades table instead

    Returns:
        Row id of the new trade record
    """
    now = datetime.now(timezone.utc).isoformat()
    table = "paper_trades" if is_paper else "trade_log"

    if is_paper:
        sql = """
            INSERT INTO paper_trades
              (symbol, action, shares, entry_price, entry_time,
               stop_loss, target_price, scanner_source, signal_reason, notes, outcome)
            VALUES (?,?,?,?,?,?,?,?,?,?,'OPEN')
        """
    else:
        sql = """
            INSERT INTO trade_log
              (symbol, action, shares, entry_price, entry_time,
               stop_loss, target_price, scanner_source, signal_reason, notes, outcome, is_paper)
            VALUES (?,?,?,?,?,?,?,?,?,?,'OPEN',0)
        """

    with _conn() as con:
        cur = con.execute(sql, (
            symbol.upper(), action.upper(), shares, entry_price, now,
            stop_loss, target_price, scanner_source, signal_reason, notes,
        ))
        return cur.lastrowid


# ─── Close a trade ────────────────────────────────────────────────────────────

def close_trade(
    trade_id: int,
    exit_price: float,
    notes: str | None = None,
    is_paper: bool = False,
) -> dict:
    """
    Close an open trade. Calculates P&L and outcome automatically.

    GFD-008: the book is long-only. P&L is always (exit − entry) × shares,
    including leftover TRIM/SELL-labeled rows.

    Returns the updated trade record.
    """
    now = datetime.now(timezone.utc).isoformat()
    table = "paper_trades" if is_paper else "trade_log"

    with _conn() as con:
        row = con.execute(
            f"SELECT * FROM {table} WHERE id=?", (trade_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"Trade id {trade_id} not found in {table}")

        row = dict(row)
        entry_price = row["entry_price"]
        shares = row["shares"]

        pnl = (exit_price - entry_price) * shares
        pnl_pct = ((exit_price - entry_price) / entry_price * 100) if entry_price else 0.0

        if pnl > 0.01:
            outcome = "WIN"
        elif pnl < -0.01:
            outcome = "LOSS"
        else:
            outcome = "BREAKEVEN"

        con.execute(
            f"""UPDATE {table}
                SET exit_price=?, exit_time=?, realized_pnl=?, pnl_pct=?, outcome=?,
                    notes=COALESCE(notes||' | '||?, notes, ?)
                WHERE id=?""",
            (exit_price, now, round(pnl, 2), round(pnl_pct, 2), outcome,
             notes, notes, trade_id),
        )

    return get_trade_by_id(trade_id, is_paper=is_paper)


def close_long_qty(
    symbol: str,
    qty: float,
    exit_price: float,
    notes: str | None = None,
    is_paper: bool = True,
) -> list[dict]:
    """FIFO-close `qty` shares of open BUY/ADD lots for `symbol`.

    Partial lots shrink in place and a closed child row is written for the
    taken quantity. P&L is always (exit − entry) × shares (GFD-008).
    """
    if qty <= 0:
        return []

    open_rows = get_trade_history(symbol=symbol, open_only=True, is_paper=is_paper, limit=500)
    longs = [r for r in open_rows if str(r.get("action", "")).upper() in ("BUY", "ADD")]
    longs.sort(key=lambda r: r.get("entry_time") or "")
    remaining = float(qty)
    closed: list[dict] = []

    for lot in longs:
        if remaining <= 1e-9:
            break
        lot_shares = float(lot["shares"])
        take = min(lot_shares, remaining)
        if take + 1e-9 >= lot_shares:
            rec = close_trade(int(lot["id"]), exit_price=exit_price, notes=notes, is_paper=is_paper)
            closed.append(rec)
            remaining -= lot_shares
            continue
        rec = _close_partial_lot(lot, take, exit_price, notes, is_paper)
        closed.append(rec)
        remaining -= take
    return closed


def _close_partial_lot(
    lot: dict,
    take: float,
    exit_price: float,
    notes: str | None,
    is_paper: bool,
) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    table = "paper_trades" if is_paper else "trade_log"
    entry_price = float(lot["entry_price"])
    left = float(lot["shares"]) - take
    pnl = (exit_price - entry_price) * take
    pnl_pct = ((exit_price - entry_price) / entry_price * 100) if entry_price else 0.0
    if pnl > 0.01:
        outcome = "WIN"
    elif pnl < -0.01:
        outcome = "LOSS"
    else:
        outcome = "BREAKEVEN"

    with _conn() as con:
        con.execute(f"UPDATE {table} SET shares=? WHERE id=?", (round(left, 6), lot["id"]))
        if is_paper:
            sql = """
                INSERT INTO paper_trades
                  (symbol, action, shares, entry_price, entry_time,
                   exit_price, exit_time, stop_loss, target_price,
                   scanner_source, signal_reason, notes,
                   realized_pnl, pnl_pct, outcome)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """
            params = (
                lot["symbol"], lot["action"], take, entry_price, lot["entry_time"],
                exit_price, now, lot.get("stop_loss"), lot.get("target_price"),
                lot.get("scanner_source"), lot.get("signal_reason"), notes,
                round(pnl, 2), round(pnl_pct, 2), outcome,
            )
        else:
            sql = """
                INSERT INTO trade_log
                  (symbol, action, shares, entry_price, entry_time,
                   exit_price, exit_time, stop_loss, target_price,
                   scanner_source, signal_reason, notes,
                   realized_pnl, pnl_pct, outcome, is_paper)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)
            """
            params = (
                lot["symbol"], lot["action"], take, entry_price, lot["entry_time"],
                exit_price, now, lot.get("stop_loss"), lot.get("target_price"),
                lot.get("scanner_source"), lot.get("signal_reason"), notes,
                round(pnl, 2), round(pnl_pct, 2), outcome,
            )
        cur = con.execute(sql, params)
        new_id = cur.lastrowid
    rec = get_trade_by_id(new_id, is_paper=is_paper)
    return rec or {
        "id": new_id,
        "symbol": lot["symbol"],
        "shares": take,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "realized_pnl": round(pnl, 2),
        "pnl_pct": round(pnl_pct, 2),
        "outcome": outcome,
    }


# ─── Queries ──────────────────────────────────────────────────────────────────

def get_trade_by_id(trade_id: int, is_paper: bool = False) -> dict | None:
    table = "paper_trades" if is_paper else "trade_log"
    with _conn() as con:
        row = con.execute(f"SELECT * FROM {table} WHERE id=?", (trade_id,)).fetchone()
    return dict(row) if row else None


def get_trade_history(
    symbol: str | None = None,
    limit: int = 50,
    open_only: bool = False,
    is_paper: bool = False,
) -> list[dict]:
    """
    Return recent trades. Filter by symbol and/or open status.

    Args:
        symbol:    Filter to specific ticker (None = all)
        limit:     Max rows to return
        open_only: True = only OPEN trades
        is_paper:  True = query paper_trades table
    """
    table = "paper_trades" if is_paper else "trade_log"
    conditions = []
    params: list[Any] = []

    if symbol:
        conditions.append("symbol=?")
        params.append(symbol.upper())
    if open_only:
        conditions.append("outcome='OPEN'")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params.append(limit)

    with _conn() as con:
        rows = con.execute(
            f"""SELECT * FROM {table} {where}
                ORDER BY entry_time DESC LIMIT ?""",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def get_win_rate(
    symbol: str | None = None,
    days: int = 30,
    is_paper: bool = False,
) -> dict:
    """
    Calculate win rate, average P&L, and trade stats for the last N days.
    """
    table = "paper_trades" if is_paper else "trade_log"
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    conditions = ["entry_time >= ?", "outcome != 'OPEN'"]
    params: list[Any] = [cutoff]

    if symbol:
        conditions.append("symbol=?")
        params.append(symbol.upper())

    where = "WHERE " + " AND ".join(conditions)

    with _conn() as con:
        rows = con.execute(
            f"SELECT outcome, pnl_pct, realized_pnl, symbol FROM {table} {where}",
            params,
        ).fetchall()
        open_count = con.execute(
            f"SELECT COUNT(*) FROM {table} WHERE outcome='OPEN'",
        ).fetchone()[0]

    rows = [dict(r) for r in rows]
    if not rows:
        return {
            "total_trades": 0, "wins": 0, "losses": 0, "breakevens": 0,
            "open_trades": open_count, "win_rate_pct": 0,
            "avg_win_pct": 0, "avg_loss_pct": 0, "avg_pnl_pct": 0,
            "total_realized_pnl": 0, "best_trade": None, "worst_trade": None,
            "period_days": days,
        }

    wins       = [r for r in rows if r["outcome"] == "WIN"]
    losses     = [r for r in rows if r["outcome"] == "LOSS"]
    breakevens = [r for r in rows if r["outcome"] == "BREAKEVEN"]

    win_rate   = len(wins) / len(rows) * 100 if rows else 0
    avg_win    = sum(r["pnl_pct"] for r in wins)   / len(wins)   if wins   else 0
    avg_loss   = sum(r["pnl_pct"] for r in losses) / len(losses) if losses else 0
    avg_pnl    = sum(r["pnl_pct"] for r in rows)   / len(rows)
    total_pnl  = sum(r["realized_pnl"] for r in rows if r["realized_pnl"])

    best  = max(rows, key=lambda r: r["pnl_pct"] or 0)
    worst = min(rows, key=lambda r: r["pnl_pct"] or 0)

    return {
        "total_trades":      len(rows),
        "wins":              len(wins),
        "losses":            len(losses),
        "breakevens":        len(breakevens),
        "open_trades":       open_count,
        "win_rate_pct":      round(win_rate, 1),
        "avg_win_pct":       round(avg_win, 2),
        "avg_loss_pct":      round(avg_loss, 2),
        "avg_pnl_pct":       round(avg_pnl, 2),
        "total_realized_pnl": round(total_pnl, 2),
        "best_trade":        best,
        "worst_trade":       worst,
        "period_days":       days,
    }


def get_scanner_performance(is_paper: bool = False) -> list[dict]:
    """Show win rate broken down by scanner_source."""
    table = "paper_trades" if is_paper else "trade_log"
    with _conn() as con:
        rows = con.execute(
            f"""
            SELECT
                scanner_source,
                COUNT(*) as total,
                SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN outcome='LOSS' THEN 1 ELSE 0 END) as losses,
                AVG(pnl_pct) as avg_pnl_pct,
                SUM(realized_pnl) as total_pnl
            FROM {table}
            WHERE outcome != 'OPEN'
            GROUP BY scanner_source
            ORDER BY avg_pnl_pct DESC
            """,
        ).fetchall()
    results = []
    for r in rows:
        d = dict(r)
        d["win_rate_pct"] = round(d["wins"] / d["total"] * 100, 1) if d["total"] else 0
        d["avg_pnl_pct"]  = round(d["avg_pnl_pct"] or 0, 2)
        d["total_pnl"]    = round(d["total_pnl"] or 0, 2)
        results.append(d)
    return results


def trade_summary_for_report(days: int = 7) -> dict:
    """Returns a compact dict ready to inject into the HTML report header."""
    real  = get_win_rate(days=days, is_paper=False)
    paper = get_win_rate(days=days, is_paper=True)
    scanner_perf = get_scanner_performance(is_paper=False)

    return {
        "real":  real,
        "paper": paper,
        "scanner_performance": scanner_perf,
        "period_days": days,
    }


if __name__ == "__main__":
    print("Initialising trade_log schema...")
    with _conn():
        pass
    print("Schema OK.")

    stats = get_win_rate(days=30)
    print(f"Win rate (30d): {stats['win_rate_pct']}% over {stats['total_trades']} trades")
    print(f"Open positions: {stats['open_trades']}")
