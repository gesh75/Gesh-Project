"""Formatting helpers for report metrics that must not invent a number."""

import math


def optional_float(value) -> float | None:
    """Parse a finite float. Missing, blank, or non-numeric values stay unknown."""
    if value is None or value == "":
        return None
    # bool is a numeric subclass: True must not become 1.0% / $1 / green VaR.
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def format_alpha_percent(value: float | None) -> str:
    """Format a benchmark percentage without inventing unavailable values."""
    if value is None:
        return "n/a"
    return f"{value:+.1f}%"


def format_optional_percent(value: float | None, *, digits: int = 1) -> str:
    """Format a percentage that may be missing. Never render a missing value as 0.0%."""
    parsed = optional_float(value)
    if parsed is None:
        return "N/A"
    return f"{parsed:.{digits}f}%"


def format_optional_usd(value: float | None) -> str:
    """Format a dollar figure that may be missing. Never render unknown as $0."""
    parsed = optional_float(value)
    if parsed is None:
        return "N/A"
    return f"${parsed:,.0f}"


def var_95_status(value: float | None) -> tuple[str, str]:
    """Color + badge for headline VaR. A missing measurement is never green OK."""
    parsed = optional_float(value)
    if parsed is None:
        return "#888", "n/a"
    if parsed > 15:
        return "#ff3c5a", "⚠️ RED >15%"
    if parsed > 10:
        return "#f0b429", "watch"
    return "#00e87a", "✓ OK"


def cvar_95_pct(monte: dict | None) -> float | None:
    """Percent CVaR from a Monte Carlo result. Never invent 0 when the tail is unknown.

    The engine historically wrote dollar ``cvar_95`` and ``var_95_pct`` but omitted
    ``cvar_95_pct``. A report that defaulted the missing key to 0 published a
    headline tail-risk of 0.0% on every daily run.
    """
    if not isinstance(monte, dict):
        return None
    explicit = optional_float(monte.get("cvar_95_pct"))
    if explicit is not None:
        return explicit
    cvar = optional_float(monte.get("cvar_95"))
    equity = optional_float(monte.get("current_equity"))
    if cvar is None or equity is None or equity <= 0:
        return None
    return round(cvar / equity * 100, 2)


def alpha_value_color(value: float | None, *, neutral: str = "#888") -> str:
    """Return the report color for a benchmark value."""
    if value is None:
        return neutral
    return "#00e87a" if value >= 0 else "#ff3c5a"
