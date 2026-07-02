"""
fft_cycles.py — FFT Spectral Cycle Detection

Decomposes price history into dominant trading cycles using Fast Fourier Transform.
No planetary harmonics or pseudoscience — pure signal processing.

Key outputs per symbol:
  • dominant_cycles  : top 3 cycle periods (days) ranked by spectral power
  • primary_cycle    : strongest cycle with phase position and direction
  • cycle_position   : normalised 0.0 (trough) → 1.0 (peak)
  • signal           : BULLISH / NEUTRAL / BEARISH for consensus node

Signal logic:
  • Trough zone (0.0–0.35, rising)  → BULLISH   (accumulation phase)
  • Mid rising   (0.35–0.65, rising) → BULLISH
  • Peak zone    (0.65–1.0,  falling)→ BEARISH   (distribution phase)
  • Falling      (any, falling)      → BEARISH
  • Unclear / low power             → NEUTRAL

Typical meaningful cycles detected:
  ~5d  (weekly rhythm)
  ~10d (biweekly options cycle)
  ~21d (monthly mean-reversion)
  ~63d (quarterly earnings cycle)
"""

from __future__ import annotations

import numpy as np
from datetime import datetime, timedelta, timezone
from typing import Any

import price_source  # Yahoo primary, Robinhood fallback (audit PR3)

# ─── Config ──────────────────────────────────────────────────────────────────
LOOKBACK_DAYS   = 252   # 1 year of trading history
MIN_CYCLE_DAYS  = 5     # shortest meaningful cycle
MAX_CYCLE_DAYS  = 63    # longest cycle (~1 quarter)
TOP_N_CYCLES    = 3     # number of dominant cycles to return
MIN_DATA_POINTS = 60    # minimum candles required


# ─── Core FFT analysis ───────────────────────────────────────────────────────

def detect_fft_cycles(symbol: str, lookback_days: int = LOOKBACK_DAYS, prices: np.ndarray | None = None) -> dict[str, Any]:
    """
    Detect dominant price cycles via FFT for a given symbol.

    Returns
    -------
    {
      symbol, dominant_cycles, primary_cycle,
      cycle_position, is_rising, signal, detail,
      n_candles, as_of, error (if failed)
    }
    """
    try:
        if prices is None:
            end   = datetime.today()
            start = end - timedelta(days=lookback_days + 40)   # extra buffer for weekends

            raw = price_source.yf_download(
                symbol,
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
                auto_adjust=True,
                progress=False,
            )
            if raw is None or len(raw) < MIN_DATA_POINTS:
                return {"error": "Insufficient data", "symbol": symbol}

            prices = raw["Close"].squeeze().dropna().values.astype(float)
        
        if len(prices) < MIN_DATA_POINTS:
            return {"error": "Insufficient data", "symbol": symbol}
            
        n = len(prices)

        # ── 1. Log-transform + linear detrend (remove secular drift) ────────
        log_p    = np.log(prices)
        x        = np.arange(n, dtype=float)
        coeffs   = np.polyfit(x, log_p, 1)
        detrended = log_p - np.polyval(coeffs, x)

        # ── 2. Hanning window to reduce spectral leakage ─────────────────────
        window   = np.hanning(n)
        windowed = detrended * window

        # ── 3. FFT power spectrum (positive frequencies only) ────────────────
        fft_vals  = np.fft.fft(windowed)
        freqs_pos = np.fft.fftfreq(n, d=1.0)[:n // 2]   # cycles per trading day
        power     = np.abs(fft_vals[:n // 2]) ** 2

        # ── 4. Mask to valid cycle range (avoid DC component + noise) ────────
        with np.errstate(divide="ignore", invalid="ignore"):
            periods = np.where(freqs_pos > 0, 1.0 / freqs_pos, 0.0)

        valid = (
            (freqs_pos > 0)
            & (periods >= MIN_CYCLE_DAYS)
            & (periods <= MAX_CYCLE_DAYS)
        )
        if not np.any(valid):
            return {"error": "No cycles in valid range", "symbol": symbol}

        masked_power = np.where(valid, power, 0.0)

        # ── 5. Find top N dominant cycles (avoid near-duplicates) ────────────
        sorted_idx = np.argsort(masked_power)[::-1]
        dominant_cycles: list[dict] = []
        used_periods: list[float]   = []

        for idx in sorted_idx:
            if masked_power[idx] == 0 or freqs_pos[idx] == 0:
                continue
            period = 1.0 / freqs_pos[idx]

            # Skip if too close to an already-selected cycle (±3 days)
            if any(abs(period - p) < 3.0 for p in used_periods):
                continue

            dominant_cycles.append({
                "period_days": round(period, 1),
                "power":       round(float(masked_power[idx]), 4),
                "freq_idx":    int(idx),
            })
            used_periods.append(period)

            if len(dominant_cycles) >= TOP_N_CYCLES:
                break

        if not dominant_cycles:
            return {"error": "No dominant cycles found", "symbol": symbol}

        # ── 6. Reconstruct primary cycle wave ────────────────────────────────
        primary = dominant_cycles[0]
        fi      = primary["freq_idx"]

        # Zero everything except the primary frequency + its conjugate pair
        fft_filtered = np.zeros(n, dtype=complex)
        fft_filtered[fi]     = fft_vals[fi]
        fft_filtered[n - fi] = fft_vals[n - fi]
        cycle_wave = np.real(np.fft.ifft(fft_filtered))

        # Normalise to [0, 1]: 0 = trough, 1 = peak
        c_min, c_max = cycle_wave.min(), cycle_wave.max()
        c_range      = c_max - c_min
        if c_range > 1e-10:
            norm_wave = (cycle_wave - c_min) / c_range
        else:
            norm_wave = np.full(n, 0.5)

        current_pos  = float(norm_wave[-1])
        prev_pos     = float(norm_wave[-2]) if n >= 2 else current_pos
        is_rising    = current_pos > prev_pos

        # Phase label
        phase_label = _phase_label(current_pos, is_rising)

        # ── 7. Signal logic ──────────────────────────────────────────────────
        if is_rising and current_pos <= 0.65:
            signal = "BULLISH"    # trough → mid peak: buying zone
        elif not is_rising and current_pos >= 0.35:
            signal = "BEARISH"    # peak → mid trough: selling zone
        else:
            signal = "NEUTRAL"    # ambiguous transition

        # Enrich primary cycle dict
        primary_out = {
            "period_days":  primary["period_days"],
            "power":        primary["power"],
            "cycle_pos":    round(current_pos, 3),
            "phase_label":  phase_label,
            "is_rising":    is_rising,
            "signal":       signal,
        }

        detail = (
            f"Primary: {primary['period_days']}d cycle | "
            f"Phase: {phase_label} ({current_pos:.0%}) | "
            f"Dir: {'↑' if is_rising else '↓'} | "
            f"Cycles: {', '.join(str(c['period_days']) + 'd' for c in dominant_cycles)}"
        )

        return {
            "symbol":          symbol,
            "dominant_cycles": dominant_cycles,
            "primary_cycle":   primary_out,
            "cycle_position":  round(current_pos, 3),
            "is_rising":       is_rising,
            "signal":          signal,
            "phase_label":     phase_label,
            "detail":          detail,
            "n_candles":       n,
            "as_of":           datetime.now(timezone.utc).isoformat() + "Z",
        }

    except Exception as e:
        return {"error": str(e), "symbol": symbol}


def _phase_label(pos: float, rising: bool) -> str:
    """Human-readable cycle phase label."""
    if pos < 0.20:
        return "TROUGH"
    if pos < 0.40:
        return "EARLY-UP" if rising else "NEAR-TROUGH"
    if pos < 0.60:
        return "MID-CYCLE"
    if pos < 0.80:
        return "LATE-UP" if rising else "EARLY-DOWN"
    return "PEAK" if rising else "LATE-DOWN"


# ─── Consensus node ───────────────────────────────────────────────────────────

def node_fft_cycle(symbol: str, prices: np.ndarray | None = None) -> tuple[int | None, str]:
    """
    10th consensus node: FFT Cycle Phase.

    Returns
    -------
    (+1, detail)  BULLISH  — cycle in accumulation / upswing
    ( 0, detail)  NEUTRAL  — uncertain phase or transition
    (-1, detail)  BEARISH  — cycle at peak / downswing
    (None, msg)   ABSTAIN  — data error
    """
    result = detect_fft_cycles(symbol, prices=prices)
    if result.get("error"):
        return None, result["error"]

    signal = result.get("signal", "NEUTRAL")
    detail = result.get("detail", "")

    if signal == "BULLISH":
        return 1, detail
    if signal == "BEARISH":
        return -1, detail
    return 0, detail


# ─── Batch entry point ───────────────────────────────────────────────────────

def batch_fft_cycles(symbols: list[str]) -> dict[str, Any]:
    """Run FFT cycle detection for a list of symbols."""
    cycles: dict[str, Any] = {}
    for sym in symbols:
        cycles[sym] = detect_fft_cycles(sym)
    return {
        "cycles":  cycles,
        "as_of":   datetime.now(timezone.utc).isoformat() + "Z",
        "symbols": symbols,
    }
