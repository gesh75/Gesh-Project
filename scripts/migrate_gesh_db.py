#!/usr/bin/env python3
"""One-time migration: copy agent tables from legacy gesh.db → gesh_financial.db.

Idempotent — skips tables that already have rows in the target. Safe to re-run.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
LEGACY = BASE / "gesh.db"
TARGET = BASE / "gesh_financial.db"

TABLES = ("news_sentiment", "sec_filings", "fundamental_scans")


def _count(conn: sqlite3.Connection, table: str) -> int:
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    except sqlite3.OperationalError:
        return -1


def migrate() -> int:
    if not LEGACY.exists():
        print(f"legacy {LEGACY} missing — nothing to migrate")
        return 0
    if not TARGET.exists():
        print(f"target {TARGET} missing — abort")
        return 1

    leg = sqlite3.connect(LEGACY)
    tgt = sqlite3.connect(TARGET)
    moved = 0
    try:
        for table in TABLES:
            if _count(leg, table) < 0:
                print(f"  skip {table}: not in legacy")
                continue
            src_n = _count(leg, table)
            dst_n = _count(tgt, table)
            if dst_n > 0:
                print(f"  skip {table}: target already has {dst_n} rows")
                continue
            ddl = leg.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            if not ddl or not ddl[0]:
                print(f"  skip {table}: no DDL in legacy")
                continue
            if dst_n < 0:
                tgt.execute(ddl[0])
            rows = leg.execute(f"SELECT * FROM {table}").fetchall()
            cols = [d[1] for d in leg.execute(f"PRAGMA table_info({table})")]
            placeholders = ",".join("?" * len(cols))
            col_list = ",".join(cols)
            tgt.executemany(
                f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})",
                rows,
            )
            tgt.commit()
            moved += len(rows)
            print(f"  migrated {table}: {len(rows)} rows")
    finally:
        leg.close()
        tgt.close()
    print(f"done — {moved} total rows migrated")
    return 0


if __name__ == "__main__":
    sys.exit(migrate())