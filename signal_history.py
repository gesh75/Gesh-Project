"""Daily per-symbol consensus snapshots (signal_history table).

Substrate for customer signal-change alerts, the card sparkline, verdict-flip
smoothing, and the weekly digest. Deliberately tiny and stdlib-only.

Anti-flapping rule (product research 2026-07): a verdict change is only
CONFIRMED once the new verdict has persisted ``persistence_days`` consecutive
snapshots — day-to-day whiplash is the #1 credibility complaint against
score-based competitors, so raw one-day flips are never surfaced.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from db import DB_PATH  # same cwd-relative / GESH_DB_PATH convention

logger = logging.getLogger("signal_history")

_DDL = """
CREATE TABLE IF NOT EXISTS signal_history (
    symbol     TEXT NOT NULL,
    date       TEXT NOT NULL,          -- YYYY-MM-DD (UTC)
    verdict    TEXT NOT NULL,          -- STRONG BUY|BUY|HOLD|SELL|STRONG SELL
    score      REAL,
    confidence REAL,
    PRIMARY KEY (symbol, date)
);
"""


def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(Path(DB_PATH))
    con.row_factory = sqlite3.Row
    con.execute(_DDL)
    return con


def record_snapshots(rows: list[dict], date: str | None = None) -> int:
    """Upsert one day's consensus rows. Bad rows are skipped, never fatal."""
    day = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    written = 0
    with _conn() as con:
        for r in rows:
            sym = str(r.get("symbol") or "").upper().strip()
            verdict = str(r.get("verdict") or "").strip()
            if not sym or not verdict:
                continue
            con.execute(
                "INSERT INTO signal_history (symbol, date, verdict, score, confidence) "
                "VALUES (?,?,?,?,?) ON CONFLICT(symbol, date) DO UPDATE SET "
                "verdict=excluded.verdict, score=excluded.score, "
                "confidence=excluded.confidence",
                (sym, day,
                 verdict,
                 float(r["score"]) if r.get("score") is not None else None,
                 float(r["confidence"]) if r.get("confidence") is not None else None))
            written += 1
    return written


def get_history(symbol: str, days: int = 30) -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT date, verdict, score, confidence FROM signal_history "
            "WHERE symbol=? ORDER BY date DESC LIMIT ?",
            (symbol.upper().strip(), max(1, days))).fetchall()
    return [dict(r) for r in reversed(rows)]


def detect_confirmed_flips(symbols: list[str], persistence_days: int = 2) -> list[dict]:
    """Confirmed verdict flips: new verdict held `persistence_days` consecutive
    snapshots AND differs from the verdict just before the streak."""
    flips: list[dict] = []
    for sym in symbols:
        hist = get_history(sym, days=persistence_days + 30)
        if len(hist) < persistence_days + 1:
            continue
        streak = hist[-persistence_days:]
        new_verdict = streak[0]["verdict"]
        if any(h["verdict"] != new_verdict for h in streak):
            continue                      # streak not uniform → not confirmed
        prior = hist[-(persistence_days + 1)]
        if prior["verdict"] == new_verdict:
            continue                      # no change at the streak boundary
        flips.append({"symbol": sym.upper().strip(),
                      "prior_verdict": prior["verdict"],
                      "new_verdict": new_verdict,
                      "score": streak[-1]["score"],
                      "confirmed_since": streak[0]["date"]})
    return flips
