"""
db.py — SQLite persistence layer for Gesh Financial.

Tables:
  analysis_runs   — every deep-analysis result (portfolio-level + per-holding JSON)
  equity_snapshots — daily equity snapshot per holding (drives ECG state machine)
"""

from __future__ import annotations
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).parent / "gesh_financial.db"


# ─── Schema ───────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS analysis_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at          TEXT    NOT NULL,          -- ISO-8601 UTC
    model           TEXT,
    portfolio_score INTEGER,
    risk_score      INTEGER,
    diversification INTEGER,
    portfolio_type  TEXT,
    portfolio_verdict TEXT,
    payload_json    TEXT,                      -- full input payload
    result_json     TEXT                       -- full deep-analysis JSON
);

CREATE TABLE IF NOT EXISTS equity_snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_at TEXT    NOT NULL,              -- ISO-8601 UTC
    symbol      TEXT    NOT NULL,
    equity      REAL    NOT NULL,
    percent_change REAL,
    current_price  REAL
);

CREATE INDEX IF NOT EXISTS idx_eq_symbol ON equity_snapshots(symbol, snapshot_at);
CREATE INDEX IF NOT EXISTS idx_runs_at   ON analysis_runs(run_at);
"""


# ─── Connection helper ────────────────────────────────────────────────────────

@contextmanager
def _conn():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        con.executescript(_DDL)
        yield con
        con.commit()
    finally:
        con.close()


def init_db() -> None:
    """Ensure schema exists. Safe to call multiple times."""
    with _conn():
        pass


# ─── Analysis Runs ────────────────────────────────────────────────────────────

def log_analysis(
    model: str,
    payload: dict[str, Any],
    result: dict[str, Any],
) -> int:
    """Insert a deep-analysis run. Returns the new row id."""
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            """
            INSERT INTO analysis_runs
              (run_at, model, portfolio_score, risk_score, diversification,
               portfolio_type, portfolio_verdict, payload_json, result_json)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                now,
                model,
                result.get("portfolio_score"),
                result.get("risk_score"),
                result.get("diversification_score"),
                result.get("portfolio_type"),
                result.get("portfolio_verdict"),
                json.dumps(payload),
                json.dumps(result),
            ),
        )
        return cur.lastrowid


def get_analysis_history(limit: int = 20) -> list[dict]:
    """Return the last N analysis runs (newest first), without bulky JSONs."""
    with _conn() as con:
        rows = con.execute(
            """
            SELECT id, run_at, model, portfolio_score, risk_score,
                   diversification, portfolio_type, portfolio_verdict
            FROM   analysis_runs
            ORDER  BY run_at DESC
            LIMIT  ?
            """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def _safe_json(raw: str | None) -> tuple[dict, bool]:
    """
    Parse a stored JSON column, tolerating malformed data.

    Returns (parsed_dict, had_error). An empty/NULL column is a valid empty dict
    (not an error); only genuinely malformed JSON sets had_error=True. This keeps
    /api/history/{id} at HTTP 200 for the ~80 rows left malformed by the
    2026-06-02 corruption + .recover, instead of 500-ing on json.loads.
    """
    if not raw:
        return {}, False
    try:
        parsed = json.loads(raw)
        return (parsed if isinstance(parsed, dict) else {}), False
    except (ValueError, TypeError):
        return {}, True


def get_analysis_by_id(run_id: int) -> dict | None:
    """Return a full analysis run (with JSONs) by id.

    Malformed payload/result JSON degrades to an empty dict with parse_error=True
    rather than raising — the history endpoint must never 500 on a corrupt row.
    """
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM analysis_runs WHERE id=?", (run_id,)
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["payload"], payload_err = _safe_json(d.pop("payload_json", None))
    d["result"],  result_err  = _safe_json(d.pop("result_json", None))
    if payload_err or result_err:
        d["parse_error"] = True
    return d


# ─── Equity Snapshots (for ECG) ───────────────────────────────────────────────

def log_equity_snapshot(holdings: list[dict]) -> None:
    """Persist current equity/price for every holding."""
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        con.executemany(
            """
            INSERT INTO equity_snapshots (snapshot_at, symbol, equity, percent_change, current_price)
            VALUES (?,?,?,?,?)
            """,
            [
                (
                    now,
                    h["symbol"],
                    h.get("equity", 0),
                    h.get("percent_change", 0),
                    h.get("current_price", 0),
                )
                for h in holdings
            ],
        )


def get_equity_history(symbol: str, days: int = 30) -> list[dict]:
    """Return the last N days of snapshots for one symbol."""
    with _conn() as con:
        rows = con.execute(
            """
            SELECT snapshot_at, equity, percent_change, current_price
            FROM   equity_snapshots
            WHERE  symbol=?
            ORDER  BY snapshot_at DESC
            LIMIT  ?
            """,
            (symbol, days),
        ).fetchall()
    return [dict(r) for r in rows]


def get_portfolio_equity_history(points: int = 60) -> list[dict]:
    """Total portfolio equity per snapshot timestamp (summed across holdings).

    Returns up to `points` most recent snapshots in chronological order — the
    series that feeds the dashboard KPI sparklines.
    """
    with _conn() as con:
        rows = con.execute(
            """
            SELECT snapshot_at, SUM(equity) AS total_equity
            FROM   equity_snapshots
            GROUP  BY snapshot_at
            ORDER  BY snapshot_at DESC
            LIMIT  ?
            """,
            (points,),
        ).fetchall()
    return [dict(r) for r in rows][::-1]  # oldest → newest


def get_all_latest_snapshots() -> list[dict]:
    """Return the most recent snapshot for each tracked symbol."""
    with _conn() as con:
        rows = con.execute(
            """
            SELECT symbol, snapshot_at, equity, percent_change, current_price
            FROM   equity_snapshots
            WHERE  (symbol, snapshot_at) IN (
                SELECT symbol, MAX(snapshot_at)
                FROM   equity_snapshots
                GROUP  BY symbol
            )
            """
        ).fetchall()
    return [dict(r) for r in rows]
