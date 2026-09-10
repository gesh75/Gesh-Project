"""Execution tests for gen_report2._gate_audit_section.

PR #243 covers the SIGNAL-vs-GATE / MODEL FAILOVER banners. This file covers
the Paper & Risk-Gate Audit *table* — the surface operators actually use to
read per-name verdicts. A mismatch between action_plan.action and
gate_results.action (ADD vs BUY after the daily-chain remap) silently drops
the verdict to an em-dash, which reads as "not checked" rather than blocked.

gen_report2.py runs its pipeline on import, so the helper is extracted via
ast (same pattern as test_degraded_provenance / test_holding_card_renesting).
"""

from __future__ import annotations

import ast
from html import escape as _esc
from pathlib import Path

import pytest

import report_share_plan

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_gate_audit():
    tree = ast.parse((REPO_ROOT / "gen_report2.py").read_text())
    ns: dict = {"_esc": _esc, "report_share_plan": report_share_plan}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_gate_audit_section":
            exec(compile(ast.Module(body=[node], type_ignores=[]), "gen_report2.py", "exec"), ns)
            return ns["_gate_audit_section"]
    raise AssertionError("gen_report2._gate_audit_section is missing")


@pytest.fixture(scope="module")
def gate_audit():
    return _load_gate_audit()


def _plan(**over) -> dict:
    row = {
        "symbol": "HOOD",
        "action": "BUY",
        "shares": 4,
        "signal_reason": "Kelly ADD after $500 buffer",
    }
    row.update(over)
    return row


def _gate(**over) -> dict:
    row = {"symbol": "HOOD", "action": "BUY", "approved": True, "reason": ""}
    row.update(over)
    return row


def test_empty_plan_renders_nothing_even_when_gates_exist(gate_audit) -> None:
    html = gate_audit({"action_plan": [], "gate_results": [_gate()]})
    assert html == ""


def test_approved_buy_shows_approved_verdict(gate_audit) -> None:
    html = gate_audit({"action_plan": [_plan()], "gate_results": [_gate()]})
    assert "HOOD" in html
    assert "BUY" in html
    assert ">4<" in html
    assert "✅ APPROVED" in html
    assert "Paper &amp; Risk-Gate Audit" in html


def test_rejected_buy_shows_escaped_reason(gate_audit) -> None:
    html = gate_audit(
        {
            "action_plan": [_plan()],
            "gate_results": [
                _gate(
                    approved=False,
                    reason="POSITION_CAP: <script>alert(1)</script>",
                )
            ],
        }
    )
    assert "⛔" in html
    assert "POSITION_CAP" in html
    assert "<script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_hold_without_gate_is_override_not_unchecked(gate_audit) -> None:
    html = gate_audit(
        {
            "action_plan": [_plan(action="HOLD", shares=0, signal_reason="Rule 5 RSI")],
            "gate_results": [],
        }
    )
    assert "— override" in html
    assert "APPROVED" not in html


def test_hold_with_matching_gate_uses_the_gate_verdict(gate_audit) -> None:
    html = gate_audit(
        {
            "action_plan": [_plan(action="HOLD", shares=0)],
            "gate_results": [_gate(action="HOLD", approved=True)],
        }
    )
    assert "✅ APPROVED" in html
    assert "— override" not in html


def test_buy_without_matching_gate_is_unchecked_dash(gate_audit) -> None:
    html = gate_audit({"action_plan": [_plan()], "gate_results": []})
    assert "—" in html
    assert "APPROVED" not in html
    assert "override" not in html


def test_action_mismatch_does_not_attach_the_other_side_verdict(gate_audit) -> None:
    """Daily chain remaps ADD→BUY before the gate. If only one side remaps,
    a (symbol, action) lookup miss must not inherit the leftover ADD row.
    """
    html = gate_audit(
        {
            "action_plan": [_plan(action="BUY")],
            "gate_results": [_gate(action="ADD", approved=True)],
        }
    )
    assert "✅ APPROVED" not in html
    assert "—" in html


def test_symbol_is_html_escaped(gate_audit) -> None:
    html = gate_audit(
        {
            "action_plan": [_plan(symbol="<img src=x onerror=alert(1)>")],
            "gate_results": [],
        }
    )
    assert "<img" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html


def test_missing_shares_defaults_to_zero(gate_audit) -> None:
    plan = _plan()
    plan.pop("shares")
    html = gate_audit({"action_plan": [plan], "gate_results": [_gate()]})
    assert ">0<" in html


def test_zero_win_rate_is_shown_missing_win_rate_is_not(gate_audit) -> None:
    shown = gate_audit(
        {
            "action_plan": [_plan()],
            "gate_results": [_gate()],
            "trade_stats": {"paper": {"win_rate_pct": 0}, "real": {"win_rate_pct": 55}},
        }
    )
    assert "paper win-rate 7d: 0%" in shown
    assert "real win-rate 7d: 55%" in shown

    hidden = gate_audit(
        {
            "action_plan": [_plan()],
            "gate_results": [_gate()],
            "trade_stats": {"paper": {}, "real": {}},
        }
    )
    assert "win-rate" not in hidden


def test_paper_records_without_stamp_are_on_file_not_today(gate_audit) -> None:
    html = gate_audit(
        {
            "action_plan": [_plan()],
            "gate_results": [_gate()],
            "run_manifest": {"run_id": "run-today"},
            "paper_session": {"created": [{"symbol": "HOOD"}], "closed": []},
        }
    )
    assert "paper record(s) on file" in html
    assert "paper record(s) today" not in html


def test_paper_records_with_matching_stamp_are_today(gate_audit) -> None:
    html = gate_audit(
        {
            "action_plan": [_plan()],
            "gate_results": [_gate()],
            "run_manifest": {"run_id": "run-today"},
            "paper_gate_run_id": "run-today",
            "paper_session": {"created": [{"symbol": "HOOD"}], "closed": []},
        }
    )
    assert "paper record(s) today" in html
    assert "on file" not in html
