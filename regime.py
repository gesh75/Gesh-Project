"""
regime.py — Market regime detection per symbol.

Uses yfinance OHLCV data to compute:
  • ADX (Average Directional Index) — trend strength
  • Bollinger Band width — volatility / compression
  • RSI — momentum

Classifies each symbol into one of:
  TRENDING        — strong directional move (ADX > 25)
  RANGING         — sideways / low volatility (ADX ≤ 20, narrow bands)
  BREAKOUT-READY  — compressed bands + rising ADX (coiling for a move)
  MOMENTUM-HIGH   — RSI > 70, may be overbought
  MOMENTUM-LOW    — RSI < 30, may be oversold

Also produces a short regime_summary string suitable for injection
into Claude prompts.

Feature (v2): FRED Macro Layer
  Injects Federal Reserve macro context into regime_summary:
  • Fed Funds Rate (DFF) — current rate direction
  • 10Y-2Y yield spread (T10Y2Y) — curve inversion / steepening
  • CPI YoY inflation (CPIAUCSL) — inflation trend
  FRED API is free and requires no API key for these public series.
"""

from __future__ import annotations

import json
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import yfinance as yf
import price_source  # Yahoo primary, Robinhood fallback (price_source.py)


# ─── Config ───────────────────────────────────────────────────────────────────
LOOKBACK_DAYS = 200    # calendar days of history to fetch
ADX_PERIOD    = 14
BB_PERIOD     = 20
RSI_PERIOD    = 14


# ─── Indicator functions ─────────────────────────────────────────────────────

def _rsi(close: np.ndarray, period: int = RSI_PERIOD) -> float:
    if len(close) < period + 1:
        return 50.0
    delta   = np.diff(close)
    gain    = np.where(delta > 0, delta, 0.0)
    loss    = np.where(delta < 0, -delta, 0.0)
    avg_g   = gain[:period].mean()
    avg_l   = loss[:period].mean()
    for i in range(period, len(gain)):
        avg_g = (avg_g * (period - 1) + gain[i]) / period
        avg_l = (avg_l * (period - 1) + loss[i]) / period
    if avg_l == 0:
        return 100.0
    rs  = avg_g / avg_l
    return round(100 - 100 / (1 + rs), 1)


def _adx(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = ADX_PERIOD) -> float:
    """Return final ADX value (0-100)."""
    n = len(close)
    if n < period * 2:
        return 15.0
    tr, plus_dm, minus_dm = [], [], []
    for i in range(1, n):
        hl  = high[i]   - low[i]
        hc  = abs(high[i]   - close[i-1])
        lc  = abs(low[i]    - close[i-1])
        tr.append(max(hl, hc, lc))
        ud  = high[i]  - high[i-1]
        dd  = low[i-1] - low[i]
        plus_dm.append(ud  if ud  > dd  and ud  > 0 else 0.0)
        minus_dm.append(dd if dd  > ud  and dd  > 0 else 0.0)

    def smooth(arr, p):
        res = [sum(arr[:p])]
        for v in arr[p:]:
            res.append(res[-1] - res[-1]/p + v)
        return res

    atr  = smooth(tr,       period)
    pDM  = smooth(plus_dm,  period)
    mDM  = smooth(minus_dm, period)

    dx_vals = []
    for i in range(len(atr)):
        if atr[i] == 0:
            continue
        pDI = 100 * pDM[i] / atr[i]
        mDI = 100 * mDM[i] / atr[i]
        denom = pDI + mDI
        if denom == 0:
            continue
        dx_vals.append(100 * abs(pDI - mDI) / denom)

    if not dx_vals:
        return 15.0
    # ADX = smoothed average of DX
    adx = sum(dx_vals[-period:]) / min(period, len(dx_vals[-period:]))
    return round(float(adx), 1)


def _bollinger_width(close: np.ndarray, period: int = BB_PERIOD) -> tuple[float, float]:
    """Return (band_width_pct, pct_b). Width = (upper-lower)/middle * 100."""
    if len(close) < period:
        return 5.0, 50.0
    window = close[-period:]
    mid    = window.mean()
    std    = window.std()
    if mid == 0:
        return 5.0, 50.0
    upper  = mid + 2 * std
    lower  = mid - 2 * std
    width  = (upper - lower) / mid * 100
    pct_b  = (close[-1] - lower) / (upper - lower) * 100 if (upper - lower) != 0 else 50.0
    return round(width, 2), round(pct_b, 1)


# ─── Classification ───────────────────────────────────────────────────────────

def _classify_regime(adx: float, bb_width: float, rsi: float, bb_width_history: list[float]) -> str:
    """Convert indicator values into a regime label."""
    # Detect compression: current width is lower than recent average.
    # Use whatever history is available rather than defaulting to the current
    # width — defaulting to bb_width makes `bb_width < avg_width*0.75` always
    # False on short history, silently disabling BREAKOUT-READY detection.
    avg_width = float(np.mean(bb_width_history[-20:])) if bb_width_history else bb_width
    compressed = bb_width < avg_width * 0.75 and bb_width < 4.0

    if adx >= 30:
        if rsi >= 70:
            return "MOMENTUM-HIGH"
        if rsi <= 30:
            return "MOMENTUM-LOW"
        return "TRENDING"
    if compressed and adx > 18:
        return "BREAKOUT-READY"
    return "RANGING"


_REGIME_ICONS = {
    "TRENDING":      "📈",
    "RANGING":       "↔️",
    "BREAKOUT-READY":"⚡",
    "MOMENTUM-HIGH": "🔥",
    "MOMENTUM-LOW":  "🥶",
}

_REGIME_DESC = {
    "TRENDING":      "Strong directional trend. Momentum strategies favoured.",
    "RANGING":       "Low-volatility sideways price action. Mean-reversion plays.",
    "BREAKOUT-READY":"Bollinger Band compression with rising ADX. Breakout imminent.",
    "MOMENTUM-HIGH": "Extremely overbought (RSI>70). Risk of near-term pullback.",
    "MOMENTUM-LOW":  "Oversold condition (RSI<30). Potential reversal opportunity.",
}


# ─── FRED Macro Layer ─────────────────────────────────────────────────────────

FRED_BASE = "https://fred.stlouisfed.org/graph/fredgraph.csv"
FRED_HEADERS = {"User-Agent": "GeshFinancial/2.0 georgy75@gmail.com"}

_FRED_SERIES = {
    "fed_funds_rate": "DFF",        # Daily effective fed funds rate
    "yield_curve":    "T10Y2Y",     # 10Y minus 2Y Treasury spread (inversion indicator)
    "cpi":            "CPIAUCSL",   # CPI All Urban Consumers (monthly, SA)
}

def _fetch_fred_series(series_id: str, limit_rows: int = 60) -> list[tuple[str, float]]:
    """
    Fetch the last `limit_rows` data points for a FRED series.
    Returns list of (date_str, value) tuples, newest last.
    No API key required for public FRED CSV endpoint.
    """
    try:
        url = f"{FRED_BASE}?id={series_id}"
        req = urllib.request.Request(url, headers=FRED_HEADERS)
        with urllib.request.urlopen(req, timeout=12) as resp:
            lines = resp.read().decode("utf-8").splitlines()
        # Lines: header + data rows "YYYY-MM-DD,value"
        rows: list[tuple[str, float]] = []
        for line in lines[1:]:  # skip header
            parts = line.strip().split(",")
            if len(parts) == 2:
                try:
                    val = float(parts[1])
                    rows.append((parts[0], val))
                except ValueError:
                    pass  # skip "." missing values
        return rows[-limit_rows:]
    except Exception:
        return []


def _rate_direction(series: list[tuple[str, float]], window: int = 5) -> str:
    """Compare last value vs window-ago value to get RISING/FALLING/FLAT."""
    vals = [v for _, v in series if not (v != v)]  # drop NaN
    if len(vals) < window + 1:
        return "FLAT"
    delta = vals[-1] - vals[-(window + 1)]
    if delta > 0.1:
        return "RISING"
    if delta < -0.1:
        return "FALLING"
    return "FLAT"


def get_macro_context() -> dict[str, Any]:
    """
    Fetch macro data from FRED and return a structured context dict + summary string.

    Returns
    -------
    {
      fed_funds_rate: float,
      fed_funds_direction: str,      # RISING / FALLING / FLAT
      yield_curve_spread: float,     # 10Y - 2Y bps (negative = inverted)
      yield_curve_state: str,        # INVERTED / FLAT / NORMAL / STEEP
      cpi_yoy_pct: float | None,     # approximate YoY CPI % change
      inflation_trend: str,          # ACCELERATING / DECELERATING / STABLE
      macro_summary: str,            # compact text for Claude prompt injection
      as_of: str,
      available: bool,
    }
    """
    result: dict[str, Any] = {
        "fed_funds_rate": None,
        "fed_funds_direction": "UNKNOWN",
        "yield_curve_spread": None,
        "yield_curve_state": "UNKNOWN",
        "cpi_yoy_pct": None,
        "inflation_trend": "UNKNOWN",
        "macro_summary": "",
        "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "available": False,
    }

    lines: list[str] = []

    # ── Fed Funds Rate ──────────────────────────────────────────────────────
    dff = _fetch_fred_series("DFF", limit_rows=30)
    if dff:
        rate = dff[-1][1]
        direction = _rate_direction(dff, window=10)
        result["fed_funds_rate"] = rate
        result["fed_funds_direction"] = direction
        dir_arrow = {"RISING": "↑", "FALLING": "↓", "FLAT": "→"}.get(direction, "→")
        lines.append(f"Fed Funds Rate: {rate:.2f}% {dir_arrow} ({direction})")
        result["available"] = True

    # ── Yield Curve (10Y - 2Y) ──────────────────────────────────────────────
    yc = _fetch_fred_series("T10Y2Y", limit_rows=30)
    if yc:
        spread = yc[-1][1]
        result["yield_curve_spread"] = spread
        if spread < -0.25:
            state = "INVERTED"
            state_note = "⚠️ recession signal"
        elif spread < 0.10:
            state = "FLAT"
            state_note = "caution"
        elif spread > 1.0:
            state = "STEEP"
            state_note = "growth positive"
        else:
            state = "NORMAL"
            state_note = "neutral"
        result["yield_curve_state"] = state
        lines.append(f"Yield Curve (10Y-2Y): {spread:+.2f}% [{state} — {state_note}]")
        result["available"] = True

    # ── CPI Inflation (YoY, BLS-headline methodology) ───────────────────────
    # Use CPIAUCNS (NOT seasonally adjusted) — the series BLS uses for the
    # official headline 12-month change. The SA series (CPIAUCSL) gives a
    # slightly different figure (~0.1pp) that read as "wrong" against the BLS
    # release. Trend still uses month-over-month direction on the same series.
    cpi = _fetch_fred_series("CPIAUCNS", limit_rows=14)  # ~14 months
    if len(cpi) >= 13:
        latest = cpi[-1][1]
        year_ago = cpi[-13][1]
        as_of = str(cpi[-1][0])[:7] if cpi[-1][0] else "?"   # YYYY-MM of latest print
        if year_ago > 0:
            yoy = round((latest - year_ago) / year_ago * 100, 2)
            result["cpi_yoy_pct"] = yoy
            result["cpi_as_of"] = as_of
            result["cpi_series"] = "CPIAUCNS"
            # Trend: compare last 3 months vs prior 3 months
            recent_avg  = sum(v for _, v in cpi[-3:]) / 3
            prior_avg   = sum(v for _, v in cpi[-6:-3]) / 3
            if recent_avg > prior_avg * 1.002:
                trend = "ACCELERATING"
            elif recent_avg < prior_avg * 0.998:
                trend = "DECELERATING"
            else:
                trend = "STABLE"
            result["inflation_trend"] = trend
            # Auditable label: value + BLS-matching series + as-of month.
            lines.append(f"CPI YoY {yoy:.1f}% as of {as_of} (FRED CPIAUCNS, BLS headline basis) — Inflation {trend}")
            result["available"] = True

    if lines:
        result["macro_summary"] = (
            "## FRED Macro Context (live)\n"
            + "\n".join(f"  • {l}" for l in lines)
        )
    else:
        result["macro_summary"] = "## FRED Macro Context: unavailable (FRED unreachable)"

    return result


# ─── Main entry ───────────────────────────────────────────────────────────────

def detect_regimes(symbols: list[str]) -> dict[str, Any]:
    """
    Detect market regime for each symbol using yfinance data.

    Returns
    -------
    {
      "regimes": [
        {symbol, regime, adx, bb_width, rsi, pct_b, icon, description, prompt_context}
      ],
      "regime_summary": str   ← compact text for Claude prompt injection
    }
    """
    end   = datetime.today()
    start = end - timedelta(days=LOOKBACK_DAYS + 30)

    try:
        raw = price_source.yf_download(
            symbols,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            auto_adjust=True,
            progress=False,
        )
    except Exception as e:
        return {"regimes": [], "regime_summary": f"Regime detection unavailable: {e}"}

    def _get_col(col: str, sym: str) -> np.ndarray:
        try:
            series = raw[col]
            # A 1-symbol request still yields (field, ticker) MultiIndex columns
            # (both yfinance and the RH fallback's multi-symbol path) — select
            # the ticker column when present, else squeeze the 1-col frame.
            # The old single-symbol branch returned a (n,1) 2-D array here,
            # which kept every ADX/BB op array-shaped until float() exploded.
            if hasattr(series, "columns"):
                series = series[sym] if sym in series.columns else series.squeeze()
            return series.dropna().values.astype(float)
        except Exception:
            return np.array([])

    regimes: list[dict] = []
    lines: list[str]   = []

    for sym in symbols:
        close = _get_col("Close", sym)
        high  = _get_col("High",  sym)
        low   = _get_col("Low",   sym)

        if len(close) < ADX_PERIOD * 2:
            regimes.append({
                "symbol":         sym,
                "regime":         "UNKNOWN",
                "adx":            None,
                "bb_width":       None,
                "rsi":            None,
                "pct_b":          None,
                "icon":           "❓",
                "description":    "Insufficient historical data.",
                "prompt_context": f"{sym}: regime unknown (no data)",
            })
            continue

        adx_val             = _adx(high, low, close)
        bb_width, pct_b     = _bollinger_width(close)
        rsi_val             = _rsi(close)

        # Compute rolling bb_width history for compression detection
        bb_history = []
        for i in range(BB_PERIOD, len(close)):
            w, _ = _bollinger_width(close[:i])
            bb_history.append(w)

        regime = _classify_regime(adx_val, bb_width, rsi_val, bb_history)
        icon   = _REGIME_ICONS.get(regime, "?")
        desc   = _REGIME_DESC.get(regime, "")

        ctx = (
            f"{sym}: {regime} (ADX={adx_val:.0f}, "
            f"BB-Width={bb_width:.1f}%, RSI={rsi_val:.0f}, "
            f"%B={pct_b:.0f}%) — {desc}"
        )
        lines.append(ctx)

        regimes.append({
            "symbol":         sym,
            "regime":         regime,
            "adx":            adx_val,
            "bb_width":       bb_width,
            "rsi":            rsi_val,
            "pct_b":          pct_b,
            "icon":           icon,
            "description":    desc,
            "prompt_context": ctx,
        })

    regime_summary = (
        "## Market Regime Context (yfinance ADX/Bollinger/RSI)\n"
        + "\n".join(f"  • {l}" for l in lines)
    )

    # ── Inject FRED macro context ──────────────────────────────────────────
    macro = get_macro_context()
    if macro["available"]:
        regime_summary = macro["macro_summary"] + "\n\n" + regime_summary

    return {
        "regimes":        regimes,
        "regime_summary": regime_summary,
        "macro":          macro,
    }
