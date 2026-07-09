"""
sec_agent.py — SEC EDGAR Filing Watcher Agent

Monitors EDGAR RSS feeds for your holdings + watchlist.
When a new 10-K, 10-Q, or 8-K is published, Claude reads and extracts:
  - Revenue trend, cash burn, debt changes
  - Management guidance language (bullish / hedging / warning signals)
  - Key risk factor changes vs prior filing
  - Red flags or positive surprises

No API key needed — EDGAR is fully public.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Any

import anthropic
import requests
from dotenv import load_dotenv

load_dotenv()

EDGAR_RSS   = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&type={form}&dateb=&owner=include&count=10&search_text=&CIK={cik}&output=atom"
EDGAR_SEARCH = "https://efts.sec.gov/LATEST/search-index?q=%22{symbol}%22&dateRange=custom&startdt={start}&enddt={end}&forms={forms}"
EDGAR_TICKER = "https://www.sec.gov/cgi-bin/browse-edgar?company=&CIK={symbol}&type=&dateb=&owner=include&count=1&search_text=&action=getcompany&output=atom"
HEADERS      = {"User-Agent": "GeshFinancial/1.0 georgy75@gmail.com"}

FILING_TYPES = ["10-K", "10-Q", "8-K"]

# Cap fresh Claude analyses per dashboard run — without this, 10 holdings × 3 forms × 2
# filings can queue 60+ sequential Haiku calls (~15-30 min) while the UI shows no progress.
MAX_ANALYSES_PER_RUN = int(os.getenv("GESH_SEC_MAX_ANALYSES", "6"))

SEC_ANALYSIS_PROMPT = """You are an expert SEC filing analyst. Analyze this filing excerpt and extract key financial intelligence.

Return ONLY valid JSON — no markdown. Use double-quotes only. No apostrophes inside strings.

{
  "filing_type": "<10-K|10-Q|8-K>",
  "company": "<company name>",
  "period": "<reporting period>",
  "sentiment": "<POSITIVE|NEUTRAL|NEGATIVE|MIXED>",
  "revenue_trend": "<ACCELERATING|STABLE|DECELERATING|DECLINING|N/A>",
  "key_findings": [
    "<specific finding with numbers where available>"
  ],
  "red_flags": [
    "<specific concern — only include if genuinely alarming>"
  ],
  "positive_signals": [
    "<specific strength or catalyst>"
  ],
  "guidance_tone": "<RAISED|MAINTAINED|LOWERED|WITHDRAWN|N/A>",
  "guidance_detail": "<one sentence on management forward guidance>",
  "action_signal": "<BULLISH|NEUTRAL|BEARISH>",
  "summary": "<3-4 sentence plain English summary of the most important takeaways for an investor>",
  "urgency": "<HIGH|MEDIUM|LOW>"
}

Be specific — reference actual numbers, percentages, dollar amounts from the filing.
HIGH urgency = earnings miss/beat >10%, major guidance change, unexpected executive departure, regulatory action."""


def _get_cik(symbol: str) -> str | None:
    """Resolve a ticker symbol to an SEC CIK number."""
    try:
        # Use EDGAR's company search API
        url = f"https://www.sec.gov/cgi-bin/browse-edgar?company=&CIK={symbol}&type=10-Q&dateb=&owner=include&count=1&search_text=&action=getcompany&output=atom"
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code != 200:
            return None
        root = ET.fromstring(r.text)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        # Extract CIK from the first entry id
        for entry in root.findall("atom:entry", ns):
            link = entry.find("atom:id", ns)
            if link is not None:
                m = re.search(r'CIK=(\d+)', link.text or "")
                if m:
                    return m.group(1).zfill(10)
        return None
    except Exception:
        return None


def _fetch_recent_filings(cik: str, form_type: str, days_back: int = 7) -> list[dict]:
    """Fetch recent filings of a given type from EDGAR RSS."""
    try:
        url = EDGAR_RSS.format(form=form_type, cik=cik)
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code != 200:
            return []

        root = ET.fromstring(r.text)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days_back)
        filings = []

        for entry in root.findall("atom:entry", ns):
            updated_el = entry.find("atom:updated", ns)
            if updated_el is None:
                continue
            try:
                updated = datetime.fromisoformat(updated_el.text.replace("Z", "+00:00").replace("+00:00", ""))
            except Exception:
                continue
            if updated < cutoff:
                continue

            title_el = entry.find("atom:title", ns)
            link_el  = entry.find("atom:link", ns)
            filings.append({
                "title":   title_el.text if title_el is not None else "",
                "url":     link_el.get("href", "") if link_el is not None else "",
                "date":    updated.isoformat(),
                "form":    form_type,
            })

        return filings
    except Exception:
        return []


def _fetch_filing_text(filing_url: str, max_chars: int = 8000) -> str:
    """Fetch and strip the text content of an SEC filing index page."""
    try:
        r = requests.get(filing_url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return ""
        # Remove HTML tags
        text = re.sub(r'<[^>]+>', ' ', r.text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text[:max_chars]
    except Exception:
        return ""


def _analyze_filing(symbol: str, filing: dict, text: str) -> dict[str, Any]:
    """Run Claude analysis on a filing excerpt."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return {"error": "No API key"}
    client = anthropic.Anthropic(api_key=api_key)

    try:
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",  # fast + cheap for filing parsing
            max_tokens=1500,
            system=SEC_ANALYSIS_PROMPT,
            messages=[{
                "role": "user",
                "content": (
                    f"Analyze this {filing['form']} filing for {symbol}.\n"
                    f"Filing title: {filing['title']}\n"
                    f"Filed: {filing['date']}\n\n"
                    f"Filing content excerpt:\n{text}\n\n"
                    "Return ONLY valid JSON starting with {{"
                )
            }]
        )
        raw = msg.content[0].text
        # Parse JSON
        raw = re.sub(r'^```(?:json)?\s*', '', raw.strip())
        raw = re.sub(r'\s*```$', '', raw)
        return json.loads(raw)
    except Exception as e:
        return {"error": str(e), "raw_text": text[:500]}


def _save_to_db(symbol: str, filing: dict, analysis: dict) -> None:
    """Save filing analysis to SQLite."""
    from agent_db import agent_db_path
    db_path = str(agent_db_path())
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sec_filings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                form_type TEXT,
                filing_date TEXT,
                filing_url TEXT,
                analysis_json TEXT,
                action_signal TEXT,
                urgency TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        # A parse failure must not masquerade as a real NEUTRAL/LOW signal —
        # mark it explicitly so it isn't mistaken for a clean read.
        if analysis.get("error"):
            action_signal, urgency = "PARSE_ERROR", "UNKNOWN"
        else:
            action_signal = analysis.get("action_signal", "NEUTRAL")
            urgency = analysis.get("urgency", "LOW")
        conn.execute(
            "INSERT INTO sec_filings (symbol, form_type, filing_date, filing_url, analysis_json, action_signal, urgency) VALUES (?,?,?,?,?,?,?)",
            (
                symbol,
                filing.get("form", ""),
                filing.get("date", ""),
                filing.get("url", ""),
                json.dumps(analysis),
                action_signal,
                urgency,
            )
        )
        conn.commit()
    finally:
        conn.close()


def _filing_already_in_db(filing_url: str) -> bool:
    """True when this filing URL was already analyzed — skip redundant Claude calls."""
    if not filing_url:
        return False
    from agent_db import agent_db_path
    db_path = str(agent_db_path())
    if not os.path.exists(db_path):
        return False
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT 1 FROM sec_filings WHERE filing_url = ? LIMIT 1",
            (filing_url,),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def _get_recent_from_db(symbols: list[str], days: int = 30) -> list[dict]:
    """Retrieve recent SEC analyses from DB."""
    from agent_db import agent_db_path
    db_path = str(agent_db_path())
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sec_filings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                form_type TEXT,
                filing_date TEXT,
                filing_url TEXT,
                analysis_json TEXT,
                action_signal TEXT,
                urgency TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        cutoff = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)).isoformat()
        placeholders = ",".join("?" * len(symbols))
        rows = conn.execute(
            f"SELECT symbol, form_type, filing_date, filing_url, analysis_json, action_signal, urgency, created_at FROM sec_filings WHERE symbol IN ({placeholders}) AND created_at >= ? ORDER BY created_at DESC LIMIT 50",
            (*symbols, cutoff)
        ).fetchall()
        return [
            {
                "symbol": r[0], "form_type": r[1], "filing_date": r[2],
                "filing_url": r[3], "analysis": json.loads(r[4]) if r[4] else {},
                "action_signal": r[5], "urgency": r[6], "analyzed_at": r[7],
            }
            for r in rows
        ]
    finally:
        conn.close()


def watch_filings(symbols: list[str], days_back: int = 7) -> dict[str, Any]:
    """
    Main entry point — watch SEC filings for a list of symbols.

    Returns
    -------
    {
      "new_filings": int,
      "results": [{symbol, form, date, analysis, action_signal, urgency}],
      "as_of": str
    }
    """
    results: list[dict] = []
    new_count = 0
    skipped_cached = 0
    analyses_done = 0

    for symbol in symbols:
        if analyses_done >= MAX_ANALYSES_PER_RUN:
            break
        print(f"  SEC: checking {symbol}...")
        cik = _get_cik(symbol)
        if not cik:
            print(f"    ⚠ Could not resolve CIK for {symbol}")
            continue

        for form_type in FILING_TYPES:
            if analyses_done >= MAX_ANALYSES_PER_RUN:
                break
            filings = _fetch_recent_filings(cik, form_type, days_back=days_back)
            for filing in filings[:1]:  # newest filing per type — enough for the dashboard
                if analyses_done >= MAX_ANALYSES_PER_RUN:
                    break
                if _filing_already_in_db(filing["url"]):
                    skipped_cached += 1
                    continue
                text = _fetch_filing_text(filing["url"])
                if not text:
                    continue
                analysis = _analyze_filing(symbol, filing, text)
                _save_to_db(symbol, filing, analysis)
                results.append({
                    "symbol":        symbol,
                    "form_type":     form_type,
                    "filing_date":   filing["date"],
                    "filing_url":    filing["url"],
                    "action_signal": analysis.get("action_signal", "NEUTRAL"),
                    "urgency":       analysis.get("urgency", "LOW"),
                    "analysis":      analysis,
                })
                new_count += 1
                analyses_done += 1

    # When everything was cached (or no new EDGAR hits), still return DB rows so the
    # dashboard renders immediately instead of looking like a failed run.
    display = results if results else _get_recent_from_db(symbols, days=days_back)

    return {
        "new_filings": new_count,
        "skipped_cached": skipped_cached,
        "results": display,
        "as_of": datetime.now(timezone.utc).isoformat() + "Z",
    }


def get_filing_history(symbols: list[str], days: int = 30) -> list[dict]:
    """Return stored filing analyses from DB without hitting EDGAR."""
    return _get_recent_from_db(symbols, days=days)
