"""M-14: detect when Kelly is uninformative (equal weight published as an optimum)."""
from __future__ import annotations

import math

import pytest

import kelly_quality as kq


def _book(values, action="HOLD"):
    return {f"S{i}": {"optimal_pct": v, "action": action} for i, v in enumerate(values)}


def test_equal_weight_book_is_degenerate():
    q = kq.kelly_degeneracy(_book([4.9] * 18 + [3.5, 3.5, 3.3, 1.2, 0.0, 0.0]))
    assert q["degenerate"] is True
    assert q["modal_pct"] == pytest.approx(4.9)
    assert q["pinned"] == 18
    assert q["sized"] == 22


def test_varied_book_is_not_degenerate():
    q = kq.kelly_degeneracy(_book([1.1, 2.4, 3.7, 4.9, 6.2, 7.5, 2.9, 0.0]))
    assert q["degenerate"] is False


def test_tiny_book_is_never_flagged():
    """3 names identical is a coincidence, not evidence."""
    assert kq.kelly_degeneracy(_book([5.0, 5.0, 5.0]))["degenerate"] is False


@pytest.mark.parametrize("junk", [None, [], "x", 5, {}, {"A": "oops"}, {"A": {"optimal_pct": None}}])
def test_junk_input_is_not_degenerate_and_never_raises(junk):
    q = kq.kelly_degeneracy(junk)
    assert q["degenerate"] is False
    assert q["sized"] == 0


def test_non_finite_and_bool_values_are_ignored():
    book = _book([4.9] * 6) | {"X": {"optimal_pct": math.nan}, "Y": {"optimal_pct": True}}
    q = kq.kelly_degeneracy(book)
    assert q["sized"] == 6 and q["degenerate"] is True


def test_values_within_rounding_tolerance_cluster_together():
    q = kq.kelly_degeneracy(_book([4.91, 4.93, 4.9, 4.92, 4.94, 4.9, 7.0, 1.0]))
    assert q["degenerate"] is True


def test_error_envelope_is_ignored():
    assert kq.kelly_degeneracy({"error": "kelly unavailable"})["degenerate"] is False


def test_server_envelope_is_unwrapped():
    env = {"status": "ok", "data": _book([4.9] * 8)}
    assert kq.kelly_degeneracy(env)["degenerate"] is True
