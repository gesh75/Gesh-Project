"""Regression coverage for unavailable alpha benchmark values."""

import pytest

from alpha_reporting import alpha_value_color, format_alpha_percent


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, "n/a"), (0.0, "+0.0%"), (1.25, "+1.2%"), (-1.25, "-1.2%")],
)
def test_format_alpha_percent_value_formats_without_inventing_data(value, expected):
    assert format_alpha_percent(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, "#888"), (0.0, "#00e87a"), (1.0, "#00e87a"), (-1.0, "#ff3c5a")],
)
def test_alpha_value_color_value_uses_neutral_for_unavailable(value, expected):
    assert alpha_value_color(value) == expected
