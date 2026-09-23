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
_MIN_RR = 1.0             # reward:risk below this → setup is NOT actionable (flagged)
_MIN_REWARD_PCT = 0.25    # target must clear at least this % above entry to be actionable


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


def compute(symbol: str, close=None, high=None, low=None, current_price=None) -> dict:
    """Support/resistance + a long-setup (entry/stop/target/RR) for `symbol`.
    Fetches 6mo daily OHLCV via price_source when arrays aren't supplied.

    `current_price`, when supplied (finite, > 0), binds the setup ENTRY (and the
    returned `price`) to the caller's authoritative live quote — so the trade setup
    matches the rest of the Analyze page (single source of truth) rather than this
    function's own 6mo last-daily-close. Pivots always use the last completed bar."""
    try:
        if close is None or high is None or low is None:
            import pandas as pd
            import price_source
            raw = price_source.yf_download(symbol, period="6mo", interval="1d",
                                           auto_adjust=True, progress=False)
            if raw is None or len(raw) < 20:
                return {"available": False, "reason": "insufficient price data"}
            # Joint row-wise alignment: drop rows where ANY of Close/High/Low is NaN
            # so the three arrays stay bar-aligned. Dropping each column independently
            # can shift them and silently misalign the OHLC bars.
            ohlc = pd.DataFrame({"Close": raw["Close"].squeeze(),
                                 "High": raw["High"].squeeze(),
                                 "Low": raw["Low"].squeeze()}).dropna()
            if len(ohlc) < 20:
                return {"available": False, "reason": "insufficient price data"}
            close = ohlc["Close"].to_numpy(dtype=float)
            high = ohlc["High"].to_numpy(dtype=float)
            low = ohlc["Low"].to_numpy(dtype=float)
        close = np.asarray(close, dtype=float)
        high = np.asarray(high, dtype=float)
        low = np.asarray(low, dtype=float)
        if len(close) < 20:
            return {"available": False, "reason": "insufficient price data"}

        last_close = float(close[-1])
        h, low_bar, c = float(high[-1]), float(low[-1]), last_close  # pivots use last bar
        # Entry binds to the caller's authoritative live quote when supplied, else the
        # last completed daily close (back-compat: every array-based caller passes none).
        price = last_close
        # bool is an int subclass: True must not bind entry to $1.00.
        if current_price is not None and not isinstance(current_price, bool):
            try:
                cp = float(current_price)
                if np.isfinite(cp) and cp > 0:
                    price = cp
            except (TypeError, ValueError, OverflowError):
                pass
        if not np.isfinite([price, h, low_bar, last_close]).all():
            return {"available": False, "reason": "non-finite price data"}
        if price <= 0:
            return {"available": False, "reason": "non-positive price"}
        entry = round(price, 2)

        # ── classic floor-trader pivots (from the last completed bar) ──────────
        p = (h + low_bar + c) / 3.0
        r1, s1 = 2 * p - low_bar, 2 * p - h
        r2, s2 = p + (h - low_bar), p - (h - low_bar)

        # ── swing structure over the window ───────────────────────────────────
        sw_highs, sw_lows = _swing_levels(high, low)
        window_hi = float(np.max(high[-60:])) if len(high) >= 60 else float(np.max(high))
        window_lo = float(np.min(low[-60:])) if len(low) >= 60 else float(np.min(low))

        # candidate support = levels BELOW entry; resistance = ABOVE entry. Round to the
        # DISPLAY precision (2dp) BEFORE comparing to entry, so a level within half a cent
        # of price can't round to == entry and surface as "support at the current price"
        # (or a resistance rounding down to == entry, which would make target == entry).
        supports = sorted({rx for rx in (round(x, 2) for x in ([s1, s2, window_lo] + sw_lows)) if rx < entry},
                          reverse=True)
        resistances = sorted({rx for rx in (round(x, 2) for x in ([r1, r2, window_hi] + sw_highs)) if rx > entry})

        nearest_support = supports[0] if supports else round(entry * 0.95, 2)
        nearest_resistance = resistances[0] if resistances else round(entry * 1.05, 2)

        # ── long trade setup ──────────────────────────────────────────────────
        stop = round(nearest_support * (1 - _STOP_BUFFER), 2)
        target = round(nearest_resistance, 2)
        # Invariant: a coherent long setup requires stop < entry < target. At 2dp
        # the levels can collapse to equality for a sub-dollar / penny price (e.g.
        # entry $0.01 → stop/target also round to $0.01). Rather than emit a
        # degenerate setup (RR undefined / stop == entry), report unavailable.
        if not (stop < entry < target):
            return {"available": False,
                    "reason": "no coherent setup at this price precision (levels collapse at 2dp)"}
        risk = entry - stop
        reward = target - entry
        rr = round(reward / risk, 2) if risk > 0 else None
        reward_pct = round((reward / entry) * 100, 2) if entry else None
        # A setup whose reward:risk is below _MIN_RR — or whose target is a few-cent
        # micro-tick above entry (reward_pct < _MIN_REWARD_PCT) — is mathematically
        # degenerate (e.g. the observed 0.01× R:R with target 5¢ above entry). We keep
        # the levels for context (available stays True) but FLAG the setup non-actionable
        # so the UI never renders it as a tradeable long.
        actionable = (rr is not None and rr >= _MIN_RR
                      and reward_pct is not None and reward_pct >= _MIN_REWARD_PCT)
        warning = None if actionable else (
            f"Reward:risk {rr}× is below {_MIN_RR:.1f}× — target only {reward_pct}% above "
            "entry; not an actionable setup at current levels.")

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
                      "reward_pct": reward_pct,
                      "actionable": actionable, "warning": warning},
            "note": ("Educational trade-setup levels (long bias) — not advice. "
                     "Entry at last close, stop below nearest support, target at "
                     "nearest resistance."),
        }
    except Exception as e:  # noqa: BLE001 — best-effort; never breaks the page
        return {"available": False, "reason": f"{type(e).__name__}"}
