"""Formatting helpers for report metrics that must not invent a number."""


def format_alpha_percent(value: float | None) -> str:
    """Format a benchmark percentage without inventing unavailable values."""
    if value is None:
        return "n/a"
    return f"{value:+.1f}%"


def format_optional_percent(value: float | None) -> str:
    """Format a percentage that may be missing. Never render a missing value as 0.0%."""
    if value is None:
        return "N/A"
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return "N/A"


def cvar_95_pct(monte: dict | None) -> float | None:
    """Percent CVaR from a Monte Carlo result. Never invent 0 when the tail is unknown.

    The engine historically wrote dollar ``cvar_95`` and ``var_95_pct`` but omitted
    ``cvar_95_pct``. A report that defaulted the missing key to 0 published a
    headline tail-risk of 0.0% on every daily run.
    """
    if not isinstance(monte, dict):
        return None
    raw = monte.get("cvar_95_pct")
    if raw is not None:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    try:
        cvar = float(monte.get("cvar_95"))
        equity = float(monte.get("current_equity"))
    except (TypeError, ValueError):
        return None
    if equity <= 0:
        return None
    return round(cvar / equity * 100, 2)


def alpha_value_color(value: float | None, *, neutral: str = "#888") -> str:
    """Return the report color for a benchmark value."""
    if value is None:
        return neutral
    return "#00e87a" if value >= 0 else "#ff3c5a"
