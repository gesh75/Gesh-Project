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


@pytest.mark.parametrize(
    "est,cur,expected",
    [
        (210.0, 394.0, round((210 - 394) / 394 * 100, 2)),  # TSLA-style overvalued (−46.7)
        (130.0, 103.0, round((130 - 103) / 103 * 100, 2)),  # undervalued (+26.2)
        (100.0, 100.0, 0.0),
    ],
)
def test_matches_ui_formula(est, cur, expected):
    assert fv.canonical_pct_diff({"estimate": est, "current": cur}) == expected


@pytest.mark.parametrize(
    "bad",
    [
        None,
        {},
        {"estimate": 10},
        {"current": 10},
        {"estimate": "x", "current": 5},
        {"estimate": 10, "current": 0},
        {"estimate": float("nan"), "current": 5},
        {"estimate": True, "current": 100.0},  # True is not $1 of estimate
        {"estimate": 100.0, "current": True},  # True is not $1 of price
        {"estimate": False, "current": 100.0},
    ],
)
def test_fails_safe_to_zero(bad):
    assert fv.canonical_pct_diff(bad) == 0.0


def test_bool_estimate_is_not_one_dollar_undervalued():
    # Without the bool guard, True→1.0 vs current 100 invents −99% overvalued.
    assert fv.canonical_pct_diff({"estimate": True, "current": 100.0}) == 0.0


def test_ignores_model_pct_diff():
    # Even if the model shipped a contradictory pct_diff, we recompute from est/cur.
    out = fv.canonical_pct_diff({"estimate": 210.0, "current": 394.0, "pct_diff": 88.0})
    assert out < 0  # canonical says overvalued, not the model's +88


def test_js_daFvPct_uses_same_formula():
    # Guard against the JS drifting from the Python canonical convention.
    html = Path("templates/dashboard.html").read_text()
    m = re.search(r"function daFvPct\(fv\)\{.*?\}", html, re.S)
    assert m, "daFvPct not found"
    body = m.group(0)
    compact = body.replace(" ", "")
    assert "fv.estimate-fv.current" in compact
    assert "Math.abs(fv.current)" in body
    # Mirror canonical_pct_diff: JSON true is not $1 of estimate/price.
    assert "typeoffv.estimate==='boolean'" in compact
    assert "typeoffv.current==='boolean'" in compact


# ── Codex #4: canonicalize at the WRITE boundary, not only on read ──────────────
# Codex #2 fixed the read sites feeding Kelly, but every write surface (run_ai2 →
# engine_results.json + log_analysis, /api/deep-analysis history) still persisted the
# model's own convention — so reports/history and Kelly disagreed. canonicalize_analysis
# normalizes at the boundary so persisted == what the read sites compute.


def _analysis(pct_diff):
    return {
        "portfolio_verdict": "HOLD",
        "holdings_analysis": [
            {
                "symbol": "NVDA",
                "verdict": "TRIM",
                "fair_value": {
                    "estimate": 91.8,
                    "current": 100.0,
                    "status": "OVERVALUED",
                    "pct_diff": pct_diff,
                },
            }
        ],
    }


def test_canonicalize_rewrites_model_pct_diff():
    out = fv.canonicalize_analysis(_analysis(999.0))  # (91.8-100)/100*100
    assert out["holdings_analysis"][0]["fair_value"]["pct_diff"] == -8.2


def test_canonicalize_is_immutable():
    src = _analysis(999.0)
    out = fv.canonicalize_analysis(src)
    assert src["holdings_analysis"][0]["fair_value"]["pct_diff"] == 999.0  # source untouched
    assert out is not src


def test_canonicalize_is_idempotent():
    once = fv.canonicalize_analysis(_analysis(999.0))
    assert fv.canonicalize_analysis(once) == once


def test_canonicalize_matches_read_site():
    """Persisted value must equal exactly what the Kelly read site recomputes."""
    out = fv.canonicalize_analysis(_analysis(999.0))
    got = out["holdings_analysis"][0]["fair_value"]
    assert got["pct_diff"] == fv.canonical_pct_diff(got)


@pytest.mark.parametrize(
    "bad",
    [
        None,
        [],
        "x",
        {},
        {"holdings_analysis": "nope"},
        {"holdings_analysis": [{"fair_value": None}]},
        {"holdings_analysis": [{"fair_value": {"estimate": "x", "current": None}}]},
        {"holdings_analysis": [None, 5]},
    ],
)
def test_canonicalize_never_raises_on_garbage(bad):
    fv.canonicalize_analysis(bad)  # must not raise — it sits on the write path


def test_canonicalize_missing_estimate_is_neutral_zero():
    out = fv.canonicalize_analysis(
        {"holdings_analysis": [{"fair_value": {"current": 100.0, "pct_diff": 42.0}}]}
    )
    assert out["holdings_analysis"][0]["fair_value"]["pct_diff"] == 0.0
