#!/usr/bin/env python3
"""Post an Agentic-RH Soak Sessions row (O2b tracker).

Env:
  NOTION_TOKEN
  NOTION_SOAK_SESSIONS_DB  default 564225425d1c416cb0d2058f8f725edc
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from notion_client import (  # noqa: E402
    checkbox,
    create_page,
    date_start,
    number,
    rich,
    select,
    title,
    url,
)

DEFAULT_DB = "564225425d1c416cb0d2058f8f725edc"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--name", required=True)
    p.add_argument("--result", default="no-trade",
                   choices=["green", "degraded", "halted", "no-trade", "error"])
    p.add_argument("--pnl", type=float, default=None)
    p.add_argument("--trades", type=int, default=None)
    p.add_argument("--sha", default="")
    p.add_argument("--notes", default="")
    p.add_argument("--evidence-url", default="")
    p.add_argument("--control-update", default="")
    p.add_argument("--counts-o2b", action="store_true")
    p.add_argument("--date", default=date.today().isoformat())
    p.add_argument("--db", default=os.environ.get("NOTION_SOAK_SESSIONS_DB", DEFAULT_DB))
    args = p.parse_args()

    props = {
        "Name": title(args.name),
        "Session date": date_start(args.date),
        "Result": select(args.result),
        "Counts toward O2b": checkbox(bool(args.counts_o2b)),
    }
    if args.pnl is not None:
        props["Day P&L"] = number(args.pnl)
    if args.trades is not None:
        props["Trades"] = number(args.trades)
    if args.sha:
        props["SHA"] = rich(args.sha)
    if args.notes:
        props["Notes"] = rich(args.notes)
    if args.evidence_url:
        props["Evidence URL"] = url(args.evidence_url)
    if args.control_update:
        props["Control update"] = url(args.control_update)

    page = create_page(args.db.replace("-", ""), props)
    print(page.get("url") or page.get("id"))


if __name__ == "__main__":
    main()
