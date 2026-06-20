"""Shim — moved to gesh-core (Phase 3.3).

Implementation now lives in ``gesh_core.tax_harvester`` (shared by the engine and
product repos). The DB path is now env-driven (``GESH_TRACKER_DB`` →
``GESH_TRADING_HOME`` → cwd) instead of ``__file__``-relative. This repo's
launchd start scripts (start_gesh_main.sh, run_daily_analysis.sh) export
``GESH_TRACKER_DB`` pointing at the engine's gesh_financial.db, preserving the
old sibling-path default.

Transparent alias preserves every import form:
    import tax_harvester
    from tax_harvester import get_harvest_summary, scan_tax_loss_candidates
"""

import sys

from gesh_core import tax_harvester as _impl

sys.modules[__name__] = _impl
