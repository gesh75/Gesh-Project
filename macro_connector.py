"""
macro_connector.py — FRED Macro Regime Node (Node 11)

Pulls key macroeconomic indicators from two sources:
  PRIMARY:  FRED API (requires FRED_API_KEY in .env — free at fred.stlouisfed.org)
  FALLBACK: yfinance proxies (works without any key)

    Series / Proxy mapping:
    • Fed Funds Rate  → DFF (FRED)       / ^IRX (13-wk T-bill, yf)
    • 10Y-2Y Spread   → T10Y2Y (FRED)    / ^TNX minus ^IRX (yf)
    • HY Credit Spread→ BAMLH0A0HYM2 (FRED) / HYG vs LQD ratio (yf)
    • VIX             → VIXCLS (FRED)    / ^VIX (yf)

Returns a BULLISH / NEUTRAL / BEARISH macro vote for the consensus engine
as Node 11, plus a compact `prompt_context` string for Claude injection.

Setup (optional, improves data accuracy):
    Add to .env:  FRED_API_KEY=your_key_here
    Get free key: https://fred.stlouisfed.org/docs/api/api_key.html

Usage:
    from macro_connector import get_macro_regime, node_macro

    result = get_macro_regime()
    print(result["summary"])

    vote, detail = node_macro()
"""

from __future__ import annotations

import os
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import requests
import yfinance as yf

# ── Optional fredapi ──────────────────────────────────────────────────────────
try:
    from fredapi import Fred
    _HAS_FREDAPI = True
except ImportError:
    _HAS_FREDAPI = False


# ── Load FRED key from .env ───────────────────────────────────────────────────
def _load_env_key() -> str | None:
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line.startswith("FRED_API_KEY="):
                return line.split("=", 1)[1].strip()
    return os.environ.get("FRED_API_KEY")

FRED_API_KEY = _load_env_key()

CACHE_FILE  = Path(__file__).parent / ".macro_cache.json"
CACHE_TTL_H = 6  # hours


# ── Cache helpers ─────────────────────────────────────────────────────────────

def _cache_valid() -> bool:
    if not CACHE_FILE.exists():
        return False
    try:
        data = json.loads(CACHE_FILE.read_text())
        fetched = datetime.fromisoformat(data["fetched_at"])
        return (datetime.utcnow() - fetched).total_seconds() < CACHE_TTL_H * 3600
    except Exception:
        return False


def _load_cache() -> dict | None:
    try:
        return json.loads(CACHE_FILE.read_text())
    except Exception:
        return None


def _save_cache(data: dict) -> None:
    try:
        CACHE_FILE.write_text(json.dumps(data, default=str))
    except Exception:
        pass


# ── FRED fetch (requires API key) ────────────────────────────────────────────

def _fred_fetch(series_id: str) -> float | None:
    if not FRED_API_KEY:
        return None
    # Try fredapi first
    if _HAS_FREDAPI:
        try:
            fred  = Fred(api_key=FRED_API_KEY)
            end   = datetime.today()
            start = end - timedelta(days=14)
            s = fred.get_series(series_id, observation_start=start.strftime("%Y-%m-%d"))
            if s is not None and not s.empty:
                return float(s.dropna().iloc[-1])
        except Exception:
            pass
    # Fall back to FRED JSON API
    try:
        url = (
            f"https://api.stlouisfed.org/fred/series/observations"
            f"?series_id={series_id}&sort_order=desc&limit=5"
            f"&file_type=json&api_key={FRED_API_KEY}"
        )
        resp = requests.get(url, timeout=8)
        if resp.status_code == 200:
            for obs in resp.json().get("observations", []):
                val = obs.get("value", ".")
                if val != ".":
                    return float(val)
    except Exception:
        pass
    return None


# ── yfinance fallback fetchers ────────────────────────────────────────────────

def _yf_last(ticker: str, period: str = "5d") -> float | None:
    try:
        raw = yf.download(ticker, period=period, auto_adjust=True, progress=False)
        if raw is None or len(raw) == 0:
            return None
        return float(raw["Close"].squeeze().dropna().iloc[-1])
    except Exception:
        return None


def _fetch_fed_funds() -> float | None:
    """Fed Funds Rate: FRED DFF → yf ^IRX (13-week T-bill annualised)."""
    val = _fred_fetch("DFF")
    if val is not None:
        return val
    # ^IRX is quoted as annualised % / 100 on yfinance — multiply back
    raw = _yf_last("^IRX", "5d")
    return round(raw, 2) if raw is not None else None


def _fetch_yield_curve() -> float | None:
    """10Y-2Y spread: FRED T10Y2Y → yf ^TNX - ^FVX proxy."""
    val = _fred_fetch("T10Y2Y")
    if val is not None:
        return val
    try:
        tnx = _yf_last("^TNX", "5d")  # 10Y yield
        tyx = _yf_last("^FVX", "5d")  # 5Y yield (closest public proxy to 2Y on yf)
        if tnx is not None and tyx is not None:
            return round(tnx - tyx, 2)
    except Exception:
        pass
    return None


def _fetch_credit_spread() -> float | None:
    """
    HY Credit Spread: FRED BAMLH0A0HYM2 → yf HYG/LQD price ratio proxy.
    When FRED unavailable, we estimate risk-off vs risk-on from ETF ratio.
    This is a proxy (not true OAS), but direction is reliable.
    """
    val = _fred_fetch("BAMLH0A0HYM2")
    if val is not None:
        return val
    # Proxy: LQD/HYG ratio — rising = HY underperforming IG = spread widening
    try:
        hyg = yf.download("HYG", period="60d", auto_adjust=True, progress=False)
        lqd = yf.download("LQD", period="60d", auto_adjust=True, progress=False)
        if hyg is None or lqd is None or len(hyg) < 5 or len(lqd) < 5:
            return None
        hyg_c = hyg["Close"].squeeze().dropna().values.astype(float)
        lqd_c = lqd["Close"].squeeze().dropna().values.astype(float)
        n = min(len(hyg_c), len(lqd_c))
        # Normalised spread proxy: high value = wider spread = risk-off
        ratio = lqd_c[-1] / hyg_c[-1]
        baseline = np.mean(lqd_c[-n:] / hyg_c[-n:])
        # Map ratio deviation to approximate OAS range 2.5–8.0%
        spread_proxy = 4.0 + (ratio - baseline) / baseline * 15
        return round(float(spread_proxy), 2)
    except Exception:
        return None


def _fetch_vix() -> float | None:
    """VIX: FRED VIXCLS → yf ^VIX."""
    val = _fred_fetch("VIXCLS")
    if val is not None:
        return val
    return _yf_last("^VIX", "5d")


# ── Macro regime computation ──────────────────────────────────────────────────

def get_macro_regime(force_refresh: bool = False) -> dict[str, Any]:
    """
    Fetch macro indicators and classify overall macro regime.

    Returns
    -------
    {
      "fed_funds":      float | None,
      "yield_curve":    float | None,   # 10Y-2Y spread %
      "credit_spread":  float | None,   # HY OAS or proxy %
      "vix":            float | None,
      "macro_score":    float,          # -1 to +1
      "macro_regime":   str,            # RISK-ON / RISK-OFF / NEUTRAL
      "rate_trend":     str,
      "data_source":    str,            # "FRED" | "yfinance" | "mixed"
      "summary":        str,
      "fetched_at":     str,
    }
    """
    if not force_refresh and _cache_valid():
        cached = _load_cache()
        if cached:
            return cached

    result: dict[str, Any] = {
        "fed_funds":     None,
        "yield_curve":   None,
        "credit_spread": None,
        "vix":           None,
        "macro_score":   0.0,
        "macro_regime":  "NEUTRAL",
        "rate_trend":    "STABLE",
        "data_source":   "yfinance",
        "summary":       "",
        "fetched_at":    datetime.utcnow().isoformat(),
    }

    fed_funds     = _fetch_fed_funds()
    yield_curve   = _fetch_yield_curve()
    credit_spread = _fetch_credit_spread()
    vix           = _fetch_vix()

    result["fed_funds"]     = round(fed_funds,     2) if fed_funds     is not None else None
    result["yield_curve"]   = round(yield_curve,   2) if yield_curve   is not None else None
    result["credit_spread"] = round(credit_spread, 2) if credit_spread is not None else None
    result["vix"]           = round(vix,           1) if vix           is not None else None
    result["data_source"]   = "FRED+yfinance" if FRED_API_KEY else "yfinance"

    score_parts: list[float] = []
    notes: list[str] = []

    # 1. Fed Funds Rate
    if fed_funds is not None:
        if fed_funds >= 5.0:
            score_parts.append(-0.4)
            notes.append(f"FFR={fed_funds:.2f}% (restrictive)")
        elif fed_funds >= 3.5:
            score_parts.append(-0.1)
            notes.append(f"FFR={fed_funds:.2f}% (elevated)")
        elif fed_funds <= 1.5:
            score_parts.append(0.4)
            notes.append(f"FFR={fed_funds:.2f}% (accommodative)")
        else:
            score_parts.append(0.1)
            notes.append(f"FFR={fed_funds:.2f}% (neutral)")

    # 2. Yield Curve (10Y-2Y)
    if yield_curve is not None:
        if yield_curve < -0.3:
            score_parts.append(-0.5)
            notes.append(f"Curve={yield_curve:.2f}% (inverted ⚠️)")
        elif yield_curve < 0:
            score_parts.append(-0.2)
            notes.append(f"Curve={yield_curve:.2f}% (flat)")
        elif yield_curve >= 0.5:
            score_parts.append(0.3)
            notes.append(f"Curve={yield_curve:.2f}% (healthy)")
        else:
            score_parts.append(0.1)
            notes.append(f"Curve={yield_curve:.2f}% (normalizing)")

    # 3. Credit Spread
    if credit_spread is not None:
        if credit_spread >= 6.0:
            score_parts.append(-0.5)
            notes.append(f"HY_OAS={credit_spread:.2f}% (stress)")
        elif credit_spread >= 4.5:
            score_parts.append(-0.2)
            notes.append(f"HY_OAS={credit_spread:.2f}% (elevated)")
        elif credit_spread <= 3.5:
            score_parts.append(0.3)
            notes.append(f"HY_OAS={credit_spread:.2f}% (tight=risk-on)")
        else:
            score_parts.append(0.0)
            notes.append(f"HY_OAS={credit_spread:.2f}% (neutral)")

    # 4. VIX
    if vix is not None:
        if vix >= 30:
            score_parts.append(-0.4)
            notes.append(f"VIX={vix:.0f} (fear)")
        elif vix >= 20:
            score_parts.append(-0.1)
            notes.append(f"VIX={vix:.0f} (caution)")
        elif vix <= 13:
            score_parts.append(0.3)
            notes.append(f"VIX={vix:.0f} (complacent)")
        else:
            score_parts.append(0.1)
            notes.append(f"VIX={vix:.0f} (normal)")

    macro_score = float(sum(score_parts) / len(score_parts)) if score_parts else 0.0
    macro_score = max(-1.0, min(1.0, macro_score))
    result["macro_score"] = round(macro_score, 3)

    if macro_score >= 0.2:
        result["macro_regime"] = "RISK-ON"
    elif macro_score <= -0.2:
        result["macro_regime"] = "RISK-OFF"
    else:
        result["macro_regime"] = "NEUTRAL"

    if fed_funds is not None:
        if fed_funds >= 5.0:
            result["rate_trend"] = "PEAK/RESTRICTIVE"
        elif fed_funds >= 3.5:
            result["rate_trend"] = "ELEVATED"
        elif fed_funds <= 1.5:
            result["rate_trend"] = "ACCOMMODATIVE"
        else:
            result["rate_trend"] = "NEUTRAL"

    # Build Claude-injectable summary
    src = result["data_source"]
    lines = [f"## Macro Regime Context ({src})"]
    lines.append(f"  Regime: {result['macro_regime']}  |  Score: {macro_score:+.2f}  |  Rates: {result['rate_trend']}")
    for note in notes:
        lines.append(f"  • {note}")
    if not notes:
        lines.append("  • Macro data unavailable — node abstaining")
    result["summary"] = "\n".join(lines)

    _save_cache(result)
    return result


# ── Consensus node function ───────────────────────────────────────────────────

VOTE_BULLISH =  1
VOTE_NEUTRAL =  0
VOTE_BEARISH = -1
ABSTAIN      = None


def node_macro() -> tuple[int | None, str]:
    """
    Node 11 — Macro (FRED/yfinance) consensus vote.

    Wire into consensus.py run_consensus_holding():
        from macro_connector import node_macro
        nodes["Macro"] = node_macro()
    """
    try:
        data = get_macro_regime()
        score  = data["macro_score"]
        regime = data["macro_regime"]
        vix    = data["vix"]
        curve  = data["yield_curve"]
        oas    = data["credit_spread"]

        # If all series failed
        if all(v is None for v in [vix, curve, oas, data["fed_funds"]]):
            return ABSTAIN, "All macro series unavailable"

        parts = [f"Regime={regime}"]
        if vix   is not None: parts.append(f"VIX={vix:.0f}")
        if curve is not None: parts.append(f"Curve={curve:.2f}%")
        if oas   is not None: parts.append(f"OAS≈{oas:.1f}%")
        parts.append(f"Score={score:+.2f}")
        detail = " · ".join(parts)

        if score >= 0.2:   return VOTE_BULLISH, detail
        if score <= -0.2:  return VOTE_BEARISH, detail
        return VOTE_NEUTRAL, detail

    except Exception as e:
        return ABSTAIN, f"Macro node error: {e}"


# ── CLI test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Fetching macro data (yfinance fallback, no FRED key needed)...\n")
    result = get_macro_regime(force_refresh=True)
    print(result["summary"])
    print(f"\nMacro Score : {result['macro_score']:+.3f}")
    print(f"Regime      : {result['macro_regime']}")
    print(f"Rate Trend  : {result['rate_trend']}")
    print(f"Data Source : {result['data_source']}")
    vote, detail = node_macro()
    vote_map = {1: "BULLISH", 0: "NEUTRAL", -1: "BEARISH", None: "ABSTAIN"}
    print(f"\nNode 11 Vote: {vote_map.get(vote, 'ABSTAIN')} — {detail}")
