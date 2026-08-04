#!/usr/bin/env python3
"""[CLAUDE 2026-07-18] Nightly verified SQLite backups (audit P0-7 / Codex Task 8).

Backs up every live GESH database using the SQLite online-backup API (safe
against concurrent writers), runs PRAGMA integrity_check on each copy, keeps
RETENTION_DAYS of history, and prunes older copies. Exits non-zero if any
backup or integrity check fails so launchd/job logs surface the failure.

Paths (2026-08-04): no hardcoded /Users. Override with:
  GESH_HOME          — this product repo root (default: directory of this file)
  GESH_TRADING_HOME  — trading sibling root (default: ../gesh-project-trading)

Restore drill (documented, run manually):
    sqlite3 backups/nightly/<file>  "PRAGMA integrity_check;"
    cp backups/nightly/<file> <original-name>.restored  # then point the app at it
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_BASE = Path(os.getenv("GESH_HOME", Path(__file__).resolve().parent))
_TRADING = Path(
    os.getenv(
        "GESH_TRADING_HOME",
        str(_BASE.parent / "gesh-project-trading"),
    )
)

TARGETS = [
    _BASE / "gesh_financial.db",
    _BASE / "gesh_auth.db",
    _BASE / "gesh.db",
    _TRADING / "gesh_financial.db",
    _TRADING / "gesh.db",
    _TRADING / "crypto_financial.db",
]
RETENTION_DAYS = 14


def backup_one(src: Path, dest_dir: Path, stamp: str) -> bool:
    dest = dest_dir / f"{src.stem}_{stamp}.db"
    try:
        s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
        # Create and restrict the destination before SQLite writes sensitive data.
        dest.touch(mode=0o600, exist_ok=True)
        dest.chmod(0o600)
        d = sqlite3.connect(dest)
        s.backup(d)
        ok = d.execute("PRAGMA integrity_check").fetchone()[0]
        d.close()
        s.close()
        dest.chmod(0o600)
        if ok != "ok":
            print(f"[backup] FAIL integrity: {src} -> {dest}: {ok}")
            return False
        print(
            f"[backup] ok: {src.parent.name}/{src.name} -> {dest.name} "
            f"({dest.stat().st_size:,} bytes)"
        )
        return True
    except Exception as e:
        print(f"[backup] FAIL {src}: {e}")
        return False


def prune(dest_dir: Path) -> None:
    cutoff = time.time() - RETENTION_DAYS * 86400
    for f in dest_dir.glob("*.db"):
        if f.stat().st_mtime < cutoff:
            f.unlink()
            print(f"[backup] pruned {f.name}")


def main() -> int:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    failures = 0
    for src in TARGETS:
        if not src.exists():
            print(f"[backup] skip missing {src}")
            continue
        dest_dir = src.parent / "backups" / "nightly"
        dest_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        dest_dir.chmod(0o700)
        if not backup_one(src, dest_dir, stamp):
            failures += 1
        prune(dest_dir)
    print(f"[backup] done: failures={failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
