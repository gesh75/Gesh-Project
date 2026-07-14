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

# CNN's dataviz API — the one live Fear & Greed source — returns HTTP 418 to bare
# user agents, so it needs a full browser-like header set. The graphdata endpoint
# carries BOTH the current score and the daily series in one call, which is why the
# old page-scrape (fragile regex) and the long-dead GitHub CSV fallback are gone —
# they returned no / empty / 6-year-stale data.
_CNN_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.cnn.com/markets/fear-and-greed",
    "Origin": "https://www.cnn.com",
}


def fetch_fear_greed() -> Optional[pd.DataFrame]:
    """Fetch the CNN Business Fear & Greed Index (0-100 contrarian sentiment gauge).

    Single source: CNN's official dataviz graphdata API, which returns the current
    score plus the daily historical series in one call. Requires full browser
    headers (the endpoint 418s bare agents). On any failure returns None — an honest
    'no data' the caller degrades cleanly, never a stale or empty frame."""
    cache = _load_cache(F_G_CACHE)
    if cache and _cache_valid(F_G_CACHE):
        try:
            return pd.DataFrame(cache["data"])
        except Exception:
            pass
    try:
        start = (datetime.utcnow() - timedelta(days=40)).strftime("%Y-%m-%d")
        url = f"https://production.dataviz.cnn.io/index/fearandgreed/graphdata/{start}"
        resp = requests.get(url, headers=_CNN_HEADERS, timeout=10)
        if resp.status_code != 200:
            logger.warning("F&G graphdata HTTP %s", resp.status_code)
            return None
        hist = (resp.json().get("fear_and_greed_historical") or {}).get("data") or []
        if not hist:
            return None
        df = pd.DataFrame(hist)
        df["date"] = pd.to_datetime(df["x"], unit="ms").dt.date
        df = df[["date", "y"]].rename(columns={"y": "fear_greed"}).dropna()
        if df.empty:
            return None
        _save_cache(F_G_CACHE, {"fetched_at": datetime.utcnow().isoformat(),
                                "data": df.to_dict("records")})
        return df
    except Exception as e:
        logger.warning("F&G fetch failed: %s", e)
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

    # Availability: each sub-score returns level=None ("no data") when its source is
    # down. If VIX and F&G are BOTH unavailable the macro read is not real — the
    # consensus node must ABSTAIN (None), not cast a neutral 0 that dilutes the tally
    # (Codex #5). margin_debt alone is too weak to stand as the sole live source.
    sources_live = [vix.get("level") is not None, fg.get("level") is not None]

    return {
        "vix": vix,
        "fear_greed": fg,
        "margin_debt": md,
        "combined_score": round(norm, 2),
        "bias": "BULLISH" if norm > 0.3 else "BEARISH" if norm < -0.3 else "NEUTRAL",
        "available": any(sources_live),
        "sources_live": sum(1 for x in sources_live if x),
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
    # A genuinely UNAVAILABLE margin reading (status != "ok") is UNKNOWN, not a definite
    # "not elevated" — degrade to None so the downstream leverage brake never reads an
    # outage as a confirmed all-clear (status is absent in unit stubs → falls through
    # to the score-based path, preserving the score-only contract).
    try:
        if md.get("status") not in (None, "ok"):
            md_elevated: Optional[bool] = None
        elif md.get("score") is not None:
            md_elevated = float(md.get("score")) < 0
        else:
            md_elevated = None
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
    detail = f"VIX={sent['vix']['detail']}; FG={sent['fear_greed']['detail']}; MD={sent['margin_debt']['detail']}"
    # Abstain (None) when the macro data is unavailable — a missing read must NOT count
    # as a neutral vote in the consensus denominator (Codex #5).
    if not sent.get("available", True):
        return None, f"LT-Macro unavailable (no live sources) — {detail}"
    score = sent["combined_score"]
    if score > 0.3:
        return 1, detail  # BULLISH tilt for long-term
    if score < -0.3:
        return -1, detail
    return 0, detail


if __name__ == "__main__":
    print(get_long_term_macro_sentiment())
