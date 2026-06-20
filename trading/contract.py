"""Shim — moved to gesh-core (Phase 3.2).

Implementation now lives in ``gesh_core.trading.contract`` (shared by the engine
and product repos). The engine version is canonical: it carries the ``manual(
source=...)`` provenance parameter (2026-06-14 fix) and the ``len(symbol) > 12``
validation cap. Product callers that call ``manual(...)`` without ``source`` get
the historical default ``"manual"`` unchanged.

Transparent alias preserves every import form:
    from trading import contract
    from trading.contract import TradeIntent, manual, from_consensus
    from .contract import TradeIntent            # inside trading/
"""

import sys

from gesh_core.trading import contract as _impl

sys.modules[__name__] = _impl
