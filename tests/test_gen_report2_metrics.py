"""Report metrics must not fabricate a confident zero.

CVaR 95% is produced as dollars (`cvar_95`); nothing writes `cvar_95_pct`.
GARCH is correctly `None` when `arch` is missing. The renderer used to coerce
both to 0.0% — a number that looks measured. These tests lock the honest
render: derive CVaR %, and print N/A when GARCH was not estimated.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _holding() -> dict:
    return {
        "symbol": "NVDA",
        "price": 100.0,
        "quantity": 1.0,
        "equity": 100.0,
        "average_buy_price": 90.0,
        "percent_change": 5.0,
    }


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


def test_snapshot_notes_render_as_banner_not_invented_numbers(tmp_path):
    report = _render(
        tmp_path,
        {
            "holdings": [_holding()],
            "equity_summary": {"total": 100.0, "cash": 0.0, "portfolio_equity": 100.0},
            "snapshot": {
                "fetched_at": "2026-08-23T00:00:00Z",
                "notes": "COST BASIS UNKNOWN — mark=avg.",
            },
            "monte_carlo": {},
            "vol_models": {},
            "kelly": {},
            "consensus": {},
            "regime": {},
            "ai_result": {},
        },
    )
    assert "COST BASIS UNKNOWN — mark=avg." in report
    assert "override-banner" in report


def test_cvar_card_derives_percent_from_dollars_when_pct_key_absent(tmp_path):
    # Measured 2026-08-21 book: $1,608.63 / $34,799.63 = 4.62% → rendered .1f.
    report = _render(
        tmp_path,
        {
            "holdings": [_holding()],
            "equity_summary": {
                "total": 34799.63,
                "cash": 1313.45,
                "portfolio_equity": 34799.63,
            },
            "monte_carlo": {
                "cvar_95": 1608.63,
                "current_equity": 34799.63,
                "var_95_pct": 2.84,
                "median": 35000,
                "prob_profit_pct": 55,
                "buckets": [],
            },
            "vol_models": {},
            "kelly": {},
            "consensus": {},
            "regime": {},
            "ai_result": {},
        },
    )
    assert "CVaR 95%" in report
    assert ">0.0%</div>" not in report.split("CVaR 95%", 1)[1][:200]
    assert "4.6%" in report.split("CVaR 95%", 1)[1][:200]


def test_cvar_card_renders_na_when_neither_dollars_nor_pct_exist(tmp_path):
    report = _render(
        tmp_path,
        {
            "holdings": [_holding()],
            "equity_summary": {"total": 1000, "cash": 1000, "portfolio_equity": 0},
            "monte_carlo": {
                "var_95_pct": 2.8,
                "median": 1000,
                "prob_profit_pct": 50,
                "buckets": [],
            },
            "vol_models": {},
            "kelly": {},
            "consensus": {},
            "regime": {},
            "ai_result": {},
        },
    )
    cvar_block = report.split("CVaR 95%", 1)[1][:200]
    assert "N/A" in cvar_block
    assert "0.0%" not in cvar_block


def test_holding_card_prints_na_for_none_garch(tmp_path):
    report = _render(
        tmp_path,
        {
            "holdings": [_holding()],
            "equity_summary": {"total": 1000, "cash": 900, "portfolio_equity": 100},
            "monte_carlo": {
                "cvar_95": 10.0,
                "current_equity": 1000,
                "var_95_pct": 2.0,
                "median": 1000,
                "prob_profit_pct": 50,
                "buckets": [],
            },
            "vol_models": {
                "NVDA": {
                    "garch_annual_pct": None,
                    "ewma_annual_pct": 31.2,
                    "estimator": "ewma:no_arch",
                }
            },
            "kelly": {},
            "consensus": {},
            "regime": {},
            "ai_result": {},
        },
    )
    assert "GARCH 0.0%" not in report
    assert "GARCH N/A" in report
    assert "31.2%" in report


def test_vol_table_prints_na_for_none_garch(tmp_path):
    report = _render(
        tmp_path,
        {
            "holdings": [_holding()],
            "equity_summary": {"total": 1000, "cash": 900, "portfolio_equity": 100},
            "monte_carlo": {
                "cvar_95": 10.0,
                "current_equity": 1000,
                "var_95_pct": 2.0,
                "median": 1000,
                "prob_profit_pct": 50,
                "buckets": [],
            },
            "vol_models": {"NVDA": {"garch_annual_pct": None, "ewma_annual_pct": 40.0}},
            "kelly": {},
            "consensus": {},
            "regime": {},
            "ai_result": {},
        },
    )
    # Table cell, not the holding-card line.
    assert ">N/A</td>" in report
    assert "GARCH 0.0%" not in report


def test_missing_monte_carlo_does_not_paint_var_as_ok_zero(tmp_path):
    report = _render(
        tmp_path,
        {
            "holdings": [_holding()],
            "equity_summary": {"total": 1000, "cash": 1000, "portfolio_equity": 0},
            "monte_carlo": {},
            "vol_models": {},
            "kelly": {},
            "consensus": {},
            "regime": {},
            "ai_result": {},
        },
    )
    var_block = report.split("VaR 95%", 1)[1][:280]
    assert "N/A" in var_block
    assert "✓ OK" not in var_block
    assert "0.0%" not in var_block
    cvar_block = report.split("CVaR 95%", 1)[1][:200]
    assert "N/A" in cvar_block
    prob_block = report.split("Prob Profit", 1)[1][:200]
    assert "N/A" in prob_block
    median_block = report.split("MC Median", 1)[1][:200]
    assert "N/A" in median_block
    assert "$0" not in median_block


def test_missing_ai_scores_render_na_not_zero(tmp_path):
    """Failover typed-blanks (portfolio_score=None) must not render as 0/100."""
    report = _render(
        tmp_path,
        {
            "holdings": [_holding()],
            "equity_summary": {"total": 1000, "cash": 1000, "portfolio_equity": 0},
            "monte_carlo": {},
            "vol_models": {},
            "kelly": {},
            "consensus": {},
            "regime": {},
            "ai_result": {},
        },
    )
    ai_block = report.split("AI Score", 1)[1][:200]
    assert "N/A" in ai_block
    assert "0/100" not in ai_block
    risk_block = report.split("AI Risk Opinion", 1)[1][:200]
    assert "N/A" in risk_block
    assert "0/100" not in risk_block
    div_block = report.split("Diversification", 1)[1][:200]
    assert "N/A" in div_block
    assert "0/100" not in div_block


def test_measured_zero_ai_score_is_kept(tmp_path):
    """A real 0 is a measurement. Only missing/unparseable scores become N/A."""
    report = _render(
        tmp_path,
        {
            "holdings": [_holding()],
            "equity_summary": {"total": 1000, "cash": 1000, "portfolio_equity": 0},
            "monte_carlo": {},
            "vol_models": {},
            "kelly": {},
            "consensus": {},
            "regime": {},
            "ai_result": {
                "portfolio_score": 0,
                "risk_score": 0,
                "diversification_score": 0,
            },
        },
    )
    assert "0/100" in report.split("AI Score", 1)[1][:200]
    assert "0/100" in report.split("AI Risk Opinion", 1)[1][:200]
    assert "0/100" in report.split("Diversification", 1)[1][:200]
