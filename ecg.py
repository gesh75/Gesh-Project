"""Shim — moved to gesh-core (Phase 3.2).

Implementation now lives in ``gesh_core.ecg`` (shared by the engine and product
repos). The product version is canonical: ``compute_ecg`` derives loss streaks
from consecutive equity CHANGES (not the percent_change-vs-cost column, which
falsely SUSPENDed every underwater holding) and names the condition that fired
in its messages.

Transparent alias preserves every import form:
    import ecg
    from ecg import compute_ecg, _classify, _loss_streak, _compute_drawdown_from_peak
"""

import sys

from gesh_core import ecg as _impl

sys.modules[__name__] = _impl
