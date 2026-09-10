"""Honest regime-prompt + Rule 5 overrides (gap P1-RSI0).

``run_ai2.py`` used ``rd.get("rsi") or 0``. A failed OHLCV fetch (null RSI)
then told Claude ``RSI=0.0`` (maximally oversold) and made Rule 5 unable to
fire. These tests lock the extracted helpers: missing/bool/NaN stay n/a and
do not invent an ADD→HOLD override.

run_ai2 executes at import, so the script is pinned by source — never imported.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from chain_regime_context import (
    build_regime_summary,
    collect_rsi_add_overrides,
    finite_regime_metric,
    format_regime_line,
)

REPO = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "value",
    [None, "", "n/a", True, False, float("nan"), float("inf"), float("-inf"), object()],
)
def test_finite_regime_metric_rejects_non_measurements(value):
    assert finite_regime_metric(value) is None


@pytest.mark.parametrize("value,expected", [(0.0, 0.0), (70.0, 70.0), (69.9, 69.9), ("72", 72.0)])
def test_finite_regime_metric_keeps_real_prints(value, expected):
    assert finite_regime_metric(value) == expected


def test_format_regime_line_missing_metrics_are_na_not_zero():
    line = format_regime_line({"symbol": "SPCX", "regime": "UNKNOWN", "rsi": None, "adx": None})
    assert "SPCX" in line
    assert "RSI=n/a" in line
    assert "ADX=n/a" in line
    assert "RSI=0.0" not in line
    assert "ADX=0.0" not in line


def test_format_regime_line_bool_true_is_not_one():
    line = format_regime_line({"symbol": "NVDA", "regime": "TRENDING", "rsi": True, "adx": True})
    assert "RSI=n/a" in line
    assert "ADX=n/a" in line
    assert "RSI=1.0" not in line


def test_format_regime_line_measured_zero_rsi_is_kept():
    line = format_regime_line(
        {"symbol": "KO", "regime": "RANGING", "adx": 12.0, "rsi": 0.0, "bb_width": 3.5, "pct_b": 40}
    )
    assert "RSI=0.0" in line
    assert "ADX=12.0" in line
    assert "BB-Width=3.5%" in line
    assert "%B=40%" in line


def test_build_regime_summary_empty_and_blank_macro():
    text = build_regime_summary([], "")
    assert "No per-symbol regime data" in text
    assert "FRED macro: unavailable" in text


def test_build_regime_summary_uses_supplied_macro():
    text = build_regime_summary(
        [{"symbol": "NVDA", "regime": "TRENDING", "adx": 28.0, "rsi": 55.0}],
        "Fed funds FALLING",
    )
    assert "NVDA: TRENDING" in text
    assert "MACRO: Fed funds FALLING" in text


def test_collect_rsi_add_overrides_inclusive_70():
    overrides = collect_rsi_add_overrides(
        [{"symbol": "NVDA", "rsi": 70.0}],
        {"NVDA": {"action": "ADD"}},
    )
    assert "NVDA" in overrides
    assert "70" in overrides["NVDA"]


def test_collect_rsi_add_overrides_69_does_not_fire():
    assert (
        collect_rsi_add_overrides(
            [{"symbol": "NVDA", "rsi": 69.9}],
            {"NVDA": {"action": "ADD"}},
        )
        == {}
    )


def test_collect_rsi_add_overrides_missing_rsi_does_not_fire_or_invent_zero():
    assert (
        collect_rsi_add_overrides(
            [{"symbol": "SPCX", "rsi": None}],
            {"SPCX": {"action": "ADD"}},
        )
        == {}
    )


def test_collect_rsi_add_overrides_buy_and_hold_are_not_rule_5():
    """Rule 5 in the AI prompt only remaps Kelly ADD, matching the prior loop."""
    rows = [{"symbol": "NVDA", "rsi": 80.0}]
    assert collect_rsi_add_overrides(rows, {"NVDA": {"action": "BUY"}}) == {}
    assert collect_rsi_add_overrides(rows, {"NVDA": {"action": "HOLD"}}) == {}


def test_collect_rsi_add_overrides_skips_blank_symbol_and_non_dict():
    assert (
        collect_rsi_add_overrides(
            [None, "x", {"symbol": "", "rsi": 80}, {"symbol": "  ", "rsi": 80}],
            {"": {"action": "ADD"}},
        )
        == {}
    )


def test_run_ai2_source_uses_honest_helpers():
    src = (REPO / "run_ai2.py").read_text()
    tree = ast.parse(src)
    imported = {
        alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module == "chain_regime_context"
        for alias in node.names
    }
    assert "build_regime_summary" in imported
    assert "collect_rsi_add_overrides" in imported
    assert 'rd.get("rsi") or 0' not in src
    assert 'rd.get("adx") or 0' not in src
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert "build_regime_summary" in names
    assert "collect_rsi_add_overrides" in names
