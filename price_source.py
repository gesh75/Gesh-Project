"""price_source.py — Yahoo-primary, Robinhood-fallback price adapter.

All engine OHLCV fetches funnel through this module so that a Yahoo Finance
``HTTP 401 / Invalid Crumb`` rate-limit storm (which periodically degrades the
whole consensus/Kelly/vol/Monte-Carlo stack to INSUFFICIENT DATA) no longer
zeroes out a daily run. On a Yahoo failure or empty result, we fall back to
Robinhood — the authenticated ``robin_stocks.get_stock_historicals`` when a
session pickle exists, else Robinhood's UNAUTHENTICATED daily historicals
endpoint (survives both an expired session and the VPN finance-category block).

Two public entry points, both returning a DataFrame shaped IDENTICALLY to
``yfinance.download()`` so existing call sites (``raw["Close"].squeeze()``,
``data["Close"]``, multi-symbol MultiIndex access) keep working unchanged:

* ``yf_download(symbols, ...)`` — try Yahoo once, then Robinhood fallback.
* ``rh_fallback(symbol, ...)`` — Robinhood only (used by consensus.py AFTER its
  own 3× Yahoo retry loop is exhausted, so Yahoo stays primary with full retry).

This file mirrors gesh-project-trading/price_source.py (the engine copy is
canonical for fallback semantics — ported 2026-07-02) plus two product-side
additions: the period-frame TTL cache (rule #42) and the session-retry TTL.

Notes
-----
* Single-symbol frames use simple columns (Open/High/Low/Close/Volume).
* Multi-symbol frames use a column MultiIndex (field, ticker) matching
  yfinance's default ``group_by="column"`` so ``frame["Close"]`` yields a
  per-ticker DataFrame.
* ``auto_adjust`` is accepted for signature parity; Robinhood already returns
  split-adjusted prices, so it is a no-op on the fallback path.
"""
from __future__ import annotations

import logging
import os
import pickle  # noqa: S403  # nosec B403 — loads only the user's own RH session file (fixed 0600 path)
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar

import pandas as pd

logger = logging.getLogger(__name__)

_OHLCV_FIELDS = ("Open", "High", "Low", "Close", "Volume")

# ── Source attestation (Codex #6) ─────────────────────────────────────────────
# This adapter routes Yahoo-primary → Robinhood-fallback and used to DISCARD which
# branch won: the outcome existed only as local control flow and a log line, so no
# caller could say which feed produced a number. Any "data_sources" field would then
# have been a guess from config — exactly the fabrication such a field exists to stop.
#
# The source rides on the frame via pandas' `.attrs`, which survives `.copy()` (so the
# TTL cache is source-transparent for free) and is absent on a plain frame (so a
# monkeypatched test degrades to "unattributable", never to a WRONG claim). Values are
# immutable strings, never lists, so a shared attrs dict can't be mutated by a caller.
_SRC_ATTR = "gesh_price_source"


def _stamp(frame, source: str):
    """Record which feed produced `frame`. Returns the frame for inline use."""
    if frame is not None:
        frame.attrs[_SRC_ATTR] = source
    return frame


def frame_source(frame) -> str | None:
    """The feed that produced `frame` — "yahoo" | "robinhood" | "yahoo+robinhood".

    None means UNATTRIBUTABLE (e.g. a frame this module never stamped). Callers must
    surface that as unknown and must NOT default it to a feed name.
    """
    return getattr(frame, "attrs", {}).get(_SRC_ATTR)

# In-process TTL cache for historical-bar downloads. The daily chain fetches the
# same symbol+window from multiple engines (consensus, kelly, monte_carlo, regime);
# caching the period-based historical frame cuts redundant Yahoo hits and 401/crumb
# rate-limit risk. Only period-based (start/end is None) frames are cached — those
# are stable intraday; live/ranged queries are never cached. Returns a copy so
# callers can't mutate the shared frame.
_YF_CACHE: OrderedDict[tuple, tuple[float, "pd.DataFrame"]] = OrderedDict()
_YF_CACHE_TTL_S = float(os.getenv("YF_CACHE_TTL_S", "600"))
_YF_CACHE_MAX_ENTRIES = max(1, int(os.getenv("YF_CACHE_MAX_ENTRIES", "128")))
_YF_CACHE_LOCK = threading.Lock()

# yfinance.download() is NOT thread-safe: it resets and fills the module-global
# yfinance.shared._DFS, and its completion check is `while len(shared._DFS) <
# len(tickers)` — so two concurrent single-ticker downloads can hand thread A the
# frame thread B fetched, with the ticker column level already dropped (nothing
# downstream can tell). Observed live 2026-07-15: a cold 40-symbol book warm
# (ThreadPoolExecutor in server._customer_book_payload) priced NVDA with TSLA's
# bars. Every yf.download call must hold this lock; the RH fallback path has no
# shared state and stays parallel.
_YF_DOWNLOAD_LOCK = threading.Lock()


def locked_yf_download(*args, **kwargs):
    """The ONLY sanctioned way to call ``yfinance.download`` outside this module.

    Behavior-preserving: no cache, no Robinhood fallback — exactly
    ``yf.download(*args, **kwargs)`` under the process-wide lock above. Exists
    because the 2026-07-15 race fix only protected THIS module's wrapper while
    ~10 other production call sites still called ``yf.download`` raw, so the
    NVDA-priced-as-TSLA contamination could recur through any of them (Codex
    review P1, 2026-07-17). A static test (tests/test_retention_p1s.py) forbids
    raw ``yf.download(`` anywhere else in production code."""
    import yfinance as yf  # lazy, matching this module's import-light convention
    with _YF_DOWNLOAD_LOCK:
        return yf.download(*args, **kwargs)


class _LockedYFProxy:
    """Wraps the yfinance module, serializing ONLY ``download`` on our lock and
    passing everything else through unchanged."""
    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        attr = getattr(self._real, name)
        if name != "download":
            return attr

        def _locked(*a, **k):
            with _YF_DOWNLOAD_LOCK:
                return attr(*a, **k)
        return _locked


def install_shared_yf_lock() -> bool:
    """Make ``gesh_core.market_fetch`` share THIS module's yf.download lock.

    Codex re-review (2026-07-18): the race fix only covered price_source's own
    wrapper, but consensus.py:483 → gesh_core.market_fetch.fetch_spy_close still
    called RAW ``yf.download``. Two separate lock domains means a price_source
    book-warm and a gesh_core SPY fetch can hit yfinance's process-global
    ``shared._DFS`` concurrently and cross-contaminate frames. We rebind ONLY
    gesh_core.market_fetch's module-level ``yf`` to a proxy that takes the SAME
    lock — the global yfinance module is untouched, so price_source's own
    ``with _YF_DOWNLOAD_LOCK: yf.download(...)`` never nests this lock (no
    deadlock). Best-effort + idempotent; a missing gesh_core is a no-op.

    NOTE: the complete fix belongs IN gesh-core (own the lock in market_fetch);
    this is the in-repo mitigation until that ships.
    """
    try:
        import gesh_core.market_fetch as _mf
    except Exception:
        return False
    if isinstance(getattr(_mf, "yf", None), _LockedYFProxy):
        return True   # already installed
    try:
        _mf.yf = _LockedYFProxy(_mf.yf)
        return True
    except Exception:
        return False


# Install at import: every process that touches price_source (server, autopilot,
# the daily chain, the retention jobs) gets the cross-module lock automatically.
install_shared_yf_lock()


# Mirror fetch_live_snapshot.py — the canonical session pickle location.
_PICKLE_PATH = os.path.expanduser("~/.tokens/robinhoodrobinhood_session.pickle")

# Process-level memo for the (cheap) session restore. Success is remembered
# forever; failure is retried after a TTL so a long-lived server picks up a
# pickle that appears (or a token refresh) without a restart.
_RH_SESSION_READY: bool | None = None
_RH_SESSION_CHECKED_AT: float = 0.0
_RH_SESSION_RETRY_S = float(os.getenv("RH_SESSION_RETRY_S", "300"))

# Customer-triggered analysis must never borrow the owner's authenticated broker
# session.  A ContextVar keeps the restriction local to the current request while
# scheduled/owner engine runs retain the resilient authenticated fallback.
_ALLOW_AUTHENTICATED_RH: ContextVar[bool] = ContextVar(
    "allow_authenticated_robinhood_price_fallback", default=True
)


@contextmanager
def unauthenticated_rh_fallback():
    """Restrict Robinhood price fallback to its public, unauthenticated feed."""
    token = _ALLOW_AUTHENTICATED_RH.set(False)
    try:
        yield
    finally:
        _ALLOW_AUTHENTICATED_RH.reset(token)


def _usable_rows(frame: pd.DataFrame | None) -> int:
    """Rows with at least one non-NaN Close.

    A Yahoo-429 storm returns a FULL-LENGTH all-NaN frame — a row-count-only
    check reads that as success, skipping the Robinhood fallback AND caching
    the poisoned frame for the TTL. Count usable values, not rows.
    """
    if frame is None or len(frame) == 0:
        return 0
    try:
        closes = frame["Close"]
    except Exception:  # no Close column — treat rows as-is (unknown shape)
        return len(frame)
    notna = closes.notna()
    if getattr(notna, "ndim", 1) > 1:   # multi-symbol: Close is a DataFrame
        return int(notna.any(axis=1).sum())
    return int(notna.sum())


def patch_rh_session_timeout(default_timeout: float | None = None) -> None:
    """Give robin_stocks' shared requests.Session a DEFAULT TIMEOUT (idempotent).

    robin_stocks issues every HTTP call with no ``timeout=`` — under the corporate
    VPN a black-holed connection blocks FOREVER inside a scheduled job, freezing
    the entry/exit cycle with the classic 0-fills + 0-rejections signature.
    Wrapping Session.request covers every get/post robin_stocks makes.
    Env-overridable via RH_HTTP_TIMEOUT.
    """
    try:
        from robin_stocks.robinhood import helper as _rh_helper
        sess = _rh_helper.SESSION
        if getattr(sess, "_gesh_timeout_patched", False):
            return
        if default_timeout is None:
            try:
                default_timeout = float(os.getenv("RH_HTTP_TIMEOUT", "20"))
            except ValueError:
                default_timeout = 20.0
        orig_request = sess.request

        def _request_with_timeout(method, url, **kwargs):  # noqa: ANN001
            kwargs.setdefault("timeout", default_timeout)
            return orig_request(method, url, **kwargs)

        sess.request = _request_with_timeout
        sess._gesh_timeout_patched = True
        logger.debug("price_source: robin_stocks session timeout set to %ss", default_timeout)
    except Exception as e:  # noqa: BLE001 — hardening must never break imports
        logger.debug("price_source: could not patch RH session timeout — %s", e)


# ─── yfinance period/interval → Robinhood span/interval translation ───────────

def _interval_to_rh(interval: str | None) -> str:
    """Map a yfinance interval string to a Robinhood interval."""
    return {
        "5m": "5minute",
        "10m": "10minute",
        "15m": "10minute",   # RH has no 15m; 10m is the closest finer grain
        "30m": "hour",
        "60m": "hour",
        "1h": "hour",
        "1d": "day",
        "1wk": "week",
        "1w": "week",
    }.get((interval or "1d").lower(), "day")


def _span_from_period(
    period: str | None,
    start: str | None,
    end: str | None,
) -> str:
    """Map a yfinance period (or start/end window) to a Robinhood span.

    Robinhood spans: day | week | month | 3month | year | 5year. We always
    round UP so the fallback never returns fewer bars than the caller needs.
    """
    days: int | None = None
    if period:
        p = period.strip().lower()
        _named = {
            "1d": 1, "5d": 5, "1mo": 31, "3mo": 93, "6mo": 186,
            "1y": 366, "2y": 732, "5y": 1830, "10y": 3650, "max": 3650,
            "ytd": 366,
        }
        if p in _named:
            days = _named[p]
        elif p.endswith("d") and p[:-1].isdigit():
            days = int(p[:-1])
        elif p.endswith("mo") and p[:-2].isdigit():
            days = int(p[:-2]) * 31
        elif p.endswith("y") and p[:-1].isdigit():
            days = int(p[:-1]) * 366
    elif start:
        try:
            s = pd.to_datetime(start)
            e = pd.to_datetime(end) if end else pd.Timestamp.utcnow().tz_localize(None)
            days = max(1, (e - s).days)
        except Exception:
            days = None

    if days is None:
        return "year"          # safe default for daily engines
    if days <= 1:
        return "day"
    if days <= 7:
        return "week"
    if days <= 31:
        return "month"
    if days <= 93:
        return "3month"
    if days <= 366:
        return "year"
    return "5year"


# ─── Robinhood session + fetch ────────────────────────────────────────────────

def _ensure_rh_session() -> bool:
    """Restore the stored Robinhood token from pickle (no MFA). Memoized.

    Success is memoized forever; failure retries after ``_RH_SESSION_RETRY_S``
    so a long-lived server picks up a refreshed pickle without a restart.
    """
    global _RH_SESSION_READY, _RH_SESSION_CHECKED_AT
    if _RH_SESSION_READY is True:
        return True
    if _RH_SESSION_READY is False and (time.time() - _RH_SESSION_CHECKED_AT) < _RH_SESSION_RETRY_S:
        return False

    patch_rh_session_timeout()   # before any RH HTTP call can be made from here
    _RH_SESSION_READY = False
    _RH_SESSION_CHECKED_AT = time.time()
    try:
        if not os.path.isfile(_PICKLE_PATH):
            logger.debug("price_source: no RH session pickle at %s", _PICKLE_PATH)
            return False
        st = os.stat(_PICKLE_PATH)
        if st.st_size == 0:
            # A failed/MFA-aborted login leaves a 0-byte pickle (observed 2026-06-29).
            # Treat as "no session" — the unauth historicals fallback covers OHLCV.
            logger.debug("price_source: empty RH session pickle — using unauth fallback")
            return False
        if st.st_uid != os.getuid() or (st.st_mode & 0o022):
            logger.warning("price_source: refusing unsafe (writable) RH session file")
            return False
        with open(_PICKLE_PATH, "rb") as fh:
            data = pickle.load(fh)  # noqa: S301  # nosec B301 — trusted local session pickle (uid+perms checked above)
        authorization = f"{data['token_type']} {data['access_token']}"
        from robin_stocks.robinhood.helper import set_login_state, update_session
        set_login_state(True)
        update_session("Authorization", authorization)
        _RH_SESSION_READY = True
        logger.info("price_source: Robinhood session restored for price fallback")
    except Exception as e:  # noqa: BLE001 — fallback must never raise
        logger.warning("price_source: RH session restore failed: %s", e)
        _RH_SESSION_READY = False
    return _RH_SESSION_READY


def _rh_history_one_unauth(symbol: str, span: str) -> pd.DataFrame | None:
    """Daily OHLCV via Robinhood's UNAUTHENTICATED historicals endpoint.

    Works with no session and survives the VPN finance-category block (which
    kills yfinance) — so consensus/Kelly/regime keep real bars even when both
    yfinance and the authed RH session are unavailable. Daily bars only;
    returns None for sub-daily intervals upstream.
    """
    try:
        from trading.robinhood_quotes import fetch_daily_ohlcv  # noqa: PLC0415
        o = fetch_daily_ohlcv(symbol, span=span)
    except Exception as e:  # noqa: BLE001 — fallback must never raise
        logger.debug("price_source: unauth RH historicals error for %s: %s", symbol, e)
        return None
    if not o or not o.get("close"):
        return None
    df = pd.DataFrame({
        "Open": o["open"], "High": o["high"], "Low": o["low"],
        "Close": o["close"], "Volume": o["volume"],
    })
    # Build a real DatetimeIndex from the bars' begins_at timestamps (when
    # present — guards older/mocked callers without a "date" key). Without
    # this the DataFrame kept pandas' default RangeIndex (0,1,2,...), so every
    # date-keyed caller silently got positional integers instead of calendar
    # dates whenever this fallback fired (2026-06-30 discovery).
    dates = o.get("date")
    if dates and len(dates) == len(df):
        df.index = pd.to_datetime(dates)
    return df[list(_OHLCV_FIELDS)] if len(df) >= 5 else None


def _rh_history_one(symbol: str, span: str, interval: str) -> pd.DataFrame | None:
    """One symbol's OHLCV as a simple-column DataFrame.

    Prefers the authenticated robin_stocks historicals (richer, any interval)
    when a live session exists; on any failure — including an expired session,
    which robin_stocks 3.4.0 cannot refresh — falls back to the UNAUTHENTICATED
    daily endpoint (daily intervals only) so fetches survive the VPN block.
    """
    rows = None
    if _ALLOW_AUTHENTICATED_RH.get() and _RH_SESSION_READY:
        try:
            import robin_stocks.robinhood as rh
            rows = rh.get_stock_historicals(
                symbol, interval=interval, span=span, bounds="regular"
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("price_source: RH historicals error for %s: %s", symbol, e)
            rows = None
    if not rows:
        # Authed path unavailable/failed → unauth daily fallback (daily only).
        if interval == "day":
            return _rh_history_one_unauth(symbol, span)
        return None

    idx: list[pd.Timestamp] = []
    recs: list[dict[str, float]] = []
    for r in rows:
        if not r or r.get("open_price") is None:
            continue
        try:
            idx.append(pd.to_datetime(r["begins_at"]))
            recs.append({
                "Open":   float(r["open_price"]),
                "High":   float(r["high_price"]),
                "Low":    float(r["low_price"]),
                "Close":  float(r["close_price"]),
                "Volume": float(r.get("volume") or 0.0),
            })
        except (KeyError, TypeError, ValueError):
            continue
    if not recs:
        return None
    df = pd.DataFrame(recs, index=pd.DatetimeIndex(idx))
    return df[list(_OHLCV_FIELDS)]


def rh_fallback(
    symbols: str | list[str],
    *,
    period: str | None = None,
    start: str | None = None,
    end: str | None = None,
    interval: str = "1d",
    **_ignored: object,
) -> pd.DataFrame | None:
    """Robinhood-only OHLCV fetch, shaped like ``yfinance.download()``.

    Returns ``None`` if every symbol fails — the same sentinel ``yf.download``
    failures produce, so callers are unchanged. Does NOT hard-require a session:
    ``_ensure_rh_session`` is consulted (the authed path is preferred when live),
    but daily fetches fall back to the unauthenticated historicals endpoint, so
    the fallback still works when the RH session is expired (the common case —
    robin_stocks 3.4.0 cannot refresh it) or absent.
    """
    if _ALLOW_AUTHENTICATED_RH.get():
        _ensure_rh_session()  # populate session only for trusted host-side callers

    span = _span_from_period(period, start, end)
    rh_interval = _interval_to_rh(interval)

    single = isinstance(symbols, str)
    sym_list = [symbols] if single else list(symbols)

    frames: dict[str, pd.DataFrame] = {}
    for sym in sym_list:
        df = _rh_history_one(sym, span, rh_interval)
        if df is not None and len(df) >= 5:
            frames[sym] = df

    if not frames:
        return None

    if single:
        logger.info("price_source: served %s from Robinhood fallback (%d bars)",
                    sym_list[0], len(frames.get(sym_list[0], [])))
        return _stamp(frames.get(sym_list[0]), "robinhood")

    # Multi-symbol → MultiIndex (field, ticker) like yfinance group_by="column".
    pieces = {
        (field, sym): frame[field]
        for sym, frame in frames.items()
        for field in _OHLCV_FIELDS
    }
    out = pd.DataFrame(pieces).sort_index()
    out.columns = pd.MultiIndex.from_tuples(out.columns, names=["Price", "Ticker"])
    logger.info("price_source: served %d/%d symbols from Robinhood fallback",
                len(frames), len(sym_list))
    return _stamp(out, "robinhood")


# ─── Universal entry point ────────────────────────────────────────────────────

def _usable_yahoo_symbols(raw: "pd.DataFrame | None", sym_list: list[str]) -> set[str]:
    """Subset of `sym_list` that have ≥5 non-NaN Close bars in a yfinance frame.

    Why this exists: a multi-symbol ``yf.download`` of a rate-limited (HTTP 429)
    batch does NOT return None — it returns a frame indexed by DATES with all-NaN
    columns. A bare ``len(raw) >= 5`` check counts date rows and passes, so the
    all-NaN frame was returned as "success" and the Robinhood fallback was
    skipped. We must inspect the Close column per symbol, not the row count.
    """
    if raw is None or len(raw) < 5:
        return set()
    try:
        close = raw["Close"]
    except Exception:  # noqa: BLE001 — unexpected shape → treat as unusable
        return set()
    if isinstance(close, pd.Series):              # single-symbol frame
        return set(sym_list) if int(close.notna().sum()) >= 5 else set()
    good: set[str] = set()                        # multi-symbol → columns are tickers
    for sym in sym_list:
        if sym in close.columns and int(close[sym].notna().sum()) >= 5:
            good.add(sym)
    return good


def yf_download(
    symbols: str | list[str],
    *,
    period: str | None = None,
    start: str | None = None,
    end: str | None = None,
    interval: str = "1d",
    auto_adjust: bool = True,
    progress: bool = False,
    use_cache: bool = True,
    **kwargs: object,
) -> pd.DataFrame | None:
    """Cached drop-in for ``yfinance.download`` (Yahoo primary, RH fallback).

    Period-based historical frames are memoized for ``_YF_CACHE_TTL_S`` to cut
    redundant Yahoo hits across engines in a single daily-chain run. Ranged
    (start/end) queries and calls with ``use_cache=False`` bypass the cache.
    Only frames with real (non-NaN) Close data are cached.
    """
    cacheable = use_cache and start is None and end is None
    key = None
    if cacheable:
        sym_key = tuple(symbols) if isinstance(symbols, (list, tuple)) else symbols
        key = (sym_key, period, interval, auto_adjust)
        now = time.time()
        with _YF_CACHE_LOCK:
            # Sweep every lookup so one-off expired symbols cannot remain resident.
            expired = [k for k, (created, _) in _YF_CACHE.items()
                       if (now - created) > _YF_CACHE_TTL_S]
            for stale_key in expired:
                _YF_CACHE.pop(stale_key, None)
            hit = _YF_CACHE.get(key)
            if hit is not None:
                _YF_CACHE.move_to_end(key)
                return hit[1].copy()

    raw = _yf_download_uncached(
        symbols, period=period, start=start, end=end, interval=interval,
        auto_adjust=auto_adjust, progress=progress, **kwargs,
    )
    if cacheable and key is not None and _usable_rows(raw) > 0:
        now = time.time()
        with _YF_CACHE_LOCK:
            _YF_CACHE[key] = (now, raw.copy())
            _YF_CACHE.move_to_end(key)
            # Evict expired frames (DataFrames are heavy) + hard-cap entry count.
            expired = [k for k, (ts, _) in _YF_CACHE.items()
                       if (now - ts) > _YF_CACHE_TTL_S]
            for stale_key in expired:
                _YF_CACHE.pop(stale_key, None)
            while len(_YF_CACHE) > _YF_CACHE_MAX_ENTRIES:
                _YF_CACHE.popitem(last=False)
    return raw


def _yf_download_uncached(
    symbols: str | list[str],
    *,
    period: str | None = None,
    start: str | None = None,
    end: str | None = None,
    interval: str = "1d",
    auto_adjust: bool = True,
    progress: bool = False,
    **kwargs: object,
) -> pd.DataFrame | None:
    """Drop-in for ``yfinance.download``: Yahoo primary, Robinhood fallback.

    One Yahoo attempt (callers needing heavy retry — e.g. consensus.py — keep
    their own loop and call ``rh_fallback`` directly). The fallback fires not
    only on a 401/crumb error or empty frame, but per-symbol whenever Yahoo
    returns a date-indexed but all-NaN column for a symbol (the HTTP-429 batch
    case) — only the symbols Yahoo failed to serve are re-fetched from Robinhood.
    """
    import yfinance as yf

    single = isinstance(symbols, str)
    sym_list = [symbols] if single else list(symbols)

    raw: pd.DataFrame | None = None
    try:
        with _YF_DOWNLOAD_LOCK:   # yfinance shared._DFS is process-global (see lock def)
            raw = yf.download(
                symbols,
                period=period,
                start=start,
                end=end,
                interval=interval,
                auto_adjust=auto_adjust,
                progress=progress,
                **kwargs,
            )
    except Exception as e:  # noqa: BLE001
        logger.debug("price_source: Yahoo download failed (%s) — trying RH", e)
        raw = None

    usable = _usable_yahoo_symbols(raw, sym_list)
    if len(usable) == len(sym_list):
        return _stamp(raw, "yahoo")                   # every symbol has real data

    missing = [s for s in sym_list if s not in usable]
    fb_arg: str | list[str] = missing[0] if (single and missing) else missing
    fb = rh_fallback(fb_arg, period=period, start=start, end=end, interval=interval)

    if not usable:                                    # Yahoo served nothing usable
        if fb is not None:
            logger.info("price_source: Yahoo unusable for %d symbol(s) — served from RH",
                        len(sym_list))
            return fb
        # BOTH sources failed. Never hand back the date-indexed all-NaN Yahoo
        # frame: it is non-None, non-empty and len>=5, so `is None`/`.empty`
        # callers (regime, monte_carlo, correlation, watchlist) would treat it as
        # data and silently propagate NaN into indicators. None = honest outage.
        logger.warning("price_source: Yahoo AND Robinhood failed for %s — returning None",
                       sym_list)
        return None

    if fb is None:                                    # partial Yahoo, RH unavailable
        return _stamp(raw, "yahoo")
    try:                                              # splice RH bars into the Yahoo frame
        # RH bars are tz-aware UTC, Yahoo bars are tz-naive — combine_first refuses to
        # join across tz-awareness. Normalize BOTH to naive daily, then dedupe (cannot
        # align duplicate labels), so the per-symbol fill actually lands.
        def _naive_daily(frame: pd.DataFrame) -> pd.DataFrame:
            idx = pd.DatetimeIndex(frame.index)
            if idx.tz is not None:
                idx = idx.tz_convert("UTC").tz_localize(None)
            frame = frame.copy()
            frame.index = idx.normalize()
            return frame[~frame.index.duplicated(keep="last")].sort_index()

        raw_c = _naive_daily(raw)
        fb_c = _naive_daily(fb)
        # Never mix price regimes WITHIN one symbol: Yahoo bars are dividend-
        # adjusted (auto_adjust=True) while RH bars are split-adjusted only. A
        # "missing" symbol can still carry 1–4 residual Yahoo bars, and
        # combine_first would splice those into its RH column — an artificial
        # level jump that corrupts ATR/stop math. Blank residuals so each missing
        # symbol's column comes from a SINGLE source.
        try:
            if isinstance(raw_c.columns, pd.MultiIndex):
                resid = [c for c in raw_c.columns if c[1] in set(missing)]
                if resid:
                    raw_c.loc[:, resid] = float("nan")
        except Exception:  # noqa: BLE001 — best-effort hygiene, never break the merge
            pass
        combined = raw_c.combine_first(fb_c)
        logger.info("price_source: filled %d Yahoo-missing symbol(s) from RH: %s",
                    len(missing), ", ".join(missing))
        return _stamp(combined, "yahoo+robinhood")
    except Exception as e:  # noqa: BLE001 — merge failure must not break the run
        logger.debug("price_source: RH/Yahoo merge failed (%s) — returning Yahoo frame", e)
        return _stamp(raw, "yahoo")


def _last_sessions(bars: pd.DataFrame, days: int) -> pd.DataFrame:
    """Trim an intraday frame to its last ``days`` TRADING sessions. The Alpaca
    fetch starts ``days + 2`` calendar days back as a weekend/holiday buffer, so
    without this a '1d' chart rendered up to 3 sessions of bars — and the
    pct_change shown for '1D' actually spanned the whole multi-day window."""
    sessions = bars.index.normalize()
    keep = sessions.unique().sort_values()[-days:]
    return bars[sessions.isin(keep)]


def intraday_5m(symbol: str, span: str) -> "pd.DataFrame | None":
    """5-minute intraday bars for ``symbol`` via Alpaca.

    Yahoo intraday is blocked in this environment (VPN finance-block) and the
    unauthed Robinhood fallback only serves daily bars, so 1D/5D charts went
    blank. Alpaca serves free intraday stock bars. Returns a DataFrame with a
    ``Close`` column, or ``None`` on any failure / missing keys (never raises).
    Key-gated: dormant when ``ALPACA_API_KEY``/``ALPACA_SECRET_KEY`` are unset.
    ``span`` is ``'1d'`` or ``'5d'``.
    """
    key = os.getenv("ALPACA_API_KEY")
    sec = os.getenv("ALPACA_SECRET_KEY")
    if not key or not sec:
        return None
    try:
        from datetime import datetime, timedelta, timezone

        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

        days = 5 if span == "5d" else 1
        client = StockHistoricalDataClient(key, sec)
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame(5, TimeFrameUnit.Minute),
            start=datetime.now(timezone.utc) - timedelta(days=days + 2),
        )
        bars = client.get_stock_bars(req).df
        if bars is None or bars.empty:
            return None
        # Alpaca returns a (symbol, timestamp) MultiIndex — drop the symbol level.
        if getattr(bars.index, "nlevels", 1) > 1:
            bars = bars.xs(symbol, level=0)
        return _last_sessions(bars.rename(columns={"close": "Close"})[["Close"]], days)
    except Exception as e:  # noqa: BLE001 — intraday is best-effort; fall back cleanly
        logger.debug("price_source: Alpaca intraday failed for %s (%s)", symbol, e)
        return None
