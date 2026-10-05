"""Statistical-honesty helpers for the public track-record page (audit Sprint 3).

Pure, stdlib-only and total: junk in gives an explicit "unknown" status, never an
exception and never an invented number.

* ``net_of_cost`` - realized P&L less a modeled execution-cost stress.
* ``min_sample_panel`` - how many closes the *observed* per-trade edge would need
  to become statistically distinguishable from zero, and when that could happen at
  the current close rate.
* ``declared_raw_trials`` - the strategy-variant registry's declared trial count,
  disclosed next to the Deflated Sharpe (which is only meaningful with N shown).
"""
from __future__ import annotations

import json
import math
import os
from datetime import date, timedelta
from typing import Any

Z_95 = 1.959964
Z_POWER_80 = 0.841621
#: Beyond this many calendar days an ETA is reported as "beyond horizon", not a date.
ETA_HORIZON_DAYS = 3650


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    f = float(value)
    return f if math.isfinite(f) else None


def net_of_cost(realized_usd: Any, gross_notional_usd: Any, bp: float) -> float | None:
    """Realized P&L minus ``bp`` basis points of gross entry notional (a stress)."""
    pnl, notional = _finite(realized_usd), _finite(gross_notional_usd)
    if pnl is None or notional is None:
        return None
    return pnl - abs(notional) * bp / 10_000.0


def t_critical_95(df: int) -> float:
    """Two-sided 95% Student-t critical value (Cornish-Fisher style approximation)."""
    df = max(int(df), 1)
    z = Z_95
    return z + (z**3 + z) / (4.0 * df) + (5 * z**5 + 16 * z**3 + 3 * z) / (96.0 * df**2)


def _clean_returns(returns: Any) -> list[float]:
    if not isinstance(returns, (list, tuple)):
        return []
    return [f for f in (_finite(x) for x in returns) if f is not None]


def _empty(status: str, n: int) -> dict[str, Any]:
    return {
        "status": status, "n": n, "mean": None, "sd": None, "t_stat": None,
        "mean_ci95": None, "n_for_significance": None, "n_for_80pct_power": None,
        "additional_closes": None, "closes_per_day": None, "eta_days": None,
        "eta_date": None, "eta_beyond_horizon": False,
    }


def min_sample_panel(
    returns: Any,
    *,
    closes_per_day: Any,
    today: date,
) -> dict[str, Any]:
    """Minimum-sample projection from per-trade *returns* (fractions, not dollars).

    ``n_for_significance`` is the sample size at which the observed mean/sd would give
    |t| >= 1.96 *if the observed edge were the true edge*; ``n_for_80pct_power`` is the
    size that would detect it with 80% power. Both are projections from a noisy
    estimate, not forecasts.
    """
    r = _clean_returns(returns)
    n = len(r)
    if n < 2:
        return _empty("insufficient_sample", n)
    mean = sum(r) / n
    var = sum((x - mean) ** 2 for x in r) / (n - 1)
    sd = math.sqrt(var) if var > 0 else 0.0
    if sd <= 0:
        # Zero spread is an insufficient sample, not an infinite t-statistic.
        return _empty("insufficient_sample", n)
    se = sd / math.sqrt(n)
    t_stat = mean / se
    crit = t_critical_95(n - 1)
    out = _empty("ok", n)
    out.update(mean=mean, sd=sd, t_stat=t_stat, mean_ci95=(mean - crit * se, mean + crit * se))
    if mean <= 0:
        out["status"] = "no_positive_edge"
        return out

    n_sig = math.ceil((Z_95 * sd / mean) ** 2)
    n_pow = math.ceil(((Z_95 + Z_POWER_80) * sd / mean) ** 2)
    n_sig, n_pow = max(n_sig, n), max(n_pow, n_sig)
    additional = max(0, n_sig - n)
    out.update(n_for_significance=n_sig, n_for_80pct_power=n_pow, additional_closes=additional)

    rate = _finite(closes_per_day)
    out["closes_per_day"] = rate if rate and rate > 0 else None
    if additional == 0 or not out["closes_per_day"]:
        return out
    days = math.ceil(additional / out["closes_per_day"])
    out["eta_days"] = days
    if days > ETA_HORIZON_DAYS:
        out["eta_beyond_horizon"] = True
    else:
        out["eta_date"] = (today + timedelta(days=days)).isoformat()
    return out


def declared_raw_trials(registry_path: str | os.PathLike | None) -> int | None:
    """``n_trials_raw`` from the strategy-variant registry, or None if unreadable."""
    if registry_path is None:
        return None
    try:
        with open(registry_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    val = data.get("n_trials_raw")
    if isinstance(val, bool) or not isinstance(val, int) or val <= 0:
        return None
    return val
