"""Momentum buy/sell signal — RSI + MACD + Volume, combined (TradingView-style).

A standalone per-ticker momentum read for the Analyze view: three classic momentum
indicators each cast a −1/0/+1 vote, summed into one BUY / SELL / NEUTRAL with a
plain-English rationale. Pure price math on daily OHLCV (via price_source); never
raises — returns {"available": False, ...} when data is thin.

Interpretation is trend-following (the standard momentum read):
  * RSI  > 55 bullish / < 45 bearish  (flags > 75 overbought, < 25 oversold)
  * MACD line above its signal + rising histogram = bullish, and vice-versa
  * Volume confirms: an above-average-volume move in the recent direction adds weight
"""
from __future__ import annotations

import numpy as np


def _ema(x: np.ndarray, span: int) -> np.ndarray:
    alpha = 2.0 / (span + 1.0)
    out = np.empty(len(x), dtype=float)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = alpha * x[i] + (1.0 - alpha) * out[i - 1]
    return out


def _rsi(close: np.ndarray, period: int = 14) -> float:
    if len(close) < period + 1:
        return 50.0
    d = np.diff(close)
    gains = np.where(d > 0, d, 0.0)
    losses = np.where(d < 0, -d, 0.0)
    ag = np.convolve(gains, np.ones(period) / period, "valid")[-1]
    al = np.convolve(losses, np.ones(period) / period, "valid")[-1]
    if al == 0:
        return 100.0 if ag > 0 else 50.0
    return float(100.0 - 100.0 / (1.0 + ag / al))


def _macd(close: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9):
    macd_line = _ema(close, fast) - _ema(close, slow)
    signal_line = _ema(macd_line, signal)
    return float(macd_line[-1]), float(signal_line[-1]), float(macd_line[-1] - signal_line[-1])


def compute(symbol: str, close=None, volume=None) -> dict:
    """Combined RSI+MACD+Volume momentum signal for `symbol`. Fetches 6mo daily
    OHLCV via price_source when close/volume aren't supplied. Never raises."""
    try:
        if close is None or volume is None:
            import price_source
            raw = price_source.yf_download(symbol, period="6mo", interval="1d",
                                           auto_adjust=True, progress=False)
            if raw is None or len(raw) < 35:
                return {"available": False, "reason": "insufficient price data"}
            close = raw["Close"].squeeze().dropna().values.astype(float)
            volume = raw["Volume"].squeeze().dropna().values.astype(float)
        close = np.asarray(close, dtype=float)
        volume = np.asarray(volume, dtype=float)
        if len(close) < 35:
            return {"available": False, "reason": "insufficient price data"}

        rsi = _rsi(close)
        macd_line, macd_sig, hist = _macd(close)
        vol20 = float(np.mean(volume[-20:])) if len(volume) >= 20 else float(np.mean(volume))
        vol_ratio = float(volume[-1] / vol20) if vol20 > 0 else 1.0
        recent_dir = 1 if close[-1] >= close[-6] else -1     # 5-day price direction

        # ── each indicator votes −1 / 0 / +1 ──────────────────────────────────
        rsi_vote = 1 if rsi > 55 else (-1 if rsi < 45 else 0)
        macd_vote = 1 if (macd_line > macd_sig and hist > 0) else (
            -1 if (macd_line < macd_sig and hist < 0) else 0)
        # volume only votes when it's confirming (elevated), in the recent direction
        vol_vote = recent_dir if vol_ratio >= 1.1 else 0

        score = rsi_vote + macd_vote + vol_vote
        signal = "BUY" if score >= 2 else ("SELL" if score <= -2 else "NEUTRAL")

        def _word(v: int) -> str:
            return "bullish" if v > 0 else ("bearish" if v < 0 else "neutral")

        parts = [
            f"RSI {rsi:.0f} ({_word(rsi_vote)}"
            + (" · overbought" if rsi > 75 else " · oversold" if rsi < 25 else "") + ")",
            f"MACD {'above' if macd_line > macd_sig else 'below'} signal ({_word(macd_vote)})",
            (f"volume {vol_ratio:.1f}× avg confirming" if vol_vote
             else f"volume {vol_ratio:.1f}× avg (no confirmation)"),
        ]
        return {
            "available": True, "symbol": symbol.upper(), "signal": signal, "score": int(score),
            "rsi": round(rsi, 1), "macd": round(macd_line, 3), "macd_signal": round(macd_sig, 3),
            "macd_hist": round(hist, 3), "volume_ratio": round(vol_ratio, 2),
            "components": {"rsi": rsi_vote, "macd": macd_vote, "volume": vol_vote},
            "rationale": " · ".join(parts) + f" → {signal}",
        }
    except Exception as e:  # noqa: BLE001 — momentum is best-effort, never breaks the page
        return {"available": False, "reason": f"{type(e).__name__}"}
