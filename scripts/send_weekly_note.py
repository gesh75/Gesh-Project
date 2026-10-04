#!/usr/bin/env python3
"""Send the next weekly learning note. Dry run unless --send is given.

    python scripts/send_weekly_note.py                       # dry run: counts only
    python scripts/send_weekly_note.py --send --only-email you@example.com --include-drafts --ignore-window
    python scripts/send_weekly_note.py --send                # the scheduled form

Never prints an address. See weekly_note.py for the rules that keep the "at most weekly" promise.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import weekly_note  # noqa: E402


def _load_env() -> None:
    try:
        from dotenv import load_dotenv  # noqa: PLC0415 - optional, mirrors run_weekly_digest.py
    except ImportError:
        return
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--send", action="store_true", help="actually send (default is a dry run)")
    p.add_argument("--only-email", help="restrict to one confirmed address (testing)")
    p.add_argument("--include-drafts", action="store_true", help="allow status: draft notes (testing)")
    p.add_argument("--ignore-window", action="store_true", help="skip the Tue-Thu 10-15 ET gate (testing)")
    p.add_argument("--limit", type=int, default=weekly_note.DEFAULT_LIMIT)
    a = p.parse_args(argv)
    _load_env()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    r = weekly_note.run(
        send=a.send,
        only_email=a.only_email,
        include_drafts=a.include_drafts,
        ignore_window=a.ignore_window,
        limit=a.limit,
    )
    print(
        f"note={r.note_id} eligible={r.eligible} sent={r.sent} failed={r.failed}"
        + (f" ({r.skipped_reason})" if r.skipped_reason else "")
    )
    return 1 if r.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
