"""Kelly share-plan + empty-plan gate audit for the daily HTML report.

The 2026-08-24 chain labeled every name Kelly HOLD (equal-weight 4.3% sits
inside the 0.6×/1.3× band) while dollar gaps still implied whole-share ADD
and TRIM. gen_report2 hid Paper & Gate when action_plan was empty, so the
report never showed 'GTLB 13 ADD / RTX 2 TRIM / NVDA HOLD 0'.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from report_share_plan import (
    gate_audit_html,
    share_count,
    share_plan_html,
    share_plan_rows,
    share_plan_summary,
    signal_gate_banner_html,
)

ROOT = Path(__file__).resolve().parents[1]


def test_share_count_add_uses_floor():
    # GTLB 2026-08-24: +$557.97 / $41.78 = 13.35 → 13 ADD
    shares, action = share_count(delta_usd=557.97, price=41.78, quantity=23.727)
    assert action == "ADD"
    assert shares == 13


def test_share_count_trim_uses_ceil():
    # RTX 2026-08-24: -$367.47 / $208.88 = 1.76 → 2 TRIM
    shares, action = share_count(delta_usd=-367.47, price=208.88, quantity=9.176)
    assert action == "TRIM"
    assert shares == 2


def test_share_count_hold_when_gap_is_under_one_share():
    shares, action = share_count(delta_usd=30.07, price=209.81, quantity=7.24)
    assert action == "ADD"
    assert shares == 0.1433


def test_share_count_tmo_shaped_subshare_add_is_not_hold_zero():
    # TMO 2026-08-26: +$566.90 / $633.96 = 0.8942 → ADD 0.8942, not HOLD 0
    shares, action = share_count(delta_usd=566.90, price=633.96, quantity=1.563)
    assert action == "ADD"
    assert shares == 0.8942


def test_share_count_penny_gap_is_hold_zero():
    shares, action = share_count(delta_usd=0.02, price=400.0, quantity=2.0)
    assert action == "HOLD"
    assert shares == 0


def test_share_count_trim_under_one_share_is_fractional_not_ceil_one():
    # DELL-shaped: -$350.84 / $435.91 = 0.8048 → TRIM 0.8049 (4-decimal ceil)
    shares, action = share_count(delta_usd=-350.84, price=435.91, quantity=4.359)
    assert action == "TRIM"
    assert shares == 0.8049
    # Tiny -$8 / $62.60 is 0.1283 sh, not a fabricated whole share
    shares, action = share_count(delta_usd=-8.03, price=62.60, quantity=24.88)
    assert action == "TRIM"
    assert shares == 0.1283


def test_share_count_trim_clamps_to_quantity():
    shares, action = share_count(delta_usd=-5000.0, price=100.0, quantity=2.0)
    assert action == "TRIM"
    assert shares == 2


def test_share_count_missing_price_is_none_not_fake_zero():
    shares, action = share_count(delta_usd=500.0, price=None, quantity=10)
    assert shares is None
    assert action == "HOLD"


def test_share_count_missing_delta_is_none_not_fake_zero():
    shares, action = share_count(delta_usd=None, price=100.0, quantity=10)
    assert shares is None
    assert action == "HOLD"


def _engine() -> dict:
    return {
        "holdings": [
            {
                "symbol": "GTLB",
                "price": 41.78,
                "quantity": 23.727,
                "equity": 991.32,
                "average_buy_price": 40.0,
                "percent_change": 4.45,
            },
            {
                "symbol": "RTX",
                "price": 208.88,
                "quantity": 9.176,
                "equity": 1916.75,
                "average_buy_price": 200.0,
                "percent_change": 4.44,
            },
            {
                "symbol": "NVDA",
                "price": 209.81,
                "quantity": 7.241,
                "equity": 1519.21,
                "average_buy_price": 180.0,
                "percent_change": 16.56,
            },
            {
                "symbol": "JMKE",
                "price": 23.41,
                "quantity": 11.0,
                "equity": 257.51,
                "average_buy_price": 23.0,
                "percent_change": 1.78,
            },
        ],
        "equity_summary": {"total": 36029.89, "cash": 1432.74, "portfolio_equity": 34597.15},
        "kelly": {
            "GTLB": {
                "action": "HOLD",
                "current_pct": 2.8,
                "optimal_pct": 4.3,
                "delta_usd": 557.97,
            },
            "RTX": {
                "action": "HOLD",
                "current_pct": 5.3,
                "optimal_pct": 4.3,
                "delta_usd": -367.47,
            },
            "NVDA": {
                "action": "HOLD",
                "current_pct": 4.2,
                "optimal_pct": 4.3,
                "delta_usd": 30.07,
            },
            "JMKE": {"action": None, "current_pct": None, "optimal_pct": None, "delta_usd": None},
        },
        "action_plan": [],
        "gate_results": [],
        "paper_session": {
            "closed": [
                {
                    "symbol": "SCHW",
                    "action": "TRIM",
                    "shares": 9.0,
                    "outcome": "LOSS",
                    "realized_pnl": -54.23,
                    "exit_price": 113.78,
                    "notes": "Stop hit",
                }
            ],
            "created": [],
        },
        "trade_stats": {"paper": {"win_rate_pct": 0}, "real": {"win_rate_pct": 0}},
    }


def test_share_plan_rows_classify_add_trim_hold():
    rows = share_plan_rows(_engine())
    by = {r["symbol"]: r for r in rows}
    # Kelly HOLD: leftover $ is drift, not an ADD/TRIM order.
    assert by["GTLB"]["kelly_action"] == "HOLD"
    assert by["GTLB"]["lot_action"] == "ADD"
    assert by["GTLB"]["size_action"] == "HOLD"
    assert by["GTLB"]["shares"] == 13
    assert by["RTX"]["lot_action"] == "TRIM"
    assert by["RTX"]["size_action"] == "HOLD"
    assert by["RTX"]["shares"] == 2
    assert by["NVDA"]["size_action"] == "HOLD"
    assert by["NVDA"]["shares"] == 0.1433
    assert by["JMKE"]["shares"] is None
    assert by["JMKE"]["size_action"] == "HOLD"


def test_share_plan_rows_follow_kelly_add_and_trim():
    eng = _engine()
    eng["kelly"]["GTLB"]["action"] = "ADD"
    eng["kelly"]["RTX"]["action"] = "TRIM"
    by = {r["symbol"]: r for r in share_plan_rows(eng)}
    assert by["GTLB"]["size_action"] == "ADD"
    assert by["GTLB"]["shares"] == 13
    assert by["RTX"]["size_action"] == "TRIM"
    assert by["RTX"]["shares"] == 2


def test_share_plan_summary_counts_names_and_shares():
    summary = share_plan_summary(share_plan_rows(_engine()))
    assert summary["trim_names"] == 0
    assert summary["trim_shares"] == 0
    assert summary["add_names"] == 0
    assert summary["add_shares"] == 0
    assert summary["hold_names"] == 4


def test_share_plan_html_easy_columns_in_order():
    html = share_plan_html(_engine())
    headers = ("Action", "Symbol", "Shares", "Now", "Kelly", "Gate")
    found = [html.find(f">{name}<") for name in headers]
    assert all(i >= 0 for i in found), found
    assert found == sorted(found)
    assert ">Kelly band<" not in html
    assert "drift 13 sh" in html
    assert "not gated — Kelly HOLD band" in html


def test_kelly_hold_with_whole_share_gap_is_not_labeled_gated():
    html = share_plan_html(_engine())
    assert "GTLB" in html
    assert "13 sh" in html
    assert "ADD" in html
    gtlb = html[html.find("GTLB") - 80 : html.find("GTLB") + 400]
    assert "not gated" in gtlb.lower()
    assert "APPROVED" not in gtlb
    assert "Kelly HOLD" in gtlb


def test_share_plan_html_lists_hold_when_delta_is_missing():
    html = share_plan_html(_engine())
    assert "JMKE" in html
    assert "HOLD" in html
    assert "N/A" in html


def test_share_plan_html_escapes_symbol():
    eng = _engine()
    eng["holdings"][0]["symbol"] = "<script>pwn</script>"
    eng["kelly"]["<script>pwn</script>"] = eng["kelly"].pop("GTLB")
    html = share_plan_html(eng)
    assert "<script>pwn</script>" not in html
    assert "&lt;script&gt;pwn&lt;/script&gt;" in html


def test_empty_action_plan_still_renders_paper_close():
    html = gate_audit_html(_engine())
    assert html.strip() != ""
    assert "Paper" in html
    assert "SCHW" in html
    assert "9" in html
    assert "LOSS" in html
    assert "-$54.23" in html or "−$54.23" in html or "-54.23" in html
    assert "0 executable" in html.lower() or "gated executable: 0" in html.lower()


def test_signal_banner_on_empty_plan_names_share_counts_and_close():
    html = signal_gate_banner_html(_engine())
    assert html.strip() != ""
    assert "HOLD 4" in html
    assert "Gated executable: 0" in html
    assert "SCHW" in html
    assert "0" in html  # gated executable


def test_approved_gate_row_still_reads_approved():
    eng = _engine()
    eng["action_plan"] = [
        {
            "symbol": "GTLB",
            "action": "BUY",
            "shares": 13,
            "signal_reason": "Kelly ADD",
        }
    ]
    eng["gate_results"] = [{"symbol": "GTLB", "action": "BUY", "approved": True, "reason": "ok"}]
    html = gate_audit_html(eng)
    assert "APPROVED" in html
    assert "GTLB" in html


def _render(tmp_path: Path, engine: dict) -> str:
    source = tmp_path / "engine_results.json"
    output = tmp_path / "report.html"
    source.write_text(json.dumps(engine))
    env = os.environ.copy()
    env.update(GESH_ENGINE_RESULTS=str(source), GESH_REPORT_OUTPUT=str(output))
    proc = subprocess.run(
        [sys.executable, "gen_report2.py"],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return output.read_text()


def test_full_report_includes_share_plan_and_paper_close(tmp_path):
    report = _render(tmp_path, _engine())
    assert "Action plan" in report
    assert "GTLB" in report
    assert "ADD" in report
    assert "TRIM" in report
    assert "13" in report
    assert "Paper" in report
    assert "SCHW" in report
    # Must not claim the dollar-drift ADD was gate-cleared.
    start = report.find("Action plan")
    gtlb_chunk = report[start : start + 4000]
    assert "APPROVED" not in gtlb_chunk or "not gated" in gtlb_chunk.lower()


def test_banner_trim_approved_does_not_say_gated_zero():
    eng = _engine()
    eng["action_plan"] = [
        {"symbol": "RTX", "action": "TRIM", "shares": 2, "signal_reason": "Kelly TRIM"}
    ]
    eng["gate_results"] = [{"symbol": "RTX", "action": "TRIM", "approved": True, "reason": "ok"}]
    html = signal_gate_banner_html(eng)
    assert "Gated executable: 0" not in html
    assert "RTX" in html
    assert "cleared" in html.lower() or "APPROVED" in html


def test_banner_buy_without_gate_row_is_not_evaluated():
    eng = _engine()
    eng["action_plan"] = [
        {"symbol": "GTLB", "action": "BUY", "shares": 13, "signal_reason": "Kelly ADD"}
    ]
    eng["gate_results"] = []
    html = signal_gate_banner_html(eng)
    assert "Gated executable: 0" not in html
    assert "NOT EVALUATED" in html
    assert "GTLB" in html


def test_share_plan_does_not_approve_mismatched_share_count():
    eng = _engine()
    eng["action_plan"] = [
        {"symbol": "GTLB", "action": "BUY", "shares": 1, "signal_reason": "cash-capped"}
    ]
    eng["gate_results"] = [{"symbol": "GTLB", "action": "BUY", "approved": True, "reason": "ok"}]
    html = share_plan_html(eng)
    gtlb = html[html.find("GTLB") : html.find("GTLB") + 800]
    assert "13 sh" in gtlb
    assert "APPROVED" not in gtlb
    assert "gated 1 sh" in gtlb


def test_share_plan_approves_when_plan_shares_match_display():
    eng = _engine()
    eng["kelly"]["GTLB"]["action"] = "ADD"
    eng["action_plan"] = [
        {"symbol": "GTLB", "action": "BUY", "shares": 13, "signal_reason": "Kelly ADD"}
    ]
    eng["gate_results"] = [{"symbol": "GTLB", "action": "BUY", "approved": True, "reason": "ok"}]
    html = share_plan_html(eng)
    i = html.find(">GTLB<")
    assert i >= 0
    chunk = html[max(0, i - 120) : i + 500]
    assert "APPROVED" in chunk
    assert "ADD" in chunk


def test_optimal_pct_zero_renders_zero_not_na():
    eng = _engine()
    eng["kelly"]["NVDA"] = {
        "action": "AVOID",
        "current_pct": 4.2,
        "optimal_pct": 0.0,
        "recommended_pct": 9.9,
        "delta_usd": -1519.21,
    }
    html = share_plan_html(eng)
    nvda = html[html.find("NVDA") : html.find("NVDA") + 800]
    assert "N/A" not in nvda or "0.0%" in nvda
    assert "0.0%" in nvda
    assert "9.9%" not in nvda
