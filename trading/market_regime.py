"""Stamp the SPY-vs-200d market regime on a portfolio snapshot, once per run.

``trading.risk_gate.evaluate`` reads ``snapshot["market_regime"]["spy_above_ma"]``
before falling back to its own SPY fetch. Snapshot builders (the autopilot
jobs and ``run_paper_gate2``) call :func:`stamp_market_regime` right after
they build the payload, so in normal operation the gate is network-free and
every intent in the run is judged against the same regime reading.

Best-effort by design: a failed or unknown probe leaves the key absent, and
the gate's existing fail-open fallback (rule #40) still applies. A caller that
already stamped a real bool is never overwritten.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .config import RiskConfig
from .risk_gate import _spy_above_ma

_DEFAULT_PROBE: Callable[[int], bool | None] = _spy_above_ma
SOURCE = "spy_sma_probe"


def stamp_market_regime(
    snapshot: dict[str, Any],
    cfg: RiskConfig,
    *,
    probe: Callable[[int], bool | None] | None = None,
) -> dict[str, Any]:
    """Set ``snapshot["market_regime"]["spy_above_ma"]`` when it can be known.

    ``probe(ma_days)`` returns True/False, or None when unknown. Defaults to the
    gate's cached SPY helper. Never raises; returns ``snapshot`` for chaining.
    """
    if not isinstance(snapshot, dict):
        return snapshot
    if not getattr(cfg, "market_regime_gate", False):
        return snapshot
    existing = snapshot.get("market_regime")
    if isinstance(existing, dict) and isinstance(existing.get("spy_above_ma"), bool):
        return snapshot
    ma_days = int(getattr(cfg, "market_regime_ma_days", 200) or 200)
    fn = probe if probe is not None else _DEFAULT_PROBE
    try:
        above = fn(ma_days)
    except Exception:  # noqa: BLE001 — stamping is best-effort; the gate falls back
        return snapshot
    if not isinstance(above, bool):
        return snapshot
    regime = dict(existing) if isinstance(existing, dict) else {}
    regime.update({"spy_above_ma": above, "ma_days": ma_days, "source": SOURCE})
    snapshot["market_regime"] = regime
    return snapshot
