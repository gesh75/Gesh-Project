"""rh_mcp_data.py — READ-ONLY market/account data via the Robinhood agent MCP.

Why this exists
---------------
Rule #54: the corporate GlobalProtect VPN SNI-category-blocks *every* finance-data
host (Yahoo, stooq, all Alpaca hosts) with a TLS reset. `price_source.py` already
carries a Yahoo→Robinhood fallback chain for that, but its Robinhood tier is the
UNAUTHENTICATED quote endpoint — thin data, no indicators, no tax lots.

Meanwhile the daily chain's step 1/5 (`fetch_snapshot_direct.py`) has been reaching
`agent.robinhood.com/mcp/trading` over its own OAuth tokens the whole time, and that
path survives the VPN. It was only ever used for three tools. The MCP actually
exposes 54. This module wraps the READ-ONLY subset as a first-class data source:

    historicals()   OHLCV bars, yfinance-shaped  → drop-in for price_source
    indicator()     broker-computed RSI/MACD/BB/ATR/VWAP/MA
    tax_lots()      per-lot cost basis + acquisition date + st/lt term
    quotes()        real-time quotes

Safety property (the point of this module)
------------------------------------------
`_ALLOWED_TOOLS` is a hard allowlist. `_call()` refuses any name not in it and
raises before a request is built. `place_equity_order`, `place_option_order`,
`cancel_equity_order`, `cancel_option_order` and `exercise_option` are deliberately
absent, so **this module structurally cannot place, cancel or exercise anything** —
that is a code invariant, not a convention. Order review lives in the separate
`rh_order_review.py` so the two capabilities never share an import surface.

Failure contract
----------------
Nothing here raises on a network, auth or parse failure — callers get `None` or an
empty frame and a log line, exactly like `price_source`. The one exception is the
allowlist violation above, which is a programming error and must be loud.

Caches are TTL'd (rule #42) — mocked tests reusing a symbol MUST call
`clear_caches()`.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import pandas as pd

logger = logging.getLogger(__name__)

# ── Hard read-only allowlist ─────────────────────────────────────────────────
# Adding a mutating tool here defeats the module's only safety guarantee.
# Order placement/cancellation belongs nowhere in this file.
_ALLOWED_TOOLS = frozenset({
    "get_equity_historicals",
    "get_equity_technical_indicators",
    "get_equity_tax_lots",
    "get_equity_quotes",
    "get_equity_fundamentals",
    "get_equity_positions",
    "get_equity_tradability",
    "get_portfolio",
    "get_realized_pnl",
    "get_pnl_trade_history",
})

_OHLCV_FIELDS = ("Open", "High", "Low", "Close", "Volume")
_SRC_ATTR = "gesh_price_source"          # mirrors price_source.py attestation
_SRC_VALUE = "robinhood_mcp"

_BARS_TTL_S = float(os.getenv("GESH_MCP_BARS_TTL_S", "1800"))    # 30 min
_LOTS_TTL_S = float(os.getenv("GESH_MCP_LOTS_TTL_S", "3600"))    # 60 min

_lock = threading.Lock()
_session: object | None = None
_session_failed_at: float = 0.0
_SESSION_RETRY_S = 300.0

_bars_cache: dict[tuple, tuple[float, object]] = {}
_lots_cache: dict[str, tuple[float, list]] = {}


def clear_caches() -> None:
    """Drop every TTL cache and force a fresh MCP session (rule #42, for tests)."""
    global _session, _session_failed_at
    with _lock:
        _bars_cache.clear()
        _lots_cache.clear()
        _session = None
        _session_failed_at = 0.0


# ── Session ──────────────────────────────────────────────────────────────────
def _direct():
    """Import fetch_snapshot_direct defensively.

    That module resolves GESH_RH_ACCOUNT at import time and calls `SystemExit`
    when it is unset (see CLAUDE.md, 'GESH_RH_ACCOUNT — where the account number
    lives'). SystemExit derives from BaseException, so a bare `except Exception`
    would NOT catch it and an unset account var would take down every caller of
    this module. Hence BaseException here.
    """
    try:
        import fetch_snapshot_direct as F
        return F
    except BaseException as e:                       # noqa: BLE001 — see docstring
        logger.warning("rh_mcp_data: fetch_snapshot_direct unavailable (%s)", e)
        return None


def _get_session():
    """Lazily build an initialized MCP session. Returns None when unavailable."""
    global _session, _session_failed_at
    with _lock:
        if _session is not None:
            return _session
        if _session_failed_at and (time.time() - _session_failed_at) < _SESSION_RETRY_S:
            return None                              # don't hammer a dead endpoint
        F = _direct()
        if F is None:
            _session_failed_at = time.time()
            return None
        try:
            mcp = F.Mcp(F._access_token())
            mcp.call("initialize", {
                "protocolVersion": F.PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "gesh-rh-mcp-data", "version": "1.0"},
            })
            mcp.notify_initialized()
            _session = mcp
            _session_failed_at = 0.0
            return mcp
        except BaseException as e:                   # noqa: BLE001
            logger.warning("rh_mcp_data: MCP session failed (%s)", e)
            _session_failed_at = time.time()
            return None


def _call(tool: str, args: dict) -> dict | None:
    """Invoke an allowlisted read-only tool. Returns None on any failure."""
    if tool not in _ALLOWED_TOOLS:
        raise ValueError(
            f"rh_mcp_data: tool {tool!r} is not on the read-only allowlist. "
            "This module must never place, cancel or exercise orders."
        )
    mcp = _get_session()
    if mcp is None:
        return None
    try:
        return mcp.tool(tool, args)
    except BaseException as e:                       # noqa: BLE001
        logger.warning("rh_mcp_data: %s failed (%s)", tool, e)
        global _session
        with _lock:
            _session = None                          # force re-init next call
        return None


def available() -> bool:
    """True when an MCP session can be established. Never raises."""
    return _get_session() is not None


def account_number() -> str | None:
    F = _direct()
    return getattr(F, "ACCT", None) if F else None


# ── Helpers ──────────────────────────────────────────────────────────────────
def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _f(v) -> float | None:
    """Robinhood returns numbers as strings; empty/None/'' must stay None."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ── OHLCV ────────────────────────────────────────────────────────────────────
def historicals(
    symbols: str | list[str],
    *,
    days: int = 365,
    interval: str = "day",
    bounds: str = "regular",
) -> pd.DataFrame:
    """OHLCV bars shaped IDENTICALLY to a yfinance OHLCV frame.

    Single symbol → simple columns (Open/High/Low/Close/Volume).
    Multiple symbols → column MultiIndex (field, ticker), matching yfinance
    ``group_by="column"`` so ``frame["Close"]`` yields a per-ticker frame.

    Returns an EMPTY DataFrame on any failure — never raises, never partial-lies.
    A returned frame carries ``.attrs['gesh_price_source'] = 'robinhood_mcp'``
    (same attestation convention as price_source.py).
    """
    syms = [symbols] if isinstance(symbols, str) else list(symbols)
    syms = [s for s in syms if s]
    if not syms:
        return pd.DataFrame()

    key = (tuple(sorted(syms)), days, interval, bounds)
    now = time.time()
    hit = _bars_cache.get(key)
    if hit and (now - hit[0]) < _BARS_TTL_S:
        return hit[1].copy()                          # .copy() preserves .attrs

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    payload = _call("get_equity_historicals", {
        "symbols": syms,
        "interval": interval,
        "start_time": _iso(start),
        "end_time": _iso(end),
        "bounds": bounds,
    })
    if not payload:
        return pd.DataFrame()

    results = (payload.get("data") or {}).get("results") or []
    per_symbol: dict[str, pd.DataFrame] = {}
    for res in results:
        sym = res.get("symbol")
        bars = res.get("bars") or []
        if not sym or not bars:
            continue
        rows, idx = [], []
        for b in bars:
            ts = b.get("begins_at")
            o, h, lo = _f(b.get("open_price")), _f(b.get("high_price")), _f(b.get("low_price"))
            c, v = _f(b.get("close_price")), _f(b.get("volume"))
            if ts is None or c is None:
                continue                              # a bar without a close is not a bar
            idx.append(pd.to_datetime(ts))
            rows.append((o, h, lo, c, v))
        if not rows:
            continue
        df = pd.DataFrame(rows, columns=list(_OHLCV_FIELDS), index=pd.DatetimeIndex(idx, name="Date"))
        per_symbol[sym] = df.sort_index()

    if not per_symbol:
        return pd.DataFrame()

    if len(syms) == 1:
        frame = per_symbol.get(syms[0], pd.DataFrame())
    else:
        frame = pd.concat(per_symbol, axis=1)         # (ticker, field)
        frame.columns = frame.columns.swaplevel(0, 1)  # → (field, ticker), yfinance order
        frame = frame.sort_index(axis=1)

    if not frame.empty:
        frame.attrs[_SRC_ATTR] = _SRC_VALUE
        _bars_cache[key] = (now, frame.copy())
    return frame


# ── Indicators ───────────────────────────────────────────────────────────────
def indicator(
    symbol: str,
    ind_type: str,
    *,
    days: int = 120,
    interval: str = "day",
    period: int | None = None,
    **extra,
) -> pd.Series | None:
    """Broker-computed indicator as a time-indexed Series. None on failure.

    ``ind_type`` is passed through verbatim (rsi, macd, bollinger_bands, atr,
    vwap, sma, ema, …). Optional tuning params (num_std, fast_period,
    slow_period, signal_period, multiplier, method) pass through via **extra.
    """
    end = datetime.now(timezone.utc)
    args: dict = {
        "symbol": symbol,
        "type": ind_type,
        "interval": interval,
        "start_time": _iso(end - timedelta(days=days)),
        "end_time": _iso(end),
    }
    if period is not None:
        args["period"] = int(period)
    args.update({k: v for k, v in extra.items() if v is not None})

    payload = _call("get_equity_technical_indicators", args)
    if not payload:
        return None
    inds = (payload.get("data") or {}).get("indicators") or []
    if not inds:
        return None
    series = inds[0].get("series") or []
    idx, vals = [], []
    for pt in series:
        v = _f(pt.get("value"))
        if pt.get("begins_at") is None or v is None:
            continue
        idx.append(pd.to_datetime(pt["begins_at"]))
        vals.append(v)
    if not vals:
        return None
    return pd.Series(vals, index=pd.DatetimeIndex(idx, name="Date"), name=ind_type)


def latest_indicator(symbol: str, ind_type: str, **kw) -> float | None:
    """Most recent indicator value, or None."""
    s = indicator(symbol, ind_type, **kw)
    return None if s is None or s.empty else float(s.iloc[-1])


def atr(symbol: str, *, period: int = 14, days: int = 120) -> float | None:
    """Latest ATR — the input the engine's stop levels are supposed to use."""
    return latest_indicator(symbol, "atr", period=period, days=days)


# ── Tax lots ─────────────────────────────────────────────────────────────────
def tax_lots(symbol: str, account: str | None = None) -> list[dict]:
    """Open tax lots for one holding: cost basis, acquisition date, st/lt term.

    Returns [] on failure — an EMPTY list means 'unknown', never 'no lots'.
    Callers must not read [] as evidence that nothing is harvestable.
    """
    acct = account or account_number()
    if not acct or not symbol:
        return []
    now = time.time()
    ck = f"{acct}:{symbol}"
    hit = _lots_cache.get(ck)
    if hit and (now - hit[0]) < _LOTS_TTL_S:
        return [dict(x) for x in hit[1]]

    payload = _call("get_equity_tax_lots", {"account_number": acct, "symbol": symbol})
    if not payload:
        return []
    raw = (payload.get("data") or {}).get("tax_lots") or []
    lots = []
    for lot in raw:
        qty = _f(lot.get("quantity_available"))
        cps = _f(lot.get("cost_per_share"))
        if qty is None or cps is None or qty <= 0:
            continue
        lots.append({
            "lot_id": lot.get("open_lot_id"),
            "quantity": qty,
            "cost_per_share": cps,
            "cost_basis": _f(lot.get("tax_cost_basis")),
            "open_date": lot.get("open_date"),
            "term": lot.get("term"),                  # 'st' | 'lt'
            "selectable": bool(lot.get("is_selectable")),
        })
    _lots_cache[ck] = (now, [dict(x) for x in lots])
    return lots


def unrealized_by_lot(symbol: str, current_price: float, account: str | None = None) -> list[dict]:
    """Per-lot unrealized P&L — the input a wash-sale-aware harvester needs.

    Each lot gains ``unrealized_usd``, ``unrealized_pct`` and ``days_held``.
    """
    out = []
    today = datetime.now(timezone.utc).date()
    for lot in tax_lots(symbol, account):
        cps = lot["cost_per_share"]
        qty = lot["quantity"]
        days_held = None
        if lot.get("open_date"):
            try:
                days_held = (today - datetime.strptime(lot["open_date"], "%Y-%m-%d").date()).days
            except ValueError:
                days_held = None
        out.append({
            **lot,
            "current_price": current_price,
            "unrealized_usd": round((current_price - cps) * qty, 2),
            "unrealized_pct": round((current_price / cps - 1) * 100, 2) if cps else None,
            "days_held": days_held,
        })
    return out


# ── Quotes ───────────────────────────────────────────────────────────────────
def quotes(symbols: str | list[str]) -> dict[str, dict]:
    """Real-time quotes keyed by symbol. {} on failure."""
    syms = [symbols] if isinstance(symbols, str) else list(symbols)
    syms = [s for s in syms if s]
    if not syms:
        return {}
    payload = _call("get_equity_quotes", {"symbols": syms})
    if not payload:
        return {}
    out = {}
    for q in (payload.get("data") or {}).get("results") or []:
        sym = q.get("symbol")
        if sym:
            out[sym] = q
    return out


if __name__ == "__main__":                            # pragma: no cover
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else "NVDA"
    print(f"available: {available()}")
    df = historicals(sym, days=30)
    print(f"{sym} bars: {len(df)} rows, source={df.attrs.get(_SRC_ATTR)}")
    if not df.empty:
        print(df.tail(3))
    print(f"{sym} ATR(14): {atr(sym)}")
    print(f"{sym} RSI(14): {latest_indicator(sym, 'rsi', period=14)}")
    for lot in tax_lots(sym):
        print("  lot", lot)
