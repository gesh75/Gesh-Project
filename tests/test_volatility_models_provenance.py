"""Provenance guards for volatility_models — a fallback must never wear the label
of the model it replaced.

Why this file exists (2026-08-07). `garch_vol()` falls back to EWMA on FOUR paths:
a short sample, `arch` not installed, a fit failure, and an out-of-range result. It
returns a bare float, so no caller can tell which happened — and `arch` is **not
installed in either project venv**, so every "GARCH" figure produced today is EWMA
wearing a GARCH label.

`compare_vol_models()` made that visible and harmless-looking at the same time:

    ewma_sig  = ewma_vol(returns)
    garch_sig = garch_vol(returns)          # silently the SAME function
    diff_bp   = (garch_sig - ewma_sig)      # therefore always exactly 0.0
    "preferred": "GARCH" if abs(diff_bp) > 20 else "EWMA"   # therefore always EWMA

A debug panel that claims to compare two models while comparing one model to itself,
reporting a `garch_daily_pct` column that is EWMA. Same failure class as the GESH
rules on fabricated provenance (#45/#51/#59): the number is not wrong, its LABEL is,
and a label that survives into a report as fact is the thing those rules exist to
prevent.

The fix is not to make GARCH work — installing `arch` is a separate decision. It is
to make the substitution *legible*, so a reader can always tell which estimator
produced a figure.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import volatility_models as vm


def _returns(n: int = 300, sigma: float = 0.02, seed: int = 0) -> pd.Series:
    rng = np.random.default_rng(seed)
    return pd.Series(rng.normal(0.0, sigma, n))


# ── the detail API: every call must say which estimator actually ran ──────────


def test_detail_reports_an_estimator_alongside_the_value():
    sigma, estimator = vm.garch_vol_detail(_returns())
    assert sigma > 0
    assert isinstance(estimator, str) and estimator


def test_detail_marks_a_short_sample_as_a_fallback_not_as_garch():
    # < 60 observations never reaches arch at all
    sigma, estimator = vm.garch_vol_detail(_returns(n=30))
    assert sigma > 0
    assert estimator.startswith("ewma:")
    assert "short" in estimator


def test_detail_marks_a_missing_arch_install_distinctly(monkeypatch):
    # the live condition in both venvs today
    monkeypatch.setitem(__import__("sys").modules, "arch", None)
    sigma, estimator = vm.garch_vol_detail(_returns())
    assert sigma > 0
    assert estimator.startswith("ewma:")


def test_detail_never_claims_garch_when_the_value_equals_ewma():
    # The invariant that actually protects a reader: if the number IS the EWMA
    # number, the label must not say GARCH.
    r = _returns()
    sigma, estimator = vm.garch_vol_detail(r)
    if sigma == pytest.approx(vm.ewma_vol(r)):
        assert estimator != "garch"


# ── the reporting surface must not fabricate a comparison ────────────────────


def test_compare_states_whether_garch_was_actually_available():
    out = vm.compare_vol_models(_returns(), "TEST")
    assert "garch_available" in out
    assert isinstance(out["garch_available"], bool)
    assert "estimator" in out


def test_compare_does_not_emit_a_garch_number_when_garch_did_not_run():
    # Reporting EWMA under a `garch_daily_pct` key is the mislabel itself. When the
    # model did not run, the honest value is None, not a copy of the EWMA figure.
    out = vm.compare_vol_models(_returns(), "TEST")
    if not out["garch_available"]:
        assert out["garch_daily_pct"] is None
        assert out["garch_annual_pct"] is None


def test_compare_does_not_report_a_meaningless_zero_difference():
    # diff_basis_pts == 0.0 was an artifact of subtracting EWMA from itself, and it
    # read as "the two models agree perfectly" — the opposite of the truth.
    out = vm.compare_vol_models(_returns(), "TEST")
    if not out["garch_available"]:
        assert out["diff_basis_pts"] is None


def test_compare_preferred_admits_when_there_was_no_choice_to_make():
    out = vm.compare_vol_models(_returns(), "TEST")
    if not out["garch_available"]:
        assert "unavailable" in str(out["preferred"]).lower()


def test_compare_still_reports_ewma_normally():
    # the fallback path must stay useful — EWMA is a real number and keeps its label
    out = vm.compare_vol_models(_returns(), "TEST")
    assert out["ewma_daily_pct"] > 0
    assert out["ewma_annual_pct"] > 0
    assert out["symbol"] == "TEST"


def test_formatter_handles_unavailable_metric():
    assert vm.format_optional_metric(None, format_spec=".1f", suffix="%") == "N/A"


def test_formatter_preserves_numeric_metric_output():
    assert vm.format_optional_metric(31.25, format_spec=".1f", suffix="%") == "31.2%"


# ── backward compatibility: existing float callers keep working ──────────────


def test_garch_vol_still_returns_a_plain_float_for_existing_callers():
    v = vm.garch_vol(_returns())
    assert isinstance(v, float) and v > 0


def test_garch_vol_agrees_with_the_detail_form():
    r = _returns()
    assert vm.garch_vol(r) == pytest.approx(vm.garch_vol_detail(r)[0])


def test_garch_portfolio_vol_is_none_when_no_name_actually_fit():
    # Short series never reaches arch; must not average EWMA and call it GARCH.
    rets = pd.DataFrame({"AAA": _returns(n=30), "BBB": _returns(n=30, seed=1)})
    holdings = [
        {"symbol": "AAA", "equity": 50.0},
        {"symbol": "BBB", "equity": 50.0},
    ]
    assert vm.garch_portfolio_vol(holdings, rets, 100.0) is None


def test_garch_portfolio_vol_is_none_if_any_name_is_ewma_fallback(monkeypatch):
    # Incomplete coverage must not be reweighted and labeled GARCH(1,1).
    rets = pd.DataFrame({"AAA": _returns(n=300, seed=0), "BBB": _returns(n=300, seed=1)})
    holdings = [
        {"symbol": "AAA", "equity": 80.0},
        {"symbol": "BBB", "equity": 20.0},
    ]
    n = {"i": 0}

    def _detail(series, scale=100.0):
        n["i"] += 1
        if n["i"] == 2:
            return 0.05, "ewma:fit_failed"
        return 0.02, vm.GARCH_ESTIMATOR

    monkeypatch.setattr(vm, "garch_vol_detail", _detail)
    assert vm.garch_portfolio_vol(holdings, rets, 100.0) is None


def test_garch_portfolio_vol_uses_weighted_garch_when_every_name_fits(monkeypatch):
    rets = pd.DataFrame({"AAA": _returns(n=300, seed=0), "BBB": _returns(n=300, seed=1)})
    holdings = [
        {"symbol": "AAA", "equity": 80.0},
        {"symbol": "BBB", "equity": 20.0},
    ]
    n = {"i": 0}

    def _detail(series, scale=100.0):
        n["i"] += 1
        return (0.02 if n["i"] == 1 else 0.05), vm.GARCH_ESTIMATOR

    monkeypatch.setattr(vm, "garch_vol_detail", _detail)
    assert vm.garch_portfolio_vol(holdings, rets, 100.0) == pytest.approx(0.8 * 0.02 + 0.2 * 0.05)
