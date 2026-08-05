#!/usr/bin/env python3
"""Sync docs/control/STATUS.json into the Active Projects Notion row.

Env:
  NOTION_TOKEN
  NOTION_ACTIVE_PROJECTS_DB   default fec5d195b038400bb4752d3e88e0b0f5
  NOTION_PROJECT_NAME         title match in Active Projects (default Agentic-RH)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from notion_client import (  # noqa: E402
    date_start,
    query_database,
    rich,
    select,
    update_page,
    url,
)

DEFAULT_DB = "fec5d195b038400bb4752d3e88e0b0f5"
ROOT = Path(__file__).resolve().parents[2]


def load_status(path: Path) -> dict:
    if not path.is_file():
        raise SystemExit(f"STATUS.json not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def find_project_page(database_id: str, name: str) -> dict:
    rows = query_database(
        database_id.replace("-", ""),
        {
            "property": "Name",
            "title": {"equals": name},
        },
        page_size=5,
    )
    if not rows:
        raise SystemExit(f"No Active Projects row titled exactly: {name}")
    return rows[0]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--status",
        type=Path,
        default=ROOT / "docs" / "control" / "STATUS.json",
    )
    p.add_argument(
        "--project-name",
        default=os.environ.get("NOTION_PROJECT_NAME", "Agentic-RH"),
    )
    p.add_argument(
        "--db",
        default=os.environ.get("NOTION_ACTIVE_PROJECTS_DB", DEFAULT_DB),
    )
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    status = load_status(args.status)
    page = find_project_page(args.db, args.project_name)

    props = {}
    sha = status.get("main_sha") or status.get("commit") or status.get("sha")
    if sha:
        props["Main SHA"] = rich(str(sha))
    next_action = status.get("next_exact_action") or status.get("next")
    if next_action:
        props["Next exact action"] = rich(str(next_action))
    binding = status.get("binding_constraint")
    if binding:
        props["Binding constraint"] = rich(str(binding))
    st = status.get("status")
    if st:
        props["Status"] = select(str(st))
    dash = status.get("repo_dashboard") or status.get("dashboard_url")
    if dash:
        props["Repo dashboard"] = url(str(dash))
    tracker = status.get("tracker_url")
    if tracker:
        props["Tracker URL"] = url(str(tracker))
    props["Last verified"] = date_start(
        str(status.get("last_verified") or date.today().isoformat())
    )

    if args.dry_run:
        print(json.dumps({"page_id": page["id"], "properties": props}, indent=2))
        return

    updated = update_page(page["id"], props)
    print(updated.get("url") or updated.get("id"))


if __name__ == "__main__":
    main()
