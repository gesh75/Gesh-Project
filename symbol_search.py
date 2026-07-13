"""Company-name → ticker search for the Analyze-box typeahead.

Loads the tradable US-equity universe from Alpaca ONCE and caches it in memory
(24h), then matches locally per keystroke — VPN-safe (Alpaca, not Yahoo) and no
per-query upstream call. Falls back to a small bundled list of well-known names
so the box still works when Alpaca keys are unset (dev / demo).
"""
from __future__ import annotations

import os
import threading
import time

_UNIVERSE: list[dict] = []      # [{"symbol", "name"}]
_LOADED_AT = 0.0
_TTL_SEC = 86_400               # refresh the universe once a day
_LOCK = threading.Lock()

# Always-available fallback (works with no Alpaca keys) — the names people type.
_FALLBACK: list[dict] = [
    {"symbol": "AAPL", "name": "Apple Inc."},
    {"symbol": "MSFT", "name": "Microsoft Corporation"},
    {"symbol": "NVDA", "name": "NVIDIA Corporation"},
    {"symbol": "AMZN", "name": "Amazon.com, Inc."},
    {"symbol": "GOOGL", "name": "Alphabet Inc. (Class A)"},
    {"symbol": "GOOG", "name": "Alphabet Inc. (Class C)"},
    {"symbol": "META", "name": "Meta Platforms, Inc."},
    {"symbol": "TSLA", "name": "Tesla, Inc."},
    {"symbol": "BRK.B", "name": "Berkshire Hathaway Inc."},
    {"symbol": "JPM", "name": "JPMorgan Chase & Co."},
    {"symbol": "V", "name": "Visa Inc."},
    {"symbol": "MA", "name": "Mastercard Incorporated"},
    {"symbol": "UNH", "name": "UnitedHealth Group Incorporated"},
    {"symbol": "HD", "name": "The Home Depot, Inc."},
    {"symbol": "PG", "name": "The Procter & Gamble Company"},
    {"symbol": "JNJ", "name": "Johnson & Johnson"},
    {"symbol": "COST", "name": "Costco Wholesale Corporation"},
    {"symbol": "WMT", "name": "Walmart Inc."},
    {"symbol": "GS", "name": "The Goldman Sachs Group, Inc."},
    {"symbol": "DIS", "name": "The Walt Disney Company"},
    {"symbol": "NFLX", "name": "Netflix, Inc."},
    {"symbol": "AMD", "name": "Advanced Micro Devices, Inc."},
    {"symbol": "INTC", "name": "Intel Corporation"},
    {"symbol": "BAC", "name": "Bank of America Corporation"},
    {"symbol": "KO", "name": "The Coca-Cola Company"},
    {"symbol": "PEP", "name": "PepsiCo, Inc."},
    {"symbol": "CRM", "name": "Salesforce, Inc."},
    {"symbol": "ORCL", "name": "Oracle Corporation"},
    {"symbol": "ADBE", "name": "Adobe Inc."},
    {"symbol": "PYPL", "name": "PayPal Holdings, Inc."},
    {"symbol": "HOOD", "name": "Robinhood Markets, Inc."},
    {"symbol": "MSTR", "name": "MicroStrategy Incorporated"},
    {"symbol": "PLTR", "name": "Palantir Technologies Inc."},
    {"symbol": "DDOG", "name": "Datadog, Inc."},
    {"symbol": "SPY", "name": "SPDR S&P 500 ETF Trust"},
    {"symbol": "QQQ", "name": "Invesco QQQ Trust"},
    {"symbol": "IWM", "name": "iShares Russell 2000 ETF"},
    {"symbol": "T", "name": "AT&T Inc."},
    {"symbol": "F", "name": "Ford Motor Company"},
    {"symbol": "BA", "name": "The Boeing Company"},
]


def _load_from_alpaca() -> list[dict]:
    """Full active, tradable US-equity + ETF universe (symbol + name). [] on any
    failure or missing keys — the caller falls back to the bundled list."""
    key, sec = os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY")
    if not key or not sec:
        return []
    try:
        from alpaca.trading.client import TradingClient  # noqa: PLC0415
        from alpaca.trading.enums import AssetStatus  # noqa: PLC0415
        from alpaca.trading.requests import GetAssetsRequest  # noqa: PLC0415
        client = TradingClient(key, sec, paper=True)             # assets are reference data
        assets = client.get_all_assets(GetAssetsRequest(status=AssetStatus.ACTIVE))
        out = []
        for a in assets:
            sym = getattr(a, "symbol", None)
            name = getattr(a, "name", None)
            if sym and name and getattr(a, "tradable", True):
                out.append({"symbol": str(sym), "name": str(name)})
        return out
    except Exception:
        return []


def _universe() -> list[dict]:
    global _UNIVERSE, _LOADED_AT
    now = time.monotonic()
    with _LOCK:
        if _UNIVERSE and (now - _LOADED_AT) < _TTL_SEC:
            return _UNIVERSE
    loaded = _load_from_alpaca()               # network OUTSIDE the lock
    with _LOCK:
        _UNIVERSE = loaded or _FALLBACK
        _LOADED_AT = time.monotonic()
        return _UNIVERSE


def search(query: str, limit: int = 10) -> list[dict]:
    """Typeahead matches for `query`, ranked exact-symbol → symbol-prefix →
    name-prefix → name-substring. Returns [{symbol, name}] (≤ limit, hard cap 25).
    A blank/1-char query returns [] (avoid dumping the whole universe)."""
    q = (query or "").strip().upper()
    if len(q) < 2:
        return []
    limit = max(1, min(int(limit or 10), 25))
    exact: list[dict] = []
    sym_pref: list[dict] = []
    name_pref: list[dict] = []
    name_sub: list[dict] = []
    for a in _universe():
        sym = a["symbol"].upper()
        name = a["name"].upper()
        if sym == q:
            exact.append(a)
        elif sym.startswith(q):
            sym_pref.append(a)
        elif name.startswith(q):
            name_pref.append(a)
        elif q in name:
            name_sub.append(a)
        if len(exact) + len(sym_pref) >= limit and len(name_pref) + len(name_sub) >= limit:
            break   # enough high-quality hits gathered; stop scanning ~11k rows
    return (exact + sym_pref + name_pref + name_sub)[:limit]
