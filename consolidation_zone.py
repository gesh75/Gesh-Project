"""
consolidation_zone.py — Consolidation Zone Detector

Identifies tight price consolidation setups suitable for breakout entries.
Based on the Black Bot TA methodology: detect 10+ consecutive candles
within a 2% price range, then produce entry / target / stop levels.

Designed to plug into the 5MIN and 1HR scanner modes.

Algorithm
---------
1. Fetch OHLCV data at the requested timeframe (default: 5m, 1d).
2. Slide a window of MIN_CANDLES across the close prices.
3. A window is a consolidation zone when:
     (max_close - min_close) / min_close * 100 ≤ RANGE_PCT_THRESHOLD
4. Return the most recent valid zone (last occurrence wins).
5. Compute trade levels:
     entry  = midpoint of zone
     stop   = zone low  - 0.5% buffer (below zone floor)
     target = previous consolidation zone top  (if found)
              OR  zone midpoint + 2 × (midpoint - stop)  (2:1 R/R fallback)

Returns a dict with status + all trade levels.  Safe to call from any
scanner — returns {"status": "no_zone"} when no setup is found.

Usage
-----
    from consolidation_zone import detect_consolidation

    result = detect_consolidation("AAPL", interval="5m", period="1d")
    if result["status"] == "found":
        print(result["entry"], result["target"], result["stop"])
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import numpy as np

import price_source  # locked yf.download adapter (thread-safety)
# ─── Config ───────────────────────────────────────────────────────────────────
MIN_CANDLES        = 10      # minimum consecutive candles to qualify
RANGE_PCT_THRESHOLD = 2.0   # max high-low range as % of low price
STOP_BUFFER_PCT    = 0.5    # extra % below zone low for stop loss
MIN_RR             = 2.0    # minimum risk/reward ratio for fallback target


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _zone_range_pct(closes: np.ndarray) -> float:
    """Return (max - min) / min * 100 for a price window."""
    lo = closes.min()
    if lo == 0:
        return 999.0
    return (closes.max() - lo) / lo * 100.0


def _find_zones(closes: np.ndarray, highs: np.ndarray, lows: np.ndarray) -> list[dict]:
    """
    Slide MIN_CANDLES window across closes.
    Return list of all qualifying zones as dicts:
        {start_idx, end_idx, zone_low, zone_high, midpoint}
    """
    zones = []
    n = len(closes)
    i = 0
    while i <= n - MIN_CANDLES:
        # Expand the window as far as the range stays within threshold
        j = i + MIN_CANDLES
        while j <= n:
            window = closes[i:j]
            if _zone_range_pct(window) > RANGE_PCT_THRESHOLD:
                break
            j += 1
        # j-1 is the last valid endpoint (minimum i+MIN_CANDLES)
        end = j - 1
        if end - i >= MIN_CANDLES:
            zone_lo  = float(lows[i:end].min())
            zone_hi  = float(highs[i:end].max())
            mid      = round((zone_lo + zone_hi) / 2, 4)
            zones.append({
                "start_idx": i,
                "end_idx":   end,
                "zone_low":  round(zone_lo, 4),
                "zone_high": round(zone_hi, 4),
                "midpoint":  mid,
                "candles":   end - i,
            })
            i = end  # skip past this zone to find the next distinct one
        else:
            i += 1
    return zones


# ─── Main entry ───────────────────────────────────────────────────────────────

def detect_consolidation(
    symbol: str,
    interval: str = "5m",
    period: str   = "1d",
) -> dict[str, Any]:
    """
    Detect the most recent consolidation zone for a symbol.

    Parameters
    ----------
    symbol   : ticker string (e.g. "AAPL")
    interval : yfinance interval — "5m" for 5MIN scanner, "1h" for 1HR scanner
    period   : yfinance period  — "1d" for intraday, "5d" for 1HR

    Returns
    -------
    On success:
    {
        "status":        "found",
        "symbol":        str,
        "zone_low":      float,
        "zone_high":     float,
        "midpoint":      float,
        "entry":         float,   # = midpoint
        "stop":          float,   # = zone_low - 0.5% buffer
        "target":        float,   # previous zone top or 2:1 R/R fallback
        "target_source": str,     # "prev_zone" | "2:1_rr"
        "rr_ratio":      float,   # (target - entry) / (entry - stop)
        "zone_candles":  int,     # how many candles in the zone
        "interval":      str,
        "fetched_at":    str,
        "prompt_context": str,    # 1-line summary for Claude prompt injection
    }

    On failure:
    {
        "status":  "no_zone" | "error",
        "symbol":  str,
        "reason":  str,
    }
    """
    try:
        df = price_source.locked_yf_download(
            symbol,
            period=period,
            interval=interval,
            auto_adjust=True,
            progress=False,
        )
    except Exception as e:
        return {"status": "error", "symbol": symbol, "reason": str(e)}

    if df is None or len(df) < MIN_CANDLES:
        return {
            "status": "no_zone",
            "symbol": symbol,
            "reason": f"Insufficient data ({len(df) if df is not None else 0} candles, need {MIN_CANDLES})",
        }

    closes = df["Close"].squeeze().values.astype(float)
    highs  = df["High"].squeeze().values.astype(float)
    lows   = df["Low"].squeeze().values.astype(float)

    # Remove NaNs
    mask   = ~(np.isnan(closes) | np.isnan(highs) | np.isnan(lows))
    closes, highs, lows = closes[mask], highs[mask], lows[mask]

    if len(closes) < MIN_CANDLES:
        return {
            "status": "no_zone",
            "symbol": symbol,
            "reason": "Not enough clean candles after NaN removal",
        }

    zones = _find_zones(closes, highs, lows)

    if not zones:
        return {
            "status": "no_zone",
            "symbol": symbol,
            "reason": f"No consolidation zone found (need {MIN_CANDLES}+ candles within {RANGE_PCT_THRESHOLD}%)",
        }

    # Most recent zone = last in list
    current_zone = zones[-1]

    # Previous zone = second to last (for target calculation)
    prev_zone = zones[-2] if len(zones) >= 2 else None

    # ── Trade levels ──────────────────────────────────────────────────────────
    entry = current_zone["midpoint"]
    stop  = round(current_zone["zone_low"] * (1 - STOP_BUFFER_PCT / 100), 4)
    risk  = entry - stop

    if risk <= 0:
        return {
            "status": "no_zone",
            "symbol": symbol,
            "reason": "Degenerate zone: entry <= stop (data anomaly)",
        }

    if prev_zone and prev_zone["zone_high"] > entry:
        # Use top of previous zone as target (Black Bot methodology)
        target        = round(prev_zone["zone_high"], 4)
        target_source = "prev_zone"
    else:
        # Fallback: 2:1 risk/reward
        target        = round(entry + MIN_RR * risk, 4)
        target_source = "2:1_rr"

    rr_ratio = round((target - entry) / risk, 2) if risk > 0 else 0.0

    # ── Prompt context ────────────────────────────────────────────────────────
    prompt_ctx = (
        f"{symbol} CONSOLIDATION ZONE [{interval}]: "
        f"{current_zone['candles']} candles in "
        f"${current_zone['zone_low']:.2f}–${current_zone['zone_high']:.2f} | "
        f"Entry ${entry:.2f} | Stop ${stop:.2f} | Target ${target:.2f} "
        f"({target_source}) | R/R {rr_ratio:.1f}x"
    )

    return {
        "status":         "found",
        "symbol":         symbol,
        "zone_low":       current_zone["zone_low"],
        "zone_high":      current_zone["zone_high"],
        "midpoint":       current_zone["midpoint"],
        "entry":          entry,
        "stop":           stop,
        "target":         target,
        "target_source":  target_source,
        "rr_ratio":       rr_ratio,
        "zone_candles":   current_zone["candles"],
        "interval":       interval,
        "fetched_at":     datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "prompt_context": prompt_ctx,
    }


def scan_consolidations(
    symbols: list[str],
    interval: str = "5m",
    period: str   = "1d",
    min_rr: float = 2.0,
) -> dict[str, Any]:
    """
    Run consolidation detection across a list of symbols.
    Returns only setups with rr_ratio >= min_rr, sorted by R/R descending.

    Usage
    -----
        from consolidation_zone import scan_consolidations
        results = scan_consolidations(["AAPL","NVDA","TSLA"], interval="5m")

    Returns
    -------
    {
        "found":   [list of result dicts, best R/R first],
        "no_zone": [list of symbols with no setup],
        "errors":  [list of (symbol, reason)],
        "summary": str   ← compact text for Claude prompt injection
    }
    """
    found, no_zone, errors = [], [], []

    for sym in symbols:
        r = detect_consolidation(sym, interval=interval, period=period)
        if r["status"] == "found":
            if r["rr_ratio"] >= min_rr:
                found.append(r)
            else:
                no_zone.append(sym)
        elif r["status"] == "no_zone":
            no_zone.append(sym)
        else:
            errors.append((sym, r.get("reason", "unknown error")))

    found.sort(key=lambda x: x["rr_ratio"], reverse=True)

    lines = [f"  • {r['prompt_context']}" for r in found]
    summary = (
        f"## Consolidation Zone Scan [{interval}] — "
        f"{len(found)} setup(s) of {len(symbols)} scanned\n"
        + ("\n".join(lines) if lines else "  No qualifying setups found.")
    )

    return {
        "found":   found,
        "no_zone": no_zone,
        "errors":  errors,
        "summary": summary,
    }


# ─── CLI test ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else "SPY"
    ivl = sys.argv[2] if len(sys.argv) > 2 else "5m"
    per = sys.argv[3] if len(sys.argv) > 3 else "1d"

    res = detect_consolidation(sym, interval=ivl, period=per)
    print(f"\n{'='*60}")
    if res["status"] == "found":
        print(f"  ✅ CONSOLIDATION ZONE FOUND — {sym}")
        print(f"  Zone:    ${res['zone_low']:.2f} – ${res['zone_high']:.2f} ({res['zone_candles']} candles)")
        print(f"  Entry:   ${res['entry']:.2f}")
        print(f"  Stop:    ${res['stop']:.2f}")
        print(f"  Target:  ${res['target']:.2f} ({res['target_source']})")
        print(f"  R/R:     {res['rr_ratio']:.1f}x")
    else:
        print(f"  ⚪ {res['status'].upper()} — {res.get('reason','')}")
    print(f"{'='*60}\n")
