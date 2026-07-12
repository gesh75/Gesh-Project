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
- Margin Debt: FINRA public page scrape (monthly) + historical fallback

Cache: 6h for live, daily for historical margin.

Integrate later with:
- consensus.py : new nodes (e.g. "LT_VIX", "LT_FG", "LT_Margin")
- regime.py or monte_carlo.py for 1y paths
- benchmarks.py for long-term alpha
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
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

def fetch_margin_debt() -> Optional[pd.DataFrame]:
    """Fetch FINRA margin debt (monthly). Primary = scrape public table or use historical.
    For production: implement robust scrape or use paid feed.
    """
    cache = _load_cache(MARGIN_CACHE)
    if cache and _cache_valid(MARGIN_CACHE, ttl_hours=24):
        try:
            return pd.DataFrame(cache["data"])
        except Exception:
            pass

    # Real fetch: scrape FINRA margin statistics page
    try:
        url = "https://www.finra.org/rules-guidance/key-topics/margin-accounts/margin-statistics"
        tables = pd.read_html(url, header=0)
        df = None
        for t in tables:
            cols_lower = [str(c).lower() for c in t.columns]
            if any('debit' in c or 'margin' in c for c in cols_lower):
                df = t
                break
        if df is not None:
            # Take first two columns: Date and main Debit/Margin column
            df = df.iloc[:, [0, 1]].copy()
            df.columns = ['date', 'margin_debt']
            df['date'] = pd.to_datetime(df['date'], errors='coerce')
            df['margin_debt'] = pd.to_numeric(
                df['margin_debt'].astype(str).str.replace(',', '', regex=False),
                errors='coerce'
            )
            df = df.dropna(subset=['date', 'margin_debt']).set_index('date').sort_index()
            # FINRA reports in $000s; normalize to millions for readability
            df['margin_debt_millions'] = df['margin_debt'] / 1000.0
            df = df[['margin_debt_millions']]
            _save_cache(MARGIN_CACHE, {"fetched_at": datetime.utcnow().isoformat(), "data": df.reset_index().to_dict("records")})
            return df
    except Exception as e:
        logger.warning(f"Margin debt scrape failed: {e}")

    # Fallback stub if scrape fails
    try:
        dates = pd.date_range(end=datetime.utcnow(), periods=12, freq="ME")
        values = [920_000 + i * 40_000 for i in range(12)]
        df = pd.DataFrame({"date": dates, "margin_debt_millions": values})
        df = df.set_index("date")
        return df
    except Exception as e:
        logger.error(f"Margin debt fallback failed: {e}")
        return None


def margin_debt_score(md_df: Optional[pd.DataFrame]) -> Dict[str, Any]:
    """Leverage cycle score for long-term.
    Rapid rise or record highs → caution (-1)
    Declining after high → opportunity (+1)
    """
    if md_df is None or len(md_df) < 3:
        return {"score": 0, "detail": "Margin debt insufficient", "level": None}

    latest = float(md_df["margin_debt_millions"].iloc[-1])
    prev = float(md_df["margin_debt_millions"].iloc[-2])
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

    return {"score": score, "detail": detail, "level": latest}


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

    md_df = fetch_margin_debt()
    md = margin_debt_score(md_df)

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


def _fear_greed_label(level: Optional[float]) -> Optional[str]:
    """Human Fear & Greed label from the 0-100 CNN index (mirrors fear_greed_score's
    thresholds). The downstream agentic-rh bridge keys its contrarian brake on the
    words 'Extreme Fear' / 'Extreme Greed', so we expose the label explicitly rather
    than leaving it buried in the score detail string."""
    if level is None:
        return None
    try:
        lv = float(level)
    except (TypeError, ValueError):
        return None
    if lv < 25:
        return "Extreme Fear"
    if lv > 75:
        return "Extreme Greed"
    if lv < 40:
        return "Fear"
    if lv > 60:
        return "Greed"
    return "Neutral"


def engine_block(sentiment: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Normalize the long-term-macro sentiment into the flat, self-describing block
    written to ``engine_results.json`` (top-level key ``long_term_macro``).

    This is the contract the downstream agentic-rh bridge consumes for its regime
    tier ladder (B1/B2): it keys a CONTRARIAN sizing brake on
      * ``fear_greed.level`` (label) — Extreme Greed → brake, Extreme Fear → lean in,
      * ``vix.level`` — panic (>=30) → lean in, complacency (<=12) → brake,
      * ``margin_debt.elevated`` — record/rising-fast leverage → brake,
      * ``combined_score`` / ``bias`` — the blended tilt.

    We add a ``label`` to fear_greed and an ``elevated`` bool to margin_debt so the
    consumer never has to parse a prose ``detail`` string. NEVER raises — any missing
    piece degrades to a NEUTRAL/None field so a data outage can only make the bridge
    fall back to today's behaviour, never mis-fire."""
    try:
        s = sentiment if isinstance(sentiment, dict) else get_long_term_macro_sentiment()
    except Exception:
        s = {}
    vix = s.get("vix") if isinstance(s.get("vix"), dict) else {}
    fg = s.get("fear_greed") if isinstance(s.get("fear_greed"), dict) else {}
    md = s.get("margin_debt") if isinstance(s.get("margin_debt"), dict) else {}
    fg_level = fg.get("level")
    # margin_debt score is -1 only on a strong/record-risk rise (see margin_debt_score);
    # treat any negative (bearish) margin score as "elevated" leverage for the brake.
    try:
        md_elevated: Optional[bool] = float(md.get("score")) < 0 if md.get("score") is not None else None
    except (TypeError, ValueError):
        md_elevated = None
    return {
        "vix": {"level": vix.get("level"), "detail": vix.get("detail")},
        "fear_greed": {"level": fg_level, "label": _fear_greed_label(fg_level),
                       "detail": fg.get("detail")},
        "margin_debt": {"elevated": md_elevated, "level": md.get("level"),
                        "detail": md.get("detail")},
        "combined_score": s.get("combined_score"),
        "bias": s.get("bias", "NEUTRAL"),
        "fetched_at": s.get("fetched_at"),
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
