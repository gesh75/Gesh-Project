"""Shim — moved to gesh-core (Phase 3.2).

Implementation now lives in ``gesh_core.kelly`` (shared by the engine and product
repos). Canonical = the engine's rule-#43-hardened fetch (Yahoo 2y + Robinhood
fallback) UNION product's injectable ``returns=`` parameter on
``kelly_size_holding`` (lets product's unit tests run network-free).

``import price_source`` inside ``_fetch_returns`` still resolves against the
running repo's own price_source. Transparent alias preserves every import form:
    import kelly
    from kelly import run_kelly_analysis, kelly_size_holding
"""

import sys

from gesh_core import kelly as _impl

sys.modules[__name__] = _impl
