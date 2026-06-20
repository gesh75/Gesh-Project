"""Shim — moved to gesh-core (Phase 3.4).

Implementation now lives in ``gesh_core.trading.state`` (shared by the engine and
product repos). Canonical = the UNION of both:
  - engine's WAL/busy_timeout pragmas, job_markers table + get_marker/set_marker
    (rule #38), and the count_orders_today(approved_only=) superset signature;
  - this repo's _resolve_db VirtioFS /tmp fallback and the two-book
    paper_realized_pnl_usd_today (paper_outcomes + trade_log paper_trades).

DB path is cwd-relative (or GESH_DB_PATH), so this repo keeps its OWN
gesh_financial.db — the separate paper books stay separate (rule #44).

Transparent alias preserves every import form AND monkeypatching of DB_PATH.
"""

import sys

from gesh_core.trading import state as _impl

sys.modules[__name__] = _impl
