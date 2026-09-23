"""Proof-page number honesty: Wilson CI + finite coercion.

``/track-record`` is the public sales asset. A 1-for-1 close must not render
as a proven 100% win rate, an empty ledger must not invent a confidence
interval, and a SQLite bool/NaN must not become a $1 fill.
"""

from __future__ import annotations

import math

import pytest

import proof_page as pp


def test_wilson_empty_ledger_is_zero_zero_not_invented():
    assert pp._wilson_ci_95(0, 0) == (0.0, 0.0)
    assert pp._wilson_ci_95(5, 0) == (0.0, 0.0)
    assert pp._wilson_ci_95(0, -1) == (0.0, 0.0)


def test_wilson_one_win_is_not_proven_one_hundred():
    """1/1 is not (1.0, 1.0) — Wilson pulls toward 0.5 so a single close
    cannot sell '100% win rate' as a measured interval."""
    lo, hi = pp._wilson_ci_95(1, 1)
    assert 0.0 < lo < 1.0
    assert hi == 1.0
    assert (lo, hi) != (1.0, 1.0)


def test_wilson_zero_wins_is_not_proven_never():
    lo, hi = pp._wilson_ci_95(0, 10)
    assert lo == 0.0
    assert 0.0 < hi < 1.0
    assert (lo, hi) != (0.0, 0.0)


def test_wilson_even_split_is_centered():
    lo, hi = pp._wilson_ci_95(5, 10)
    assert lo < 0.5 < hi
    assert lo == pytest.approx(0.2366, abs=5e-4)
    assert hi == pytest.approx(0.7634, abs=5e-4)


def test_wilson_precision_rounds():
    lo, hi = pp._wilson_ci_95(1, 2, precision=2)
    assert lo == round(lo, 2) and hi == round(hi, 2)


@pytest.mark.parametrize(
    "value, positive, expected",
    [
        (True, False, None),  # bool is not $1
        (False, False, None),
        ("100", False, None),  # string is not coerced
        (None, False, None),
        (float("nan"), False, None),
        (float("inf"), False, None),
        (float("-inf"), False, None),
        (0.0, True, None),  # $0 is unpriced when positive=True
        (0, True, None),
        (-5.0, True, None),
        (0.0, False, 0.0),
        (-5.0, False, -5.0),
        (12.5, True, 12.5),
        (12, False, 12.0),
    ],
)
def test_finite_float_rejects_bool_nan_and_non_positive(value, positive, expected):
    got = pp._finite_float(value, positive=positive)
    if expected is None:
        assert got is None
    elif isinstance(expected, float) and math.isnan(expected):
        assert got is None
    else:
        assert got == pytest.approx(expected)
