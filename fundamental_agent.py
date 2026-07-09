"""
fundamental_agent.py — Fundamental Momentum Detector

Weekly job: scans a universe of stocks for companies where fundamentals
are IMPROVING but price has NOT yet moved — the "before the market knows" edge.

Scoring criteria:
  - Revenue growth accelerating (QoQ + YoY)
  - Margins expanding
  - Debt decreasing / cash increasing
  - Earnings revisions positive (analysts raising estimates)
  - Insider buying (recent purchases)
  - Short interest decreasing (squeeze potential)
  - P/E below sector average (undervalued vs growth)

Companies scoring highest = best asymmetric opportunities.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

import anthropic
import numpy as np
import pandas as pd
import yfinance as yf
from dotenv import load_dotenv

load_dotenv()

# Extended screening universe for fundamental analysis
FUNDAMENTAL_UNIVERSE: list[str] = [
    # High-growth tech
    "NVDA","META","GOOGL","MSFT","AAPL","AMZN","AVGO","ORCL","CRM","ADBE",
    "AMD","PLTR","SNOW","NET","DDOG","CRWD","PANW","MDB","HUBS","ARM",
    # Finance / value
    "JPM","GS","BAC","V","MA","AXP","BLK","SCHW","COF","MS",
    # Healthcare / biotech
    "LLY","UNH","ABBV","ISRG","TMO","REGN","DXCM","MRNA","GEHC","CI",
    # Energy
    "XOM","CVX","COP","EOG","OXY","SLB","MPC","HAL","DVN","BKR",
    # Consumer
    "COST","WMT","HD","MCD","NKE","BKNG","MAR","CMG","SBUX","LULU",
    # Industrials / infra
    "CAT","DE","GE","RTX","HON","LMT","NOC","BA","UPS","FDX",
    # Emerging / speculative
    "RDDT","HOOD","SOFI","AFRM","UPST","IONQ","RXRX","SOUN","ASTS","RKLB",
    # Small/mid value
    "SMCI","DELL","HPE","WDC","STX","PSTG","NTAP","NTNX","ZM","DOCU",
]

FUNDAMENTAL_PROMPT = """You are a fundamental equity analyst. Analyze these stocks and identify the TOP 15 with the best IMPROVING fundamentals where price has NOT yet caught up.

Focus on "before the market knows" opportunities:
- Revenue growth ACCELERATING (not just positive — getting better each quarter)
- Margins EXPANDING (gross margin or operating margin improving)
- Balance sheet STRENGTHENING (cash up, debt down)
- Analyst estimates being RAISED (positive revisions)
- Insider BUYING (not selling)
- Reasonable valuation for the growth rate (PEG < 1.5 ideal)

Return ONLY valid JSON. No markdown. Double-quotes only. No apostrophes.

{
  "scan_date": "<today's date>",
  "market_summary": "<2-3 sentence macro context affecting fundamentals>",
  "top_opportunities": [
    {
      "symbol": "<TICKER>",
      "company_name": "<name>",
      "sector": "<sector>",
      "fundamental_score": <0-100>,
      "momentum_gap": "<HIGH|MEDIUM|LOW — how much price lags behind fundamentals>",
      "revenue_trend": "<ACCELERATING|STABLE|DECELERATING>",
      "margin_trend": "<EXPANDING|STABLE|CONTRACTING>",
      "balance_sheet": "<STRENGTHENING|STABLE|WEAKENING>",
      "valuation_vs_growth": "<CHEAP|FAIR|EXPENSIVE relative to earnings growth>",
      "insider_activity": "<BUYING|NEUTRAL|SELLING>",
      "key_catalyst": "<the specific upcoming or recent catalyst driving fundamentals>",
      "investment_horizon": "<1-3 months|3-6 months|6-12 months>",
      "thesis": "<2-3 sentence explanation of why fundamentals improve before price moves>",
      "risk": "<main risk to this thesis>",
      "pe_ratio": <number or null>,
      "revenue_growth": <percentage or null>,
      "earnings_growth": <percentage or null>,
      "analyst_upside": <percentage or null>
    }
  ],
  "sector_rotation_signals": [
    {"sector": "<name>", "signal": "<INFLOW|OUTFLOW|NEUTRAL>", "reason": "<why>"}
  ],
  "macro_headwinds": ["<risk>"],
  "macro_tailwinds": ["<opportunity>"]
}

Order by fundamental_score descending. Be realistic and specific — reference actual numbers from the data."""


def _fetch_fundamentals_batch(symbols: list[str]) -> list[dict]:
    """Batch-fetch fundamental data for a list of symbols using yfinance."""
    results = []

    # Price momentum in one batch call
    try:
        raw = yf.download(symbols, period="3mo", progress=False, auto_adjust=True)
        if isinstance(raw.columns, pd.MultiIndex):
            closes = raw["Close"]
        else:
            closes = raw[["Close"]].rename(columns={"Close": symbols[0]}) if symbols else pd.DataFrame()
    except Exception:
        closes = pd.DataFrame()

    for sym in symbols:
        try:
            ticker = yf.Ticker(sym)
            info   = ticker.info

            def safe(key, default=None):
                v = info.get(key)
                if v is None or v != v:  # None or NaN
                    return default
                try:
                    return float(v) if default is not None and isinstance(default, float) else v
                except (TypeError, ValueError):
                    return default

            # Price momentum
            p_now = p_1m = p_3m = None
            if sym in closes.columns:
                prices = closes[sym].dropna()
                if len(prices) >= 1:
                    p_now = float(prices.iloc[-1])
                if len(prices) >= 20:
                    p_1m = float(prices.iloc[-20])
                if len(prices) >= 1:
                    p_3m = float(prices.iloc[0])

            ret_1m = round((p_now / p_1m - 1) * 100, 2) if p_now and p_1m else None
            ret_3m = round((p_now / p_3m - 1) * 100, 2) if p_now and p_3m else None

            # Core fundamentals
            rev_growth     = safe("revenueGrowth")      # quarterly YoY
            earn_growth    = safe("earningsGrowth")
            gross_margin   = safe("grossMargins")
            op_margin      = safe("operatingMargins")
            forward_pe     = safe("forwardPE")
            trailing_pe    = safe("trailingPE")
            pb             = safe("priceToBook")
            debt_equity    = safe("debtToEquity")
            curr_ratio     = safe("currentRatio")
            free_cashflow  = safe("freeCashflow")
            inst_pct       = safe("institutionsPercentHeld")
            insider_pct    = safe("heldPercentInsiders")
            target_mean    = safe("targetMeanPrice")
            num_analysts   = safe("numberOfAnalystOpinions", 0)
            reco_key       = info.get("recommendationKey", "hold")
            short_pct      = safe("shortPercentOfFloat")
            beta           = safe("beta")
            sector         = info.get("sector", "")
            company        = info.get("shortName", sym)
            market_cap     = safe("marketCap", 0.0)

            analyst_upside = None
            if target_mean and p_now and p_now > 0:
                analyst_upside = round((target_mean / p_now - 1) * 100, 1)

            # Insider transactions (last 90 days)
            insider_buys = insider_sells = 0
            try:
                df = ticker.insider_transactions
                if df is not None and not df.empty:
                    for _, row in df.head(10).iterrows():
                        txn = str(row.get("Transaction", "")).lower()
                        if "purchase" in txn or "buy" in txn:
                            insider_buys += 1
                        elif "sale" in txn or "sell" in txn:
                            insider_sells += 1
            except Exception:
                pass

            insider_signal = "BUYING" if insider_buys > insider_sells else ("SELLING" if insider_sells > insider_buys else "NEUTRAL")

            results.append({
                "symbol":         sym,
                "company_name":   company,
                "sector":         sector,
                "price":          round(p_now, 2) if p_now else None,
                "ret_1m":         ret_1m,
                "ret_3m":         ret_3m,
                "revenue_growth": round(rev_growth * 100, 1) if rev_growth else None,
                "earnings_growth":round(earn_growth * 100, 1) if earn_growth else None,
                "gross_margin":   round(gross_margin * 100, 1) if gross_margin else None,
                "op_margin":      round(op_margin * 100, 1) if op_margin else None,
                "forward_pe":     round(forward_pe, 1) if forward_pe else None,
                "trailing_pe":    round(trailing_pe, 1) if trailing_pe else None,
                "pb_ratio":       round(pb, 2) if pb else None,
                "debt_equity":    round(debt_equity, 2) if debt_equity else None,
                "current_ratio":  round(curr_ratio, 2) if curr_ratio else None,
                "free_cashflow_b":round(free_cashflow / 1e9, 2) if free_cashflow else None,
                "inst_ownership": round(inst_pct * 100, 1) if inst_pct else None,
                "insider_pct":    round(insider_pct * 100, 1) if insider_pct else None,
                "insider_signal": insider_signal,
                "insider_buys":   insider_buys,
                "insider_sells":  insider_sells,
                "analyst_target": round(target_mean, 2) if target_mean else None,
                "analyst_upside": analyst_upside,
                "num_analysts":   int(num_analysts) if num_analysts else 0,
                "recommendation": reco_key,
                "short_float_pct":round(short_pct * 100, 1) if short_pct else None,
                "beta":           round(beta, 2) if beta else None,
                "market_cap_b":   round(market_cap / 1e9, 1) if market_cap else None,
            })
        except Exception as e:
            results.append({"symbol": sym, "error": str(e)})

    return results


def _pre_score(stock: dict) -> float:
    """Quick numeric pre-score to rank stocks before sending to Claude."""
    score = 0.0
    rev   = stock.get("revenue_growth")
    earn  = stock.get("earnings_growth")
    upside = stock.get("analyst_upside")
    pe    = stock.get("forward_pe")
    ins   = stock.get("insider_signal", "NEUTRAL")
    ret1m = stock.get("ret_1m", 0) or 0

    if rev  and rev  > 0: score += min(rev, 60) * 0.4
    if earn and earn > 0: score += min(earn, 40) * 0.3
    if upside and upside > 0: score += min(upside, 30) * 0.5
    if pe and 0 < pe < 30: score += 15
    if ins == "BUYING": score += 20
    elif ins == "SELLING": score -= 10
    # Penalise pure momentum (price already moved)
    if ret1m and ret1m > 30: score -= 10
    return round(score, 1)


def _save_fundamental_scan(results: dict) -> None:
    from agent_db import agent_db_path
    db_path = str(agent_db_path())
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS fundamental_scans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_json TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("INSERT INTO fundamental_scans (scan_json) VALUES (?)", (json.dumps(results),))
        conn.commit()
    finally:
        conn.close()


def get_latest_fundamental_scan() -> dict | None:
    from agent_db import agent_db_path
    db_path = str(agent_db_path())
    if not os.path.exists(db_path):
        return None
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS fundamental_scans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_json TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        row = conn.execute("SELECT scan_json, created_at FROM fundamental_scans ORDER BY created_at DESC LIMIT 1").fetchone()
        if row:
            data = json.loads(row[0])
            data["cached_at"] = row[1]
            return data
        return None
    finally:
        conn.close()


def run_fundamental_scan(universe: list[str] | None = None, top_n_for_ai: int = 35) -> dict[str, Any]:
    """
    Main entry point — fetch fundamentals for universe, score, send top N to Claude.

    Returns Claude's structured fundamental opportunity report.
    """
    if universe is None:
        universe = FUNDAMENTAL_UNIVERSE

    print(f"  FUNDAMENTAL: fetching data for {len(universe)} stocks...")
    stocks = _fetch_fundamentals_batch(universe)

    # Pre-score and take top N (exclude errors)
    valid = [s for s in stocks if "error" not in s and s.get("price")]
    for s in valid:
        s["_pre_score"] = _pre_score(s)
    valid.sort(key=lambda x: x["_pre_score"], reverse=True)
    top = valid[:top_n_for_ai]
    # Remove internal scoring field before sending to Claude
    for s in top:
        s.pop("_pre_score", None)

    print(f"  FUNDAMENTAL: sending top {len(top)} to Claude for deep analysis...")

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return {"error": "No ANTHROPIC_API_KEY"}

    client = anthropic.Anthropic(api_key=api_key)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=6000,
            system=FUNDAMENTAL_PROMPT,
            messages=[{
                "role": "user",
                "content": (
                    f"Today is {today}. Analyze these {len(top)} stocks and identify the top 15 "
                    f"fundamental improvement opportunities where price has not yet caught up.\n\n"
                    f"```json\n{json.dumps(top, indent=2)}\n```\n\n"
                    "Return ONLY valid JSON starting with {{. No apostrophes in strings."
                )
            }]
        )
        raw = msg.content[0].text
        raw = re.sub(r'^```(?:json)?\s*', '', raw.strip())
        raw = re.sub(r'\s*```$', '', raw)

        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            # Try fixing trailing commas
            fixed = re.sub(r',\s*([}\]])', r'\1', raw)
            result = json.loads(fixed)

        result["stocks_analyzed"] = len(valid)
        result["as_of"] = datetime.now(timezone.utc).isoformat() + "Z"
        _save_fundamental_scan(result)
        return result

    except Exception as e:
        return {"error": str(e), "stocks_analyzed": len(valid), "as_of": datetime.now(timezone.utc).isoformat() + "Z"}
