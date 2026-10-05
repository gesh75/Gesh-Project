"""Sprint 3 (items 11-13): net-of-cost, minimum-sample and trial-disclosure maths."""
from __future__ import annotations

import math
from datetime import date

import pytest

import proof_stats as ps


# ── net of cost ───────────────────────────────────────────────────────────────
def test_net_of_cost_subtracts_bp_on_gross_notional():
    assert ps.net_of_cost(53.28, 10_000.0, 20) == pytest.approx(53.28 - 20.0)


def test_net_of_cost_can_flip_sign_and_is_not_floored():
    assert ps.net_of_cost(5.0, 10_000.0, 50) == pytest.approx(-45.0)


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), True, "x"])
def test_net_of_cost_unknown_inputs_return_none(bad):
    assert ps.net_of_cost(bad, 1000.0, 20) is None
    assert ps.net_of_cost(10.0, bad, 20) is None


# ── minimum-sample panel ──────────────────────────────────────────────────────
def _returns(mean: float, sd: float, n: int) -> list[float]:
    """Deterministic sample with an EXACT sample mean and (approximately) sd."""
    half = [sd if i % 2 == 0 else -sd for i in range(n)]
    shift = mean - sum(half) / n
    return [h + shift for h in half]


def test_positive_edge_reports_trades_needed_and_eta():
    r = _returns(0.011, 0.09, 55)
    p = ps.min_sample_panel(r, closes_per_day=55 / 136, today=date(2026, 10, 4))
    assert p["status"] == "ok"
    assert p["n"] == 55
    assert p["mean"] == pytest.approx(0.011, abs=1e-9)
    # n_sig = (1.96*sd/mean)^2 ~ (1.96*0.0908/0.011)^2 ~ 262
    assert 240 <= p["n_for_significance"] <= 290
    assert p["n_for_80pct_power"] > p["n_for_significance"]
    assert p["additional_closes"] == p["n_for_significance"] - 55
    assert p["eta_date"] is not None and p["eta_date"] > "2026-10-04"
    assert p["t_stat"] < 1.96  # not significant today
    lo, hi = p["mean_ci95"]
    assert lo < 0.011 < hi


def test_eta_matches_rate_arithmetic():
    r = _returns(0.01, 0.09, 40)
    p = ps.min_sample_panel(r, closes_per_day=0.5, today=date(2026, 1, 1))
    assert p["additional_closes"] > 0
    expected_days = math.ceil(p["additional_closes"] / 0.5)
    assert p["eta_days"] == expected_days


def test_significant_sample_needs_no_more_closes():
    r = _returns(0.05, 0.02, 40)
    p = ps.min_sample_panel(r, closes_per_day=0.3, today=date(2026, 1, 1))
    assert p["t_stat"] > 1.96
    assert p["additional_closes"] == 0
    assert p["eta_date"] is None


def test_non_positive_edge_has_no_required_sample():
    p = ps.min_sample_panel(_returns(-0.004, 0.05, 30), closes_per_day=0.2, today=date(2026, 1, 1))
    assert p["status"] == "no_positive_edge"
    assert p["n_for_significance"] is None and p["eta_date"] is None


def test_too_few_closes_is_not_computed():
    p = ps.min_sample_panel([0.01], closes_per_day=0.2, today=date(2026, 1, 1))
    assert p["status"] == "insufficient_sample"
    assert p["mean"] is None


def test_zero_variance_is_insufficient_not_infinite_t():
    p = ps.min_sample_panel([0.01] * 10, closes_per_day=0.2, today=date(2026, 1, 1))
    assert p["status"] == "insufficient_sample"
    assert p["t_stat"] is None


def test_tiny_edge_is_capped_instead_of_a_silly_eta():
    p = ps.min_sample_panel(_returns(0.00001, 0.1, 50), closes_per_day=0.2, today=date(2026, 1, 1))
    assert p["status"] == "ok"
    assert p["eta_date"] is None and p["eta_beyond_horizon"] is True


def test_unknown_close_rate_gives_counts_but_no_date():
    p = ps.min_sample_panel(_returns(0.011, 0.09, 55), closes_per_day=None, today=date(2026, 1, 1))
    assert p["status"] == "ok" and p["n_for_significance"] > 55
    assert p["eta_date"] is None and p["eta_beyond_horizon"] is False


@pytest.mark.parametrize("junk", [None, "x", 5, [None, "a", True, float("nan")]])
def test_junk_returns_never_raise(junk):
    p = ps.min_sample_panel(junk, closes_per_day=0.2, today=date(2026, 1, 1))
    assert p["status"] == "insufficient_sample"


def test_t_critical_is_wider_than_normal_for_small_samples():
    assert ps.t_critical_95(5) > ps.t_critical_95(50) > 1.96


# ── trial disclosure ──────────────────────────────────────────────────────────
def test_trial_disclosure_reads_raw_count_from_registry(tmp_path):
    reg = tmp_path / "strategy_variant_registry.json"
    reg.write_text('{"n_trials_raw": 240}')
    assert ps.declared_raw_trials(reg) == 240


@pytest.mark.parametrize("content", ['{"n_trials_raw": 0}', '{"n_trials_raw": true}',
                                     '{"n_trials_raw": "240"}', "not json", "[]", "{}"])
def test_trial_disclosure_rejects_bad_registry(tmp_path, content):
    reg = tmp_path / "r.json"
    reg.write_text(content)
    assert ps.declared_raw_trials(reg) is None


def test_trial_disclosure_missing_file_is_none(tmp_path):
    assert ps.declared_raw_trials(tmp_path / "nope.json") is None
