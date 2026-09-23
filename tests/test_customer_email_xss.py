"""Customer alert/digest HTML must escape untrusted symbol and verdict text.

Flip emails are built from signal_history + the customer's book. A crafted
ticker or verdict string must never become a live ``<script>`` in the inbox.
"""

from __future__ import annotations

import ast
import html as html_mod
import os
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]


def _extract(path: Path, names: set[str]) -> dict:
    src = path.read_text()
    tree = ast.parse(src)
    chunks: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            chunk = ast.get_source_segment(src, node)
            assert chunk is not None
            chunks.append(chunk)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in names:
                    seg = ast.get_source_segment(src, node)
                    assert seg is not None
                    chunks.append(seg)
    ns: dict = {
        "_html": html_mod,
        "os": os,
        "auth_users": SimpleNamespace(issue_token=lambda email, ttl=0, typ="": "test-token"),
    }
    exec("\n\n".join(chunks), ns)  # noqa: S102 — extracted helpers only
    return ns


def test_alert_email_escapes_symbol_and_verdicts():
    ns = _extract(
        REPO / "run_signal_alerts.py",
        {"_build_email", "_base_url", "PERSISTENCE_DAYS", "UNSUB_TTL"},
    )
    html = ns["_build_email"](
        "cust@x.com",
        [
            {
                "symbol": "<script>alert(1)</script>",
                "prior_verdict": "<b>BUY</b>",
                "new_verdict": "HOLD<img src=x onerror=alert(1)>",
                "confirmed_since": "2026-09-01",
            }
        ],
    )
    assert "<script>" not in html
    assert "<img" not in html
    assert "<b>BUY</b>" not in html
    assert "&lt;script&gt;" in html
    assert "&lt;b&gt;BUY&lt;/b&gt;" in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html


def test_digest_compose_escapes_flip_fields():
    src = (REPO / "run_weekly_digest.py").read_text()
    assert "e(f['symbol'])" in src
    assert "e(f['prior_verdict'])" in src
    assert "e(f['new_verdict'])" in src
    assert "e(f['confirmed_since'])" in src
