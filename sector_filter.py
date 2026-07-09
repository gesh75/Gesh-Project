"""
sector_filter.py — ETF Sector Volume Spike Pre-Filter

Checks whether the sector ETF for a given stock is showing unusual volume
(≥ 1.2× its 20-day average) before running full engine analysis on the
individual stock. This eliminates weak-sector setups and reduces false
signals in the scanner pipeline.

Methodology (Black Bot TA)
--------------------------
  1. Map each symbol to its primary sector ETF (e.g. NVDA → XLK).
  2. Fetch the ETF's most recent session volume + 20-day average volume.
  3. Volume ratio = today's volume / 20d average.
  4. HOT if ratio ≥ SPIKE_THRESHOLD (default 1.2×).
  5. Return a structured result including the ratio, ETF, and a prompt string.

Sector ETF Map
--------------
Covers all GICS sectors + special categories used in the GESH screener
universe (tech, finance, healthcare, energy, consumer, industrials, REIT,
materials, utilities, communication, and a fallback to SPY).

Usage
-----
    from sector_filter import is_sector_hot, batch_sector_check

    # Single check
    result = is_sector_hot("NVDA")
    if result["hot"]:
        # proceed with full engine analysis
        ...

    # Batch pre-filter
    report = batch_sector_check(["AAPL", "JPM", "LLY", "XOM"])
    hot_symbols    = [r["symbol"] for r in report["hot"]]
    cold_symbols   = [r["symbol"] for r in report["cold"]]
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import numpy as np
import yfinance as yf

# ─── Config ───────────────────────────────────────────────────────────────────
SPIKE_THRESHOLD  = 1.2   # volume ratio to be considered "hot"
AVG_PERIOD       = 20    # trading days for volume average
FETCH_PERIOD     = "30d" # enough to cover AVG_PERIOD + buffer
FETCH_INTERVAL   = "1d"

# ─── Sector → ETF Map ─────────────────────────────────────────────────────────
# Maps individual tickers to their primary sector ETF.
# Extend as the screener universe grows.

SECTOR_ETF_MAP: dict[str, str] = {
    # ── Technology ──────────────────────────────────────────────────────────
    "NVDA": "XLK", "AAPL": "XLK", "MSFT": "XLK", "GOOGL": "XLK",
    "GOOG": "XLK", "META": "XLK", "AMD":  "XLK", "AVGO": "XLK",
    "ORCL": "XLK", "CRM":  "XLK", "NOW":  "XLK", "ADBE": "XLK",
    "QCOM": "XLK", "TXN":  "XLK", "MU":   "XLK", "AMAT": "XLK",
    "LRCX": "XLK", "KLAC": "XLK", "MRVL": "XLK", "SMCI": "XLK",
    "DELL": "XLK", "HPE":  "XLK", "IONQ": "XLK",

    # ── Semiconductors (overlap with XLK, use SOXX for pure semi) ──────────
    "SOXX": "SOXX", "SMH": "SOXX",

    # ── Communication Services ──────────────────────────────────────────────
    "NFLX": "XLC", "DIS": "XLC", "TMUS": "XLC", "T": "XLC",
    "VZ":   "XLC", "SNAP": "XLC", "PINS": "XLC", "RDDT": "XLC",

    # ── Consumer Discretionary ──────────────────────────────────────────────
    "AMZN": "XLY", "TSLA": "XLY", "HD":   "XLY", "LOW":  "XLY",
    "MCD":  "XLY", "SBUX": "XLY", "NKE":  "XLY", "BKNG": "XLY",
    "ABNB": "XLY", "TJX":  "XLY", "HOOD": "XLY",

    # ── Consumer Staples ────────────────────────────────────────────────────
    "COST": "XLP", "WMT": "XLP", "PG": "XLP", "KO": "XLP",
    "PEP":  "XLP", "PM":  "XLP", "MO": "XLP",

    # ── Healthcare ──────────────────────────────────────────────────────────
    "LLY":  "XLV", "UNH": "XLV", "JNJ": "XLV", "ABBV": "XLV",
    "MRK":  "XLV", "PFE": "XLV", "TMO": "XLV", "ABT":  "XLV",
    "DHR":  "XLV", "ISRG": "XLV",

    # ── Financials ──────────────────────────────────────────────────────────
    "JPM":  "XLF", "GS":  "XLF", "MS":  "XLF", "BAC":  "XLF",
    "WFC":  "XLF", "C":   "XLF", "BLK": "XLF", "SCHW": "XLF",
    "AXP":  "XLF", "V":   "XLF", "MA":  "XLF", "PYPL": "XLF",
    "COF":  "XLF", "SQ":  "XLF",

    # ── Energy ──────────────────────────────────────────────────────────────
    "XOM":  "XLE", "CVX": "XLE", "COP": "XLE", "EOG": "XLE",
    "SLB":  "XLE", "PSX": "XLE", "MPC": "XLE", "VLO": "XLE",

    # ── Industrials ─────────────────────────────────────────────────────────
    "CAT":  "XLI", "DE":  "XLI", "GE":  "XLI", "RTX": "XLI",
    "HON":  "XLI", "UNP": "XLI", "UPS": "XLI", "FDX": "XLI",
    "LMT":  "XLI", "BA":  "XLI",

    # ── Materials ───────────────────────────────────────────────────────────
    "NEM":  "XLB", "FCX": "XLB", "APD": "XLB", "LIN": "XLB",

    # ── Utilities ───────────────────────────────────────────────────────────
    "NEE":  "XLU", "SO":  "XLU", "DUK": "XLU", "AEP": "XLU",

    # ── Real Estate ─────────────────────────────────────────────────────────
    "AMT":  "XLRE", "PLD": "XLRE", "EQIX": "XLRE", "SPG": "XLRE",
}

# Sector ETF display names for reporting
ETF_NAMES: dict[str, str] = {
    "XLK":  "Tech (XLK)",
    "XLC":  "Comm Services (XLC)",
    "XLY":  "Consumer Disc (XLY)",
    "XLP":  "Consumer Staples (XLP)",
    "XLV":  "Healthcare (XLV)",
    "XLF":  "Financials (XLF)",
    "XLE":  "Energy (XLE)",
    "XLI":  "Industrials (XLI)",
    "XLB":  "Materials (XLB)",
    "XLU":  "Utilities (XLU)",
    "XLRE": "Real Estate (XLRE)",
    "SOXX": "Semiconductors (SOXX)",
    "SPY":  "S&P 500 (SPY)",   # fallback
}

# ─── Volume fetch & ratio ─────────────────────────────────────────────────────

def _get_volume_ratio(etf: str) -> tuple[float, float, float]:
    """
    Fetch ETF volume data and return (today_vol, avg_vol, ratio).
    Returns (0, 0, 0) on failure.
    """
    try:
        df = yf.download(
            etf,
            period=FETCH_PERIOD,
            interval=FETCH_INTERVAL,
            auto_adjust=True,
            progress=False,
        )
    except Exception:
        return 0.0, 0.0, 0.0

    if df is None or len(df) < 5:
        return 0.0, 0.0, 0.0

    try:
        vol_s = df["Volume"].squeeze()
    except (KeyError, TypeError):
        return 0.0, 0.0, 0.0

    vol = vol_s.dropna().values.astype(float)
    if len(vol) < 2:
        return 0.0, 0.0, 0.0

    today_vol = float(vol[-1])

    # Average over up to AVG_PERIOD prior sessions (exclude today)
    prior   = vol[-(AVG_PERIOD + 1):-1]
    avg_vol = float(prior.mean()) if len(prior) > 0 else float(vol[:-1].mean())

    if avg_vol == 0:
        return today_vol, avg_vol, 0.0

    ratio = round(today_vol / avg_vol, 3)
    return round(today_vol, 0), round(avg_vol, 0), ratio


# ─── Main entry ───────────────────────────────────────────────────────────────

def is_sector_hot(
    symbol: str,
    threshold: float = SPIKE_THRESHOLD,
) -> dict[str, Any]:
    """
    Check whether the sector ETF for a symbol shows a volume spike.

    Parameters
    ----------
    symbol    : stock ticker (e.g. "NVDA")
    threshold : volume ratio to be considered hot (default 1.2)

    Returns
    -------
    {
        "symbol":        str,
        "etf":           str,    # sector ETF used
        "etf_name":      str,    # human-readable ETF name
        "hot":           bool,
        "volume_ratio":  float,  # today / 20d avg
        "today_volume":  float,
        "avg_volume":    float,
        "threshold":     float,
        "fetched_at":    str,
        "prompt_context": str,
    }
    """
    etf      = SECTOR_ETF_MAP.get(symbol.upper(), "SPY")
    etf_name = ETF_NAMES.get(etf, etf)

    today_vol, avg_vol, ratio = _get_volume_ratio(etf)

    hot = ratio >= threshold and ratio > 0

    if ratio > 0:
        prompt_ctx = (
            f"{symbol} sector {etf_name}: volume ratio {ratio:.2f}x "
            f"({'🔥 HOT' if hot else '❄️ COLD'} — "
            f"threshold {threshold:.1f}x | "
            f"today {today_vol:,.0f} vs avg {avg_vol:,.0f})"
        )
    else:
        prompt_ctx = f"{symbol} sector {etf_name}: volume data unavailable"

    return {
        "symbol":         symbol.upper(),
        "etf":            etf,
        "etf_name":       etf_name,
        "hot":            hot,
        "volume_ratio":   ratio,
        "today_volume":   today_vol,
        "avg_volume":     avg_vol,
        "threshold":      threshold,
        "fetched_at":     datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "prompt_context": prompt_ctx,
    }


def batch_sector_check(
    symbols: list[str],
    threshold: float = SPIKE_THRESHOLD,
) -> dict[str, Any]:
    """
    Run sector hot-check across a list of symbols.
    De-duplicates ETF fetches so each ETF is only downloaded once.

    Returns
    -------
    {
        "hot":     [list of result dicts for hot symbols],
        "cold":    [list of result dicts for cold symbols],
        "summary": str   ← compact text for Claude prompt injection
    }
    """
    # Step 1: resolve each symbol to its ETF and batch-fetch unique ETFs
    unique_etfs = {SECTOR_ETF_MAP.get(s.upper(), "SPY") for s in symbols}
    etf_data: dict[str, tuple[float, float, float]] = {}
    for etf in unique_etfs:
        etf_data[etf] = _get_volume_ratio(etf)

    hot, cold = [], []

    for sym in symbols:
        sym_up   = sym.upper()
        etf      = SECTOR_ETF_MAP.get(sym_up, "SPY")
        etf_name = ETF_NAMES.get(etf, etf)
        today_vol, avg_vol, ratio = etf_data[etf]
        is_hot = ratio >= threshold and ratio > 0

        if ratio > 0:
            prompt_ctx = (
                f"{sym_up} sector {etf_name}: {ratio:.2f}x "
                f"({'HOT' if is_hot else 'cold'})"
            )
        else:
            prompt_ctx = f"{sym_up} sector {etf_name}: no data"

        record = {
            "symbol":         sym_up,
            "etf":            etf,
            "etf_name":       etf_name,
            "hot":            is_hot,
            "volume_ratio":   ratio,
            "today_volume":   today_vol,
            "avg_volume":     avg_vol,
            "threshold":      threshold,
            "fetched_at":     datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "prompt_context": prompt_ctx,
        }

        (hot if is_hot else cold).append(record)

    # Sort hot by ratio descending
    hot.sort(key=lambda x: x["volume_ratio"], reverse=True)

    hot_lines  = [f"  🔥 {r['prompt_context']}" for r in hot]
    cold_lines = [f"  ❄️  {r['prompt_context']}" for r in cold]

    summary = (
        f"## Sector Volume Pre-Filter — "
        f"{len(hot)} hot / {len(cold)} cold of {len(symbols)} symbols\n"
        + "\n".join(hot_lines + cold_lines)
    )

    return {"hot": hot, "cold": cold, "summary": summary}


def get_sector_etf(symbol: str) -> str:
    """Return the mapped sector ETF for a symbol (SPY as fallback)."""
    return SECTOR_ETF_MAP.get(symbol.upper(), "SPY")


# ─── CLI test ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    symbols = sys.argv[1:] if len(sys.argv) > 1 else ["NVDA", "AAPL", "JPM", "XOM"]

    print(f"\nSector Volume Pre-Filter — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 60)

    report = batch_sector_check(symbols)
    print(report["summary"])

    print("\nDetailed results:")
    for r in report["hot"] + report["cold"]:
        status = "🔥 HOT" if r["hot"] else "❄️  cold"
        print(
            f"  {r['symbol']:6s} → {r['etf_name']:25s}  "
            f"ratio={r['volume_ratio']:.2f}x  {status}"
        )
    print("=" * 60)
