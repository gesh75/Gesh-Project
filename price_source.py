"""price_source.py — Yahoo-primary, Robinhood-fallback price adapter.

All engine OHLCV fetches funnel through this module so that a Yahoo Finance
``HTTP 401 / Invalid Crumb`` rate-limit storm (which periodically degrades the
whole consensus/Kelly/vol/Monte-Carlo stack to INSUFFICIENT DATA) no longer
zeroes out a daily run. On a Yahoo failure or empty result, we fall back to
``robin_stocks.get_stock_historicals`` using the already-restored Robinhood
session pickle.

Two public entry points, both returning a DataFrame shaped IDENTICALLY to
``yfinance.download()`` so existing call sites (``raw["Close"].squeeze()``,
``data["Close"]``, multi-symbol MultiIndex access) keep working unchanged:

* ``yf_download(symbols, ...)`` — try Yahoo once, then Robinhood fallback.
* ``rh_fallback(symbol, ...)`` — Robinhood only (used by consensus.py AFTER its
  own 3× Yahoo retry loop is exhausted, so Yahoo stays primary with full retry).

Robinhood fallback is HOST-ONLY: it needs the session pickle written by the
Robinhood login. In the sandbox (no pickle) ``_ensure_rh_session()`` returns
False and the fallback is a no-op — callers degrade exactly as before.

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
import pickle
from datetime import datetime, timezone

import pandas as pd

logger = logging.getLogger(__name__)

_OHLCV_FIELDS = ("Open", "High", "Low", "Close", "Volume")

# Mirror fetch_live_snapshot.py — the canonical session pickle location.
_PICKLE_PATH = os.path.expanduser("~/.tokens/robinhoodrobinhood_session.pickle")

# Process-level memo so we attempt the (cheap) session restore at most once.
_RH_SESSION_READY: bool | None = None


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

    Mirrors fetch_live_snapshot.py::_restore_session so the engine processes —
    which never logged in themselves — can reach Robinhood market data.
    """
    global _RH_SESSION_READY
    if _RH_SESSION_READY is not None:
        return _RH_SESSION_READY

    _RH_SESSION_READY = False
    try:
        if not os.path.isfile(_PICKLE_PATH):
            logger.debug("price_source: no RH session pickle at %s", _PICKLE_PATH)
            return False
        st = os.stat(_PICKLE_PATH)
        if st.st_uid != os.getuid() or (st.st_mode & 0o022):
            logger.warning("price_source: refusing unsafe (writable) RH session file")
            return False
        with open(_PICKLE_PATH, "rb") as fh:
            data = pickle.load(fh)
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


def _rh_history_one(symbol: str, span: str, interval: str) -> pd.DataFrame | None:
    """Fetch one symbol's OHLCV from Robinhood as a simple-column DataFrame."""
    try:
        import robin_stocks.robinhood as rh
        rows = rh.get_stock_historicals(
            symbol, interval=interval, span=span, bounds="regular"
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("price_source: RH historicals error for %s: %s", symbol, e)
        return None
    if not rows:
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

    Returns ``None`` if the session is unavailable or every symbol fails — the
    same sentinel ``yf.download`` failures produce, so callers are unchanged.
    """
    if not _ensure_rh_session():
        return None

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
        return frames.get(sym_list[0])

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
    return out


# ─── Universal entry point ────────────────────────────────────────────────────

def yf_download(
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
    their own loop and call ``rh_fallback`` directly). On a 401/crumb error or
    an empty/too-short frame, transparently fall back to Robinhood.
    """
    import yfinance as yf

    raw: pd.DataFrame | None = None
    try:
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

    if raw is not None and len(raw) >= 5:
        return raw

    fb = rh_fallback(symbols, period=period, start=start, end=end, interval=interval)
    return fb if fb is not None else raw
