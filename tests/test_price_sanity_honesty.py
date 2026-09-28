"""Bool / overflow honesty for the 52-wk price-sanity gate.

``float(True)`` is 1.0. A JSON ``true`` in a last mark or 52-wk bound used to
become $1 and then fail the ½× / 2× band, blocking an AI card or trade setup
in strict mode. Fail-open: unknown inputs never flag.
"""

from __future__ import annotations

import pytest

import price_sanity as ps


def test_bool_price_is_not_one_dollar_suspect():
    suspect, reason = ps.is_price_suspect(True, low_52=500.0, high_52=1200.0)
    assert suspect is False
    assert reason == ""


def test_bool_false_price_is_not_zero_then_suspect():
    suspect, reason = ps.is_price_suspect(False, low_52=500.0, high_52=1200.0)
    assert suspect is False
    assert reason == ""


def test_bool_52w_high_does_not_flag_a_real_quote():
    """True as high_52 used to be $1, so a $50 print looked 50× the band."""
    suspect, reason = ps.is_price_suspect(50.0, low_52=40.0, high_52=True)
    assert suspect is False
    assert reason == ""


def test_bool_52w_low_does_not_flag_a_real_quote():
    suspect, reason = ps.is_price_suspect(50.0, low_52=True, high_52=1200.0)
    assert suspect is False
    assert reason == ""


@pytest.mark.parametrize(
    "price,lo,hi",
    [
        (float("inf"), 100, 200),
        (float("-inf"), 100, 200),
        (float("nan"), 100, 200),
        (50, float("inf"), 200),
        (50, 100, float("inf")),
    ],
)
def test_non_finite_inputs_fail_open(price, lo, hi):
    assert ps.is_price_suspect(price, lo, hi)[0] is False


def test_positive_helper_rejects_bool_and_overflow():
    # Keep the overflow integer out of parametrize ids (int→str hits the
    # 4300-digit default limit and fails collection).
    huge = 10**10000
    assert ps._positive(True) is None
    assert ps._positive(False) is None
    assert ps._positive(huge) is None
    assert ps.is_price_suspect(huge, 100, 200)[0] is False
    assert ps._positive(73.83) == pytest.approx(73.83)
    assert ps._positive(1) == 1.0
