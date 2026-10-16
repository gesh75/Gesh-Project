"""
congress_agent.py — Congressional trade tracker (INFORMATIONAL, publisher-lane).

The GESH-buildable extract of Autopilot's growth engine. Autopilot's viral hook is
the "Pelosi / Congress Tracker": it turns PUBLIC STOCK Act disclosures into a
"trade like a politician" narrative — then *executes* those trades in the user's
brokerage (copy-trading), which requires RIA registration. GESH does NOT execute.
This module surfaces the same public data as **research/commentary only**:

  * general, impersonal, regularly-published information about public disclosures;
  * no trade execution, no personalized recommendation, no tailored advice.

It is DESIGNED for the Advisers-Act "publisher's exclusion" lane (Lowe v. SEC) —
that is a design goal, NOT a legal conclusion. The overall paid-product funnel
(tracker → /pricing) still needs securities-counsel review before launch; the
disclaimers here do not by themselves settle adviser status.

Attribution discipline: rows are reported *on a member's filing* — the actual
filer may be the member, a spouse, or a dependent (the source's `owner` field).
We preserve and display `owner` and never claim the member personally traded.

Data source — VERIFIED 2026-07-17 (the free-no-key options are gone):
  * The old house/senate stock-watcher S3 buckets now return 403 (checked via the
    real fetch path) — they are NOT a reliable default anymore.
  * Finnhub `/stock/congressional-trading` exists but is a PREMIUM endpoint
    ($11.99–$99.99/mo); GESH's key is free-tier, so it 403s.
  * **Financial Modeling Prep (FMP)** documents congressional endpoints for the
    Senate and House as SEPARATE feeds (`/stable/senate-latest`,
    `/stable/house-latest`). Set FMP_API_KEY to enable.
    ⚠ LICENSING: redisplaying FMP data on a PUBLIC page generally requires an
    FMP commercial/display agreement — resolve that before enabling in prod.
Resolution order: FMP (if FMP_API_KEY set) → CONGRESS_DATA_URL (any JSON-array URL) →
none. On ANY failure the module serves the last good cached rows (stale-if-error)
and flags degraded status; with no prior cache it returns an honest empty state —
offline-safe, like every other GESH agent.

⚠ The exact FMP field mapping should be confirmed against a live response
(_norm handles FMP's camelCase aliases and never raises on unexpected shapes).
"""
from __future__ import annotations

import os
import threading
import time
from collections import defaultdict

import requests
from dotenv import load_dotenv

load_dotenv()

HEADERS = {"User-Agent": "GeshFinancial/1.0 georgy75@gmail.com"}

# FMP congressional endpoints (activate with FMP_API_KEY). Senate and House are
# SEPARATE feeds — both are required or every House trade is silently omitted.
# Resolved at CALL time by _fmp_key() so a rotated key takes effect without a restart
# and tests can monkeypatch.setenv. The module attribute is an OVERRIDE slot: None means
# "read the environment"; a str (tests pin "" or "k") beats the environment.
_FMP_KEY: str | None = None
_FMP_ENDPOINTS: tuple[str, ...] = (
    "https://financialmodelingprep.com/stable/senate-latest?apikey={k}",
    "https://financialmodelingprep.com/stable/house-latest?apikey={k}",
)

# Operator-configured JSON-array source (e.g. a self-hosted dump or a working mirror).
# No baked-in default: the old public S3 buckets 403 now (see module docstring), so a
# silent default would only burn a request per refresh and imply a source that is dead.
# Same override-slot convention as _FMP_KEY; see _sources().
_SOURCES: tuple[str, ...] | None = None


def _fmp_key() -> str:
    """FMP API key: module override if a test pinned one, else the live environment."""
    if _FMP_KEY is not None:
        return _FMP_KEY
    return os.getenv("FMP_API_KEY", "").strip()


def _sources() -> tuple[str, ...]:
    """Operator JSON-array URLs: module override if pinned, else CONGRESS_DATA_URL now."""
    if _SOURCES is not None:
        return _SOURCES
    url = os.getenv("CONGRESS_DATA_URL", "").strip()
    return (url,) if url else ()

DISCLOSURE_LAG_NOTE = (
    "Congress discloses trades up to 45 days after they happen (STOCK Act). This is "
    "lagged public information for research — not a live signal, recommendation, or advice."
)

FILING_ATTRIBUTION_NOTE = (
    "Transactions are reported on the named member's disclosure filing — the filer "
    "may be the member, a spouse, or a dependent, and amounts are reported as ranges."
)

LEADERBOARD_WINDOW = (
    "Counts buy disclosures within the latest feed window (the most recent "
    "disclosures returned per chamber) — a rolling recent sample, not full history."
)

# Raw-row cache: "_raw" → (monotonic_ts, wallclock_ts, rows). 1h TTL.
_CACHE: dict[str, tuple[float, float, list[dict]]] = {}
_TTL = int(os.getenv("CONGRESS_CACHE_TTL", "3600"))  # 1h

# Single-flight + outage state. last_attempt throttles retries so a down source
# can't be stampeded by public traffic; degraded/last_error feed source_status().
_FETCH_LOCK = threading.Lock()
_STATE = {"last_attempt": 0.0, "degraded": False, "last_error": None}
_RETRY_BACKOFF_S = 60  # min seconds between upstream attempts after a failure


def _fetch_from_sources() -> list[dict]:
    """One upstream sweep: FMP senate + house (keyed), then the operator URL."""
    rows: list[dict] = []
    # 1) FMP (keyed) — merge the senate + house latest feeds.
    key = _fmp_key()
    if key:
        for tmpl in _FMP_ENDPOINTS:
            try:
                r = requests.get(tmpl.format(k=key), headers=HEADERS, timeout=12)
                if r.ok and isinstance(data := r.json(), list):
                    rows.extend(data)
            except Exception:  # noqa: BLE001 — best-effort; degrade to empty
                continue
    # 2) Operator-configured JSON-array URL (fallback / self-host).
    if not rows:
        for url in _sources():
            try:
                r = requests.get(url, headers=HEADERS, timeout=12)
                if r.ok and isinstance(data := r.json(), list) and data:
                    rows = data
                    break
            except Exception:  # noqa: BLE001 — best-effort; degrade to empty
                continue
    return rows


def _fetch_raw() -> list[dict]:
    """Raw transaction list — 1h cached, single-flight, stale-if-error.

    A failed refresh NEVER overwrites good cached data: the last successful rows
    keep being served (flagged degraded in source_status()) until a retry succeeds.
    Concurrent cold requests collapse onto one upstream call via _FETCH_LOCK, and
    retries are rate-limited to one attempt per _RETRY_BACKOFF_S."""
    now = time.monotonic()
    hit = _CACHE.get("_raw")
    if hit and now - hit[0] < _TTL:
        return hit[2]
    with _FETCH_LOCK:
        # Re-check under the lock: only one thread fetches; the rest get the fill.
        now = time.monotonic()
        hit = _CACHE.get("_raw")
        if hit and now - hit[0] < _TTL:
            return hit[2]
        if now - _STATE["last_attempt"] < _RETRY_BACKOFF_S:
            return hit[2] if hit else []
        _STATE["last_attempt"] = now
        rows = _fetch_from_sources()
        if rows:
            _CACHE["_raw"] = (now, time.time(), rows)
            _STATE["degraded"] = False
            _STATE["last_error"] = None
            return rows
        # Failure: stale-if-error — keep serving the previous good data, flag it.
        _STATE["degraded"] = bool(_fmp_key() or _sources())
        _STATE["last_error"] = "all configured sources failed or returned no data"
        if hit:
            return hit[2]
        _CACHE["_raw"] = (now, time.time(), [])
        return []


def source_status() -> dict:
    """Provenance for the public surfaces: source, chambers covered, fetched-at,
    and degraded flag — so an outage is never presented as successful empty data."""
    hit = _CACHE.get("_raw")
    key = _fmp_key()
    configured = bool(key or _sources())
    return {
        "configured": configured,
        "source": ("fmp" if key else "custom-url") if configured else None,
        "chambers": (["senate", "house"] if key else []) if configured else [],
        "fetched_at": (time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(hit[1]))
                       if hit else None),
        "degraded": _STATE["degraded"],
        "note": _STATE["last_error"] if _STATE["degraded"] else None,
    }


def _norm(txn: dict) -> dict | None:
    """Normalize a source row to GESH's shape. Returns None for junk rows.

    NEVER raises: every field is str()-coerced and the whole body is guarded —
    one malformed record is discarded, not a 500 for every Congress surface.
    `owner` is preserved (e.g. "Spouse") so filings are never misattributed to
    the member personally; `link` keeps the original filing URL when provided.
    """
    try:
        if not isinstance(txn, dict):
            return None
        sym = str(txn.get("ticker") or txn.get("symbol") or "").strip().upper()
        if not sym or sym in ("--", "N/A", "NONE"):
            return None
        action = str(txn.get("type") or txn.get("transaction") or "").lower()
        fmp_name = " ".join(
            str(p) for p in (txn.get("firstName"), txn.get("lastName")) if p
        ).strip()
        return {
            "symbol": sym,
            "politician": str(txn.get("representative") or txn.get("senator")
                              or txn.get("name") or fmp_name
                              or txn.get("office") or "Unknown"),
            "owner": str(txn.get("owner") or "").strip(),
            "link": str(txn.get("link") or txn.get("filingUrl")
                        or txn.get("url") or "").strip(),
            "action": action,                                   # purchase / sale / exchange
            "is_buy": ("purchase" in action or "buy" in action),
            "amount": str(txn.get("amount") or txn.get("range") or ""),
            "tx_date": str(txn.get("transaction_date") or txn.get("transactionDate")
                           or txn.get("date") or ""),
            "disclosed": str(txn.get("disclosure_date") or txn.get("disclosureDate")
                           or txn.get("dateRecieved") or ""),
        }
    except Exception:  # noqa: BLE001 — a bad row must never take the page down
        return None


def _normalized() -> list[dict]:
    """All rows normalized, junk dropped, exact duplicates removed.

    Dedup key = the full disclosure tuple: the same trade can appear in multiple
    feeds (and across refetches), which would otherwise double-count the
    leaderboard."""
    out: list[dict] = []
    seen: set[tuple] = set()
    for t in _fetch_raw():
        n = _norm(t)
        if not n:
            continue
        key = (n["symbol"], n["politician"], n["action"],
               n["amount"], n["tx_date"], n["disclosed"])
        if key in seen:
            continue
        seen.add(key)
        out.append(n)
    return out


def _sort_key(r: dict) -> str:
    return r.get("disclosed") or r.get("tx_date") or ""


def recent_activity(limit: int = 40) -> dict:
    """Most recently DISCLOSED congressional transactions, newest first. Informational."""
    rows = sorted(_normalized(), key=_sort_key, reverse=True)
    return {"disclaimer": DISCLOSURE_LAG_NOTE, "attribution": FILING_ATTRIBUTION_NOTE,
            "count": len(rows), "trades": rows[:limit]}


def leaderboard(top: int = 15) -> dict:
    """Most-BOUGHT tickers in recent congressional filings (by count of buy
    disclosures), over a defined rolling window — framed strictly as data."""
    buys: dict[str, int] = defaultdict(int)
    for n in _normalized():
        if n["is_buy"]:
            buys[n["symbol"]] += 1
    ranked = sorted(buys.items(), key=lambda kv: kv[1], reverse=True)[:top]
    return {"disclaimer": DISCLOSURE_LAG_NOTE, "attribution": FILING_ATTRIBUTION_NOTE,
            "window": LEADERBOARD_WINDOW,
            "top_bought": [{"symbol": s, "buy_disclosures": c} for s, c in ranked]}


def for_symbol(symbol: str, limit: int = 20) -> dict:
    """Congressional trades for one ticker — attaches to the ticker research card."""
    sym = str(symbol).strip().upper()
    rows = sorted((n for n in _normalized() if n["symbol"] == sym),
                  key=_sort_key, reverse=True)
    return {"symbol": sym, "disclaimer": DISCLOSURE_LAG_NOTE,
            "attribution": FILING_ATTRIBUTION_NOTE,
            "count": len(rows), "trades": rows[:limit]}


def _filer_label(r: dict) -> str:
    """'Jane Doe (spouse)' when the filing names a non-member owner — honest
    attribution instead of implying the member personally traded."""
    p = str(r.get("politician", ""))
    o = str(r.get("owner", "") or "").strip()
    if o and o.lower() not in ("self", "member"):
        return f"{p} ({o.lower()})"
    return p


def render_watch_html(data: dict | None = None) -> str:
    """Public, shareable Congress-Watch page (the acquisition magnet). Server-rendered,
    ALL dynamic strings escaped. Informational; links to /track-record and /pricing."""
    import html as _h

    data = data or {"top_bought": [], "recent": []}
    lb = data.get("top_bought") or []
    recent = data.get("recent") or []
    status = data.get("status") or source_status()
    e = _h.escape

    lb_rows = "".join(
        f"<tr><td style='padding:8px 14px;font-weight:700'>{i+1}</td>"
        f"<td style='padding:8px 14px;color:#00e87a;font-weight:700'>{e(str(r.get('symbol','')))}</td>"
        f"<td style='padding:8px 14px'>{e(str(r.get('buy_disclosures','')))}</td></tr>"
        for i, r in enumerate(lb)
    ) or "<tr><td colspan='3' style='padding:14px;color:#888'>No recent disclosures available.</td></tr>"

    def _recent_row(r: dict) -> str:
        sym = e(str(r.get("symbol", "")))
        link = str(r.get("link", "") or "")
        if link.startswith(("https://", "http://")):
            sym_cell = (f"<a href='{e(link, quote=True)}' target='_blank' "
                        f"rel='nofollow noopener'>{sym}</a>")
        else:
            sym_cell = sym
        return (f"<tr><td style='padding:6px 14px;color:#00e87a'>{sym_cell}</td>"
                f"<td style='padding:6px 14px'>{e(_filer_label(r))}</td>"
                f"<td style='padding:6px 14px;color:{'#00e87a' if r.get('is_buy') else '#ff6b6b'}'>"
                f"{e(str(r.get('action','')).title())}</td>"
                f"<td style='padding:6px 14px'>{e(str(r.get('amount','')))}</td>"
                f"<td style='padding:6px 14px;color:#aaa'>{e(str(r.get('disclosed','')))}</td></tr>")

    recent_rows = "".join(_recent_row(r) for r in recent[:25]) \
        or "<tr><td colspan='5' style='padding:14px;color:#888'>No recent disclosures available.</td></tr>"

    # Provenance line: chambers covered + freshness + health — an outage is shown
    # as degraded, never as a successful "no disclosures" state.
    if not status.get("configured"):
        src_line = "No disclosure source configured — set FMP_API_KEY to light this page up."
    elif status.get("degraded"):
        src_line = (f"⚠ Source degraded — showing last good data"
                    f"{(' fetched ' + e(status['fetched_at'])) if status.get('fetched_at') else ''}.")
    else:
        chambers = " + ".join(status.get("chambers") or [])
        src_line = (f"Source: {e(str(status.get('source') or ''))}"
                    f"{(' · ' + e(chambers)) if chambers else ''}"
                    f"{(' · fetched ' + e(status['fetched_at'])) if status.get('fetched_at') else ''}")

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
<div class='note'>{e(DISCLOSURE_LAG_NOTE)}<br>{e(FILING_ATTRIBUTION_NOTE)}<br>Not affiliated with any government official. GESH does not execute trades on your behalf.</div>
<h3>Most-bought tickers in recent congressional filings</h3>
<p style='color:#8b98a5;font-size:12px;margin:0 0 6px'>{e(LEADERBOARD_WINDOW)}</p>
<table><thead><tr><th>#</th><th>Ticker</th><th>Buy disclosures</th></tr></thead><tbody>{lb_rows}</tbody></table>
<h3>Latest disclosed transactions</h3>
<table><thead><tr><th>Ticker</th><th>Filed by</th><th>Action</th><th>Amount</th><th>Disclosed</th></tr></thead><tbody>{recent_rows}</tbody></table>
<p>Want AI analysis on any of these — regime, fundamentals, consensus, risk? <a href='/pricing'>See GESH</a> · <a href='/track-record'>simulated paper track record</a>.</p>
<a class='cta' href='/pricing'>Analyze these tickers with GESH →</a>
<p style='color:#5a6570;font-size:12px;margin-top:24px'>{src_line}<br>Informational and educational only. Not financial advice. Congressional disclosure data is public and lagged.</p>
</div></body></html>"""


def load_watch_data() -> dict:
    """Bundle the public page's data in one shot (leaderboard + recent + source status)."""
    return {"top_bought": leaderboard().get("top_bought", []),
            "recent": recent_activity(limit=25).get("trades", []),
            "status": source_status()}
