"""
news_agent.py — News Sentiment Tracker Agent

Monitors financial news for your holdings + watchlist using free sources:
  1. Yahoo Finance RSS (no key needed)
  2. Finnhub company news API (free tier: 60 calls/min)
  3. Seeking Alpha RSS (no key needed)

Claude scores each article:
  - Sentiment: POSITIVE / NEGATIVE / NEUTRAL
  - Impact level: HIGH / MEDIUM / LOW
  - Key facts extracted
  - Rolling 7-day sentiment score per company

Macro overlay: FRED macro signal from regime.py (Fed Funds Rate, yield curve, CPI).
Replaces Polymarket scraper which was removed — FRED is more reliable and has no
rate limits. Both were providing the same signal; regime.py's version has VIX + HY OAS.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from dotenv import load_dotenv

import llm_gateway

load_dotenv()

HAIKU_MODEL = llm_gateway.resolve_model("haiku", default="haiku")

HEADERS = {"User-Agent": "GeshFinancial/2.0 georgy75@gmail.com"}

# Re-use DB sentiment younger than this — avoids N sequential Haiku calls every click.
NEWS_CACHE_HOURS = int(os.getenv("GESH_NEWS_CACHE_HOURS", "12"))
MAX_NEWS_SYMBOLS = int(os.getenv("GESH_NEWS_MAX_SYMBOLS", "12"))

# ─── Free news sources ────────────────────────────────────────────────────────


def _fetch_yahoo_news(symbol: str, max_items: int = 8) -> list[dict]:
    """Yahoo Finance RSS feed — no key needed."""
    items = []
    try:
        url = f"https://finance.yahoo.com/rss/headline?s={symbol}"
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code == 200 and "<item>" in r.text:
            root = ET.fromstring(r.text)
            for item in root.findall(".//item")[:max_items]:
                title = item.findtext("title", "")
                desc = item.findtext("description", "")
                pub = item.findtext("pubDate", "")
                link = item.findtext("link", "")
                if title:
                    items.append(
                        {
                            "title": title,
                            "description": desc[:500],
                            "published": pub,
                            "url": link,
                            "source": "Yahoo Finance",
                        }
                    )
    except Exception:
        pass
    return items


def _fetch_seeking_alpha_rss(symbol: str, max_items: int = 5) -> list[dict]:
    """Seeking Alpha RSS — no key needed."""
    items = []
    try:
        url = f"https://seekingalpha.com/api/sa/combined/{symbol.upper()}.xml"
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code == 200 and "<item>" in r.text:
            root = ET.fromstring(r.text)
            for item in root.findall(".//item")[:max_items]:
                title = item.findtext("title", "")
                desc = item.findtext("description", "") or item.findtext(
                    "{http://purl.org/rss/1.0/modules/content/}encoded", ""
                )
                pub = item.findtext("pubDate", "")
                link = item.findtext("link", "")
                # Strip HTML tags from description
                desc = re.sub(r"<[^>]+>", " ", desc)[:500]
                if title:
                    items.append(
                        {
                            "title": title,
                            "description": desc.strip(),
                            "published": pub,
                            "url": link,
                            "source": "Seeking Alpha",
                        }
                    )
    except Exception:
        pass
    return items


def _fetch_marketwatch_rss(symbol: str, max_items: int = 5) -> list[dict]:
    """MarketWatch RSS feed — no key needed."""
    items = []
    try:
        url = "https://feeds.marketwatch.com/marketwatch/realtimeheadlines/"
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code == 200 and "<item>" in r.text:
            root = ET.fromstring(r.text)
            sym_upper = symbol.upper()
            for item in root.findall(".//item")[:50]:
                title = item.findtext("title", "")
                # Filter for symbol-relevant headlines
                if (
                    sym_upper in title.upper()
                    or sym_upper in item.findtext("description", "").upper()
                ):
                    desc = item.findtext("description", "")[:500]
                    pub = item.findtext("pubDate", "")
                    link = item.findtext("link", "")
                    if title:
                        items.append(
                            {
                                "title": title,
                                "description": desc,
                                "published": pub,
                                "url": link,
                                "source": "MarketWatch",
                            }
                        )
                        if len(items) >= max_items:
                            break
    except Exception:
        pass
    return items


def _fetch_alpaca_news(symbol: str, limit: int = 8) -> list[dict]:
    """Recent news for `symbol` via Alpaca (VPN-safe, unlike the RSS feeds; key-gated
    on ALPACA_API_KEY/ALPACA_SECRET_KEY). Returns [{title,url,source,published}] or []
    on missing keys / any failure — never raises."""
    key, sec = os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY")
    if not key or not sec:
        return []
    try:
        from alpaca.data.historical.news import NewsClient  # noqa: PLC0415
        from alpaca.data.requests import NewsRequest  # noqa: PLC0415

        res = NewsClient(key, sec).get_news(NewsRequest(symbols=symbol, limit=limit))
        # The articles live under res.data['news']; res has no .news attribute. Items
        # may be dicts or objects — read either. (res.news kept as a defensive fallback.)
        data = getattr(res, "data", None)
        items = (
            (data.get("news") if isinstance(data, dict) else None)
            or getattr(res, "news", None)
            or []
        )

        def _g(a, k):
            v = a.get(k) if isinstance(a, dict) else getattr(a, k, None)
            return v if v is not None else ""

        out = []
        for a in items[:limit]:
            out.append(
                {
                    "title": _g(a, "headline"),
                    "url": _g(a, "url"),
                    "source": _g(a, "source"),
                    "published": str(_g(a, "created_at"))[:16],
                }
            )
        return out
    except Exception:
        return []


def _fetch_finnhub_news(symbol: str, days_back: int = 7) -> list[dict]:
    """Finnhub company news — free tier, no key needed for basic use."""
    try:
        api_key = os.getenv("FINNHUB_API_KEY", "")
        end_dt = datetime.now(timezone.utc).replace(tzinfo=None)
        start_dt = end_dt - timedelta(days=days_back)
        params = {
            "symbol": symbol,
            "from": start_dt.strftime("%Y-%m-%d"),
            "to": end_dt.strftime("%Y-%m-%d"),
        }
        if api_key:
            params["token"] = api_key
        r = requests.get(
            "https://finnhub.io/api/v1/company-news", params=params, headers=HEADERS, timeout=10
        )
        if r.status_code != 200:
            return []
        data = r.json()
        return [
            {
                "title": a.get("headline", ""),
                "description": a.get("summary", "")[:500],
                "published": datetime.utcfromtimestamp(a.get("datetime", 0)).isoformat(),
                "url": a.get("url", ""),
                "source": a.get("source", "Finnhub"),
            }
            for a in data[:8]
        ]
    except Exception:
        return []


def _fetch_all_news(symbol: str, days_back: int = 7) -> list[dict]:
    """Combine news from all available sources, deduplicated by title.
    Source priority: Yahoo RSS → Finnhub → Seeking Alpha RSS → MarketWatch RSS.
    """
    yahoo = _fetch_yahoo_news(symbol)
    finnhub = _fetch_finnhub_news(symbol, days_back=days_back)
    seeking_alpha = _fetch_seeking_alpha_rss(symbol) if len(yahoo) + len(finnhub) < 3 else []
    marketwatch = _fetch_marketwatch_rss(symbol) if len(yahoo) + len(finnhub) < 3 else []

    seen_titles: set[str] = set()
    combined = []
    for item in yahoo + finnhub + seeking_alpha + marketwatch:
        title_key = item["title"][:60].lower()
        if title_key not in seen_titles and item["title"]:
            seen_titles.add(title_key)
            combined.append(item)
    return combined[:12]


# ─── Claude Sentiment Analysis ────────────────────────────────────────────────

NEWS_PROMPT = """You are a financial news analyst. Analyze these news articles about {symbol} and return a JSON sentiment report.

Return ONLY valid JSON. No markdown. Use double-quotes only. No apostrophes in strings.

{{
  "symbol": "{symbol}",
  "overall_sentiment": "<VERY_POSITIVE|POSITIVE|NEUTRAL|NEGATIVE|VERY_NEGATIVE>",
  "sentiment_score": <integer -100 to +100>,
  "impact_level": "<HIGH|MEDIUM|LOW>",
  "article_count": <number>,
  "key_themes": ["<theme1>", "<theme2>", "<theme3>"],
  "positive_signals": ["<specific positive fact from news>"],
  "negative_signals": ["<specific negative fact from news>"],
  "notable_events": ["<any earnings, products, lawsuits, partnerships, executive changes>"],
  "momentum_narrative": "<2-3 sentences describing the current news narrative around this company>",
  "investor_action": "<WATCH_CLOSELY|RESEARCH_MORE|MONITOR|NO_ACTION>",
  "confidence": "<HIGH|MEDIUM|LOW based on news volume and clarity>"
}}

scoring guide: +100=extremely bullish news, 0=neutral, -100=extremely bearish/crisis news."""


def _public_articles(articles: list[dict], limit: int = 5) -> list[dict]:
    """Allow-list article dicts to the 4 public headline fields — the shape the
    dashboard "Recent News" card renders and /api/news/{symbol} trims to."""
    return [
        {
            "title": a.get("title", ""),
            "url": a.get("url", ""),
            "source": a.get("source", ""),
            "published": a.get("published", ""),
        }
        for a in articles[:limit]
    ]


def analyze_news_sentiment(symbol: str, articles: list[dict]) -> dict[str, Any]:
    """Run Claude analysis on a batch of news articles for one symbol."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key or not articles:
        return {
            "symbol": symbol,
            "overall_sentiment": "NEUTRAL",
            "sentiment_score": 0,
            "article_count": 0,
            "articles": _public_articles(articles),
        }

    articles_text = "\n\n".join(
        f"[{i + 1}] {a['title']}\nSource: {a['source']} | Published: {a['published']}\n{a['description']}"
        for i, a in enumerate(articles)
    )

    try:
        client = llm_gateway.create_client()
        msg = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=1200,
            system=NEWS_PROMPT.format(symbol=symbol),
            messages=[
                {
                    "role": "user",
                    "content": f"Analyze these {len(articles)} news articles for {symbol}:\n\n{articles_text}\n\nReturn ONLY valid JSON starting with {{",
                }
            ],
        )
        raw = msg.content[0].text
        raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
        raw = re.sub(r"\s*```$", "", raw)
        result = json.loads(raw)
        result["articles"] = _public_articles(articles)  # first 5 articles for display
        return result
    except Exception as e:
        # Keep the fetched headlines even when the model call fails — the cached
        # row still has to feed the "Recent News" card via get_cached_articles().
        return {
            "symbol": symbol,
            "overall_sentiment": "NEUTRAL",
            "sentiment_score": 0,
            "error": str(e),
            "article_count": len(articles),
            "articles": _public_articles(articles),
        }


# ─── DB persistence ───────────────────────────────────────────────────────────


def _save_sentiment(symbol: str, result: dict) -> None:
    from agent_db import agent_db_path

    db_path = str(agent_db_path())
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS news_sentiment (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                overall_sentiment TEXT,
                sentiment_score INTEGER,
                impact_level TEXT,
                analysis_json TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute(
            "INSERT INTO news_sentiment (symbol, overall_sentiment, sentiment_score, impact_level, analysis_json) VALUES (?,?,?,?,?)",
            (
                symbol,
                result.get("overall_sentiment", "NEUTRAL"),
                result.get("sentiment_score", 0),
                result.get("impact_level", "LOW"),
                json.dumps(result),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _fresh_sentiment(symbol: str, max_age_hours: int = NEWS_CACHE_HOURS) -> dict | None:
    """Return the latest cached sentiment row if younger than max_age_hours."""
    from agent_db import agent_db_path

    db_path = str(agent_db_path())
    if not os.path.exists(db_path):
        return None
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            """SELECT overall_sentiment, sentiment_score, impact_level, analysis_json, created_at
               FROM news_sentiment WHERE symbol = ? ORDER BY created_at DESC LIMIT 1""",
            (symbol.upper(),),
        ).fetchone()
        if not row:
            return None
        created = row[4]
        try:
            ts = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age_h = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
            if age_h > max_age_hours:
                return None
        except Exception:
            return None
        analysis = json.loads(row[3]) if row[3] else {}
        rolling = _compute_rolling_score(symbol, days=7)
        return {
            "symbol": symbol.upper(),
            "overall_sentiment": row[0],
            "sentiment_score": row[1],
            "rolling_score": rolling,
            "impact_level": row[2],
            "article_count": analysis.get("article_count", 0),
            "as_of": str(created),
            "analysis": analysis,
            "cached": True,
        }
    finally:
        conn.close()


def _get_sentiment_history(symbols: list[str], days: int = 7) -> list[dict]:
    from agent_db import agent_db_path

    db_path = str(agent_db_path())
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS news_sentiment (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                overall_sentiment TEXT,
                sentiment_score INTEGER,
                impact_level TEXT,
                analysis_json TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        cutoff = (
            datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
        ).isoformat()
        placeholders = ",".join("?" * len(symbols))
        rows = conn.execute(
            f"SELECT symbol, overall_sentiment, sentiment_score, impact_level, analysis_json, created_at FROM news_sentiment WHERE symbol IN ({placeholders}) AND created_at >= ? ORDER BY created_at DESC",
            (*symbols, cutoff),
        ).fetchall()
        # Return latest per symbol
        seen: set[str] = set()
        results = []
        for r in rows:
            if r[0] not in seen:
                seen.add(r[0])
                results.append(
                    {
                        "symbol": r[0],
                        "overall_sentiment": r[1],
                        "sentiment_score": r[2],
                        "impact_level": r[3],
                        "analysis": json.loads(r[4]) if r[4] else {},
                        "analyzed_at": r[5],
                    }
                )
        return results
    finally:
        conn.close()


# ─── Rolling sentiment score ──────────────────────────────────────────────────


def _compute_rolling_score(symbol: str, days: int = 7) -> float | None:
    """Compute average sentiment score over the last N days from DB."""
    from agent_db import agent_db_path

    db_path = str(agent_db_path())
    if not os.path.exists(db_path):
        return None
    conn = sqlite3.connect(db_path)
    try:
        cutoff = (
            datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
        ).isoformat()
        rows = conn.execute(
            "SELECT sentiment_score FROM news_sentiment WHERE symbol=? AND created_at>=?",
            (symbol, cutoff),
        ).fetchall()
        if not rows:
            return None
        return round(sum(r[0] for r in rows) / len(rows), 1)
    finally:
        conn.close()


# ─── Main Entry Point ─────────────────────────────────────────────────────────


def scan_news(symbols: list[str], days_back: int = 7) -> dict[str, Any]:
    """
    Scan news for all symbols, analyze sentiment, store in DB.

    Returns
    -------
    {
      "results": [{symbol, overall_sentiment, sentiment_score, impact_level, rolling_score, analysis}],
      "market_mood": "<RISK_ON|NEUTRAL|RISK_OFF>",
      "as_of": str
    }
    """
    results = []
    skipped_cached = 0
    sym_list = list(dict.fromkeys(s.upper() for s in symbols))[:MAX_NEWS_SYMBOLS]

    for symbol in sym_list:
        print(f"  NEWS: scanning {symbol}...")
        cached = _fresh_sentiment(symbol)
        if cached:
            results.append(cached)
            skipped_cached += 1
            continue

        articles = _fetch_all_news(symbol, days_back=days_back)
        if not articles:
            results.append(
                {
                    "symbol": symbol,
                    "overall_sentiment": "NEUTRAL",
                    "sentiment_score": 0,
                    "article_count": 0,
                }
            )
            continue

        analysis = analyze_news_sentiment(symbol, articles)
        _save_sentiment(symbol, analysis)

        rolling = _compute_rolling_score(symbol, days=7)
        results.append(
            {
                "symbol": symbol,
                "overall_sentiment": analysis.get("overall_sentiment", "NEUTRAL"),
                "sentiment_score": analysis.get("sentiment_score", 0),
                "rolling_score": rolling,
                "impact_level": analysis.get("impact_level", "LOW"),
                "article_count": len(articles),
                "analysis": analysis,
            }
        )

    # Compute market mood from average score
    scores = [
        r["sentiment_score"] for r in results if isinstance(r.get("sentiment_score"), (int, float))
    ]
    avg = sum(scores) / len(scores) if scores else 0
    mood = "RISK_ON" if avg > 20 else "RISK_OFF" if avg < -20 else "NEUTRAL"

    # ── FRED macro overlay (replaces Polymarket — more reliable, no rate limits) ──
    # Blend FRED macro signal with news mood: if they disagree, lean risk-off.
    # Polymarket was removed: overlaps entirely with FRED macro in regime.py.
    fred_signal = "NEUTRAL"
    try:
        from regime import get_macro_context

        macro_ctx = get_macro_context()
        fred_signal = macro_ctx.get("macro_signal", "NEUTRAL")
    except Exception:
        pass

    combined_mood = mood
    if fred_signal != "NEUTRAL":
        if mood == "RISK_ON" and fred_signal == "RISK_OFF":
            combined_mood = "NEUTRAL"  # conflicting — be cautious
        elif mood == "NEUTRAL" and fred_signal == "RISK_OFF":
            combined_mood = "RISK_OFF"
        elif mood == "NEUTRAL" and fred_signal == "RISK_ON":
            combined_mood = "RISK_ON"

    return {
        "results": results,
        "skipped_cached": skipped_cached,
        "market_mood": combined_mood,
        "news_mood": mood,
        "avg_score": round(avg, 1),
        "fred_signal": fred_signal,
        "as_of": datetime.now(timezone.utc).isoformat() + "Z",
    }


def get_news_history(symbols: list[str], days: int = 7) -> list[dict]:
    """Return stored sentiment from DB without fetching new news."""
    return _get_sentiment_history(symbols, days=days)


def get_cached_articles(symbol: str, days: int = 7, limit: int = 8) -> list[dict]:
    """Stored articles for `symbol` from cached news_sentiment rows — read-only,
    no LLM, allow-listed to the 4 public headline fields. Feeds the dashboard
    "Recent News" card when every live feed is empty (Alpaca key-gated, RSS
    blocked on the owner's VPN). Newest rows first, deduped by title; never
    raises — an unreadable cache is an empty feed, not a 500."""
    try:
        from agent_db import agent_db_path

        db_path = str(agent_db_path())
        if not os.path.exists(db_path):
            return []
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(
                "SELECT analysis_json FROM news_sentiment"
                " WHERE symbol = ? AND created_at >= ? ORDER BY created_at DESC",
                (symbol.upper(), cutoff),
            ).fetchall()
        finally:
            conn.close()
        seen: set[str] = set()
        out: list[dict] = []
        for (raw,) in rows:
            try:
                articles = (json.loads(raw) or {}).get("articles") or []
            except Exception:
                continue
            for a in articles:
                title = str(a.get("title") or "").strip()
                if not title or title.lower() in seen:
                    continue
                seen.add(title.lower())
                out.append(
                    {
                        "title": title,
                        "url": a.get("url", ""),
                        "source": a.get("source", ""),
                        "published": a.get("published", ""),
                    }
                )
                if len(out) >= limit:
                    return out
        return out
    except Exception:
        return []
