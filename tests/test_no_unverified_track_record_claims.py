"""Compliance guard: customer-facing copy must not call the paper record live/verified.

The public track record is a simulated paper-trading record (SEC Marketing Rule
206(4)-1(d)(6): hypothetical performance). Words like "live track record" or
"verified track record" overstate it. This scans shipped source and templates,
not tests or docs.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BANNED = re.compile(r"\b(?:live|verified|audited|proven)\s+track[\s-]+record\b", re.IGNORECASE)
SKIP_DIRS = {"tests", "docs", "venv", ".git", "node_modules", "__pycache__", ".worktrees", "archive"}
SUFFIXES = {".py", ".html", ".jinja", ".j2", ".txt"}


def _shipped_files() -> list[Path]:
    out: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in SUFFIXES:
            continue
        rel_parts = path.relative_to(ROOT).parts
        if any(part in SKIP_DIRS or part.startswith(".") for part in rel_parts[:-1]):
            continue
        out.append(path)
    return out


def test_scanner_actually_sees_source_files():
    names = {p.name for p in _shipped_files()}
    assert {"server.py", "congress_agent.py"} <= names


def test_no_live_or_verified_track_record_claims_in_shipped_copy():
    offenders = []
    for path in _shipped_files():
        for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if BANNED.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{lineno}: {line.strip()[:100]}")
    assert not offenders, "overstated track-record wording:\n" + "\n".join(offenders)
