"""Disclosure banners in gen_report2.py must never let a degraded run look clean.

PR #236 added a MODEL FAILOVER banner so a Grok/Gemini-authored analysis cannot
read as a normal Claude run. The SIGNAL vs GATE banner (2026-07-17) is the same
hazard class: "the plan says ADD but the gate blocked everything" cannot hide
in the table. Both functions were untested; a silent interpolation miss or a
loosened predicate would make a failover / blocked-gate day look like a clean
one.

gen_report2.py executes the full report at import time, so the banner builders
are extracted via AST (same pattern as test_degraded_provenance.py). A subprocess
wiring test then proves the HTML actually interpolates them.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from html import escape as _esc
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_banner_fns() -> dict[str, object]:
    src = (REPO_ROOT / "gen_report2.py").read_text()
    tree = ast.parse(src)
    wanted = {"_provider_banner", "_signal_gate_banner"}
    ns: dict[str, object] = {"_esc": _esc}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            segment = ast.get_source_segment(src, node)
            assert segment, f"could not extract {node.name} from gen_report2.py"
            exec(segment, ns)
    missing = wanted - ns.keys()
    assert not missing, f"gen_report2.py is missing banner helper(s): {sorted(missing)}"
    return ns


@pytest.fixture(scope="module")
def banners() -> dict[str, object]:
    return _load_banner_fns()


# ── MODEL FAILOVER (PR #236) ─────────────────────────────────────────────────


def test_provider_banner_hidden_on_a_clean_anthropic_run(banners):
    html = banners["_provider_banner"](
        {"ai_provider": "anthropic", "ai_failover_count": 0}
    )
    assert html == ""


def test_provider_banner_hidden_when_count_is_zero_even_if_provider_is_set(banners):
    """A leftover provider stamp without a failover count must not warn."""
    html = banners["_provider_banner"]({"ai_provider": "grok", "ai_failover_count": 0})
    assert html == ""


def test_provider_banner_hidden_when_provider_is_still_anthropic(banners):
    """Count without a non-Anthropic vendor is not a disclosed swap.

    run_ai2 defaults ai_provider to 'anthropic' when LAST_FAILOVER is missing.
    The banner must not claim a vendor swap it cannot name.
    """
    html = banners["_provider_banner"](
        {"ai_provider": "anthropic", "ai_failover_count": 2}
    )
    assert html == ""


def test_provider_banner_discloses_grok_failover(banners):
    html = banners["_provider_banner"](
        {"ai_provider": "grok", "ai_failover_count": 1}
    )
    assert "MODEL FAILOVER" in html
    assert "<b>grok</b>" in html
    assert "not grammar-constrained" in html
    assert "blank rather than estimated" in html


def test_provider_banner_discloses_gemini_failover(banners):
    html = banners["_provider_banner"](
        {"ai_provider": "gemini", "ai_failover_count": 1}
    )
    assert "MODEL FAILOVER" in html
    assert "<b>gemini</b>" in html


def test_provider_banner_escapes_untrusted_provider_name(banners):
    html = banners["_provider_banner"](
        {"ai_provider": '<img src=x onerror=alert(1)>', "ai_failover_count": 1}
    )
    assert "<img" not in html
    assert "onerror" not in html
    assert "&lt;img" in html


# ── SIGNAL vs GATE (2026-07-17) ──────────────────────────────────────────────


def test_signal_gate_banner_hidden_when_there_is_no_plan(banners):
    assert banners["_signal_gate_banner"]({}) == ""
    assert banners["_signal_gate_banner"]({"action_plan": [], "gate_results": []}) == ""


def test_signal_gate_banner_ignores_non_add_actions_and_unmatched_gates(banners):
    html = banners["_signal_gate_banner"](
        {
            "action_plan": [
                {"symbol": "IONQ", "action": "TRIM"},
                {"symbol": "HOOD", "action": "ADD"},  # no matching gate row
            ],
            "gate_results": [
                {"symbol": "IONQ", "action": "TRIM", "approved": True},
            ],
        }
    )
    assert html == ""


def test_signal_gate_banner_all_adds_blocked(banners):
    html = banners["_signal_gate_banner"](
        {
            "action_plan": [
                {"symbol": "HOOD", "action": "ADD"},
                {"symbol": "CAT", "action": "BUY"},
            ],
            "gate_results": [
                {"symbol": "HOOD", "action": "ADD", "approved": False, "reason": "CASH_FLOOR"},
                {"symbol": "CAT", "action": "BUY", "approved": False, "reason": "CASH_FLOOR"},
            ],
        }
    )
    assert "SIGNAL vs GATE" in html
    assert "2 name(s)" in html
    assert "HOOD" in html and "CAT" in html
    assert "net actionable trades today: 0" in html
    assert "position cap" not in html


def test_signal_gate_banner_mentions_position_cap_when_that_is_the_reason(banners):
    html = banners["_signal_gate_banner"](
        {
            "action_plan": [{"symbol": "NVDA", "action": "ADD"}],
            "gate_results": [
                {
                    "symbol": "NVDA",
                    "action": "ADD",
                    "approved": False,
                    "reason": "POSITION_CAP exceeded",
                }
            ],
        }
    )
    assert "size &gt; 2% position cap" in html
    assert "net actionable trades today: 0" in html


def test_signal_gate_banner_mixed_cleared_and_blocked(banners):
    html = banners["_signal_gate_banner"](
        {
            "action_plan": [
                {"symbol": "HOOD", "action": "ADD"},
                {"symbol": "IONQ", "action": "ADD"},
            ],
            "gate_results": [
                {"symbol": "HOOD", "action": "ADD", "approved": True},
                {"symbol": "IONQ", "action": "ADD", "approved": False, "reason": "ORDER_RATE"},
            ],
        }
    )
    assert "1 ADD signal(s) cleared" in html
    assert "HOOD" in html
    assert "1 blocked" in html
    assert "IONQ" in html


def test_signal_gate_banner_all_adds_cleared(banners):
    html = banners["_signal_gate_banner"](
        {
            "action_plan": [
                {"symbol": "HOOD", "action": "BUY"},
                {"symbol": "BAC", "action": "ADD"},
            ],
            "gate_results": [
                {"symbol": "HOOD", "action": "BUY", "approved": True},
                {"symbol": "BAC", "action": "ADD", "approved": True},
            ],
        }
    )
    assert html.startswith('<div class="override-banner"')
    assert "all 2 planned ADD action(s) cleared" in html


def test_signal_gate_banner_escapes_symbol_names(banners):
    evil = "<script>alert(1)</script>"
    html = banners["_signal_gate_banner"](
        {
            "action_plan": [{"symbol": evil, "action": "ADD"}],
            "gate_results": [
                {"symbol": evil, "action": "ADD", "approved": False, "reason": "KILL_SWITCH"}
            ],
        }
    )
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


# ── Wiring: banners actually land in the generated HTML ──────────────────────


def _minimal_engine(**overrides) -> dict:
    payload = {
        "holdings": [],
        "equity_summary": {"total": 1000, "cash": 1000, "portfolio_equity": 0},
        "ai_result": {"portfolio_verdict": "HOLD", "portfolio_score": 50, "risk_score": 40},
        "monte_carlo": {"buckets": []},
    }
    payload.update(overrides)
    return payload


def _render(tmp_path: Path, engine: dict) -> str:
    source = tmp_path / "engine_results.json"
    output = tmp_path / "report.html"
    source.write_text(json.dumps(engine))
    env = os.environ.copy()
    env.update(GESH_ENGINE_RESULTS=str(source), GESH_REPORT_OUTPUT=str(output))
    subprocess.run(
        [sys.executable, "gen_report2.py"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert output.is_file(), "GESH_REPORT_OUTPUT was ignored — report not written"
    return output.read_text()


def test_report_interpolates_failover_and_signal_gate_banners(tmp_path):
    html = _render(
        tmp_path,
        _minimal_engine(
            ai_provider="grok",
            ai_failover_count=1,
            action_plan=[
                {"symbol": "HOOD", "action": "ADD"},
                {"symbol": "IONQ", "action": "ADD"},
            ],
            gate_results=[
                {"symbol": "HOOD", "action": "ADD", "approved": True},
                {"symbol": "IONQ", "action": "ADD", "approved": False, "reason": "CASH_FLOOR"},
            ],
        ),
    )
    assert "MODEL FAILOVER" in html
    assert "<b>grok</b>" in html
    assert "SIGNAL vs GATE" in html
    assert "HOOD" in html and "IONQ" in html


def test_clean_report_omits_both_disclosure_banners(tmp_path):
    html = _render(tmp_path, _minimal_engine())
    assert "MODEL FAILOVER" not in html
    assert "SIGNAL vs GATE" not in html
