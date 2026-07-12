"""
long_term_macro.py — Macro Sentiment Overlay for Long-Term (1-3y) Horizon

Proposal 1: Macro Sentiment Overlay Module (MVP)

Fetches and scores:
- VIX (volatility regime) — already partially in macro_connector, extended here for long-term
- Fear & Greed Index (CNN) — sentiment contrarian
- Margin Debt (FINRA) — leverage cycle / over-optimism

Returns scores for integration into consensus (new Long-Term nodes) or as overlay.

Usage:
    from long_term_macro import get_long_term_macro_sentiment, node_long_term_macro

    sentiment = get_long_term_macro_sentiment()
    vote, detail = node_long_term_macro()

Data sources (no new paid APIs required for MVP):
- VIX: yfinance (^VIX) or FRED (already used in macro_connector)
- Fear & Greed: CNN public endpoint (scrape-friendly) + historical GitHub fallback
- Margin Debt: FINRA public page scrape (monthly, official units = $ millions);
  unavailable on failure — NEVER synthesized

Cache: 6h for live, daily for historical margin.

Integrate later with:
- consensus.py : new nodes (e.g. "LT_VIX", "LT_FG", "LT_Margin")
- regime.py or monte_carlo.py for 1y paths
- benchmarks.py for long-term alpha
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
import requests
import yfinance as yf

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).parent
F_G_CACHE = CACHE_DIR / ".fear_greed_cache.json"
MARGIN_CACHE = CACHE_DIR / ".margin_debt_cache.json"
CACHE_TTL_H = 6

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _cache_valid(path: Path, ttl_hours: int = CACHE_TTL_H) -> bool:
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text())
        fetched = datetime.fromisoformat(data.get("fetched_at", ""))
        return (datetime.utcnow() - fetched).total_seconds() < ttl_hours * 3600
    except Exception:
        return False


def _load_cache(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _save_cache(path: Path, data: dict) -> None:
    try:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, default=str))
        tmp.replace(path)
    except Exception as e:
        logger.warning(f"Cache save failed for {path}: {e}")


# ──────────────────────────────────────────────────────────────────────────────
# VIX (reuse/extend existing)
# ──────────────────────────────────────────────────────────────────────────────

def fetch_vix(period: str = "1y") -> Optional[pd.Series]:
    """Fetch VIX close. Uses yfinance ^VIX (fast, no key needed)."""
    try:
        df = yf.download("^VIX", period=period, progress=False, auto_adjust=True)
        if df is None or df.empty:
            return None
        # .squeeze() to handle MultiIndex columns that yfinance sometimes returns
        return df["Close"].squeeze().dropna()
    except Exception as e:
        logger.warning(f"VIX fetch failed: {e}")
        return None


def vix_regime_score(vix_series: Optional[pd.Series]) -> Dict[str, Any]:
    """Score VIX for long-term overlay.
    High VIX (>25-30) historically associated with higher forward returns (fear premium)
    but higher near-term risk. For 1-3y: bullish tilt on elevated VIX.
    """
    if vix_series is None or len(vix_series) < 5:
        return {"score": 0, "detail": "VIX insufficient data", "level": None}

    latest = float(vix_series.iloc[-1])
    avg_1y = float(vix_series.tail(252).mean()) if len(vix_series) > 252 else float(vix_series.mean())

    if latest > 30:
        score = 1   # high fear → long-term bullish tilt
        detail = f"VIX high {latest:.1f} (avg {avg_1y:.1f})"
    elif latest < 15:
        score = -1  # complacency
        detail = f"VIX low {latest:.1f}"
    else:
        score = 0
        detail = f"VIX normal {latest:.1f}"

    return {"score": score, "detail": detail, "level": latest}


# ──────────────────────────────────────────────────────────────────────────────
# Fear & Greed (CNN)
# ──────────────────────────────────────────────────────────────────────────────

def fetch_fear_greed() -> Optional[pd.DataFrame]:
    """Fetch CNN Fear & Greed. Prioritize live current from page + graphdata for series."""
    cache = _load_cache(F_G_CACHE)
    if cache and _cache_valid(F_G_CACHE):
        try:
            return pd.DataFrame(cache["data"])
        except Exception:
            pass

    df = None
    current_score = None

    # 1. Scrape current score from the main page (reliable for current value)
    try:
        url = 'https://www.cnn.com/markets/fear-and-greed'
        resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}, timeout=10)
        if resp.status_code == 200:
            import re
            # Look for the prominent number near "Fear & Greed"
            m = re.search(r'(\d{1,3})\s*</[^>]+>\s*<[^>]*>\s*(?:Fear|Greed)', resp.text, re.IGNORECASE | re.DOTALL)
            if not m:
                m = re.search(r'fear-and-greed[^>]*>(\d{1,3})', resp.text, re.IGNORECASE)
            if m:
                current_score = int(m.group(1))
    except Exception as e:
        logger.warning(f"F&G current scrape failed: {e}")

    # 2. Try graphdata for historical series (for trend if needed)
    try:
        start = (datetime.utcnow() - timedelta(days=40)).strftime("%Y-%m-%d")
        url = f"https://production.dataviz.cnn.io/index/fearandgreed/graphdata/{start}"
        resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            hist = data.get("fear_and_greed_historical", {}).get("data", [])
            if hist:
                df = pd.DataFrame(hist)
                df["date"] = pd.to_datetime(df["x"], unit="ms").dt.date
                df = df[["date", "y"]].rename(columns={"y": "fear_greed"})
    except Exception as e:
        logger.warning(f"F&G graphdata failed: {e}")

    # Ensure we have current value
    if current_score is not None:
        today = datetime.utcnow().date()
        if df is None or df.empty:
            df = pd.DataFrame({"date": [today], "fear_greed": [current_score]})
        else:
            # update or append current
            if str(df['date'].iloc[-1]) == str(today):
                df.at[df.index[-1], 'fear_greed'] = current_score
            else:
                df = pd.concat([df, pd.DataFrame({"date": [today], "fear_greed": [current_score]})], ignore_index=True)

    if df is not None and not df.empty:
        _save_cache(F_G_CACHE, {"fetched_at": datetime.utcnow().isoformat(), "data": df.to_dict("records")})
        return df

    # Final fallback
    try:
        hist_url = "https://raw.githubusercontent.com/hackingthemarkets/sentiment-fear-and-greed/master/datasets/fear-greed.csv"
        df = pd.read_csv(hist_url)
        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date']).dt.date
        else:
            df = df.iloc[:, :2]
            df.columns = ['date', 'fear_greed']
            df['date'] = pd.to_datetime(df['date']).dt.date
        df = df[['date', 'fear_greed']].dropna()
        return df
    except Exception as e:
        logger.error(f"F&G all failed: {e}")
        return None


def fear_greed_score(fg_df: Optional[pd.DataFrame]) -> Dict[str, Any]:
    """Contrarian long-term score.
    <25 Extreme Fear → +1 (buy opportunity)
    >75 Extreme Greed → -1 (caution)
    """
    if fg_df is None or fg_df.empty:
        return {"score": 0, "detail": "F&G no data", "level": None}

    latest = float(fg_df["fear_greed"].iloc[-1])
    if latest < 25:
        score = 1
        detail = f"Extreme Fear {latest:.0f}"
    elif latest > 75:
        score = -1
        detail = f"Extreme Greed {latest:.0f}"
    elif latest < 40:
        score = 0.5
        detail = f"Fear {latest:.0f}"
    elif latest > 60:
        score = -0.5
        detail = f"Greed {latest:.0f}"
    else:
        score = 0
        detail = f"Neutral {latest:.0f}"

    return {"score": score, "detail": detail, "level": latest}


# ──────────────────────────────────────────────────────────────────────────────
# Margin Debt (FINRA)
# ──────────────────────────────────────────────────────────────────────────────

FINRA_MARGIN_URL = "https://www.finra.org/rules-guidance/key-topics/margin-accounts/margin-statistics"
FINRA_MARGIN_SOURCE = "FINRA margin statistics (finra.org)"
# FINRA month labels are "May-26" style — parsed with an EXPLICIT format only.
# An inferring parser turns "May-26" into year-0001 garbage that mis-sorts rows.
FINRA_DATE_FORMAT = "%b-%y"
# Cache schema version. v1 caches carried mis-parsed dates and /1000 values —
# the loader rejects anything but the current schema so they are never served.
MARGIN_CACHE_SCHEMA = 2


def _fetch_finra_tables() -> list[pd.DataFrame]:
    """Network seam: raw tables from the FINRA margin-statistics page."""
    return pd.read_html(FINRA_MARGIN_URL, header=0)


def _parse_finra_margin_table(tables: list[pd.DataFrame]) -> Optional[pd.DataFrame]:
    """Extract [date, margin_debt_millions] from FINRA tables.
    Values are kept in FINRA's official units — $ MILLIONS. Never rescale."""
    df = None
    for t in tables:
        cols_lower = [str(c).lower() for c in t.columns]
        if any('debit' in c or 'margin' in c for c in cols_lower):
            df = t
            break
    if df is None:
        return None
    df = df.iloc[:, [0, 1]].copy()
    df.columns = ['date', 'margin_debt_millions']
    df['date'] = pd.to_datetime(df['date'], format=FINRA_DATE_FORMAT, errors='coerce')
    df['margin_debt_millions'] = pd.to_numeric(
        df['margin_debt_millions'].astype(str).str.replace(',', '', regex=False),
        errors='coerce'
    )
    df = df.dropna(subset=['date', 'margin_debt_millions']).set_index('date').sort_index()
    if df.empty:
        return None
    return df[['margin_debt_millions']]


def _margin_cache_payload(cache: Optional[dict]) -> Optional[Dict[str, Any]]:
    """Serve a cached series only if it is the current schema (post-fix units/dates)."""
    if not cache or cache.get("schema") != MARGIN_CACHE_SCHEMA:
        return None
    if not cache.get("as_of") or not cache.get("series"):
        return None
    return {
        "status": "ok",
        "source": cache.get("source", FINRA_MARGIN_SOURCE),
        "as_of": cache["as_of"],
        "quality": "cached",
        "series": cache["series"],
    }


def fetch_margin_debt() -> Dict[str, Any]:
    """FINRA margin debt (monthly, official units = $ millions).

    Returns {"status", "source", "as_of", "quality", "series"}:
      * quality "live"   — fresh scrape from FINRA
      * quality "cached" — served from .margin_debt_cache.json (fresh or stale)
      * status "unavailable" — FINRA unreachable and no usable cache.
    On failure NO synthetic series is ever produced — fabricated market data
    is a stop-ship violation of the project's #1 rule.
    """
    cache = _load_cache(MARGIN_CACHE)
    cached_payload = _margin_cache_payload(cache)
    if cached_payload and _cache_valid(MARGIN_CACHE, ttl_hours=24):
        return cached_payload

    try:
        df = _parse_finra_margin_table(_fetch_finra_tables())
        if df is not None:
            series = [
                {"date": ts.strftime("%Y-%m"), "margin_debt_millions": float(v)}
                for ts, v in df["margin_debt_millions"].items()
            ]
            as_of = series[-1]["date"]
            _save_cache(MARGIN_CACHE, {
                "schema": MARGIN_CACHE_SCHEMA,
                "fetched_at": datetime.utcnow().isoformat(),
                "source": FINRA_MARGIN_SOURCE,
                "as_of": as_of,
                "series": series,
            })
            return {
                "status": "ok",
                "source": FINRA_MARGIN_SOURCE,
                "as_of": as_of,
                "quality": "live",
                "series": series,
            }
    except Exception as e:
        logger.warning(f"Margin debt scrape failed: {e}")

    # Stale cache beats nothing — but it must say so (quality + its own as_of).
    if cached_payload:
        return cached_payload

    return {
        "status": "unavailable",
        "source": FINRA_MARGIN_SOURCE,
        "as_of": None,
        "quality": "unavailable",
        "series": [],
    }


def margin_debt_score(md: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Leverage cycle score for long-term.
    Rapid rise or record highs → caution (-1)
    Declining after high → opportunity (+1)
    """
    md = md or {}
    provenance = {
        "source": md.get("source", FINRA_MARGIN_SOURCE),
        "as_of": md.get("as_of"),
        "quality": md.get("quality", "unavailable"),
    }
    series = md.get("series") or []
    if md.get("status") != "ok" or len(series) < 3:
        detail = "Margin debt unavailable" if md.get("status") != "ok" else "Margin debt insufficient"
        return {"score": 0, "detail": detail, "level": None,
                "status": "unavailable", **provenance}

    latest = float(series[-1]["margin_debt_millions"])
    prev = float(series[-2]["margin_debt_millions"])
    mom = (latest - prev) / prev if prev > 0 else 0

    if mom > 0.08:  # strong rise
        score = -1
        detail = f"Margin debt rising fast +{mom*100:.1f}% (record risk)"
    elif mom < -0.03:
        score = 1
        detail = f"Margin debt contracting {mom*100:.1f}%"
    else:
        score = 0
        detail = f"Margin debt stable (latest {latest/1e6:.1f}T)"

    return {"score": score, "detail": detail, "level": latest, "status": "ok", **provenance}


# ──────────────────────────────────────────────────────────────────────────────
# Public API for Proposal 1
# ──────────────────────────────────────────────────────────────────────────────

def get_long_term_macro_sentiment() -> Dict[str, Any]:
    """Main entry for Proposal 1 overlay.
    Returns combined sentiment for long-term (1-3y) bias.
    """
    vix_s = fetch_vix()
    vix = vix_regime_score(vix_s)

    fg_df = fetch_fear_greed()
    fg = fear_greed_score(fg_df)

    md = margin_debt_score(fetch_margin_debt())

    total = vix["score"] + fg["score"] + md["score"]
    # Normalize to -1 to +1 range roughly
    norm = max(min(total / 3.0, 1.0), -1.0)

    return {
        "vix": vix,
        "fear_greed": fg,
        "margin_debt": md,
        "combined_score": round(norm, 2),
        "bias": "BULLISH" if norm > 0.3 else "BEARISH" if norm < -0.3 else "NEUTRAL",
        "fetched_at": datetime.utcnow().isoformat(),
    }


def node_long_term_macro(symbol: str = None) -> tuple[int | None, str]:
    """Consensus node compatible return for long-term macro sentiment.
    Can be called as a new node or blended into existing MACRO node.
    """
    sent = get_long_term_macro_sentiment()
    score = sent["combined_score"]
    detail = f"VIX={sent['vix']['detail']}; FG={sent['fear_greed']['detail']}; MD={sent['margin_debt']['detail']}"
    if score > 0.3:
        return 1, detail  # BULLISH tilt for long-term
    if score < -0.3:
        return -1, detail
    return 0, detail


if __name__ == "__main__":
    print(get_long_term_macro_sentiment())
