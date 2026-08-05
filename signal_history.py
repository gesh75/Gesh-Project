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
import os
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

# Durable confirmed-flip EVENTS (Codex review P1, 2026-07-17). detect_confirmed_flips
# only sees a flip while it sits at the latest streak boundary — one more snapshot and
# it is gone, so a failed alert send was lost forever and the Friday digest missed
# anything confirmed earlier in the week. Events persist; delivery reads events.
_FLIPS_DDL = """
CREATE TABLE IF NOT EXISTS signal_flips (
    symbol         TEXT NOT NULL,
    prior_verdict  TEXT NOT NULL,
    new_verdict    TEXT NOT NULL,
    confirmed_date TEXT NOT NULL,      -- YYYY-MM-DD of the first day of the streak
    score          REAL,
    created_at     TEXT NOT NULL,
    PRIMARY KEY (symbol, confirmed_date)
);
"""

# A "flip" between a real verdict and a data-outage marker is noise, not signal —
# BUY → INSUFFICIENT DATA must never reach a customer inbox (Codex follow-up).
_NON_VERDICTS = ("INSUFFICIENT", "UNKNOWN", "ERROR", "N/A")


def _is_real_verdict(v: str) -> bool:
    up = (v or "").strip().upper()
    return bool(up) and not any(marker in up for marker in _NON_VERDICTS)


def _conn() -> sqlite3.Connection:
    path = Path(DB_PATH)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute(_DDL)
    con.execute(_FLIPS_DDL)
    # alert_log/digest_log in this DB carry customer emails; the auth DB is 0600
    # but this one shipped 0644 (Codex finding). Tighten best-effort — same user
    # owns every reader (server + launchd agents), so 0600 is safe.
    try:
        if path.exists() and (path.stat().st_mode & 0o077):
            os.chmod(path, 0o600)
    except OSError:  # read-only mount etc. — never let hygiene break the job
        pass
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
            try:
                score = float(r["score"]) if r.get("score") is not None else None
                confidence = float(r["confidence"]) if r.get("confidence") is not None else None
            except (TypeError, ValueError):
                continue
            con.execute(
                "INSERT INTO signal_history (symbol, date, verdict, score, confidence) "
                "VALUES (?,?,?,?,?) ON CONFLICT(symbol, date) DO UPDATE SET "
                "verdict=excluded.verdict, score=excluded.score, "
                "confidence=excluded.confidence",
                (sym, day, verdict, score, confidence),
            )
            written += 1
    return written


def get_history(symbol: str, days: int = 30) -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT date, verdict, score, confidence FROM signal_history "
            "WHERE symbol=? ORDER BY date DESC LIMIT ?",
            (symbol.upper().strip(), max(1, days)),
        ).fetchall()
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
            continue  # streak not uniform → not confirmed
        prior = hist[-(persistence_days + 1)]
        if prior["verdict"] == new_verdict:
            continue  # no change at the streak boundary
        flips.append(
            {
                "symbol": sym.upper().strip(),
                "prior_verdict": prior["verdict"],
                "new_verdict": new_verdict,
                "score": streak[-1]["score"],
                "confirmed_since": streak[0]["date"],
            }
        )
    return flips


def record_confirmed_flips(symbols: list[str], persistence_days: int = 2) -> list[dict]:
    """Persist today's confirmed flips as durable events (INSERT OR IGNORE — a flip
    is one event, however many snapshots later observe it). Non-verdict states
    (INSUFFICIENT DATA etc.) are filtered on BOTH sides: a data outage is not a
    signal change. Returns the flips that are NEWLY recorded this call."""
    detected = detect_confirmed_flips(symbols, persistence_days=persistence_days)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    new: list[dict] = []
    with _conn() as con:
        for f in detected:
            if not (_is_real_verdict(f["prior_verdict"]) and _is_real_verdict(f["new_verdict"])):
                continue
            cur = con.execute(
                "INSERT OR IGNORE INTO signal_flips "
                "(symbol, prior_verdict, new_verdict, confirmed_date, score, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (
                    f["symbol"],
                    f["prior_verdict"],
                    f["new_verdict"],
                    f["confirmed_since"],
                    f["score"],
                    now,
                ),
            )
            if cur.rowcount:
                new.append(f)
    return new


def flips_since(symbols: list[str], since_date: str) -> list[dict]:
    """Durable flip events for `symbols` with confirmed_date >= since_date
    (YYYY-MM-DD, inclusive). This is what delivery reads — the weekly digest asks
    for the market week, the alerts job for its retry window — so an event
    confirmed Monday is still visible Friday even though the live detector has
    long since moved past it."""
    want = {s.upper().strip() for s in symbols if s}
    if not want:
        return []
    with _conn() as con:
        rows = con.execute(
            "SELECT symbol, prior_verdict, new_verdict, confirmed_date, score "
            "FROM signal_flips WHERE confirmed_date >= ? ORDER BY confirmed_date, symbol",
            (since_date,),
        ).fetchall()
    return [
        {
            "symbol": r["symbol"],
            "prior_verdict": r["prior_verdict"],
            "new_verdict": r["new_verdict"],
            "confirmed_since": r["confirmed_date"],
            "score": r["score"],
        }
        for r in rows
        if r["symbol"] in want
    ]
