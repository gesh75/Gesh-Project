#!/usr/bin/env python3
"""Post a Control Updates row to Notion (CI or local).

Env:
  NOTION_TOKEN                 required
  NOTION_CONTROL_UPDATES_DB    default: ee4a034ecb4940cc8cd3452b997e5cd7
  NOTION_PROJECT               default: Agentic-RH
  NOTION_RELATED_PROJECT_PAGE  optional Active Projects page URL/id
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from notion_client import (  # noqa: E402
    create_page,
    date_start,
    rich,
    select,
    title,
    url,
)

DEFAULT_DB = "ee4a034ecb4940cc8cd3452b997e5cd7"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--name", required=True)
    p.add_argument("--sha", default="")
    p.add_argument("--summary", default="")
    p.add_argument("--next", dest="next_action", default="")
    p.add_argument("--binding", default="")
    p.add_argument("--evidence-url", default="")
    p.add_argument("--source-page", default="")
    p.add_argument("--project", default=os.environ.get("NOTION_PROJECT", "Agentic-RH"))
    p.add_argument(
        "--date",
        default=date.today().isoformat(),
        help="YYYY-MM-DD (default: today UTC/local)",
    )
    p.add_argument(
        "--db",
        default=os.environ.get("NOTION_CONTROL_UPDATES_DB", DEFAULT_DB),
    )
    p.add_argument(
        "--related-project",
        default=os.environ.get("NOTION_RELATED_PROJECT_PAGE", ""),
        help="Active Projects page id or URL for relation",
    )
    args = p.parse_args()

    props = {
        "Name": title(args.name),
        "Project": select(args.project),
        "Date": date_start(args.date),
    }
    if args.sha:
        props["SHA"] = rich(args.sha)
    if args.summary:
        props["Summary"] = rich(args.summary)
    if args.next_action:
        props["Next"] = rich(args.next_action)
    if args.binding:
        props["Binding constraint"] = rich(args.binding)
    if args.evidence_url:
        props["Evidence URL"] = url(args.evidence_url)
    if args.source_page:
        props["Source page"] = url(args.source_page)
    if args.related_project:
        page_id = args.related_project.rstrip("/").split("/")[-1].split("?")[0]
        page_id = page_id.replace("-", "")
        if len(page_id) == 32:
            page_id = (
                f"{page_id[:8]}-{page_id[8:12]}-{page_id[12:16]}-"
                f"{page_id[16:20]}-{page_id[20:]}"
            )
        props["Related project"] = {"relation": [{"id": page_id}]}

    page = create_page(args.db.replace("-", ""), props)
    print(page.get("url") or page.get("id"))


if __name__ == "__main__":
    main()
