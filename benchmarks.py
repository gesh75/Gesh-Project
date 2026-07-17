"""
benchmarks.py — Custom Thematic Benchmarking

Maps each holding to its closest sector ETF peer instead of only comparing
to SPY.  Weekly reviews and daily reports get a 3-column alpha table:

  vs_market  (stock return − SPY return)
  vs_sector  (stock return − sector ETF return)
  abs_return (absolute stock return)

This instantly shows whether NVDA is beating the broader semis index or
just riding the sector tide.

Key functions
─────────────
  get_alpha(ticker, period)      → {vs_market, vs_sector, sector_etf, ...}
  batch_alpha(tickers, period)   → list of alpha dicts, sorted by vs_market
  alpha_summary_text(tickers)    → compact string for Claude prompt injection
  get_sector_benchmark(ticker)   → sector ETF ticker string
"""

from __future__ import annotations

from typing import Any
import numpy as np
import yfinance as yf


import price_source  # locked yf.download adapter (thread-safety)
# ─── Benchmark mapping ────────────────────────────────────────────────────────

SECTOR_BENCHMARKS: dict[str, str] = {
    # Technology / Semis
    "NVDA": "SMH", "AMD":  "SMH", "INTC": "SMH", "QCOM": "SMH", "AVGO": "SMH",
    "AAPL": "XLK", "MSFT": "XLK", "GOOGL": "XLK", "META": "XLK", "AMZN": "XLK",
    "PLTR": "IGV", "CRM": "IGV", "DDOG": "IGV", "SNOW": "IGV", "NET":  "IGV",
    # Crypto / Alt-Asset
    "COIN": "BITQ", "MSTR": "BITQ", "HOOD": "BITQ", "MARA": "BITQ",
    "IONQ": "QTUM",
    # Finance
    "SOFI": "XLF", "GS":   "XLF", "JPM":  "XLF", "BAC":  "XLF",
    # EV / Auto
    "TSLA": "KARS", "RIVN": "KARS", "NIO":  "KARS",
    # Healthcare
    "LLY":  "XLV",  "UNH":  "XLV",  "ABBV": "XLV",
    # Energy
    "XOM":  "XLE",  "CVX":  "XLE",
    # Consumer
    "COST": "XLY",  "WMT":  "XLP",  "AMZN": "XLY",
    # Broad market ETFs (benchmark against SPY)
    "SPY":  "SPY",  "QQQ":  "SPY",  "IWM":  "SPY",
    "DEFAULT": "SPY",
}


def get_sector_benchmark(ticker: str) -> str:
    """Return the best sector ETF benchmark for a ticker."""
    return SECTOR_BENCHMARKS.get(ticker.upper(), SECTOR_BENCHMARKS["DEFAULT"])


def get_alpha(ticker: str, period: str = "1mo") -> dict[str, Any]:
    """
    Compute alpha for a single ticker vs its sector ETF and SPY.

    Parameters
    ----------
    ticker : stock symbol
    period : yfinance period string — '1mo', '3mo', '1y', '1wk', etc.

    Returns
    -------
    {
        ticker, sector_etf, period,
        stock_return_pct,
        spy_return_pct,
        sector_return_pct,
        vs_market_pct,   (stock − SPY)
        vs_sector_pct,   (stock − sector ETF)
        alpha_grade,     (A/B/C/D/F)
        summary,         (human-readable string)
    }
    """
    ticker = ticker.upper()
    sector_etf = get_sector_benchmark(ticker)
    peers = list({ticker, "SPY", sector_etf})

    try:
        data = price_source.locked_yf_download(peers, period=period, auto_adjust=True, progress=False)["Close"]
        if isinstance(data, yf.Series if hasattr(yf, "Series") else type(None)):
            data = data.to_frame(name=peers[0])

        def _period_return(sym: str) -> float:
            if sym not in data.columns:
                return 0.0
            s = data[sym].dropna()
            if len(s) < 2:
                return 0.0
            return float((s.iloc[-1] / s.iloc[0] - 1) * 100)

        stock_ret  = _period_return(ticker)
        spy_ret    = _period_return("SPY")
        sector_ret = _period_return(sector_etf)

    except Exception as exc:
        return {"ticker": ticker, "error": str(exc)}

    vs_market = stock_ret - spy_ret
    vs_sector = stock_ret - sector_ret

    # Simple alpha grade
    score = vs_market * 0.5 + vs_sector * 0.5
    if score >= 5:    grade = "A"
    elif score >= 2:  grade = "B"
    elif score >= 0:  grade = "C"
    elif score >= -3: grade = "D"
    else:             grade = "F"

    summary = (
        f"{ticker} {period}: {stock_ret:+.1f}% | "
        f"vs SPY {vs_market:+.1f}% | "
        f"vs {sector_etf} {vs_sector:+.1f}% | "
        f"Grade {grade}"
    )

    return {
        "ticker":           ticker,
        "sector_etf":       sector_etf,
        "period":           period,
        "stock_return_pct": round(stock_ret,  2),
        "spy_return_pct":   round(spy_ret,    2),
        "sector_return_pct":round(sector_ret, 2),
        "vs_market_pct":    round(vs_market,  2),
        "vs_sector_pct":    round(vs_sector,  2),
        "alpha_grade":      grade,
        "summary":          summary,
    }


def batch_alpha(
    tickers: list[str],
    period: str = "1mo",
) -> list[dict[str, Any]]:
    """
    Compute alpha for multiple tickers in one yfinance batch call.
    Returns list sorted by vs_market descending (best alpha first).
    """
    if not tickers:
        return []

    tickers_upper = [t.upper() for t in tickers]
    sector_map    = {t: get_sector_benchmark(t) for t in tickers_upper}
    all_symbols   = list({s for s in sector_map.values()} | set(tickers_upper) | {"SPY"})

    try:
        data = price_source.locked_yf_download(all_symbols, period=period, auto_adjust=True, progress=False)["Close"]
    except Exception:
        data = None

    def _ret(sym: str) -> float:
        if data is None or not hasattr(data, "columns") or sym not in data.columns:
            return 0.0
        s = data[sym].dropna()
        return float((s.iloc[-1] / s.iloc[0] - 1) * 100) if len(s) >= 2 else 0.0

    spy_ret = _ret("SPY")
    results: list[dict] = []
    for ticker in tickers_upper:
        etf        = sector_map[ticker]
        stock_ret  = _ret(ticker)
        sector_ret = _ret(etf)
        vs_market  = stock_ret - spy_ret
        vs_sector  = stock_ret - sector_ret
        score = vs_market * 0.5 + vs_sector * 0.5
        grade = "A" if score >= 5 else "B" if score >= 2 else "C" if score >= 0 else "D" if score >= -3 else "F"
        results.append({
            "ticker":            ticker,
            "sector_etf":        etf,
            "period":            period,
            "stock_return_pct":  round(stock_ret,  2),
            "spy_return_pct":    round(spy_ret,    2),
            "sector_return_pct": round(sector_ret, 2),
            "vs_market_pct":     round(vs_market,  2),
            "vs_sector_pct":     round(vs_sector,  2),
            "alpha_grade":       grade,
        })

    return sorted(results, key=lambda x: x["vs_market_pct"], reverse=True)


def alpha_summary_text(tickers: list[str], period: str = "1mo") -> str:
    """Compact multi-line alpha table for Claude prompt injection."""
    rows = batch_alpha(tickers, period)
    if not rows:
        return "Alpha benchmarking unavailable."
    lines = [f"Alpha vs Market & Sector ({period}):"]
    lines.append(f"  {'Ticker':<7} {'vs SPY':>8} {'vs Sector':>10} {'Grade':>6}")
    lines.append("  " + "─" * 33)
    for r in rows:
        lines.append(
            f"  {r['ticker']:<7} {r['vs_market_pct']:>+7.1f}%"
            f" {r['vs_sector_pct']:>+9.1f}%"
            f" {r['alpha_grade']:>6}"
        )
    return "\n".join(lines)


# ─── CLI test ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    test_tickers = ["NVDA", "MSTR", "PLTR", "GS", "TSLA"]
    print(alpha_summary_text(test_tickers, "1mo"))
