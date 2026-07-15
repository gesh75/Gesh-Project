"""Canonical fair-value % — one convention across UI, Kelly, and persisted values.

Codex #2: the dashboard recomputes the FV gap as (estimate-current)/|current|*100,
but Kelly consumed the model's raw pct_diff. fair_value.canonical_pct_diff is now the
single server-side source; this locks the formula and the fail-safe (0.0 when it
can't be computed), and asserts the JS daFvPct in dashboard.html uses the same math.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import fair_value as fv


@pytest.mark.parametrize("est,cur,expected", [
    (210.0, 394.0, round((210 - 394) / 394 * 100, 2)),   # TSLA-style overvalued (−46.7)
    (130.0, 103.0, round((130 - 103) / 103 * 100, 2)),   # undervalued (+26.2)
    (100.0, 100.0, 0.0),
])
def test_matches_ui_formula(est, cur, expected):
    assert fv.canonical_pct_diff({"estimate": est, "current": cur}) == expected


@pytest.mark.parametrize("bad", [
    None, {}, {"estimate": 10}, {"current": 10}, {"estimate": "x", "current": 5},
    {"estimate": 10, "current": 0}, {"estimate": float("nan"), "current": 5},
])
def test_fails_safe_to_zero(bad):
    assert fv.canonical_pct_diff(bad) == 0.0


def test_ignores_model_pct_diff():
    # Even if the model shipped a contradictory pct_diff, we recompute from est/cur.
    out = fv.canonical_pct_diff({"estimate": 210.0, "current": 394.0, "pct_diff": 88.0})
    assert out < 0                      # canonical says overvalued, not the model's +88


def test_js_daFvPct_uses_same_formula():
    # Guard against the JS drifting from the Python canonical convention.
    html = Path("templates/dashboard.html").read_text()
    m = re.search(r"function daFvPct\(fv\)\{.*?\}", html, re.S)
    assert m, "daFvPct not found"
    body = m.group(0)
    assert "fv.estimate-fv.current" in body.replace(" ", "")
    assert "Math.abs(fv.current)" in body


# ── write-time persistence (Codex #4): normalize pct_diff before it is stored ──

def test_normalize_rewrites_persisted_pct_diff_to_canonical():
    # A model that shipped a contradictory +88 pct_diff must be persisted as the
    # canonical (overvalued, negative) value so reports/history match the dashboard.
    analysis = {"holdings_analysis": [
        {"symbol": "TSLA", "verdict": "TRIM",
         "fair_value": {"estimate": 210.0, "current": 394.0, "status": "OVERVALUED",
                        "pct_diff": 88.0}}]}
    out = fv.normalize_holdings_fair_values(analysis)
    stored = out["holdings_analysis"][0]["fair_value"]["pct_diff"]
    assert stored == fv.canonical_pct_diff({"estimate": 210.0, "current": 394.0})
    assert stored < 0                                        # not the model's +88


def test_normalize_is_noop_without_estimate_current():
    # A bare/legacy fair_value (only pct_diff) is left untouched — never clobbered to 0.
    analysis = {"holdings_analysis": [
        {"symbol": "X", "fair_value": {"pct_diff": -8.2}}]}
    fv.normalize_holdings_fair_values(analysis)
    assert analysis["holdings_analysis"][0]["fair_value"]["pct_diff"] == -8.2


def test_normalize_handles_missing_or_malformed_holdings():
    assert fv.normalize_holdings_fair_values(None) is None
    assert fv.normalize_holdings_fair_values({}) == {}
    # holdings without a dict fair_value must not raise
    a = {"holdings_analysis": [{"symbol": "Y"}, {"symbol": "Z", "fair_value": None}, "junk"]}
    assert fv.normalize_holdings_fair_values(a) is a


def test_normalize_is_idempotent():
    analysis = {"holdings_analysis": [
        {"fair_value": {"estimate": 130.0, "current": 103.0, "pct_diff": 999.0}}]}
    once = fv.normalize_holdings_fair_values(analysis)["holdings_analysis"][0]["fair_value"]["pct_diff"]
    twice = fv.normalize_holdings_fair_values(analysis)["holdings_analysis"][0]["fair_value"]["pct_diff"]
    assert once == twice
