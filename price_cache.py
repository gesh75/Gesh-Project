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
from collections import OrderedDict
from collections.abc import Callable

# Valuing a book re-reads every symbol, so repeat views must not re-fetch
# upstream. Same 5-minute TTL the slice had inside server's per-ticker cache.
CLOSE_TTL_SEC = 300
# Failed lookups need a short cache too.  Without it, a book filled with
# syntactically-valid nonexistent tickers turns every dashboard refresh into N
# fresh provider calls.  Keep failures much shorter-lived than real prices so a
# transient provider outage recovers promptly.
NEGATIVE_CLOSE_TTL_SEC = 60
# Keep the negative cache bounded: customer-supplied one-shot invalid tickers
# must not remain resident forever after their short TTL expires.
MAX_NEGATIVE_CLOSE_CACHE_ENTRIES = 512
_close_cache: OrderedDict[str, tuple[float, float | None]] = OrderedDict()
# Previous-session close, same TTL/lock. Kept in its OWN map rather than widening
# the tuple so every existing _close_cache reader keeps working unchanged.
_prior_cache: dict[str, tuple[float, float]] = {}  # sym -> (monotonic_ts, prior_close)
_close_lock = threading.Lock()


def has_cached_close(sym: str) -> bool:
    """True when `sym` has an unexpired positive or negative cache entry."""
    now = time.monotonic()
    with _close_lock:
        hit = _close_cache.get(sym)
        if not hit:
            return False
        ttl = CLOSE_TTL_SEC if hit[1] is not None else NEGATIVE_CLOSE_TTL_SEC
        if (now - hit[0]) < ttl:
            _close_cache.move_to_end(sym)
            return True
        _close_cache.pop(sym, None)
        return False


def clear() -> None:
    """Drop every cached close (test isolation between TestClient runs)."""
    with _close_lock:
        _close_cache.clear()
        _prior_cache.clear()


def _prune_close_cache_locked(now: float) -> None:
    """Evict stale and excess negative close-cache entries.

    `_close_lock` must be held by the caller.
    """
    for cached_sym, (ts, price) in list(_close_cache.items()):
        ttl = CLOSE_TTL_SEC if price is not None else NEGATIVE_CLOSE_TTL_SEC
        if (now - ts) >= ttl:
            _close_cache.pop(cached_sym, None)

    negative_syms = [
        cached_sym for cached_sym, (_ts, price) in _close_cache.items() if price is None
    ]
    excess = len(negative_syms) - MAX_NEGATIVE_CLOSE_CACHE_ENTRIES
    for cached_sym in negative_syms[: max(0, excess)]:
        _close_cache.pop(cached_sym, None)


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

        raw = price_source.yf_download(
            sym, period="1mo", interval="1d", auto_adjust=True, progress=False
        )
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
            elif len(cols) == 1 and (
                not isinstance(cols[0], str) or cols[0].upper() in (sym.upper(), "CLOSE")
            ):
                close_obj = close_obj[cols[0]]  # unlabeled/neutral single column
            else:
                return None, meta  # labeled with a DIFFERENT ticker
        price, substituted = _close_price_guarded(close_obj.squeeze())
        meta["substituted"] = substituted
        return price, meta
    except Exception:
        return None, meta


def _close_price_guarded(closes) -> tuple[float | None, bool]:
    """Latest close off a Close series, with the bad-tick guard. Returns
    (price, substituted) — shared by _latest_close_meta and the batch book warm
    so both paths price identically.

    Bad-tick guard: a single split-mismatched / partial quote (the observed
    NVDA $17.16 flash against a ~$205 book) poisons valuation, weights, and
    Kelly. If the latest close is wildly off the recent median (a real move
    stays inside 0.5×–2×), fall back to the most recent in-band close, or the
    median if none qualifies — never persist an obvious outlier."""
    closes = closes.dropna()
    if len(closes) == 0:
        return None, False
    import numpy as np

    arr = np.array(closes).flatten().astype(float)
    last = round(float(arr[-1]), 2)
    if len(arr) >= 5:
        med = float(np.median(arr[-20:]))
        if med > 0 and not (med * 0.5 <= last <= med * 2.0):
            for v in arr[-2::-1]:
                if med * 0.5 <= float(v) <= med * 2.0:
                    return round(float(v), 2), True
            return round(med, 2), True
    return last, False


def warm_close_cache_batch(symbols: list[str]) -> None:
    """Warm the close cache with ONE multi-symbol download.

    The book warm used to fan out single-ticker fetches over a
    ThreadPoolExecutor, but price_source's _YF_DOWNLOAD_LOCK (the yfinance
    shared._DFS race fix) serializes them — a cold 40-symbol bulk import
    degraded to 40 sequential round-trips. One batched yf.download is
    internally safe (a single shared._DFS reset) and one round-trip. Prices go
    through the same bad-tick guard as the per-symbol path
    (_close_price_guarded). Symbols the batch fails to serve are simply left
    cold — the caller's valuation loop fetches them per-symbol."""
    try:
        import price_source

        raw = price_source.yf_download(
            list(symbols), period="1mo", interval="1d", auto_adjust=True, progress=False
        )
        if raw is None or getattr(raw, "empty", True):
            return
        close = raw["Close"]
    except Exception:
        return
    for sym in symbols:
        try:
            if getattr(close, "ndim", 1) > 1:
                if sym not in getattr(close, "columns", []):
                    continue
                series = close[sym]
            elif len(symbols) == 1:
                series = close
            else:
                continue  # flat frame for a multi-symbol ask
            price, _substituted = _close_price_guarded(series)
            prev = _prior_close_from_series(series)  # same frame, no extra fetch
        except Exception:
            continue
        if price is not None and price > 0:
            with _close_lock:
                _close_cache[sym] = (time.monotonic(), price)
                if prev is not None:
                    _prior_cache[sym] = (time.monotonic(), prev)


def _prior_close_from_series(closes) -> float | None:
    """The session BEFORE the latest one, off the same Close series.

    Day change is `equity - prior_close_equity` (portfolio_valuation), so the
    previous session's close is an accounting input, not decoration. It comes
    out of the frame `_latest_close_meta` already downloads — no extra request.
    Returns None (never the latest close) when there is no prior bar: a missing
    prior close must DISABLE day change, and defaulting it to today's price
    would report a flat day we cannot evidence."""
    try:
        import numpy as np

        arr = np.array(closes.dropna()).flatten().astype(float)
    except Exception:
        return None
    if len(arr) < 2:
        return None
    prev = round(float(arr[-2]), 2)
    if prev <= 0:
        return None
    if len(arr) >= 5:  # same bad-tick guard as the latest close
        med = float(np.median(arr[-20:]))
        if med > 0 and not (med * 0.5 <= prev <= med * 2.0):
            return None
    return prev


def prior_close(sym: str) -> float | None:
    """Previous-session close for `sym`, cached alongside the latest close."""
    now = time.monotonic()
    with _close_lock:
        hit = _prior_cache.get(sym)
        if hit and now - hit[0] < CLOSE_TTL_SEC:
            return hit[1]
    try:
        import price_source

        raw = price_source.yf_download(
            sym, period="1mo", interval="1d", auto_adjust=True, progress=False
        )
        if raw is None or getattr(raw, "empty", True):
            return None
        close_obj = raw["Close"]
        if getattr(close_obj, "ndim", 1) > 1:
            cols = list(getattr(close_obj, "columns", []))
            if sym in cols:
                close_obj = close_obj[sym]
            elif len(cols) == 1:
                close_obj = close_obj[cols[0]]
            else:
                return None
        value = _prior_close_from_series(close_obj.squeeze())
    except Exception:
        return None
    if value is not None:
        with _close_lock:
            _prior_cache[sym] = (time.monotonic(), value)
    return value


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
    _latest_close at call time. Missing prices are negatively cached for a
    shorter interval so invalid book symbols cannot cause an upstream request
    on every view while transient data outages still recover quickly."""
    with _close_lock:
        hit = _close_cache.get(sym)
        if hit:
            ttl = CLOSE_TTL_SEC if hit[1] is not None else NEGATIVE_CLOSE_TTL_SEC
            if (time.monotonic() - hit[0]) < ttl:
                _close_cache.move_to_end(sym)
                return hit[1]
            _close_cache.pop(sym, None)
    price = (fetch or _latest_close)(sym)
    with _close_lock:
        now = time.monotonic()
        _prune_close_cache_locked(now)
        _close_cache[sym] = (now, price)
        _close_cache.move_to_end(sym)
        _prune_close_cache_locked(now)
    return price
