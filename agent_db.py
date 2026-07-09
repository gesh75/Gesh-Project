"""Canonical DB path for agent persistence (news, SEC, fundamentals, consensus cache).

All agent tables live in gesh_financial.db via gesh_core.db.DB_PATH (or GESH_DB_PATH).
Previously fragmented across a legacy gesh.db — do not hardcode paths in agents.
"""

from __future__ import annotations

import os
from pathlib import Path

import db as _db


def agent_db_path() -> Path:
    """Resolved path for agent SQLite tables."""
    override = os.environ.get("GESH_DB_PATH", "").strip()
    if override:
        return Path(override)
    return Path(_db.DB_PATH)