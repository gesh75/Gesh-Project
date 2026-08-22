"""
crypto_correlation.py — Crypto-Equity Correlation Node (Node 12)

Tracks how equity holdings move in relation to Bitcoin and Ethereum.
In 2026, crypto/equity correlations have become a meaningful portfolio
risk factor — high correlation means your "diversified" portfolio may
actually move as a single block.

Also fixes the ECG node import error by providing a safe wrapper.

Key functions
─────────────
  get_crypto_correlations(symbols, lookback_days)
      → {BTC: {NVDA: corr, MSTR: corr, ...}, ETH: {...}}

  node_crypto_correlation(symbol, lookback_days)
      → (vote, detail_string)   — drop-in consensus node signature

  crypto_risk_summary(holdings, equity_summary)
      → {avg_btc_corr, risk_label, highest_corr_symbol, summary_text}

  node_ecg_safe(symbol)
      → (vote | None, detail)   — ECG with guaranteed safe import handling
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import price_source  # locked yf.download adapter (thread-safety)
from datetime import datetime, timedelta


# ─── Config ───────────────────────────────────────────────────────────────────

CRYPTO_PROXIES: list[str] = ["BTC-USD", "ETH-USD"]
DEFAULT_LOOKBACK: int = 90   # calendar days

# Vote thresholds
HIGH_CORR_THRESHOLD  = 0.70   # → BEARISH signal (corr too high = systemic risk)
LOW_CORR_THRESHOLD   = 0.25   # → BULLISH signal (decorrelated = true diversification)
ABSTAIN_THRESHOLD    = 0.45   # → NEUTRAL


# ─── Data fetching ────────────────────────────────────────────────────────────

def _fetch_returns(
    symbols: list[str],
    lookback_days: int = DEFAULT_LOOKBACK,
) -> pd.DataFrame:
    """Fetch log returns for symbols + BTC + ETH in one yfinance call."""
    all_syms = list(set(symbols) | set(CRYPTO_PROXIES))
    end   = datetime.today()
    start = end - timedelta(days=lookback_days + 10)
    try:
        data = price_source.locked_yf_download(
            all_syms,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            auto_adjust=True,
            progress=False,
        )["Close"]
        if not isinstance(data, pd.DataFrame):
            data = data.to_frame()
        log_ret = np.log(data / data.shift(1)).dropna(how="all")
        return log_ret
    except Exception:
        return pd.DataFrame()


def get_crypto_correlations(
    symbols: list[str],
    lookback_days: int = DEFAULT_LOOKBACK,
) -> dict[str, dict[str, float]]:
    """
    Compute rolling Pearson correlation of each equity vs BTC and ETH.

    Returns
    -------
    {
        "BTC-USD": {"NVDA": 0.62, "MSTR": 0.91, ...},
        "ETH-USD": {"NVDA": 0.55, "MSTR": 0.88, ...},
    }
    """
    log_ret = _fetch_returns(symbols, lookback_days)
    if log_ret.empty:
        return {"BTC-USD": {}, "ETH-USD": {}}

    result: dict[str, dict[str, float]] = {}
    for crypto in CRYPTO_PROXIES:
        if crypto not in log_ret.columns:
            result[crypto] = {}
            continue
        corrs: dict[str, float] = {}
        for sym in symbols:
            if sym not in log_ret.columns:
                continue
            pair = log_ret[[crypto, sym]].dropna()
            if len(pair) < 20:
                continue
            corr = float(pair.corr().iloc[0, 1])
            if not np.isnan(corr):
                corrs[sym] = round(corr, 3)
        result[crypto] = corrs
    return result


# ─── Consensus node ───────────────────────────────────────────────────────────

BULLISH  =  1
NEUTRAL  =  0
BEARISH  = -1
ABSTAIN  = None


def node_crypto_correlation(
    symbol: str,
    lookback_days: int = DEFAULT_LOOKBACK,
) -> tuple[int | None, str]:
    """
    Consensus node signature: returns (vote, detail_string).

    Logic:
      High BTC correlation (>0.70)  → BEARISH  (systemic crypto risk leaks in)
      Low BTC correlation  (<0.25)  → BULLISH  (true diversification signal)
      Moderate / no data            → NEUTRAL
    """
    try:
        corrs = get_crypto_correlations([symbol], lookback_days)
        btc_corr = corrs.get("BTC-USD", {}).get(symbol)
        eth_corr = corrs.get("ETH-USD", {}).get(symbol)

        if btc_corr is None:
            return ABSTAIN, f"CryptoCorr: no data for {symbol}"

        eth_str = f" ETH={eth_corr:+.2f}" if eth_corr is not None else ""
        detail  = f"CryptoCorr({lookback_days}d): BTC={btc_corr:+.2f}{eth_str}"

        if btc_corr >= HIGH_CORR_THRESHOLD:
            return BEARISH, f"{detail} → HIGH crypto overlap (systemic risk)"
        if btc_corr <= LOW_CORR_THRESHOLD:
            return BULLISH, f"{detail} → LOW crypto overlap (diversified)"
        return NEUTRAL, f"{detail} → moderate crypto overlap"

    except Exception as exc:
        return ABSTAIN, f"CryptoCorr error: {exc}"


# ─── Portfolio-level risk summary ─────────────────────────────────────────────

def _holding_equity(h: dict) -> float:
    for key in ("equity", "market_value", "value", "marketValue"):
        try:
            v = float(h.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if v > 0:
            return v
    return 0.0


def crypto_risk_summary(
    holdings: list[dict],
    lookback_days: int = DEFAULT_LOOKBACK,
) -> dict[str, Any]:
    """
    Portfolio-level crypto correlation risk summary for dashboard injection.

    avg_btc_corr is equity-weighted (GFD-026). Unweighted mean is still
    returned as unweighted_avg_btc_corr so a concentration case is visible.

    Returns
    -------
    {
        avg_btc_corr, unweighted_avg_btc_corr, weighting,
        max_btc_corr, highest_corr_symbol,
        risk_label, correlations, summary_text
    }
    """
    symbols = [h["symbol"] for h in holdings]
    if not symbols:
        return {"summary_text": "No holdings to analyse.", "risk_label": "UNKNOWN"}

    corrs = get_crypto_correlations(symbols, lookback_days)
    btc_map = corrs.get("BTC-USD", {})
    eth_map = corrs.get("ETH-USD", {})

    if not btc_map:
        return {"summary_text": "Crypto correlation data unavailable.", "risk_label": "UNKNOWN"}

    equity_by_sym = {h["symbol"]: _holding_equity(h) for h in holdings}
    pairs = [(sym, corr, equity_by_sym.get(sym, 0.0)) for sym, corr in btc_map.items()]
    values = np.array([p[1] for p in pairs], dtype=float)
    weights = np.array([p[2] for p in pairs], dtype=float)
    unweighted = float(np.mean(values))
    if float(weights.sum()) > 0:
        avg_btc = float(np.average(values, weights=weights))
        weighting = "equity"
    else:
        avg_btc = unweighted
        weighting = "equal-fallback"

    max_btc = max(btc_map.values())
    top_sym = max(btc_map, key=btc_map.__getitem__)

    if avg_btc >= 0.65:    risk_label = "HIGH — portfolio moves with crypto"
    elif avg_btc >= 0.40:  risk_label = "MODERATE — partial crypto overlap"
    else:                   risk_label = "LOW — mostly decorrelated from crypto"

    detail_lines = [
        f"  {sym}: BTC={btc_map.get(sym, 0):+.2f}  ETH={eth_map.get(sym, 0):+.2f}"
        for sym in symbols if sym in btc_map
    ]

    summary = (
        f"Crypto-Equity Correlation ({lookback_days}d): "
        f"avg BTC corr={avg_btc:+.2f} (equity-weighted) → {risk_label}. "
        f"Highest: {top_sym} ({max_btc:+.2f})."
    )

    return {
        "avg_btc_corr":              round(avg_btc, 3),
        "unweighted_avg_btc_corr":   round(unweighted, 3),
        "weighting":                 weighting,
        "max_btc_corr":              round(max_btc, 3),
        "highest_corr_symbol":       top_sym,
        "risk_label":                risk_label,
        "correlations":              {sym: {"btc": btc_map.get(sym), "eth": eth_map.get(sym)} for sym in symbols},
        "summary_text":              summary,
        "detail_lines":              detail_lines,
    }


# ─── Safe ECG wrapper ─────────────────────────────────────────────────────────

def node_ecg_safe(symbol: str) -> tuple[int | None, str]:
    """
    Drop-in replacement for node_ecg() in consensus.py.

    Wraps the ECG import in a try/except so a broken ecg.py never
    crashes a consensus run — it just returns ABSTAIN with an error note.
    This fixes the known '_drawdown' import error documented in CLAUDE.md.
    """
    try:
        from ecg import _compute_drawdown_from_peak, _loss_streak, _classify
        from db import get_equity_history
        import sqlite3

        history = get_equity_history(symbol, days=30)
        if not history or len(history) < 5:
            return ABSTAIN, "ECG: insufficient equity history"
        eq_list = [row["equity"] for row in history]
        dd      = _compute_drawdown_from_peak(eq_list)
        streak  = _loss_streak(eq_list)
        status  = _classify(dd, streak)
        vote    = BEARISH if status == "SUSPENDED" else (NEUTRAL if status == "CAUTION" else BULLISH)
        return vote, f"ECG={status} DD={dd:.1f}% Streak={streak}"
    except Exception as exc:
        return ABSTAIN, f"ECG unavailable: {exc}"


# ─── CLI test ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    test_syms = ["NVDA", "MSTR", "COIN", "GS", "TSLA"]
    summary = crypto_risk_summary([{"symbol": s} for s in test_syms])
    print(summary["summary_text"])
    for line in summary["detail_lines"]:
        print(line)
