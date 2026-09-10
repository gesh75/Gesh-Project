"""Paper/gate audit must not label a MERGE-kept session as "today" (gap F-07).

``run_engines2`` MERGE-loads yesterday's ``action_plan`` / ``paper_session``.
A degraded ``run_paper_gate2`` skip writes nothing, so the leftover rows used
to render as "N paper record(s) today". "today" is allowed only when
``paper_gate_run_id`` equals this run's ``run_manifest.run_id``.
"""

from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

from report_share_plan import gate_audit_html, paper_audit_when_label
from run_manifest import paper_gate_provenance

REPO = Path(__file__).resolve().parents[1]


def _eng(**over) -> dict:
    row = {
        "action_plan": [{"symbol": "HOOD", "action": "BUY", "shares": 2, "signal_reason": "Kelly"}],
        "gate_results": [{"symbol": "HOOD", "action": "BUY", "approved": True, "reason": "ok"}],
        "paper_session": {"created": [{"symbol": "HOOD"}], "closed": []},
        "trade_stats": {},
        "run_manifest": {"run_id": "run-today"},
    }
    row.update(over)
    return row


def test_paper_audit_when_label_missing_stamp_is_on_file():
    assert paper_audit_when_label(_eng()) == "on file"
    assert paper_audit_when_label({}) == "on file"
    assert paper_audit_when_label(None) == "on file"


def test_paper_audit_when_label_matching_run_id_is_today():
    assert paper_audit_when_label(_eng(paper_gate_run_id="run-today")) == "today"


def test_paper_audit_when_label_yesterdays_stamp_is_on_file():
    assert paper_audit_when_label(_eng(paper_gate_run_id="run-yesterday")) == "on file"


def test_paper_audit_when_label_blank_ids_are_on_file():
    assert (
        paper_audit_when_label(_eng(run_manifest={"run_id": ""}, paper_gate_run_id="")) == "on file"
    )
    assert (
        paper_audit_when_label(_eng(run_manifest={"run_id": "  "}, paper_gate_run_id="  "))
        == "on file"
    )


def test_gate_audit_html_says_on_file_without_stamp():
    html = gate_audit_html(_eng())
    assert "paper record(s) on file" in html
    assert "paper record(s) today" not in html
    assert "auto-closed on file" in html


def test_gate_audit_html_says_today_when_stamp_matches():
    html = gate_audit_html(_eng(paper_gate_run_id="run-today"))
    assert "paper record(s) today" in html
    assert "on file" not in html


def test_paper_gate_provenance_copies_run_id():
    now = datetime(2026, 9, 10, 14, 30, tzinfo=timezone.utc)
    stamp = paper_gate_provenance({"run_manifest": {"run_id": "abc-123"}}, now=now)
    assert stamp["paper_gate_run_id"] == "abc-123"
    assert stamp["paper_gate_ran_at"].startswith("2026-09-10T14:30:00")


def test_paper_gate_provenance_missing_manifest_does_not_invent_a_run():
    stamp = paper_gate_provenance({}, now="2026-09-10T14:30:00Z")
    assert stamp["paper_gate_run_id"] is None
    assert stamp["paper_gate_ran_at"] == "2026-09-10T14:30:00Z"
    assert paper_audit_when_label({**stamp, "run_manifest": {}}) == "on file"


def test_run_paper_gate2_persist_applies_provenance():
    src = (REPO / "run_paper_gate2.py").read_text()
    tree = ast.parse(src)
    imported = {
        alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module == "run_manifest"
        for alias in node.names
    }
    # Persist-site import is inside main(); walk the whole tree.
    imported |= {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "run_manifest"
        for alias in node.names
    }
    assert "paper_gate_provenance" in imported
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert "paper_gate_provenance" in names
