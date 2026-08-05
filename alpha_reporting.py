"""Formatting helpers for alpha benchmark output."""


def format_alpha_percent(value: float | None) -> str:
    """Format a benchmark percentage without inventing unavailable values."""
    if value is None:
        return "n/a"
    return f"{value:+.1f}%"


def alpha_value_color(value: float | None, *, neutral: str = "#888") -> str:
    """Return the report color for a benchmark value."""
    if value is None:
        return neutral
    return "#00e87a" if value >= 0 else "#ff3c5a"
