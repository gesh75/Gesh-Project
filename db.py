"""Shim — moved to gesh-core (Phase 3.6).

Implementation now lives in ``gesh_core.db`` (shared by the engine and product
repos). Canonical = the UNION of both: engine's ``connect()`` WAL helper +
trade_log/paper_trades DDL + env-overridable DB_PATH, plus this repo's
``_safe_json`` corruption-tolerant ``get_analysis_by_id`` and
``get_portfolio_equity_history``. DB path is cwd-relative (or GESH_DB_PATH), so
this repo keeps its OWN gesh_financial.db (rule #44).

Transparent alias preserves every import form AND monkeypatching of DB_PATH:
    import db as dbmod
    from db import init_db, log_analysis, get_portfolio_equity_history
    monkeypatch.setattr(dbmod, "DB_PATH", tmp)   # still works
"""

import sys

from gesh_core import db as _impl

sys.modules[__name__] = _impl
