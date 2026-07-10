#!/usr/bin/env python3
"""Classify ``live_portfolio_snapshot.json`` freshness for the daily chain's tiered gate.

The daily-analysis chain (``run_daily_analysis.sh``) used to hard-abort whenever
BOTH snapshot-fetch paths (MCP + legacy robin_stocks) failed, which left
``engine_results.json`` to go stale and starved the ``agentic-rh`` reader into its
advisory-only fallback. This helper lets the chain fall back to the last
*serviceable* snapshot instead of aborting: engines + GESH signals still refresh
on last-known holdings (only cash/sizing is stale), and a genuinely missing or
too-old snapshot is still fatal.

Prints ONE classification token to stdout and human detail to stderr:

    FRESH        age <= 30 min                    -> normal run
    SERVICEABLE  30 min < age <= serviceable_max  -> degraded run (last-known holdings)
    STALE        age > serviceable_max            -> fatal (abort the chain)
    MISSING      no readable snapshot             -> fatal (abort the chain)

Usage: ``snapshot_state.py <serviceable_max_min> [snapshot_path]``
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

FRESH_MAX_MIN = 30.0
DEFAULT_SERVICEABLE_MAX_MIN = 1440.0  # 24h; override via GESH_SNAPSHOT_SERVICEABLE_MIN
DEFAULT_SNAPSHOT_PATH = "live_portfolio_snapshot.json"


def classify(path: str, serviceable_max_min: float,
             *, now: datetime | None = None) -> tuple[str, str]:
    """Return ``(state, detail)`` for the snapshot at ``path``.

    ``state`` is one of FRESH / SERVICEABLE / STALE / MISSING. ``detail`` is a
    human-readable line (may carry a source WARNING). Never raises — an
    unreadable/absent/malformed snapshot classifies as MISSING."""
    now = now or datetime.now(timezone.utc)
    try:
        with open(path) as fh:
            snap = json.load(fh)
        age_min = (now - datetime.fromisoformat(snap["fetched_at"])).total_seconds() / 60
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return "MISSING", f"snapshot unreadable ({path}): {exc}"

    src = snap.get("source", "UNKNOWN")
    holdings = len(snap.get("holdings", []))
    detail = f"snapshot age: {age_min:.1f} min  holdings: {holdings}  source: {src}"
    if src != "robinhood_mcp":
        detail += ("\nWARNING: snapshot source is not 'robinhood_mcp' — legacy fallback ran; "
                   "cash fields reflect that path. Verify deployable before trusting sizing.")

    if age_min <= FRESH_MAX_MIN:
        return "FRESH", detail
    if age_min <= serviceable_max_min:
        return "SERVICEABLE", detail
    return "STALE", detail


def main(argv: list[str]) -> int:
    serviceable_max_min = float(argv[1]) if len(argv) > 1 else DEFAULT_SERVICEABLE_MAX_MIN
    path = argv[2] if len(argv) > 2 else DEFAULT_SNAPSHOT_PATH
    state, detail = classify(path, serviceable_max_min)
    print(detail, file=sys.stderr)
    print(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
