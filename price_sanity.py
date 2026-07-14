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


def _positive(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
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
