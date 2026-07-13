"""Support / resistance levels + a stop-loss / target trade-setup suggestion.

Per-ticker "trade setup" for the Analyze view: computes key price levels and a
concrete long-side entry / stop / target with a risk:reward ratio, from daily
OHLCV (via price_source — VPN-safe). Two level families, both classic:

  * Classic floor-trader PIVOTS from the last completed bar (P, R1/R2, S1/S2)
  * Recent SWING high / low over the lookback window (major structural S/R)

The setup is educational, not advice: entry = last close, stop = nearest support
below (with a small buffer), target = nearest resistance above, R:R = reward/risk.
Never raises — returns {"available": False, ...} when data is thin.
"""
from __future__ import annotations

import numpy as np

_STOP_BUFFER = 0.005      # 0.5% below the support level for the stop


def _swing_levels(high: np.ndarray, low: np.ndarray, left: int = 3, right: int = 3):
    """Fractal swing highs (resistance) / lows (support): a bar higher/lower than
    `left` bars before and `right` bars after it."""
    highs, lows = [], []
    for i in range(left, len(high) - right):
        window_h = high[i - left:i + right + 1]
        window_l = low[i - left:i + right + 1]
        if high[i] == window_h.max() and (window_h == high[i]).sum() == 1:
            highs.append(float(high[i]))
        if low[i] == window_l.min() and (window_l == low[i]).sum() == 1:
            lows.append(float(low[i]))
    return highs, lows


def compute(symbol: str, close=None, high=None, low=None) -> dict:
    """Support/resistance + a long-setup (entry/stop/target/RR) for `symbol`.
    Fetches 6mo daily OHLCV via price_source when arrays aren't supplied."""
    try:
        if close is None or high is None or low is None:
            import price_source
            raw = price_source.yf_download(symbol, period="6mo", interval="1d",
                                           auto_adjust=True, progress=False)
            if raw is None or len(raw) < 20:
                return {"available": False, "reason": "insufficient price data"}
            close = raw["Close"].squeeze().dropna().values.astype(float)
            high = raw["High"].squeeze().dropna().values.astype(float)
            low = raw["Low"].squeeze().dropna().values.astype(float)
        close = np.asarray(close, dtype=float)
        high = np.asarray(high, dtype=float)
        low = np.asarray(low, dtype=float)
        if len(close) < 20:
            return {"available": False, "reason": "insufficient price data"}

        price = float(close[-1])
        h, low_bar, c = float(high[-1]), float(low[-1]), price

        # ── classic floor-trader pivots (from the last completed bar) ──────────
        p = (h + low_bar + c) / 3.0
        r1, s1 = 2 * p - low_bar, 2 * p - h
        r2, s2 = p + (h - low_bar), p - (h - low_bar)

        # ── swing structure over the window ───────────────────────────────────
        sw_highs, sw_lows = _swing_levels(high, low)
        window_hi = float(np.max(high[-60:])) if len(high) >= 60 else float(np.max(high))
        window_lo = float(np.min(low[-60:])) if len(low) >= 60 else float(np.min(low))

        # candidate support = levels BELOW price; resistance = levels ABOVE price
        supports = sorted({round(x, 2) for x in ([s1, s2, window_lo] + sw_lows) if x < price},
                          reverse=True)
        resistances = sorted({round(x, 2) for x in ([r1, r2, window_hi] + sw_highs) if x > price})

        nearest_support = supports[0] if supports else round(price * 0.95, 2)
        nearest_resistance = resistances[0] if resistances else round(price * 1.05, 2)

        # ── long trade setup ──────────────────────────────────────────────────
        entry = round(price, 2)
        stop = round(nearest_support * (1 - _STOP_BUFFER), 2)
        target = round(nearest_resistance, 2)
        risk = entry - stop
        reward = target - entry
        rr = round(reward / risk, 2) if risk > 0 else None

        return {
            "available": True, "symbol": symbol.upper(), "price": entry,
            "support": [round(x, 2) for x in supports[:3]],
            "resistance": [round(x, 2) for x in resistances[:3]],
            "pivots": {"P": round(p, 2), "R1": round(r1, 2), "R2": round(r2, 2),
                       "S1": round(s1, 2), "S2": round(s2, 2)},
            "swing_high_60d": round(window_hi, 2), "swing_low_60d": round(window_lo, 2),
            "setup": {"entry": entry, "stop": stop, "target": target,
                      "risk_reward": rr,
                      "risk_pct": round((risk / entry) * 100, 2) if entry else None,
                      "reward_pct": round((reward / entry) * 100, 2) if entry else None},
            "note": ("Educational trade-setup levels (long bias) — not advice. "
                     "Entry at last close, stop below nearest support, target at "
                     "nearest resistance."),
        }
    except Exception as e:  # noqa: BLE001 — best-effort; never breaks the page
        return {"available": False, "reason": f"{type(e).__name__}"}
