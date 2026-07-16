"""robinhood_quotes.py — VPN-resilient last-price fallback via Robinhood's public API.

Why this exists (2026-06-23): the GlobalProtect corporate VPN category-blocks
Yahoo Finance *and* Alpaca (both fail with a TLS reset / curl exit 35) whenever
it is connected. With Yahoo dead, `paper_trading._fetch_current_prices()`
returned {} and `autopilot.tracker_close_scan` skipped every exit — the autonomy
gate silently froze at 8/30 closed trades. Robinhood's public quotes endpoint
(`api.robinhood.com/quotes/`) is NOT in the blocked category and needs no auth,
so it serves as the fallback price source.

Contract mirrors `_fetch_current_prices`: returns {symbol: float}. Missing /
unparseable symbols are omitted; a total failure returns {} (never raises).

Stdlib-only (urllib) — no SDK dependency, so it works in the autopilot venv and
under launchd without extra installs.
"""
from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

_QUOTES_URL = "https://api.robinhood.com/quotes/"
_HISTORICALS_URL = "https://api.robinhood.com/quotes/historicals/"
_TIMEOUT = 8  # seconds
_CHUNK = 50   # symbols per request (RH accepts comma-batched symbols)
_USER_AGENT = "gesh-financial/1.0 (close-scan price fallback)"


def _urlopen(url: str, timeout: int):
    """Thin seam around urllib so tests can monkeypatch the network out."""
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    # Fixed https RH API endpoint only — no user-controlled scheme.
    return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310  # nosec B310


def _coerce_price(entry: dict) -> float | None:
    """Pick the best available price from a single RH quote result."""
    for key in ("last_trade_price", "last_extended_hours_trade_price", "previous_close"):
        raw = entry.get(key)
        if raw in (None, ""):
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    return None


def _fetch_chunk(symbols: list[str]) -> dict[str, float]:
    qs = urllib.parse.urlencode({"symbols": ",".join(symbols)})
    url = f"{_QUOTES_URL}?{qs}"
    with _urlopen(url, timeout=_TIMEOUT) as resp:
        payload = json.loads(resp.read())
    prices: dict[str, float] = {}
    for entry in payload.get("results") or []:
        if not entry:  # RH returns null for unknown tickers
            continue
        sym = entry.get("symbol")
        if entry.get("trading_halted"):
            continue
        px = _coerce_price(entry)
        if sym and px is not None and px > 0:
            prices[sym] = px
    return prices


def fetch_daily_ohlcv(symbol: str, span: str = "year") -> dict[str, list[float]] | None:
    """Daily OHLCV bars from Robinhood's UNAUTHENTICATED historicals endpoint.

    Why: the engine's ATR-adaptive exit needs ~60 daily bars. yfinance is
    VPN-category-blocked and the authenticated robin_stocks historicals path needs
    a live session (robin_stocks 3.4.0 can't refresh it). This endpoint returns
    full OHLC bars with no auth and survives the VPN, so ATR keeps working.

    span ∈ {day, week, month, 3month, year, 5year}. Returns parallel lists
    {open, high, low, close, volume} (oldest→newest) or None on any failure
    (never raises). pandas-free so this module stays stdlib-only.
    """
    if not symbol:
        return None
    qs = urllib.parse.urlencode(
        {"symbols": symbol, "interval": "day", "span": span, "bounds": "regular"}
    )
    url = f"{_HISTORICALS_URL}?{qs}"
    try:
        with _urlopen(url, timeout=_TIMEOUT) as resp:
            payload = json.loads(resp.read())
    except Exception as e:  # noqa: BLE001 — fallback must degrade, not crash
        logger.warning("robinhood_quotes: historicals fetch failed for %s — %s", symbol, e)
        return None

    results = payload.get("results") or []
    if not results or not results[0]:
        return None
    # Contamination guard: trust the response only if its echoed symbol matches
    # the request — a mismatched echo means these bars belong to another ticker.
    echoed = str(results[0].get("symbol") or "").upper()
    if echoed and echoed != symbol.upper():
        logger.warning("robinhood_quotes: historicals echoed %s for a %s request — discarding",
                       echoed, symbol)
        return None
    bars = results[0].get("historicals") or []
    # "date" carries each bar's begins_at timestamp (raw string, unparsed — kept
    # pandas-free per this module's stdlib-only design) so callers can rebuild a
    # real DatetimeIndex. Previously discarded: price_source._rh_history_one_unauth
    # built its DataFrame with no index at all (a default RangeIndex), so every
    # caller of this fallback got positional "0","1","2"... instead of calendar
    # dates whenever Yahoo Finance failed and this path fired.
    out: dict[str, list] = {"open": [], "high": [], "low": [], "close": [], "volume": [], "date": []}
    for b in bars:
        if not b or b.get("open_price") is None:
            continue
        try:
            o = float(b["open_price"])
            h = float(b["high_price"])
            lo = float(b["low_price"])
            c = float(b["close_price"])
        except (KeyError, TypeError, ValueError):
            continue
        out["open"].append(o)
        out["high"].append(h)
        out["low"].append(lo)
        out["close"].append(c)
        out["volume"].append(float(b.get("volume") or 0.0))
        out["date"].append(b.get("begins_at"))
    return out if out["close"] else None


def fetch_last_prices(symbols: list[str]) -> dict[str, float]:
    """Return {symbol: last_price} from Robinhood's public quotes endpoint.

    Never raises — on any network/parse error returns whatever was collected so
    far (possibly {}), so callers can treat an empty dict as "fallback unavailable".
    """
    if not symbols:
        return {}
    prices: dict[str, float] = {}
    for i in range(0, len(symbols), _CHUNK):
        chunk = symbols[i : i + _CHUNK]
        try:
            prices.update(_fetch_chunk(chunk))
        except Exception as e:  # noqa: BLE001 — fallback must degrade, not crash
            logger.warning("robinhood_quotes: fetch failed for %s — %s", chunk, e)
    return prices
