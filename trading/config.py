"""Shim — moved to gesh-core (Phase 3.4).

Implementation now lives in ``gesh_core.trading.config`` (shared by the engine and
product repos). Canonical = engine's superset RiskConfig (this repo GAINS the
extra safety-gate fields: max_sector_pct, earnings_proximity_hours,
max_consecutive_loss_days, min_confidence_risk_off) UNION product's
empty-string-robust env parsing. Paper-mode defaults unchanged.

Transparent alias preserves every import form:
    from trading.config import RiskConfig, load_risk_config
    from .config import RiskConfig            # inside trading/
"""

import sys

from gesh_core.trading import config as _impl

sys.modules[__name__] = _impl
