"""Earnings history + projections for the visual deep-company analysis card.

Returns the last several quarters of EPS actual-vs-estimate (beat/miss) plus any
forward estimate, in a flat shape the frontend renders as Chart.js bars — the
Qualtrim / FinChat-style earnings visual Nikolai asked for.

Source order (VPN matters — the owner's server is behind a Yahoo-blocking VPN):
  1. Finnhub `/stock/earnings` — VPN-safe, key-gated (FINNHUB_API_KEY)
  2. yfinance `get_earnings_dates` — fallback (blocked under the owner VPN)
Both degrade to {"available": False, "reason": ...}; never raises.
"""
from __future__ import annotations

import os

import requests

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; GESH/1.0)"}


def _from_finnhub(symbol: str) -> list[dict] | None:
    key = os.getenv("FINNHUB_API_KEY", "")
    if not key:
        return None
    try:
        r = requests.get("https://finnhub.io/api/v1/stock/earnings",
                         params={"symbol": symbol, "token": key},
                         headers=_HEADERS, timeout=10)
        if r.status_code != 200:
            return None
        rows = r.json() or []
        out = []
        for e in rows:
            actual, est = e.get("actual"), e.get("estimate")
            if actual is None and est is None:
                continue
            surprise = (round((actual - est) / abs(est) * 100, 1)
                        if (actual is not None and est not in (None, 0)) else None)
            out.append({"period": e.get("period"), "eps_actual": actual,
                        "eps_estimate": est, "surprise_pct": surprise})
        # Finnhub returns newest-first; present oldest→newest for the chart
        return list(reversed(out))[-8:] or None
    except Exception:
        return None


def _from_yfinance(symbol: str) -> list[dict] | None:
    try:
        import yfinance as yf
        df = yf.Ticker(symbol).get_earnings_dates(limit=12)
        if df is None or df.empty:
            return None
        out = []
        for idx, row in df.iterrows():
            actual = row.get("Reported EPS")
            est = row.get("EPS Estimate")
            if actual is None and est is None:
                continue
            act = None if actual != actual else (float(actual) if actual is not None else None)  # NaN guard
            es = None if est != est else (float(est) if est is not None else None)
            if act is None and es is None:
                continue
            surprise = (round((act - es) / abs(es) * 100, 1)
                        if (act is not None and es not in (None, 0)) else None)
            out.append({"period": str(idx)[:10], "eps_actual": act,
                        "eps_estimate": es, "surprise_pct": surprise})
        out = [r for r in out if r["eps_actual"] is not None]     # reported quarters
        return sorted(out, key=lambda r: r["period"])[-8:] or None
    except Exception:
        return None


def get_earnings(symbol: str) -> dict:
    """Earnings history (EPS actual vs estimate, beat/miss) for `symbol`. Finnhub
    primary (VPN-safe), yfinance fallback. Never raises."""
    sym = (symbol or "").strip().upper()
    if not sym:
        return {"available": False, "reason": "no symbol"}
    quarters = _from_finnhub(sym) or _from_yfinance(sym)
    if not quarters:
        return {"available": False, "symbol": sym,
                "reason": "no earnings source (set FINNHUB_API_KEY for VPN-safe data)"}
    beats = sum(1 for q in quarters if q.get("surprise_pct") not in (None,) and q["surprise_pct"] > 0)
    scored = [q for q in quarters if q.get("surprise_pct") is not None]
    return {
        "available": True, "symbol": sym, "quarters": quarters,
        "beat_count": beats, "scored_count": len(scored),
        "avg_surprise_pct": (round(sum(q["surprise_pct"] for q in scored) / len(scored), 1)
                             if scored else None),
        "note": "EPS reported vs Wall-St estimate per quarter (beat = above estimate).",
    }
