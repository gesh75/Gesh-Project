"""
market_data.py — SimplyWallSt-inspired market intelligence layer.

Pulls the following per-symbol from yfinance (no extra API keys needed):
  • Analyst price targets & recommendation consensus
  • Historical performance returns  (7D / 1M / 3M / 1Y / YTD)
  • Recent insider transactions     (buys & sells, last 10)
  • Institutional / insider ownership breakdown
"""

from __future__ import annotations

from datetime import datetime, timedelta, date, timezone
from typing import Any

import numpy as np
import pandas as pd
import yfinance as yf


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _safe(val, default=None):
    """Return val unless it is None/NaN/inf."""
    try:
        if val is None:
            return default
        f = float(val)
        if not (f == f) or f in (float("inf"), float("-inf")):  # NaN / inf
            return default
        return f
    except (TypeError, ValueError):
        return val if val is not None else default


def _ret_from_hist(hist: pd.DataFrame | None, days: int) -> float | None:
    """% price change over the last `days` calendar days, sliced from ONE
    already-fetched history frame (no extra HTTP call per window)."""
    try:
        if hist is None or hist.empty or len(hist) < 2:
            return None
        closes = hist["Close"].dropna()
        if len(closes) < 2:
            return None
        start_ts = closes.index[-1] - pd.Timedelta(days=days)
        window = closes[closes.index >= start_ts]
        if len(window) < 2:
            return None
        old, new = float(window.iloc[0]), float(closes.iloc[-1])
        if old == 0:
            return None
        return round((new - old) / old * 100, 2)
    except Exception:
        return None


def _ytd_from_hist(hist: pd.DataFrame | None) -> float | None:
    """% price change from Jan 1 of the frame's last year, sliced locally."""
    try:
        if hist is None or hist.empty or len(hist) < 2:
            return None
        closes = hist["Close"].dropna()
        if len(closes) < 2:
            return None
        last_ts = closes.index[-1]
        jan1 = pd.Timestamp(year=last_ts.year, month=1, day=1, tz=getattr(closes.index, "tz", None))
        window = closes[closes.index >= jan1]
        if len(window) < 2:
            return None
        old, new = float(window.iloc[0]), float(closes.iloc[-1])
        if old == 0:
            return None
        return round((new - old) / old * 100, 2)
    except Exception:
        return None


# ─── 1. Analyst Price Targets ─────────────────────────────────────────────────

_RECO_LABEL = {
    "strongBuy":  {"label": "Strong Buy",  "cls": "reco-strong-buy"},
    "buy":        {"label": "Buy",          "cls": "reco-buy"},
    "hold":       {"label": "Hold",         "cls": "reco-hold"},
    "sell":       {"label": "Sell",         "cls": "reco-sell"},
    "strongSell": {"label": "Strong Sell",  "cls": "reco-strong-sell"},
}


def get_analyst_targets(symbols: list[str]) -> dict[str, dict]:
    """
    Returns per-symbol analyst data:
      target_mean, target_high, target_low, target_median,
      num_analysts, recommendation, upside_pct
    """
    results: dict[str, dict] = {}
    for sym in symbols:
        try:
            info = yf.Ticker(sym).info
            current = _safe(info.get("currentPrice") or info.get("regularMarketPrice"))
            mean    = _safe(info.get("targetMeanPrice"))
            high    = _safe(info.get("targetHighPrice"))
            low     = _safe(info.get("targetLowPrice"))
            median  = _safe(info.get("targetMedianPrice"))
            n       = _safe(info.get("numberOfAnalystOpinions"), 0)
            reco    = info.get("recommendationKey", "hold")
            reco_meta = _RECO_LABEL.get(reco, {"label": reco.title(), "cls": "reco-hold"})

            upside = None
            if current and mean:
                upside = round((mean - current) / current * 100, 1)

            results[sym] = {
                "current":        current,
                "target_mean":    mean,
                "target_high":    high,
                "target_low":     low,
                "target_median":  median,
                "num_analysts":   int(n) if n else 0,
                "recommendation": reco_meta["label"],
                "reco_cls":       reco_meta["cls"],
                "upside_pct":     upside,
            }
        except Exception as e:
            results[sym] = {"error": str(e)}
    return results


# ─── 2. Historical Performance Returns ────────────────────────────────────────

def get_performance_returns(symbols: list[str]) -> dict[str, dict]:
    """
    Returns per-symbol % returns over: 7D, 1M (30D), 3M (90D), 1Y (365D), YTD.
    """
    results: dict[str, dict] = {}
    for sym in symbols:
        try:
            # ONE 1y history call per symbol, sliced locally for every window —
            # the old 5-calls-per-symbol pattern was a Yahoo-429 magnet.
            hist = yf.Ticker(sym).history(period="1y", auto_adjust=True)
            results[sym] = {
                "7d":  _ret_from_hist(hist, 7),
                "1m":  _ret_from_hist(hist, 30),
                "3m":  _ret_from_hist(hist, 90),
                "1y":  _ret_from_hist(hist, 365),
                "ytd": _ytd_from_hist(hist),
            }
        except Exception as e:
            results[sym] = {"error": str(e)}
    return results


# ─── 3. Insider Transactions ──────────────────────────────────────────────────

def get_insider_transactions(symbols: list[str], max_per_symbol: int = 8) -> dict[str, list]:
    """
    Returns last `max_per_symbol` insider transactions per symbol.
    Each entry: {name, title, transaction, shares, value, date, is_buy}
    """
    results: dict[str, list] = {}
    for sym in symbols:
        try:
            df = yf.Ticker(sym).insider_transactions
            if df is None or df.empty:
                results[sym] = []
                continue

            rows = []
            # yfinance columns vary — handle gracefully
            for _, row in df.head(max_per_symbol).iterrows():
                txn_text = str(row.get("Transaction", row.get("Shares", ""))).strip()
                shares   = _safe(row.get("Shares", row.get("Value", 0)), 0)
                value    = _safe(row.get("Value", 0), 0)

                # Determine buy vs sell from transaction text or shares sign
                is_buy = (
                    "purchase" in txn_text.lower()
                    or "buy" in txn_text.lower()
                    or (isinstance(shares, (int, float)) and shares > 0
                        and "sale" not in txn_text.lower()
                        and "sell" not in txn_text.lower())
                )

                # Date column
                raw_date = row.get("Date", row.get("Start Date", ""))
                try:
                    dt = str(raw_date)[:10]
                except Exception:
                    dt = ""

                rows.append({
                    "name":        str(row.get("Insider", row.get("Name", "Unknown"))).strip(),
                    "title":       str(row.get("Position", row.get("Relation", ""))).strip(),
                    "transaction": txn_text,
                    "shares":      abs(int(shares)) if shares else 0,
                    "value":       abs(int(value))  if value  else 0,
                    "date":        dt,
                    "is_buy":      is_buy,
                })
            results[sym] = rows
        except Exception as e:
            results[sym] = []
    return results


# ─── 4. Institutional Ownership ───────────────────────────────────────────────

def get_institutional_ownership(symbols: list[str]) -> dict[str, dict]:
    """
    Returns per-symbol ownership breakdown:
      institution_pct, insider_pct, retail_pct (estimated), top_holders
    """
    results: dict[str, dict] = {}
    for sym in symbols:
        try:
            info = yf.Ticker(sym).info
            inst_pct   = _safe(info.get("institutionsPercentHeld"), 0) or 0
            insider_pct = _safe(info.get("heldPercentInsiders"), 0) or 0
            inst_pct    = round(inst_pct * 100, 1)
            insider_pct = round(insider_pct * 100, 1)
            retail_pct  = round(max(0, 100 - inst_pct - insider_pct), 1)

            # Top 3 institutional holders
            try:
                holders_df = yf.Ticker(sym).institutional_holders
                top = []
                if holders_df is not None and not holders_df.empty:
                    for _, row in holders_df.head(3).iterrows():
                        pct = _safe(row.get("% Out", row.get("pctHeld", 0)), 0)
                        top.append({
                            "name": str(row.get("Holder", "")).strip(),
                            "pct":  round(float(pct) * 100, 2) if pct and pct < 1 else round(float(pct or 0), 2),
                        })
            except Exception:
                top = []

            results[sym] = {
                "institution_pct": inst_pct,
                "insider_pct":     insider_pct,
                "retail_pct":      retail_pct,
                "top_holders":     top,
            }
        except Exception as e:
            results[sym] = {"institution_pct": 0, "insider_pct": 0, "retail_pct": 100, "top_holders": [], "error": str(e)}
    return results


# ─── Combined Entry Point ─────────────────────────────────────────────────────

def get_all_market_data(symbols: list[str]) -> dict[str, Any]:
    """
    Fetch all market intelligence for a list of symbols concurrently.
    """
    import concurrent.futures

    analyst_targets = {}
    performance = {}
    insider_trades = {}
    ownership = {}

    def fetch_for_symbol(sym: str) -> tuple[str, dict[str, Any], dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
        try:
            ticker = yf.Ticker(sym)
        except Exception:
            return sym, {}, {}, [], {}

        info = None
        try:
            info = ticker.info
            current = _safe(info.get("currentPrice") or info.get("regularMarketPrice"))
            mean = _safe(info.get("targetMeanPrice"))
            high = _safe(info.get("targetHighPrice"))
            low = _safe(info.get("targetLowPrice"))
            median = _safe(info.get("targetMedianPrice"))
            n = _safe(info.get("numberOfAnalystOpinions"), 0)
            reco = info.get("recommendationKey", "hold")
            reco_meta = _RECO_LABEL.get(reco, {"label": reco.title(), "cls": "reco-hold"})
            upside = round((mean - current) / current * 100, 1) if current and mean else None
            an = {
                "current": current,
                "target_mean": mean,
                "target_high": high,
                "target_low": low,
                "target_median": median,
                "num_analysts": int(n) if n else 0,
                "recommendation": reco_meta["label"],
                "reco_cls": reco_meta["cls"],
                "upside_pct": upside,
            }
        except Exception:
            an = {}

        try:
            hist = ticker.history(period="1y", auto_adjust=True)
            pe = {
                "7d": _ret_from_hist(hist, 7),
                "1m": _ret_from_hist(hist, 30),
                "3m": _ret_from_hist(hist, 90),
                "1y": _ret_from_hist(hist, 365),
                "ytd": _ytd_from_hist(hist),
            }
        except Exception:
            pe = {}

        try:
            df = ticker.insider_transactions
            ins: list[dict[str, Any]] = []
            if df is not None and not df.empty:
                for _, row in df.head(8).iterrows():
                    txn_text = str(row.get("Transaction", row.get("Shares", ""))).strip()
                    shares = _safe(row.get("Shares", row.get("Value", 0)), 0)
                    value = _safe(row.get("Value", 0), 0)
                    is_buy = (
                        "purchase" in txn_text.lower()
                        or "buy" in txn_text.lower()
                        or (
                            isinstance(shares, (int, float))
                            and shares > 0
                            and "sale" not in txn_text.lower()
                            and "sell" not in txn_text.lower()
                        )
                    )
                    raw_date = row.get("Date", row.get("Start Date", ""))
                    try:
                        dt = str(raw_date)[:10]
                    except Exception:
                        dt = ""
                    ins.append({
                        "name": str(row.get("Insider", row.get("Name", "Unknown"))).strip(),
                        "title": str(row.get("Position", row.get("Relation", ""))).strip(),
                        "transaction": txn_text,
                        "shares": abs(int(shares)) if shares else 0,
                        "value": abs(int(value)) if value else 0,
                        "date": dt,
                        "is_buy": is_buy,
                    })
        except Exception:
            ins = []

        try:
            if info is None:
                raise ValueError("ticker info unavailable")
            inst_pct = _safe(info.get("institutionsPercentHeld"), 0) or 0
            insider_pct = _safe(info.get("heldPercentInsiders"), 0) or 0
            inst_pct = round(inst_pct * 100, 1)
            insider_pct = round(insider_pct * 100, 1)
            retail_pct = round(max(0, 100 - inst_pct - insider_pct), 1)
            top: list[dict[str, Any]] = []
            try:
                holders_df = ticker.institutional_holders
                if holders_df is not None and not holders_df.empty:
                    for _, row in holders_df.head(3).iterrows():
                        pct = _safe(row.get("% Out", row.get("pctHeld", 0)), 0)
                        top.append({
                            "name": str(row.get("Holder", "")).strip(),
                            "pct": round(float(pct) * 100, 2)
                            if pct and pct < 1
                            else round(float(pct or 0), 2),
                        })
            except Exception:
                top = []
            own = {
                "institution_pct": inst_pct,
                "insider_pct": insider_pct,
                "retail_pct": retail_pct,
                "top_holders": top,
            }
        except Exception:
            own = {}
        return sym, an, pe, ins, own

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(symbols) or 1, 10)) as executor:
        for sym, an, pe, ins, own in executor.map(fetch_for_symbol, symbols):
            analyst_targets[sym] = an
            performance[sym] = pe
            insider_trades[sym] = ins
            ownership[sym] = own

    return {
        "analyst_targets": analyst_targets,
        "performance":     performance,
        "insider_trades":  insider_trades,
        "ownership":       ownership,
        "as_of":           datetime.now(timezone.utc).isoformat(),
    }


# ─── Investment Screener ──────────────────────────────────────────────────────

SCREENER_UNIVERSE: list[str] = [
    # Mega-cap tech / AI
    "AAPL","MSFT","NVDA","GOOGL","META","AMZN","TSLA","AVGO","ORCL","ADBE",
    # Large-cap tech
    "CRM","AMD","INTC","QCOM","TXN","MU","AMAT","LRCX","MRVL","KLAC",
    # AI / Cloud SaaS
    "PLTR","SNOW","NET","DDOG","CRWD","ZS","PANW","MDB","HUBS","GTLB",
    # Finance
    "JPM","BAC","GS","MS","V","MA","AXP","BLK","SCHW","COF",
    # Healthcare / Biotech
    "LLY","UNH","ABBV","MRK","TMO","ISRG","DXCM","REGN","MRNA","GEHC",
    # Energy
    "XOM","CVX","COP","EOG","SLB","OXY","MPC","HAL","DVN","BKR",
    # Consumer / Travel
    "COST","WMT","HD","MCD","NKE","BKNG","MAR","ABNB","CMG","SBUX",
    # Emerging growth
    "RDDT","ARM","SMCI","DELL","IONQ","RXRX","SOUN","ASTS","RKLB","HOOD",
    # Value / Dividend
    "BRK-B","KO","PEP","PG","JNJ","CAT","DE","GE","RTX","HON",
    # Chinese ADRs
    "BABA","JD","PDD","NIO","BIDU",
]


def screen_market(top_n: int = 40) -> list[dict]:
    """
    Batch-screen SCREENER_UNIVERSE using yfinance.

    Steps
    -----
    1. Batch-download 30-day OHLCV (one network call — fast).
    2. Score each symbol on momentum (1W + 1M) + volume surge.
    3. Fetch fundamentals (analyst targets, P/E, sector …) for the top ``top_n``
       by momentum score — the bottleneck but kept to ≤40 calls.

    Returns a list of candidate dicts ready for Claude scoring.
    """
    import datetime as _dt

    universe = list(dict.fromkeys(SCREENER_UNIVERSE))

    # ── 1. Batch price + volume download ────────────────────────────────────
    # Use period= (relative) instead of start/end= (absolute) — more resilient
    # to yfinance crumb expiry and always includes today's partial-day bar.
    try:
        import price_source
        raw = price_source.yf_download(
            universe,
            period="40d",
            progress=False,
            auto_adjust=True,
            threads=False,  # avoid crumb collisions under parallel load
        )
        if raw is None or raw.empty:
            print("⚠  Screener batch download returned no data (Yahoo + RH fallback)")
            return []
    except Exception as e:
        print(f"⚠  Screener batch download failed: {e}")
        return []

    # Handle both MultiIndex (multiple tickers) and flat (single ticker) structures
    if isinstance(raw.columns, pd.MultiIndex):
        closes  = raw["Close"]
        volumes = raw["Volume"] if "Volume" in raw.columns.get_level_values(0) else pd.DataFrame()
    else:
        # Shouldn't happen with multiple tickers but guard anyway
        closes  = raw[["Close"]].rename(columns={"Close": universe[0]}) if "Close" in raw.columns else pd.DataFrame()
        volumes = raw[["Volume"]].rename(columns={"Volume": universe[0]}) if "Volume" in raw.columns else pd.DataFrame()

    candidates: list[dict] = []
    for sym in universe:
        try:
            col = sym if sym in closes.columns else None
            if col is None:
                continue
            prices = closes[col].dropna()
            vols   = volumes[col].dropna() if col in volumes.columns else None
            if len(prices) < 6:
                continue

            p_now = float(prices.iloc[-1])
            p_1w  = float(prices.iloc[-6])   if len(prices) >= 6  else p_now
            p_1m  = float(prices.iloc[0])

            ret_1w = round((p_now / p_1w - 1) * 100, 2) if p_1w else 0.0
            ret_1m = round((p_now / p_1m - 1) * 100, 2) if p_1m else 0.0

            vol_ratio = 1.0
            if vols is not None and len(vols) >= 5:
                vol_ratio = round(float(vols.iloc[-5:].mean()) / (float(vols.mean()) or 1), 2)

            # Composite pre-score: 60 % 1W + 30 % 1M + 10 % vol surge
            pre_score = 0.6 * ret_1w + 0.3 * ret_1m + 10 * max(0, vol_ratio - 1)

            candidates.append({
                "symbol":    sym,
                "price":     round(p_now, 2),
                "ret_1w":    ret_1w,
                "ret_1m":    ret_1m,
                "vol_ratio": vol_ratio,
                "pre_score": round(pre_score, 2),
            })
        except Exception:
            continue

    # ── 2. Sort and keep top_n for fundamental enrichment ───────────────────
    candidates.sort(key=lambda x: x["pre_score"], reverse=True)
    top = candidates[:top_n]

    # ── 3. Enrich with fundamentals (analyst targets, P/E, sector …) ────────
    import concurrent.futures

    def _fetch_info(cand):
        sym = cand["symbol"]
        try:
            info = yf.Ticker(sym).info
            target_mean = _safe(info.get("targetMeanPrice"))
            current     = _safe(info.get("currentPrice") or info.get("regularMarketPrice"))
            upside      = round((target_mean / current - 1) * 100, 1) \
                          if target_mean and current else None
            reco        = info.get("recommendationKey", "hold")
            reco_label  = _RECO_LABEL.get(reco, {"label": reco.title(), "cls": ""})["label"]

            cand.update({
                "company_name":   info.get("shortName", sym),
                "sector":         info.get("sector", ""),
                "pe_ratio":       _safe(info.get("forwardPE") or info.get("trailingPE")),
                "pb_ratio":       _safe(info.get("priceToBook")),
                "revenue_growth": _safe(info.get("revenueGrowth")),
                "earnings_growth":_safe(info.get("earningsGrowth")),
                "market_cap":     _safe(info.get("marketCap"), 0),
                "target_mean":    target_mean,
                "analyst_upside": upside,
                "num_analysts":   int(_safe(info.get("numberOfAnalystOpinions"), 0) or 0),
                "recommendation": reco_label,
                "reco_key":       reco,
            })
        except Exception:
            # Tag the row so a degraded scan is VISIBLE downstream — silent
            # enrichment failures made Claude score on momentum alone.
            cand["enrich_error"] = True
            cand.setdefault("company_name", sym)
            cand.setdefault("sector", "")

    # 4 workers, not 20: the batch download above already sets threads=False to
    # avoid Yahoo crumb collisions — a 20-way .info burst reintroduced them.
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        executor.map(_fetch_info, top)

    return top


# ── Robinhood-backed company data (VPN-safe) ───────────────────────────────────
# yfinance's .info is category-blocked under the owner's VPN (curl 35 reset), so
# the analyst/fundamentals cards on the customer Analyze view came back empty.
# Robinhood's authed reads (ratings + fundamentals) survive the block, so these are
# the primary source for those cards; the yfinance path stays as a fallback. Both
# return None on any failure — never raise.

def _customer_rh_session_blocked() -> bool:
    """True when this call must not spend the owner's authenticated robin_stocks session.

    price_source._ALLOW_AUTHENTICATED_RH is False on customer HTTP / mybook paths
    (server middleware + the 202 worker wrap). rh_analyst / rh_fundamentals used
    to ignore that flag and call get_ratings / get_fundamentals whenever a
    session was already live — the remaining leak after the Yahoo-401 pickle
    restore was closed.
    """
    try:
        from price_source import _ALLOW_AUTHENTICATED_RH
    except Exception:
        return False
    return not bool(_ALLOW_AUTHENTICATED_RH.get())


def rh_analyst(symbol: str) -> dict[str, Any] | None:
    """Analyst ratings for `symbol` from Robinhood: buy/hold/sell counts + a derived
    consensus. Returns None if unavailable."""
    if _customer_rh_session_blocked():
        return None
    try:
        import robin_stocks.robinhood as rh  # noqa: PLC0415 — optional, lazy
        s = (rh.stocks.get_ratings(symbol) or {}).get("summary") or {}
        b = int(s.get("num_buy_ratings", 0) or 0)
        h = int(s.get("num_hold_ratings", 0) or 0)
        sl = int(s.get("num_sell_ratings", 0) or 0)
        total = b + h + sl
        if total == 0:
            return None
        reco = "Buy" if (b >= h and b >= sl) else ("Sell" if (sl > b and sl > h) else "Hold")
        return {"recommendation": reco, "num_buy": b, "num_hold": h,
                "num_sell": sl, "num_analysts": total}
    except Exception:
        return None


def rh_fundamentals(symbol: str) -> dict[str, Any] | None:
    """Company fundamentals for `symbol` from Robinhood (PE, P/B, market cap, 52-week
    range, dividend yield, sector). Returns None if unavailable."""
    if _customer_rh_session_blocked():
        return None
    try:
        import robin_stocks.robinhood as rh  # noqa: PLC0415 — optional, lazy
        rows = rh.stocks.get_fundamentals(symbol) or []
        f = rows[0] if rows else None
        if not f:
            return None

        def _num(key: str):
            v = f.get(key)
            try:
                return round(float(v), 2) if v not in (None, "") else None
            except (TypeError, ValueError):
                return None

        out = {"pe_ratio": _num("pe_ratio"), "pb_ratio": _num("pb_ratio"),
               "market_cap": _num("market_cap"), "high_52": _num("high_52_weeks"),
               "low_52": _num("low_52_weeks"), "dividend_yield": _num("dividend_yield"),
               "sector": (f.get("sector") or None), "industry": (f.get("industry") or None)}
        return out if any(v is not None for v in out.values()) else None
    except Exception:
        return None
