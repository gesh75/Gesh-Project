"""Regression coverage for unavailable alpha / risk-report values."""

import pytest

from alpha_reporting import (
    alpha_value_color,
    cvar_95_pct,
    format_alpha_percent,
    format_optional_percent,
    format_optional_usd,
    optional_float,
    var_95_status,
)


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


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, "N/A"), (4.62, "4.6%"), (0.0, "0.0%"), ("x", "N/A"), (float("nan"), "N/A")],
)
def test_format_optional_percent_never_invents_zero_for_missing(value, expected):
    assert format_optional_percent(value) == expected


def test_format_optional_percent_can_render_whole_numbers():
    assert format_optional_percent(61.2, digits=0) == "61%"
    assert format_optional_percent(None, digits=0) == "N/A"


def test_format_optional_usd_never_invents_zero_for_missing():
    assert format_optional_usd(None) == "N/A"
    assert format_optional_usd(1234.56) == "$1,235"
    assert format_optional_usd(float("inf")) == "N/A"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        ("x", None),
        (float("nan"), None),
        (4.62, 4.62),
        (0.0, 0.0),
    ],
)
def test_optional_float_rejects_non_finite_and_keeps_real_zero(value, expected):
    assert optional_float(value) == expected


def test_var_95_status_missing_is_never_green_ok():
    assert var_95_status(None) == ("#888", "n/a")
    assert var_95_status(4.2) == ("#00e87a", "✓ OK")
    assert var_95_status(12.0)[0] == "#f0b429"
    assert var_95_status(16.0) == ("#ff3c5a", "⚠️ RED >15%")


def test_cvar_95_pct_uses_explicit_percent_when_present():
    assert cvar_95_pct({"cvar_95_pct": 4.62, "cvar_95": 99, "current_equity": 1000}) == 4.62


def test_cvar_95_pct_derives_from_dollar_cvar_when_percent_key_missing():
    # The engine used to emit only dollar cvar_95. Deriving keeps the report
    # honest instead of defaulting a missing key to headline 0.0%.
    assert cvar_95_pct({"cvar_95": 1608.63, "current_equity": 34799.63}) == pytest.approx(4.62)


def test_cvar_95_pct_returns_none_when_tail_cannot_be_measured():
    assert cvar_95_pct({}) is None
    assert cvar_95_pct({"cvar_95": 100, "current_equity": 0}) is None
    assert cvar_95_pct(None) is None
    assert cvar_95_pct({"cvar_95_pct": float("nan")}) is None


def test_cvar_95_pct_falls_through_from_invalid_percent_to_dollar_ratio():
    assert cvar_95_pct(
        {"cvar_95_pct": "n/a", "cvar_95": 200, "current_equity": 1000}
    ) == pytest.approx(20.0)
