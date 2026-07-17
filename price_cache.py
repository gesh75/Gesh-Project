"""Shared close-price cache — extracted from server.py (debt item, 2026-07-17).

Owns the latest-daily-close lookup (`_latest_close_meta` / `_latest_close`) and
the 5-minute per-ticker TTL cache in front of it (`_cached_close`), so that cron
consumers (run_weekly_digest) can value a customer book WITHOUT importing the
FastAPI app — an unrelated server import-time failure used to silently zero
customers' book values in the digest.

server.py re-exports these names and keeps thin wrappers so its historical
monkeypatch seams (server._latest_close, server._latest_close_meta,
server._cached_close) still steer behavior exactly as before the extraction.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable

# Valuing a book re-reads every symbol, so repeat views must not re-fetch
# upstream. Same 5-minute TTL the slice had inside server's per-ticker cache.
CLOSE_TTL_SEC = 300
_close_cache: dict[str, tuple[float, float]] = {}   # sym -> (monotonic_ts, price)
_close_lock = threading.Lock()


def has_cached_close(sym: str) -> bool:
    """True when `sym` has an unexpired close in the cache (no fetch)."""
    with _close_lock:
        hit = _close_cache.get(sym)
    return bool(hit and (time.monotonic() - hit[0]) < CLOSE_TTL_SEC)


def clear() -> None:
    """Drop every cached close (test isolation between TestClient runs)."""
    with _close_lock:
        _close_cache.clear()


def _latest_close_meta(sym: str) -> tuple[float | None, dict]:
    """`_latest_close` plus the PROVENANCE of the number it returns (Codex #6).

    Returns (price, {"source": str|None, "substituted": bool}).
      * source      — the feed that actually served the bars ("yahoo" | "robinhood" |
                      "yahoo+robinhood"), read off the frame. None = unattributable;
                      callers must surface that as unknown, never default it to a feed.
      * substituted — True when the bad-tick guard below returned something OTHER than
                      the latest close (a prior in-band close, or the 20-bar median).
                      That substitution was previously SILENT and indistinguishable
                      from a real quote, which is its own honesty gap.
    """
    meta: dict = {"source": None, "substituted": False}
    try:
        import price_source
        raw = price_source.yf_download(sym, period="1mo", interval="1d",
                                       auto_adjust=True, progress=False)
        if raw is None or getattr(raw, "empty", True):
            return None, meta
        # Read the source BEFORE the frame is discarded by the .squeeze() below.
        meta["source"] = price_source.frame_source(raw)
        close_obj = raw["Close"]
        # Contamination guard: a single-symbol request must never be priced off a
        # multi-ticker frame by flattening it — position [-1] would be whichever
        # ticker sorts last, not `sym` (the audited NVDA-priced-as-TSLA book).
        # Select this symbol's column or refuse.
        if getattr(close_obj, "ndim", 1) > 1:
            cols = list(getattr(close_obj, "columns", []))
            if sym in cols:
                close_obj = close_obj[sym]
            elif len(cols) == 1 and (not isinstance(cols[0], str)
                                     or cols[0].upper() in (sym.upper(), "CLOSE")):
                close_obj = close_obj[cols[0]]   # unlabeled/neutral single column
            else:
                return None, meta               # labeled with a DIFFERENT ticker
        closes = close_obj.squeeze().dropna()
        if len(closes) == 0:
            return None, meta
        import numpy as np
        arr = np.array(closes).flatten().astype(float)
        last = round(float(arr[-1]), 2)
        # Bad-tick guard: a single split-mismatched / partial quote (the observed
        # NVDA $17.16 flash against a ~$205 book) poisons valuation, weights, and
        # Kelly. If the latest close is wildly off the recent median (a real move
        # stays inside 0.5×–2×), fall back to the most recent in-band close, or the
        # median if none qualifies — never persist an obvious outlier.
        if len(arr) >= 5:
            med = float(np.median(arr[-20:]))
            if med > 0 and not (med * 0.5 <= last <= med * 2.0):
                meta["substituted"] = True
                for v in arr[-2::-1]:
                    if med * 0.5 <= float(v) <= med * 2.0:
                        return round(float(v), 2), meta
                return round(med, 2), meta
        return last, meta
    except Exception:
        return None, meta


def _latest_close(sym: str) -> float | None:
    """VPN-safe latest daily close for grounding the single-ticker research card —
    reuses the chart's RH-fallback-backed price_source path (not raw yfinance).
    Returns None when the ticker has no data, so the caller 404s rather than let
    the model fabricate a price. Behavior-identical wrapper over _latest_close_meta."""
    return _latest_close_meta(sym)[0]


def _cached_close(sym: str, fetch: Callable[[str], float | None] | None = None) -> float | None:
    """_latest_close with the shared 5-min per-ticker cache in front — valuing a
    book re-reads every symbol, so repeat views must not re-fetch upstream.

    `fetch` is the patchable-seam hook: server passes its own module-global
    _latest_close so tests monkeypatching server._latest_close keep steering
    book valuation. None (the cron/default path) resolves this module's
    _latest_close at call time. A None price is never cached — a transient
    data outage must not stick for the TTL."""
    with _close_lock:
        hit = _close_cache.get(sym)
    if hit and (time.monotonic() - hit[0]) < CLOSE_TTL_SEC:
        return hit[1]
    price = (fetch or _latest_close)(sym)
    if price is not None:
        with _close_lock:
            _close_cache[sym] = (time.monotonic(), price)
    return price
