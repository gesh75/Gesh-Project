"""Cross-source price sanity check.

A whole-series feed error (observed: NFLX priced ~$73.83, ≈10× low) can't be caught
by the intraday median bad-tick guard in server._latest_close — every recent close is
wrong, so the median is wrong too. But the 52-week range comes from a DIFFERENT feed
field (Robinhood fundamentals, primary), so a price implausibly far outside that band
is a strong signal the price feed is broken for that symbol.

This only FLAGS (never suppresses) — a book must still total, and a genuine gap
shouldn't vanish. Thresholds are deliberately wide (½× low / 2× high) so only an
egregious error fires, never normal volatility or a fresh 52-week low.
"""
from __future__ import annotations

import math
import os


def strict_reject_enabled() -> bool:
    """True when GESH_REJECT_SUSPECT_PRICES opts into STRICT price quality.

    Default (unset) keeps the advisory behavior this module was built for: flag the
    quote, let the decision through, let the user judge. In strict mode a suspect quote
    instead BLOCKS the decision paths that would be built on it (AI card, trade setup),
    so a broken feed can't silently ground a recommendation — and the LLM spend is
    skipped entirely because the check runs before it.
    """
    return os.getenv("GESH_REJECT_SUSPECT_PRICES", "").strip().lower() in (
        "1", "true", "yes", "on")


def _positive(x) -> float | None:
    """A usable dollar figure. Bool / NaN / inf / overflow stay unknown.

    JSON ``true`` must not become $1 via ``float(True)`` — that $1 can then
    fail the 52-wk band and block an AI card or trade setup.
    """
    if isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError, OverflowError):
        return None
    return v if math.isfinite(v) and v > 0 else None


def is_price_suspect(price, low_52, high_52) -> tuple[bool, str]:
    """(suspect, reason). Fail-open: missing/zero/non-finite inputs → (False, "")."""
    p = _positive(price)
    if p is None:
        return False, ""
    lo, hi = _positive(low_52), _positive(high_52)
    if lo is not None and p < lo * 0.5:
        return True, (f"${p:,.2f} is far below the 52-wk low ${lo:,.2f} "
                      f"— likely a price-feed error, not a real quote")
    if hi is not None and p > hi * 2.0:
        return True, (f"${p:,.2f} is far above the 52-wk high ${hi:,.2f} "
                      f"— likely a price-feed error, not a real quote")
    return False, ""
