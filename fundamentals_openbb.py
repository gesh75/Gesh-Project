"""
OpenBB fundamentals wrapper for Nodes 4 & 5 in consensus.py.

Replaces the unreliable yfinance .info crumb approach with OpenBB's
structured endpoints (yfinance provider — no API key needed).

Fallback: if OpenBB fails for any reason, returns None so the caller
can fall back to the original _ticker_info_with_retry() path.
"""
from __future__ import annotations

import logging
import time
from typing import TypedDict

logger = logging.getLogger(__name__)

_OPENBB_AVAILABLE = False
obb = None  # type: ignore[assignment]
try:
    from openbb import obb  # noqa: F401  — kept at module level so tests can patch it
    _OPENBB_AVAILABLE = True
except ImportError:
    logger.warning("openbb not installed — fundamentals will use yfinance fallback")


class FundamentalsData(TypedDict, total=False):
    """Typed dict returned by get_fundamentals_openbb()."""
    # Node 4 — fundamentals
    rev_growth: float | None
    pe: float | None
    profit_margin: float | None
    eps_growth: float | None
    # Node 5 — analyst
    rec_key: str | None
    target_mean_price: float | None
    current_price: float | None
    num_analysts: int | None
    # source tag
    source: str


def get_fundamentals_openbb(symbol: str) -> FundamentalsData | None:
    """
    Fetch fundamentals via OpenBB (yfinance provider).

    Returns FundamentalsData dict on success, None on failure.
    Caller should fall back to _ticker_info_with_retry() on None.
    """
    if not _OPENBB_AVAILABLE or obb is None:
        return None

    data: FundamentalsData = {"source": "openbb"}

    try:
        metrics = obb.equity.fundamental.metrics(symbol, provider="yfinance")
        df = metrics.to_dataframe()
        if df.empty:
            return None
        row = df.iloc[0]

        data["rev_growth"] = _safe_float(row.get("revenue_growth"))
        data["pe"] = _safe_float(row.get("forward_pe")) or _safe_float(row.get("pe_ratio"))
        data["profit_margin"] = _safe_float(row.get("profit_margin"))
        data["eps_growth"] = _safe_float(row.get("earnings_growth"))
        data["current_price"] = _safe_float(row.get("current_price") if "current_price" in row else None)
    except Exception as exc:
        logger.debug("openbb metrics failed for %s: %s", symbol, exc)
        return None

    try:
        # Small stagger to avoid crumb collision with metrics call
        time.sleep(0.3)
        consensus = obb.equity.estimates.consensus(symbol, provider="yfinance")
        df_c = consensus.to_dataframe()
        if not df_c.empty:
            row_c = df_c.iloc[0]
            data["rec_key"] = str(row_c.get("recommendation") or "").lower()
            data["target_mean_price"] = _safe_float(row_c.get("target_consensus"))
            if data.get("current_price") is None:
                data["current_price"] = _safe_float(row_c.get("current_price"))
            data["num_analysts"] = _safe_int(row_c.get("number_of_analysts"))
    except Exception as exc:
        logger.debug("openbb consensus failed for %s: %s", symbol, exc)
        # Analyst data is optional — don't abort fundamentals

    return data


def _safe_float(val: object) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
        return None if (f != f) else f  # NaN guard
    except (TypeError, ValueError):
        return None


def _safe_int(val: object) -> int | None:
    f = _safe_float(val)
    return None if f is None else int(f)
