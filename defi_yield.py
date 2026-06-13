"""
defi_yield.py — DeFi & Cash Yield Optimization (Phase 3)

Evaluates idle cash balances against available yield-bearing instruments
and recommends the optimal cash allocation strategy.

Instruments evaluated
─────────────────────
  Tier 1 — Traditional (low risk):
    • HYSA (High-Yield Savings)   — approximated via current Fed Funds Rate - 0.5%
    • 3-Month T-Bills (^IRX)      — fetched live from yfinance
    • Money Market (VMFXX proxy)  — Fed Funds Rate - 0.25%

  Tier 2 — Semi-traditional (low-medium risk):
    • SHY  (1-3yr Treasury ETF)   — live yield from yfinance
    • SGOV (0-3mo T-Bill ETF)     — live yield from yfinance
    • BIL  (0-3mo T-Bill ETF)     — live yield from yfinance

  Tier 3 — DeFi stablecoins (medium risk — informational only):
    • USDC on-chain yield         — fetched from DeFi Llama API (free)
    • USDT yield                  — fetched from DeFi Llama API (free)
    • Aave USDC                   — fetched from DeFi Llama API

NOTE: DeFi yields are INFORMATIONAL ONLY.  This module NEVER executes
DeFi transactions.  Robinhood cash stays in Robinhood unless the user
explicitly moves it.  This is a rate comparison tool, not a DeFi gateway.

Key functions
─────────────
  get_yield_landscape(cash_usd)      → {tiers, recommendation, monthly_projection}
  get_tbill_rate()                   → current 3-month T-bill yield %
  get_defi_yields()                  → {USDC: apy, USDT: apy, AAVE_USDC: apy}
  yield_summary_for_claude(cash_usd) → compact string for prompt injection
"""

from __future__ import annotations

import json
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Any

import yfinance as yf


# ─── Config ───────────────────────────────────────────────────────────────────

# DeFi Llama pool IDs (USDC/USDT major pools — free, no API key)
DEFI_LLAMA_POOLS: dict[str, str] = {
    "USDC_AAVE_V3":   "aa54d84a-6e42-4e70-9b14-1e2a9c2fe1b8",  # Aave v3 USDC
    "USDT_AAVE_V3":   "b4c84a56-2e44-46ba-8945-6f0cd5ac2000",  # Aave v3 USDT
    "USDC_COMPOUND":  "0f45d730-b9cf-4901-b50a-439b5401e2d1",  # Compound USDC
}
DEFI_LLAMA_URL = "https://yields.llama.fi/pool/{pool_id}"

RISK_LABEL: dict[str, str] = {
    "tier1": "Very Low — FDIC/Gov backed",
    "tier2": "Low — ETF, tradable",
    "tier3": "Medium — Smart contract risk (informational only)",
}


# ─── Live rate fetchers ───────────────────────────────────────────────────────

def get_tbill_rate() -> float:
    """Fetch current 3-month T-bill yield from yfinance (^IRX, in %)."""
    try:
        ticker = yf.Ticker("^IRX")
        hist   = ticker.history(period="5d")
        if not hist.empty:
            return round(float(hist["Close"].iloc[-1]), 3)
    except Exception:
        pass
    return 5.25   # fallback to approximate current rate


def get_fed_funds_rate() -> float:
    """Approximate Fed Funds Rate via ^IRX or FRED fallback."""
    try:
        req = urllib.request.Request(
            "https://api.stlouisfed.org/fred/series/observations"
            "?series_id=DFF&sort_order=desc&limit=1&file_type=json",
            headers={"User-Agent": "GESH-Financial/1.0"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            return float(data["observations"][0]["value"])
    except Exception:
        return get_tbill_rate() + 0.25


def get_etf_yield(ticker_sym: str) -> float:
    """Estimate yield for a short-duration bond ETF from its 30-day SEC yield proxy."""
    try:
        t    = yf.Ticker(ticker_sym)
        info = t.info
        # Try dividendYield field; annualise if needed
        dy = info.get("dividendYield") or info.get("yield") or 0.0
        return round(float(dy) * 100, 3) if dy < 1 else round(float(dy), 3)
    except Exception:
        return 0.0


def get_defi_yields() -> dict[str, Any]:
    """
    Fetch DeFi stablecoin yields from DeFi Llama (free, no API key).
    Returns best APY per protocol.
    """
    results: dict[str, Any] = {}
    for name, pool_id in DEFI_LLAMA_POOLS.items():
        try:
            url = DEFI_LLAMA_URL.format(pool_id=pool_id)
            req = urllib.request.Request(url, headers={"User-Agent": "GESH-Financial/1.0"})
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read())
                pool = data.get("data", {})
                results[name] = {
                    "apy":       round(float(pool.get("apy", 0)), 2),
                    "tvl_usd":   pool.get("tvlUsd", 0),
                    "chain":     pool.get("chain", "?"),
                    "symbol":    pool.get("symbol", name),
                    "risk_note": "Smart contract risk — informational only",
                }
        except Exception:
            results[name] = {"apy": 0.0, "error": "unavailable"}
    return results


# ─── Main landscape ───────────────────────────────────────────────────────────

def get_yield_landscape(cash_usd: float = 0.0) -> dict[str, Any]:
    """
    Full yield landscape for idle cash.

    Returns
    -------
    {
        cash_usd,
        tbill_rate, fed_rate,
        tiers: {tier1: [...], tier2: [...], tier3: [...]},
        recommendation: {instrument, apy, monthly_income_usd, reason},
        monthly_projection: [...],
        generated_at,
        disclaimer,
    }
    """
    tbill   = get_tbill_rate()
    fed     = get_fed_funds_rate()
    defi    = get_defi_yields()

    # Tier 1 — traditional
    tier1 = [
        {"name": "3-Month T-Bill (^IRX)", "apy": tbill,             "risk": RISK_LABEL["tier1"]},
        {"name": "HYSA (est.)",            "apy": max(fed - 0.5, 0), "risk": RISK_LABEL["tier1"]},
        {"name": "Money Market (est.)",    "apy": max(fed - 0.25, 0),"risk": RISK_LABEL["tier1"]},
    ]

    # Tier 2 — short bond ETFs
    sgov_y = get_etf_yield("SGOV") or tbill * 0.98
    bil_y  = get_etf_yield("BIL")  or tbill * 0.97
    shy_y  = get_etf_yield("SHY")  or tbill * 0.92
    tier2 = [
        {"name": "SGOV (0-3mo T-Bill ETF)", "apy": sgov_y, "risk": RISK_LABEL["tier2"]},
        {"name": "BIL  (0-3mo T-Bill ETF)", "apy": bil_y,  "risk": RISK_LABEL["tier2"]},
        {"name": "SHY  (1-3yr Tsy ETF)",    "apy": shy_y,  "risk": RISK_LABEL["tier2"]},
    ]

    # Tier 3 — DeFi (informational only)
    tier3 = []
    for name, info in defi.items():
        tier3.append({
            "name":      name,
            "apy":       info.get("apy", 0.0),
            "tvl_usd":   info.get("tvl_usd", 0),
            "chain":     info.get("chain", "?"),
            "risk":      RISK_LABEL["tier3"],
            "note":      info.get("risk_note", ""),
        })

    # Recommendation — best Tier 1/2 option (never recommend DeFi as primary)
    all_safe = sorted(tier1 + tier2, key=lambda x: x["apy"], reverse=True)
    best = all_safe[0] if all_safe else {"name": "T-Bill", "apy": tbill}
    monthly_income = round(cash_usd * best["apy"] / 100 / 12, 2) if cash_usd > 0 else 0.0

    recommendation = {
        "instrument":        best["name"],
        "apy":               best["apy"],
        "monthly_income_usd":monthly_income,
        "annual_income_usd": round(monthly_income * 12, 2),
        "reason": (
            f"{best['name']} currently offers {best['apy']:.2f}% APY — "
            f"the highest risk-adjusted yield on idle cash."
        ),
    }

    # Monthly projection table (6 months)
    projection = []
    for mo in range(1, 7):
        projection.append({
            "month":      mo,
            "income_usd": round(cash_usd * best["apy"] / 100 / 12 * mo, 2),
            "cumulative": round(cash_usd * (1 + best["apy"] / 100 / 12) ** mo - cash_usd, 2),
        })

    return {
        "cash_usd":         round(cash_usd, 2),
        "tbill_rate_pct":   tbill,
        "fed_rate_pct":     fed,
        "tiers":            {"tier1": tier1, "tier2": tier2, "tier3": tier3},
        "recommendation":   recommendation,
        "monthly_projection":projection,
        "generated_at":     datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        "disclaimer":       (
            "DeFi yields (Tier 3) are INFORMATIONAL ONLY. "
            "GESH Financial does not execute DeFi transactions. "
            "Past yield rates do not guarantee future returns. "
            "NOT FINANCIAL ADVICE."
        ),
    }


def yield_summary_for_claude(cash_usd: float) -> str:
    """Compact summary for Claude prompt injection."""
    if cash_usd < 100:
        return "Cash yield: insufficient cash balance for yield evaluation."
    try:
        data = get_yield_landscape(cash_usd)
        rec  = data["recommendation"]
        return (
            f"Cash Yield Opportunity (${cash_usd:,.0f} idle): "
            f"Best rate = {rec['apy']:.2f}% via {rec['instrument']}. "
            f"Monthly income = ${rec['monthly_income_usd']:,.0f}. "
            f"Annual = ${rec['annual_income_usd']:,.0f}. "
            f"{rec['reason']}"
        )
    except Exception as exc:
        return f"Cash yield analysis unavailable: {exc}"


# ─── CLI test ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    landscape = get_yield_landscape(cash_usd=5000.0)
    print(f"T-Bill: {landscape['tbill_rate_pct']:.2f}%  Fed: {landscape['fed_rate_pct']:.2f}%")
    rec = landscape["recommendation"]
    print(f"Best option: {rec['instrument']} @ {rec['apy']:.2f}% → "
          f"${rec['monthly_income_usd']:.2f}/mo")
    print("\nDeFi (informational):")
    for t3 in landscape["tiers"]["tier3"]:
        print(f"  {t3['name']}: {t3['apy']:.2f}% APY")
