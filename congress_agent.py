"""
congress_agent.py — Congressional trade tracker (INFORMATIONAL, publisher-lane).

The legal, GESH-buildable extract of Autopilot's growth engine. Autopilot's viral
hook is the "Pelosi / Congress Tracker": it turns PUBLIC STOCK Act disclosures into
a "trade like a politician" narrative — then *executes* those trades in the user's
brokerage (copy-trading), which requires RIA registration. GESH does NOT execute.
This module surfaces the same public data as **research/commentary only**:

  * general, impersonal, regularly-published information about public disclosures;
  * no trade execution, no personalized recommendation, no tailored advice.

That is the Advisers-Act "publisher's exclusion" lane (Lowe v. SEC) — a financial
newsletter, not an adviser. Every payload carries the 45-day-lag disclaimer so the
data is never mistaken for a live signal (which is also Autopilot's #1 substantive
weakness: by the time a congressional trade is disclosed, the edge is stale).

Data source — VERIFIED 2026-07-17 (the free-no-key options are gone):
  * The old house/senate stock-watcher S3 buckets now return 403 (checked via the
    real fetch path) — they are NOT a reliable default anymore.
  * Finnhub `/stock/congressional-trading` exists but is a PREMIUM endpoint
    ($11.99–$99.99/mo); GESH's key is free-tier, so it 403s.
  * **Financial Modeling Prep (FMP)** has dedicated congressional endpoints on its
    FREE tier (JSON). This is the recommended working source — set FMP_API_KEY.
Resolution order: FMP (if FMP_API_KEY set) → CONGRESS_DATA_URL (any JSON-array URL) →
none. On ANY failure it returns an honest empty state — offline-safe, like every
other GESH agent. The page renders a clean "no disclosures available" until a source
is configured, so it is safe to ship dark and light up once a key is added.

⚠ The FMP endpoint + field mapping should be confirmed on first real response
(_norm already handles FMP's camelCase aliases); the module degrades gracefully if
the shape differs.
"""
from __future__ import annotations

import os
import time
from collections import defaultdict

import requests
from dotenv import load_dotenv

load_dotenv()

HEADERS = {"User-Agent": "GeshFinancial/1.0 georgy75@gmail.com"}

# FMP free-tier congressional endpoints (activate with FMP_API_KEY). Latest senate +
# house trades, JSON. The single confirmed FREE path as of 2026-07-17.
_FMP_KEY = os.getenv("FMP_API_KEY", "").strip()
_FMP_ENDPOINTS: tuple[str, ...] = (
    "https://financialmodelingprep.com/api/v4/senate-trading-rss-feed?page=0&apikey={k}",
    "https://financialmodelingprep.com/api/v4/senate-disclosure-rss-feed?page=0&apikey={k}",
)

# Operator-configured JSON-array source (e.g. a self-hosted dump or a working mirror).
# No baked-in default: the old public S3 buckets 403 now (see module docstring), so a
# silent default would only burn a request per refresh and imply a source that is dead.
_SOURCES: tuple[str, ...] = tuple(u for u in (os.getenv("CONGRESS_DATA_URL", "").strip(),) if u)

DISCLOSURE_LAG_NOTE = (
    "Congress discloses trades up to 45 days after they happen (STOCK Act). This is "
    "lagged public information for research — not a live signal, recommendation, or advice."
)

_CACHE: dict[str, tuple[float, list[dict]]] = {}
_TTL = int(os.getenv("CONGRESS_CACHE_TTL", "3600"))  # 1h


def _fetch_raw() -> list[dict]:
    """Fetch the raw transaction list from the first working source. Cached; offline-safe."""
    now = time.monotonic()
    hit = _CACHE.get("_raw")
    if hit and now - hit[0] < _TTL:
        return hit[1]
    rows: list[dict] = []
    # 1) FMP free tier (keyed) — merge the senate + house latest feeds.
    if _FMP_KEY:
        for tmpl in _FMP_ENDPOINTS:
            try:
                r = requests.get(tmpl.format(k=_FMP_KEY), headers=HEADERS, timeout=12)
                if r.ok and isinstance(data := r.json(), list):
                    rows.extend(data)
            except Exception:  # noqa: BLE001 — best-effort; degrade to empty
                continue
    # 2) Operator-configured JSON-array URL (fallback / self-host).
    if not rows:
        for url in _SOURCES:
            try:
                r = requests.get(url, headers=HEADERS, timeout=12)
                if r.ok and isinstance(data := r.json(), list) and data:
                    rows = data
                    break
            except Exception:  # noqa: BLE001 — best-effort; degrade to empty
                continue
    _CACHE["_raw"] = (now, rows)
    return rows


def _norm(txn: dict) -> dict | None:
    """Normalize a stock-watcher row to GESH's shape. Returns None for junk rows."""
    if not isinstance(txn, dict):
        return None
    sym = str(txn.get("ticker") or txn.get("symbol") or "").strip().upper()
    if not sym or sym in ("--", "N/A", "NONE"):
        return None
    action = str(txn.get("type") or txn.get("transaction") or "").lower()
    fmp_name = " ".join(p for p in (txn.get("firstName"), txn.get("lastName")) if p).strip()
    return {
        "symbol": sym,
        "politician": (txn.get("representative") or txn.get("senator") or txn.get("name")
                       or fmp_name or txn.get("office") or "Unknown"),
        "action": action,                                   # purchase / sale / exchange
        "is_buy": ("purchase" in action or "buy" in action),
        "amount": str(txn.get("amount") or txn.get("range") or ""),
        "tx_date": str(txn.get("transaction_date") or txn.get("transactionDate")
                       or txn.get("date") or ""),
        "disclosed": str(txn.get("disclosure_date") or txn.get("disclosureDate")
                         or txn.get("dateRecieved") or ""),
    }


def _sort_key(r: dict) -> str:
    return r.get("disclosed") or r.get("tx_date") or ""


def recent_activity(limit: int = 40) -> dict:
    """Most recently DISCLOSED congressional transactions, newest first. Informational."""
    rows = sorted((n for t in _fetch_raw() if (n := _norm(t))), key=_sort_key, reverse=True)
    return {"disclaimer": DISCLOSURE_LAG_NOTE, "count": len(rows), "trades": rows[:limit]}


def leaderboard(top: int = 15) -> dict:
    """Most-BOUGHT tickers by Congress (by count of buy disclosures) — the shareable
    'what is Congress buying' leaderboard, framed strictly as data."""
    buys: dict[str, int] = defaultdict(int)
    for t in _fetch_raw():
        n = _norm(t)
        if n and n["is_buy"]:
            buys[n["symbol"]] += 1
    ranked = sorted(buys.items(), key=lambda kv: kv[1], reverse=True)[:top]
    return {"disclaimer": DISCLOSURE_LAG_NOTE,
            "top_bought": [{"symbol": s, "buy_disclosures": c} for s, c in ranked]}


def for_symbol(symbol: str, limit: int = 20) -> dict:
    """Congressional trades for one ticker — attaches to the ticker research card."""
    sym = str(symbol).strip().upper()
    rows = sorted((n for t in _fetch_raw() if (n := _norm(t)) and n["symbol"] == sym),
                  key=_sort_key, reverse=True)
    return {"symbol": sym, "disclaimer": DISCLOSURE_LAG_NOTE,
            "count": len(rows), "trades": rows[:limit]}


def render_watch_html(data: dict | None = None) -> str:
    """Public, shareable Congress-Watch page (the acquisition magnet). Server-rendered,
    ALL dynamic strings escaped. Informational; links to /track-record and /pricing."""
    import html as _h

    data = data or {"top_bought": [], "recent": []}
    lb = data.get("top_bought") or []
    recent = data.get("recent") or []
    e = _h.escape

    lb_rows = "".join(
        f"<tr><td style='padding:8px 14px;font-weight:700'>{i+1}</td>"
        f"<td style='padding:8px 14px;color:#00e87a;font-weight:700'>{e(str(r.get('symbol','')))}</td>"
        f"<td style='padding:8px 14px'>{e(str(r.get('buy_disclosures','')))}</td></tr>"
        for i, r in enumerate(lb)
    ) or "<tr><td colspan='3' style='padding:14px;color:#888'>No recent disclosures available.</td></tr>"

    recent_rows = "".join(
        f"<tr><td style='padding:6px 14px;color:#00e87a'>{e(str(r.get('symbol','')))}</td>"
        f"<td style='padding:6px 14px'>{e(str(r.get('politician','')))}</td>"
        f"<td style='padding:6px 14px;color:{'#00e87a' if r.get('is_buy') else '#ff6b6b'}'>"
        f"{e(str(r.get('action','')).title())}</td>"
        f"<td style='padding:6px 14px'>{e(str(r.get('amount','')))}</td>"
        f"<td style='padding:6px 14px;color:#aaa'>{e(str(r.get('disclosed','')))}</td></tr>"
        for r in recent[:25]
    ) or "<tr><td colspan='5' style='padding:14px;color:#888'>No recent disclosures available.</td></tr>"

    return f"""<!doctype html><html><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'>
<title>Congress Trade Watch · GESH</title>
<meta property='og:title' content='What is Congress buying? · GESH Congress Trade Watch'>
<meta property='og:description' content='Live tracker of public congressional stock disclosures — free, informational.'>
<style>body{{background:#0b0e13;color:#e8edf2;font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;margin:0}}
.wrap{{max-width:860px;margin:0 auto;padding:28px 18px}}h1{{font-size:26px;margin:0 0 4px}}
table{{width:100%;border-collapse:collapse;margin:10px 0 26px}}th{{text-align:left;padding:8px 14px;color:#8b98a5;font-size:12px;text-transform:uppercase;border-bottom:1px solid #232a33}}
tr:nth-child(even){{background:#11151c}}.note{{background:#14181f;border-left:3px solid #f0b429;padding:10px 14px;border-radius:6px;color:#cbb;font-size:13px;margin:14px 0}}
a{{color:#00e87a;text-decoration:none}}.cta{{display:inline-block;margin-top:8px;padding:10px 18px;background:#00e87a;color:#04120a;border-radius:8px;font-weight:700}}</style>
</head><body><div class='wrap'>
<h1>🏛️ Congress Trade Watch</h1>
<p style='color:#8b98a5;margin:0 0 14px'>Public STOCK Act disclosures, tracked and organized — free.</p>
<div class='note'>{e(DISCLOSURE_LAG_NOTE)}<br>Not affiliated with any government official. GESH does not execute trades on your behalf.</div>
<h3>Most-bought tickers by Congress (recent disclosures)</h3>
<table><thead><tr><th>#</th><th>Ticker</th><th>Buy disclosures</th></tr></thead><tbody>{lb_rows}</tbody></table>
<h3>Latest disclosed transactions</h3>
<table><thead><tr><th>Ticker</th><th>Member</th><th>Action</th><th>Amount</th><th>Disclosed</th></tr></thead><tbody>{recent_rows}</tbody></table>
<p>Want AI analysis on any of these — regime, fundamentals, consensus, risk? <a href='/pricing'>See GESH</a> · <a href='/track-record'>verified track record</a>.</p>
<a class='cta' href='/pricing'>Analyze these tickers with GESH →</a>
<p style='color:#5a6570;font-size:12px;margin-top:24px'>Informational and educational only. Not financial advice. Congressional disclosure data is public and lagged.</p>
</div></body></html>"""


def load_watch_data() -> dict:
    """Bundle the public page's data in one shot (leaderboard + recent)."""
    return {"top_bought": leaderboard().get("top_bought", []),
            "recent": recent_activity(limit=25).get("trades", [])}
