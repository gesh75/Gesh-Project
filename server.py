from __future__ import annotations
from contextlib import asynccontextmanager
from pathlib import Path
import asyncio, math, time, threading, uvicorn, os, hmac
from fastapi import FastAPI, HTTPException, Request, Depends, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
import logging, re
from collections import defaultdict, deque

# ─── FD-leak safety net: raise per-process file descriptor limit ──────────────
# Long-running uvicorn + yfinance + sqlite + robin_stocks tends to leak FDs
# (HTTP keep-alive sockets, yfinance crumb/tz cache opens, sqlite handles).
# macOS default soft limit is 256/1024 — bump to 10240 so a slow leak takes
# days, not hours, to bite. Also enforced via launchd plist as defense-in-depth.
try:
    import resource
    _soft, _hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    _target = 10240
    resource.setrlimit(resource.RLIMIT_NOFILE,
                       (max(_soft, _target), max(_hard, _target)))
    print(f"✓ RLIMIT_NOFILE raised: soft={_soft}→{max(_soft, _target)}")
except Exception as _rl_err:
    print(f"⚠  Could not raise RLIMIT_NOFILE: {_rl_err}")


def _sanitize(obj):
    """Recursively replace NaN/Inf floats with None so JSON serialization never crashes."""
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    return obj


def require_api_key(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> bool:
    """Auth gate for mutating / spend-triggering endpoints (config, trades, paper,
    live analysis runs).

    Accepts EITHER proof of ownership:
      1. A valid OWNER session cookie (dashboard path — no key in the browser,
         closes the Codex stored-XSS -> key-theft vector).
      2. The `X-API-Key` header matching GESH_API_KEY (headless scripts / cron).

    A CUSTOMER session is explicitly rejected (403): these endpoints mutate the
    shared engine cache and spend the owner's LLM/broker budget (Codex P1).
    Fails closed if GESH_API_KEY is unset and there is no owner session.
    """
    if auth_users.auth_enabled():
        email = auth_users.authenticate_session(request.cookies.get(auth_users.COOKIE_NAME))
        if email and auth_users.is_owner(email):
            return True
        if email:
            raise HTTPException(status_code=403, detail="Owner-only action")
    expected = os.getenv("GESH_API_KEY", "")
    if not expected:
        raise HTTPException(status_code=503, detail="Endpoint disabled: GESH_API_KEY not configured")
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return True


def require_customer_or_owner(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> bool:
    """Auth gate for the on-demand per-ticker LLM research card. Unlike
    require_api_key (owner-only), this ALSO admits any authenticated CUSTOMER
    session — the feature exists for customers to run on their own book of tickers.
    It still rejects ANONYMOUS callers so the endpoint is never an open, uncapped
    LLM-spend surface: with the login wall ON a valid session (customer or owner) is
    required; with the wall OFF (no customer concept) it falls back to the owner
    X-API-Key. Fails closed."""
    if auth_users.auth_enabled():
        email = auth_users.authenticate_session(request.cookies.get(auth_users.COOKIE_NAME))
        if email:
            return True
        raise HTTPException(status_code=401, detail="Authentication required")
    expected = os.getenv("GESH_API_KEY", "")
    if not expected:
        raise HTTPException(status_code=503, detail="Endpoint disabled: GESH_API_KEY not configured")
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return True


logger = logging.getLogger("gesh.server")


def _server_error(exc: Exception) -> HTTPException:
    """Log the real exception server-side; return a generic 500 so internal
    details (paths, SQL, upstream API bodies) never leak to clients. An
    HTTPException raised intentionally inside a handler passes through unchanged."""
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, RobinhoodOfflineError):
        # Expected, non-secret condition: the broker session expired. Degrade to a
        # clean 503 (frontend renders this as "Offline") instead of a 500 + traceback.
        logger.warning("Robinhood offline: %s", exc)
        return HTTPException(status_code=503, detail=str(exc))
    logger.exception("Unhandled server error: %s", exc)
    return HTTPException(status_code=500, detail="Internal server error")


_ALLOWED_INTERVALS = {"1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h", "1d", "5d", "1wk", "1mo", "3mo"}
_ALLOWED_PERIODS = {"1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "ytd", "max", "10d", "30d", "60d"}


def _validate_timeframe(interval: str, period: str) -> None:
    if interval not in _ALLOWED_INTERVALS or period not in _ALLOWED_PERIODS:
        raise HTTPException(status_code=400, detail="Invalid interval or period")


_SYMBOL_RE = re.compile(r"^[A-Z0-9.\-=^]{1,15}$")


def _safe_symbol(symbol: str) -> str:
    """Validate + normalize a ticker path param — blocks traversal/injection.
    Allows stocks, crypto (BTC-USD), forex (EURUSD=X), and ^indices."""
    s = (symbol or "").strip().upper()
    if not _SYMBOL_RE.match(s):
        raise HTTPException(status_code=400, detail="Invalid symbol")
    return s


# ── Lightweight per-IP rate limiting (dependency-free, single-process) ────────
_RATE_LOCK = threading.Lock()
_RATE_HITS: dict[tuple, deque] = defaultdict(deque)
_HEAVY_PREFIXES = (
    "/api/deep-analysis", "/api/analyze-ticker", "/api/screener", "/api/consensus",
    "/api/monte-carlo", "/api/agents/", "/api/gemini/", "/api/trading/deep-debate",
    # Per-ticker analyze GETs: each cache miss is 1-2 live upstream fetches
    # (yfinance+FRED / Robinhood / RSS), so an authed customer cycling symbols at
    # the normal 300/min cap could hammer the providers. Trailing slash on purpose:
    # the exact "/api/regime" (dashboard-polled, chain-cached) stays in the normal
    # bucket — only the /{symbol} variants are heavy.
    "/api/regime/", "/api/fundamentals/", "/api/analyst/", "/api/news/",
    # Customer-book analysis = regime + Monte Carlo + Kelly + one Claude call.
    # Book CRUD (/api/mybook without /analyze) stays in the normal bucket.
    "/api/mybook/analyze",
)


def _rate_ok(ip: str, heavy: bool) -> bool:
    limit, window = (30, 60) if heavy else (300, 60)
    now = time.time()
    key = (ip, "heavy" if heavy else "all")
    with _RATE_LOCK:
        hits = _RATE_HITS[key]
        while hits and hits[0] <= now - window:
            hits.popleft()
        if len(hits) >= limit:
            return False
        hits.append(now)
        return True


def _bucket_ok(ip: str, bucket: str, limit: int, window: int) -> bool:
    """Generic sliding-window limiter for a named bucket (e.g. magic-link re-sends).
    Tighter than the global /api/ limiter so an outbound-email endpoint can't be abused
    to spam known-subscriber inboxes."""
    now = time.time()
    key = (ip, bucket)
    with _RATE_LOCK:
        hits = _RATE_HITS[key]
        while hits and hits[0] <= now - window:
            hits.popleft()
        if len(hits) >= limit:
            return False
        hits.append(now)
        return True


from ai_analyzer import MODEL, analyze_portfolio, deep_analyze_portfolio, deep_analyze_ticker, analyze_screener
from auth import login, logout
from data_fetcher import DEFAULT_WATCHLIST, build_data_payload, RobinhoodOfflineError, load_snapshot_payload
from fundamental_agent import run_fundamental_scan, get_latest_fundamental_scan
from news_agent import scan_news, get_news_history
from sec_agent import watch_filings, get_filing_history
from db import init_db, log_analysis, log_equity_snapshot, get_analysis_history, get_analysis_by_id, get_equity_history
from ecg import compute_ecg
from fft_cycles import batch_fft_cycles
# Ollama removed — 3B local model provides no signal value vs Sonnet. Use Gemini for second opinions.
from gemini_agent import (
    get_gemini_status,
    gemini_quick_analysis,
    gemini_second_opinion,
    gemini_market_commentary,
    gemini_news_sentiment,
)
from market_data import get_all_market_data, screen_market, get_analyst_targets
from kelly import run_kelly_analysis
from consensus import run_consensus_portfolio
from monte_carlo import run_monte_carlo
from regime import detect_regimes
from allocation_config import load_config, update_config, validate_action, get_allocation_summary, get_scanner_params
from trade_log import log_trade, close_trade, get_win_rate, get_trade_history, get_scanner_performance, trade_summary_for_report
from paper_trading import run_paper_session, check_and_close_paper_positions, get_paper_summary
from consolidation_zone import scan_consolidations, detect_consolidation
from sector_filter import batch_sector_check, is_sector_hot
from trading.state import init_trading_tables
from trading.contract import from_consensus, manual as manual_intent
from trading.risk_gate import evaluate as risk_gate_evaluate
from trading.execution import place as execution_place
import auth_users   # multi-user login wall (OFF unless GESH_AUTH_ENABLED is set)
import billing      # Lemon Squeezy payment webhook → provision/revoke auth_users
import email_sender # transactional email (magic-link recovery via /auth/request-link)


# ─── Startup / Shutdown ───────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app):
    init_db()
    init_trading_tables()
    print("✓ SQLite DB initialised (core + trading tables)")

    # Bound Robinhood login + startup cache write so a stale token / MFA prompt
    # can never block uvicorn startup indefinitely. Run off the event loop with a
    # hard timeout; on failure the app still starts and serves cached-snapshot
    # data (CLAUDE.md #5). Mirrors the gesh-project-trading server fix.
    def _startup_auth() -> None:
        login()
        print("✓ Robinhood session established")
        # Write live cache immediately on startup so the daily-analysis
        # skill always has fresh data even before the first dashboard poll.
        try:
            from fetch_live_data import fetch_and_cache
            fetch_and_cache(logout_after=False)  # keep session alive for server
        except Exception as _ce:
            print(f"⚠  Startup cache write failed: {_ce}")

    try:
        await asyncio.wait_for(asyncio.to_thread(_startup_auth), timeout=60)
    except asyncio.TimeoutError:
        print("⚠  Robinhood auth/cache timed out (60s) — serving cached snapshot data")
    except Exception as e:
        print(f"⚠  Auth failed: {e} — serving cached snapshot data")
    yield
    try:
        logout()
    except Exception:
        pass


app = FastAPI(title="Gesh Financial Observability", lifespan=lifespan)


@app.middleware("http")
async def _rate_limit_middleware(request: Request, call_next):
    if request.url.path.startswith("/api/"):
        ip = request.client.host if request.client else "unknown"
        heavy = request.url.path.startswith(_HEAVY_PREFIXES)
        if not _rate_ok(ip, heavy):
            return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded — slow down"})
    return await call_next(request)


@app.middleware("http")
async def _auth_gate_middleware(request: Request, call_next):
    """Login wall — INERT unless GESH_AUTH_ENABLED is set. When enabled, GET / and
    every /api/* read requires a valid session cookie (public paths exempt):
      protected HTML page → 302 /login   ·   protected /api/* → 401 JSON.
    Leaves the existing per-endpoint X-API-Key gate on mutating endpoints intact."""
    if auth_users.auth_enabled():
        decision = auth_users.gate_decision(
            request.url.path, request.cookies.get(auth_users.COOKIE_NAME)
        )
        if decision == "redirect":
            return RedirectResponse(url="/login", status_code=302)
        if decision == "unauthorized":
            return JSONResponse(status_code=401, content={"detail": "Authentication required — sign in"})
    return await call_next(request)


# ─── Customer view: model-book masking (launch gap #1) ─────────────────────────
# Customers pay for the RESEARCH (signals, regime, Kelly %, risk, analysis) —
# not a window into the owner's bank life. Non-owner sessions get every /api/*
# JSON response normalized to a $100k MODEL BOOK: all dollar amounts and share
# quantities are scaled by (model / real_total), so percentages, ratios and
# per-share prices stay exactly right while real wealth never leaves the server.
# Owner-only views that embed raw dollars in HTML (/report, /invest, /progress)
# are refused outright for customers.

_MODEL_BOOK_USD = float(os.getenv("CUSTOMER_MODEL_BOOK_USD", "100000"))
# /watch is the INTERNAL cockpit (raw paper P&L, provisional win rate) — owner only.
_OWNER_ONLY_PATHS = ("/report", "/invest", "/progress", "/watch")
# Customer research endpoints whose bodies carry NO owner dollars — single-ticker AI
# research cards (public per-share fair value / price forecasts, not the owner's book).
# Money-masking would wrongly REDACT their public price prose ("$180" -> "$•••") and
# gut the customer analysis surface, so they are served RAW. ONLY add paths here that
# provably contain no owner position/cash data.
_MASK_EXEMPT_PREFIXES = (
    "/api/analyze-ticker",
    # The customer's OWN book — positions THEY entered, valued from public quotes.
    # Masking would redact the customer's own dollars back at them. Responses are
    # built exclusively from customer_positions + live prices: zero owner data.
    "/api/mybook",
)
_MONEY_KEYS = {
    "equity", "cash", "deployable", "settled_cash", "buying_power",
    "buying_power_leveraged", "margin_buying_power", "withdrawable_amount",
    "pending_deposits", "uncleared_deposits", "total", "account_equity",
    "portfolio_equity", "adjusted_equity_previous_close", "equity_value",
    "market_value", "recommended_usd", "delta_usd", "cost_basis",
    "total_equity", "day_change", "total_value",
    # Monte-Carlo / distribution outcomes (Codex P0: these leaked raw dollars)
    "mean", "median", "p5", "p25", "p75", "p95", "var_95", "cvar_95",
    "current_equity", "center",
    # 8th-pass (Fable audit): owner-$ keys the recursion previously passed through
    # RAW — same leak class as the ECG array / sparkline `series`. `equity_change`
    # = per-position unrealized $ P&L (data_fetcher), `extended_hours_equity` =
    # account equity (data_fetcher), `realized_pnl`/`total_realized_pnl` = trade $
    # P&L (trade_log, incl. nested best/worst_trade rows — scaled recursively).
    "equity_change", "extended_hours_equity", "realized_pnl", "total_realized_pnl",
}
_QTY_KEYS = {"quantity", "qty", "shares", "fill_qty", "delta_shares",
             "shares_to_buy", "shares_to_trim"}

# PUBLIC market-data subtrees kept VERBATIM (never scaled). The key `shares`
# scales owner sizing (fetch_live_data / paper_trading holdings), but the same key
# under `insider_trades` is a PUBLIC transaction count — scaling it by the
# model-book factor shows the customer a corrupt number (8th-pass, Fable audit).
# Semantic-path rule: decide by subtree, not by the bare key.
_PUBLIC_RAW_KEYS = {"insider_trades"}

# Bare-number arrays that carry OWNER DOLLARS (no dict key on the elements, so
# the recursive scaler would return them unchanged and leak — Codex P0 on
# /api/ecg's equity_history). Listed keys have their numeric elements scaled.
_MONEY_LIST_KEYS = {"equity_history", "equity_series", "equity_curve",
                    "series"}   # /api/portfolio/history KPI-sparkline equity (owner $)

# Prose REDACTION (Codex P0): AI narratives embed owner sizing ("sell 4-5 shares
# ~$450 at $112.73"). The old regex SCALER was doubly broken — it scaled only the
# last number in a range ("4-13.33 shares"), left un-prefixed range endpoints
# raw ("$1,199-560"), and scaled per-share PRICES ($112.73 -> $300), corrupting
# the advice. Prices must NEVER be scaled, and a scaler can't tell a position
# dollar from a price. So for customers we REDACT owner-specific figures — every
# $-amount and every share count (incl. ranges) -> a neutral token. Nothing
# leaks and nothing is financially wrong; the qualitative thesis remains.
_MONEY_RE = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?(?:\s?[-–—]\s?\$?\d[\d,]*(?:\.\d+)?)?")
_SHARES_RE = re.compile(r"\b\d+(?:\.\d+)?(?:\s?[-–—]\s?\d+(?:\.\d+)?)?\s+shares?\b", re.I)
_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")

# STRING masking is by SEMANTIC PATH (the dict key), never by string shape — a
# per-share price forecast ("$180–210") and a portfolio histogram bucket label
# ("$25,274–$28,518") are shape-identical, so shape can't tell them apart
# (Codex P0). Three explicit policies:
#   SCALE  — the value is portfolio dollars: only the Monte-Carlo bucket label.
#   KEEP   — the value is a PER-SHARE price / forecast (public, stock-level, not
#            owner-private): predictions, fair value, targets → leave raw.
#   REDACT — everything else with a $-amount or share count is owner sizing prose.
_KEEP_STRING_KEYS = {"3m", "12m", "prediction", "fair_value", "estimate",
                     "target", "current", "price", "forecast", "analyst_target"}


def _scale_label(text: str, factor: float) -> str:
    return _NUM_RE.sub(
        lambda m: f"{float(m.group(0).replace(',', '')) * factor:,.0f}", text)


def _redact_prose(text: str) -> str:
    text = _MONEY_RE.sub("$•••", text)          # owner $-amounts / ranges
    text = _SHARES_RE.sub("a position", text)   # share counts / ranges
    return text


def _customer_scale_factor() -> float:
    """model_book / real_total from the live snapshot. Fail CLOSED: if the real
    total can't be determined, return 0.0 so dollars zero out rather than leak."""
    try:
        snap = _json.loads((Path(__file__).parent / "live_portfolio_snapshot.json").read_text())
        total = float(snap.get("equity_summary", {}).get("total", 0) or 0)
        return _MODEL_BOOK_USD / total if total > 0 else 0.0
    except Exception:
        return 0.0


def _scale_money(obj, factor: float, parent_key: str = ""):
    """Recursively build a customer-safe copy (never mutates):
      * dict money/qty fields → scaled by factor;
      * bare-number arrays under a MONEY_LIST key (e.g. equity_history) → each
        element scaled (semantic list context, Codex P0 — these have no dict key
        so the naive recursion returned them raw);
      * strings → owner-specific $-amounts and share counts REDACTED (never
        scaled: a scaler corrupts per-share prices and half-scales ranges)."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            lk = str(k).lower()
            if lk in _PUBLIC_RAW_KEYS:
                out[k] = v                          # public market data — never scale
            elif isinstance(v, (int, float)) and not isinstance(v, bool) and (
                lk in _MONEY_KEYS or lk.endswith("_usd") or lk.endswith("_dollars")
            ):
                out[k] = round(v * factor, 2)
            elif isinstance(v, (int, float)) and not isinstance(v, bool) and lk in _QTY_KEYS:
                out[k] = round(v * factor, 4)
            elif isinstance(v, list) and lk in _MONEY_LIST_KEYS:
                out[k] = [round(x * factor, 2) if isinstance(x, (int, float))
                          and not isinstance(x, bool) else _scale_money(x, factor, lk)
                          for x in v]
            elif isinstance(v, str) and lk == "label" and parent_key == "buckets":
                out[k] = _scale_label(v, factor)   # MC histogram label = portfolio $
            elif isinstance(v, str) and lk in _KEEP_STRING_KEYS:
                out[k] = v                          # per-share price/forecast — keep raw
            elif isinstance(v, str) and ("$" in v or "share" in v.lower()):
                out[k] = _redact_prose(v)           # owner sizing prose — redact
            else:
                out[k] = _scale_money(v, factor, lk)
        return out
    if isinstance(obj, list):
        return [_scale_money(x, factor, parent_key) for x in obj]
    # A bare string reached outside a dict (e.g. list element): safe default is
    # to KEEP price-forecast contexts, else REDACT anything with $ / shares.
    if isinstance(obj, str) and ("$" in obj or "share" in obj.lower()):
        return obj if parent_key in _KEEP_STRING_KEYS else _redact_prose(obj)
    return obj


@app.middleware("http")
async def _customer_view_middleware(request: Request, call_next):
    if not auth_users.auth_enabled():
        return await call_next(request)
    email = auth_users.authenticate_session(request.cookies.get(auth_users.COOKIE_NAME))
    if not email or auth_users.is_owner(email):
        return await call_next(request)

    path = request.url.path
    if path == "/report" or any(path == p or path.startswith(p + "/") for p in _OWNER_ONLY_PATHS):
        return JSONResponse(status_code=403, content={"detail": "Owner-only view"})

    # Public single-ticker research cards carry no owner dollars — serve RAW so their
    # per-share price prose is not redacted into uselessness for the customer.
    if any(path == p or path.startswith(p + "/") for p in _MASK_EXEMPT_PREFIXES):
        return await call_next(request)

    resp = await call_next(request)
    if not path.startswith("/api/") or "application/json" not in resp.headers.get("content-type", ""):
        return resp
    body = b"".join([chunk async for chunk in resp.body_iterator])
    try:
        masked = _scale_money(_json.loads(body), _customer_scale_factor())
        if isinstance(masked, dict):
            masked["view"] = "model_book"   # UI/debug tell — customer-scaled response
        return JSONResponse(status_code=resp.status_code, content=masked)
    except Exception:
        # Fail CLOSED for customers: never fall through to the raw-dollar body.
        return JSONResponse(status_code=500, content={"detail": "masking error"})


# ─── Browser hardening headers (Codex P1) ──────────────────────────────────────
# The dashboard renders AI/news/SEC text (some model- or externally-sourced)
# through innerHTML. Defense in depth: this CSP allows inline scripts/handlers
# (the app uses onclick=/onchange= + one inline <script> block) but pins
# connect-src/img-src/script-src so an injected handler CANNOT beacon data to an
# attacker host or pull external script — the classic XSS exfiltration channels.
# Chart.js is the one external script, allow-listed by exact host + SRI (below).
_CHARTJS_HOST = "https://cdn.jsdelivr.net"
_FONTS_CSS = "https://fonts.googleapis.com"
_FONTS_FILES = "https://fonts.gstatic.com"
_CSP = (
    "default-src 'self'; "
    f"script-src 'self' 'unsafe-inline' {_CHARTJS_HOST}; "
    f"style-src 'self' 'unsafe-inline' {_FONTS_CSS}; "
    "img-src 'self' data:; "
    f"font-src 'self' data: {_FONTS_FILES}; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; "
    "object-src 'none'"
)


@app.middleware("http")
async def _security_headers_middleware(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("Content-Security-Policy", _CSP)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    # HSTS only over real HTTPS (the Cloudflare tunnel terminates TLS) — never on
    # plain-http localhost, which would poison the dev origin.
    if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return resp


# Explicit CORS policy: same-origin dashboard only, no credentialed cross-origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:9000", "http://127.0.0.1:9000"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
HTML = Path(__file__).parent / "templates" / "dashboard.html"


# ─── Login wall routes (active only when GESH_AUTH_ENABLED) ────────────────────
_LOGIN_PAGE = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>GESH — Sign in</title>
<style>body{background:#080d14;color:#e8eef6;font-family:-apple-system,Segoe UI,Roboto,sans-serif;
display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0}
.card{background:#0d1520;border:1px solid #1a2840;border-radius:12px;padding:32px;width:320px}
h1{color:#00d4ff;font-size:20px;margin:0 0 4px}.sub{color:#5b7290;font-size:12px;margin-bottom:20px}
label{display:block;color:#5b7290;font-size:12px;margin:12px 0 4px}
input{width:100%;box-sizing:border-box;background:#080d14;border:1px solid #1a2840;border-radius:8px;
color:#e8eef6;padding:10px;font-size:14px}
button{width:100%;margin-top:20px;background:#00d4ff;color:#03121a;border:0;border-radius:8px;
padding:11px;font-weight:700;font-size:14px;cursor:pointer}
.err{color:#ff3c5a;font-size:12px;margin-top:12px;min-height:14px}</style></head>
<body><form class=card id=f>
<h1>GESH Financial</h1><div class=sub>Signal-accuracy research · sign in</div>
<label>Email</label><input name=email type=email autocomplete=username required autofocus>
<label>Password</label><input name=password type=password autocomplete=current-password required>
<button type=submit>Sign in</button><div class=err id=e></div>
<div class=sub style="margin-top:16px">Subscribed but no password? <a href="#" id=ml style="color:#00d4ff">Email me a sign-in link</a></div>
<div class=err id=m style="color:#00e87a"></div></form>
<script>document.getElementById('f').addEventListener('submit',async function(ev){ev.preventDefault();
var r=await fetch('/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({email:ev.target.email.value,password:ev.target.password.value})});
if(r.ok){location.href='/';}else{document.getElementById('e').textContent='Invalid email or password.';}});
document.getElementById('ml').addEventListener('click',async function(ev){ev.preventDefault();
var em=document.querySelector('input[name=email]').value;
if(!em){document.getElementById('e').textContent='Enter your email first.';return;}
await fetch('/auth/request-link',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({email:em})});
document.getElementById('m').textContent='If that email has access, a sign-in link is on its way.';});
</script></body></html>"""


@app.get("/healthz")
def healthz():
    return {"status": "ok", "auth": auth_users.auth_enabled()}


@app.get("/api/me")
def whoami(request: Request):
    """Session role for the frontend nav (owner vs customer). No PII, no secrets —
    lets the customer UI render Analyze/Track-Record and hide the owner's book on
    load without racing the portfolio fetch."""
    if not auth_users.auth_enabled():
        return {"role": "owner"}
    email = auth_users.authenticate_session(request.cookies.get(auth_users.COOKIE_NAME))
    if not email:
        raise HTTPException(status_code=401, detail="Authentication required")
    return {"role": "owner" if auth_users.is_owner(email) else "customer"}


def _session_email(request: Request) -> str | None:
    """The requesting user's identity for per-user data (watchlist). In dev/solo
    mode (auth off) everything is the owner bucket; in production it's the session."""
    if not auth_users.auth_enabled():
        return os.getenv("GESH_OWNER_EMAIL", "georgy75@gmail.com").strip().lower()
    return auth_users.authenticate_session(request.cookies.get(auth_users.COOKIE_NAME))


@app.get("/api/watchlist")
def watchlist_get(request: Request):
    """The current user's saved tickers (per-user, session-scoped)."""
    email = _session_email(request)
    if not email:
        raise HTTPException(status_code=401, detail="Authentication required")
    return {"status": "ok", "data": auth_users.get_watch(email)}


@app.post("/api/watchlist")
async def watchlist_add(request: Request):
    email = _session_email(request)
    if not email:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        body = await request.json()
    except Exception:
        body = {}
    sym = _safe_symbol(body.get("symbol", ""))
    ok = auth_users.add_watch(email, sym)
    return {"status": "ok" if ok else "full", "data": auth_users.get_watch(email)}


@app.delete("/api/watchlist/{symbol}")
def watchlist_remove(symbol: str, request: Request):
    email = _session_email(request)
    if not email:
        raise HTTPException(status_code=401, detail="Authentication required")
    auth_users.remove_watch(email, _safe_symbol(symbol))
    return {"status": "ok", "data": auth_users.get_watch(email)}


def _cookie_secure() -> bool:
    """Whether to set the Secure flag on the session cookie. Defaults to True (production
    is HTTPS); set GESH_COOKIE_SECURE=0 ONLY for local plain-HTTP dev. This prevents the
    session cookie shipping over plaintext on a TLS deploy because someone forgot to flip a
    hardcoded False (M2)."""
    return os.getenv("GESH_COOKIE_SECURE", "1").strip().lower() not in ("0", "false", "no", "off")


@app.get("/login", response_class=HTMLResponse)
def login_page():
    return _LOGIN_PAGE


@app.post("/auth/login")
async def auth_login(request: Request):
    """JSON login → sets an httpOnly session cookie. (JSON, not form, to avoid a
    python-multipart dependency on the running server.)"""
    try:
        body = await request.json()
    except Exception:
        body = {}
    email = (body.get("email") or "").strip()
    password = body.get("password") or ""
    if not auth_users.verify_credentials(email, password):
        return JSONResponse(status_code=401, content={"ok": False, "detail": "Invalid email or password"})
    auth_users.touch_last_login(email)
    resp = JSONResponse(content={"ok": True})
    resp.set_cookie(
        auth_users.COOKIE_NAME, auth_users.issue_token(email),
        httponly=True, samesite="lax", max_age=auth_users.TOKEN_TTL_SECONDS,
        secure=_cookie_secure(),
    )
    return resp


@app.post("/auth/logout")
def auth_logout():
    resp = JSONResponse(content={"ok": True})
    resp.delete_cookie(auth_users.COOKIE_NAME)
    return resp


@app.get("/auth/magic")
def auth_magic(token: str = ""):
    """Passwordless login from the onboarding email's magic link.

    The URL token is SINGLE-USE: consume_magic_token validates + atomically spends it, so
    a leaked link (history/Referer/logs) can't be replayed. We then mint a SEPARATE fresh
    session token for the cookie — the short-TTL URL token is never reused as the session.
    Redirect drops the token from the URL and sets Referrer-Policy: no-referrer so the
    landing nav can't leak it onward."""
    email = auth_users.consume_magic_token(token)
    if not email:
        return RedirectResponse(url="/login?e=expired", status_code=302)
    u = auth_users.get_user(email)
    if not u or not u["is_active"]:
        return RedirectResponse(url="/login?e=expired", status_code=302)
    auth_users.touch_last_login(email)
    resp = RedirectResponse(url="/", status_code=303)   # 303 See Other after consuming the token
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.set_cookie(
        auth_users.COOKIE_NAME,
        auth_users.issue_token(email, typ="session"),   # fresh SESSION token (not the magic one)
        httponly=True, samesite="lax", max_age=auth_users.TOKEN_TTL_SECONDS,
        secure=_cookie_secure(),
    )
    return resp


@app.post("/auth/request-link")
async def auth_request_link(request: Request):
    """Recovery path (H5): re-send a sign-in link to an active subscriber whose onboarding
    email never arrived or whose link expired. Always returns ok=True (never reveals whether
    an address exists); only actually emails when the address maps to an active user.
    Rate-limited per IP (5 / 10 min) so it can't be abused to spam subscriber inboxes."""
    ip = request.client.host if request.client else "unknown"
    if not _bucket_ok(ip, "request-link", limit=5, window=600):
        return JSONResponse(status_code=429, content={"ok": False, "detail": "Too many requests"})
    try:
        body = await request.json()
    except Exception:
        body = {}
    email = (body.get("email") or "").strip()
    token = auth_users.issue_magic_token(email) if email else None
    if token:
        import os as _os
        base = _os.getenv("APP_BASE_URL", "").rstrip("/")
        tier = (auth_users.get_user(email) or {}).get("tier")
        email_sender.send_magic_link(email, f"{base}/auth/magic?token={token}", tier=tier)
    return JSONResponse(content={"ok": True})


# ─── Payment: pricing + Lemon Squeezy webhook (provision/revoke) ──────────────
@app.get("/pricing", response_class=HTMLResponse)
def pricing_page():
    """Public pricing page — ONE plan during the alpha. All features are currently
    identical across access (only is_active is enforced), so selling a single honest tier
    avoids charging Trader/Desk prices for the same product. Re-introduce tiers once
    per-tier entitlement gating exists (Phase 2). Checkout URL + price from env; no card
    data ever touches this server."""
    import os as _os
    name = _os.getenv("LS_PLAN_NAME", "GESH Access")
    price = _os.getenv("LS_PLAN_PRICE", "49")
    url = _os.getenv("LS_CHECKOUT_PRIMARY", _os.getenv("LS_CHECKOUT_OBSERVER", "#"))
    feats = ["Signal-accuracy proof (live track record)", "Daily AI portfolio report",
             "Watch dashboard", "Diversification radar"]
    items = "".join(f"<li>{f}</li>" for f in feats)
    card = (
        "<div style=\"border:1px solid #1a2840;border-radius:12px;padding:28px;"
        "background:#0d1520;max-width:360px\">"
        f"<h2 style=\"color:#00d4ff;margin:0\">{name}</h2>"
        f"<p style=\"font-size:34px;margin:8px 0\"><strong>${price}</strong>"
        "<span style=\"color:#667;font-size:14px\">/mo</span></p>"
        f"<ul style=\"color:#aeb9c7;line-height:1.8\">{items}</ul>"
        f"<a href=\"{url}\" style=\"display:block;text-align:center;background:#00d4ff;"
        "color:#001018;padding:12px;border-radius:8px;text-decoration:none;"
        "font-weight:600;margin-top:12px\">Subscribe</a></div>"
    )
    return (
        "<!doctype html><html><head><meta charset=utf-8><title>GESH — Pricing</title>"
        "<meta name=viewport content=\"width=device-width,initial-scale=1\"></head>"
        "<body style=\"font-family:system-ui,sans-serif;background:#080d14;color:#e6edf3;"
        "max-width:900px;margin:auto;padding:32px\">"
        "<h1 style=\"color:#00d4ff\">GESH Financial — Access</h1>"
        "<p style=\"color:#aeb9c7\">AI signal intelligence for your portfolio. "
        "See the <a href=\"/track-record\" style=\"color:#00e87a\">live track record</a> first.</p>"
        f"<div style=\"margin:24px 0\">{card}</div>"
        "<p style=\"color:#667;font-size:12px\">By subscribing you agree to the "
        "<a href=\"/terms\" style=\"color:#889\">Terms</a> &amp; "
        "<a href=\"/disclaimer\" style=\"color:#889\">Disclaimer</a>. "
        "NOT FINANCIAL ADVICE — FOR INFORMATIONAL PURPOSES ONLY.</p></body></html>"
    )


@app.get("/billing/success", response_class=HTMLResponse)
def billing_success():
    """Post-checkout landing — the magic-link email lands separately."""
    return (
        "<!doctype html><html><head><meta charset=utf-8><title>Payment received</title></head>"
        "<body style=\"font-family:system-ui,sans-serif;background:#080d14;color:#e6edf3;"
        "max-width:560px;margin:auto;padding:48px;text-align:center\">"
        "<h1 style=\"color:#00e87a\">✓ Payment received</h1>"
        "<p>Check your email for a sign-in link to access GESH. "
        "It can take a minute to arrive.</p>"
        "<p><a href=\"/login\" style=\"color:#00d4ff\">Go to login</a></p></body></html>"
    )


@app.post("/api/billing/webhook")
async def billing_webhook(request: Request):
    """Lemon Squeezy webhook — signature-verified + idempotent. Provisions/revokes the
    auth_users account. Public (in PUBLIC_PREFIXES) because it carries its own HMAC auth."""
    raw = await request.body()
    sig = request.headers.get("X-Signature")
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    try:
        result = billing.handle_webhook(raw, sig, payload)
        return JSONResponse(content=result)
    except PermissionError:
        return JSONResponse(status_code=401, content={"ok": False, "detail": "bad signature"})
    except ValueError as e:
        return JSONResponse(status_code=400, content={"ok": False, "detail": str(e)})
    except billing.RetryableError as e:
        # Non-2xx so Lemon Squeezy RETRIES (it only retries non-2xx) — e.g. the onboarding
        # email could not be sent yet (#1). The dedup claim was already released.
        return JSONResponse(status_code=503, content={"ok": False, "detail": str(e), "retry": True})
    except Exception as exc:  # noqa: BLE001
        raise _server_error(exc)


# ─── Public pages: signal-accuracy proof + legal (no auth required) ───────────
@app.get("/track-record", response_class=HTMLResponse)
def track_record_page():
    """Public signal-accuracy proof page — renders live closed-trade stats from
    the tracker (honest 'day X of 180, N/30 closed' until the gate is met)."""
    try:
        import proof_page
        return proof_page.render_html(proof_page.load_track_record())
    except Exception as exc:
        raise _server_error(exc)


@app.get("/terms", response_class=HTMLResponse)
@app.get("/disclaimer", response_class=HTMLResponse)
def terms_page():
    """Public Terms of Service & Disclaimer (internal compliance notes stripped)."""
    import legal_pages
    return legal_pages.render_terms_html()


@app.get("/progress", response_class=HTMLResponse)
def progress_page():
    """Internal founder cockpit — weekly autonomy-gate burndown to the Sep alpha.
    Reads the weekly_snapshots row written by the Friday tracker job. NOT public:
    no /progress in the auth public-prefix list, so the login wall gates it."""
    try:
        import progress_view
        return progress_view.render_progress_html(progress_view.load_progress())
    except Exception as exc:
        raise _server_error(exc)


@app.get("/watch", response_class=HTMLResponse)
def watch_page():
    """Internal live paper-trading cockpit — open positions, recent closes, the
    gate→fill→exit activity feed, and N/30 autonomy progress. Read-only, paper-only.
    NOT public: not in the auth public-prefix list, so the login wall gates it."""
    try:
        import watch_view
        return watch_view.render_watch_html(watch_view.load_watch_data())
    except Exception as exc:
        raise _server_error(exc)


@app.get("/report", response_class=HTMLResponse)
def daily_report_page():
    """The daily-chain HTML report (gesh_report.html) — ticker tape, holdings
    cards, Monte Carlo/Kelly charts, action plan, and the Paper & Risk-Gate
    Audit section. Regenerated by run_daily_analysis.sh (launchd, 10:30 ET).
    NOT public: not in the auth public-prefix list, so the login wall gates it."""
    report_path = Path(__file__).parent / "gesh_report.html"
    if not report_path.exists():
        raise HTTPException(status_code=404,
                            detail="No daily report yet — the 10:30 chain has not produced one.")
    try:
        return HTMLResponse(report_path.read_text())
    except Exception as exc:
        raise _server_error(exc)


@app.get("/watch/data")
def watch_data(lookback_days: int = 7, activity_limit: int = 50):
    """Read-only JSON snapshot powering the /watch 12s poll. Same auth gate as /watch."""
    try:
        import watch_view
        return JSONResponse(
            watch_view.load_watch_data(lookback_days=lookback_days, activity_limit=activity_limit)
        )
    except Exception as exc:
        raise _server_error(exc)


@app.get("/invest", response_class=HTMLResponse)
def invest_page():
    """Internal Diversification Radar — book sector concentration, ranked NEW
    (non-held) candidates with multi-factor scores + suggested sizing, and swap
    ideas when slots/cash are full. Read-only, advisory-only (never auto-trades).
    NOT public: not in the auth public-prefix list, so the login wall gates it
    (the dollar figures are account-identifying)."""
    try:
        import invest_view
        return invest_view.render_invest_html(invest_view.load_invest_data())
    except Exception as exc:
        raise _server_error(exc)


@app.get("/invest/data")
def invest_data(what_if_cash: float | None = None):
    """Read-only JSON snapshot powering the /invest 12s poll. Reads the radar the daily
    10:30 pipeline persisted into engine_results.json. `what_if_cash` (the slider) re-sizes
    the picks for a hypothetical deployable amount server-side. Same auth gate as /invest."""
    try:
        import invest_view
        return JSONResponse(invest_view.load_invest_data(what_if_cash=what_if_cash))
    except Exception as exc:
        raise _server_error(exc)


_FAVICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<rect width="32" height="32" rx="7" fill="#080d14"/>'
    '<path d="M6 22 L13 14 L18 18 L26 8" fill="none" stroke="#00d4ff" '
    'stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/></svg>'
)


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    """Inline SVG favicon (cyan up-trend on the dark house background) — silences the
    /favicon.ico 404. Already in auth_users.PUBLIC_PREFIXES, so it stays public."""
    from fastapi.responses import Response
    return Response(content=_FAVICON_SVG, media_type="image/svg+xml")

# ─── Engine-result cache (powers "never empty" run-tabs in the dashboard) ─────
# Reuses the engine_results.json file Gesh already maintains. POST run-handlers
# persist their result here; the dashboard reads it back via GET /api/*/cached
# so each run-tab shows the last result by default instead of a blank screen.
import json as _json
from datetime import datetime as _dt, timezone as _tz

ENGINE_RESULTS = Path(__file__).parent / "engine_results.json"
_engine_cache_lock = threading.Lock()


def _load_engine_results() -> dict:
    try:
        return _json.loads(ENGINE_RESULTS.read_text())
    except Exception:
        return {}


def _save_engine_result(key: str, value) -> None:
    """Merge one engine result into engine_results.json with a *_ran_at stamp.

    Builds a new dict and atomic-writes (temp file + replace) so a concurrent
    reader never sees a half-written file (immutability of the on-disk artifact).
    """
    try:
        with _engine_cache_lock:
            data = dict(_load_engine_results())
            data[key] = value
            data[f"{key}_ran_at"] = _dt.now(_tz.utc).isoformat()
            tmp = ENGINE_RESULTS.with_name(ENGINE_RESULTS.name + ".tmp")
            tmp.write_text(_json.dumps(_sanitize(data), default=str))
            tmp.replace(ENGINE_RESULTS)
    except Exception as _e:
        print(f"⚠  engine-result cache write failed for {key}: {_e}")


def _cached_engine(key: str) -> dict:
    """Return the cached result for an engine key, or {cached:false} if absent."""
    data = _load_engine_results()
    val = data.get(key)
    if val is not None:
        return {"status": "ok", "cached": True, "data": val,
                "ran_at": data.get(f"{key}_ran_at")}
    return {"cached": False}


def _is_fresh(ran_at: str | None, max_age_h: float = 24.0) -> bool:
    if not ran_at:
        return False
    try:
        ts = _dt.fromisoformat(ran_at)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=_tz.utc)
        return (_dt.now(_tz.utc) - ts).total_seconds() < max_age_h * 3600
    except Exception:
        return False

# ─── In-process throttles / caches (FD-leak mitigation) ──────────────────────
# Dashboard polls /api/portfolio every few seconds. Logging an equity snapshot
# on *every* poll is pointless for ECG (which only needs minute-level cadence)
# and was the source of "Snapshot log failed: unable to open database file"
# spam once FDs got tight. Throttle to one write per SNAPSHOT_INTERVAL_SEC.
SNAPSHOT_INTERVAL_SEC = 300  # 5 minutes — plenty for ECG
_last_snapshot_at: float = 0.0
_snapshot_lock = threading.Lock()

def _maybe_log_snapshot(holdings: list[dict]) -> None:
    """Rate-limited equity snapshot + live cache write. Skips if last write < interval ago."""
    global _last_snapshot_at
    now = time.monotonic()
    with _snapshot_lock:
        if now - _last_snapshot_at < SNAPSHOT_INTERVAL_SEC:
            return
        _last_snapshot_at = now
    try:
        log_equity_snapshot(holdings)
    except Exception as snap_err:
        print(f"⚠  Snapshot log failed: {snap_err}")
    # Refresh live cache so the daily-analysis skill always gets current data
    try:
        from fetch_live_data import fetch_and_cache
        import threading as _t
        _t.Thread(target=fetch_and_cache, kwargs={"logout_after": False}, daemon=True).start()
    except Exception as cache_err:
        print(f"⚠  Live cache refresh failed: {cache_err}")

# /api/chart/{symbol}?period=… cache. yfinance opens its tz cache + a fresh
# session every call; back-to-back polls were a major FD churn source.
_chart_cache: dict[tuple[str, str], tuple[float, dict]] = {}
_chart_cache_lock = threading.Lock()
CHART_TTL_SEC = 30

# Per-ticker analyze GETs (regime/fundamentals/analyst/news) cache. Watchlist
# chips make repeat clicks on the same symbol the common pattern, and every miss
# is a live upstream fetch. Successful responses only — a transient upstream 404
# must not stick for 5 minutes.
_ticker_get_cache: dict[tuple[str, str], tuple[float, dict]] = {}
_ticker_get_lock = threading.Lock()
TICKER_GET_TTL_SEC = 300


def _ticker_get_cached(bucket: str, sym: str) -> dict | None:
    with _ticker_get_lock:
        hit = _ticker_get_cache.get((bucket, sym))
    return hit[1] if hit and (time.monotonic() - hit[0]) < TICKER_GET_TTL_SEC else None


def _ticker_get_store(bucket: str, sym: str, resp: dict) -> dict:
    with _ticker_get_lock:
        _ticker_get_cache[(bucket, sym)] = (time.monotonic(), resp)
    return resp


# ─── Dashboard ────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def dashboard():
    return HTMLResponse(HTML.read_text().replace("{{ model }}", MODEL))


# ─── Portfolio data ───────────────────────────────────────────────────────────

def _resolved_payload(_watchlist=None) -> dict:
    """Portfolio payload with the confirmed-offline → MCP-snapshot fallback.

    The reauth-dead Robinhood session raises RobinhoodOfflineError; every analysis
    endpoint should degrade to the snapshot the daily chain / MCP already built,
    NOT hard-503 (Codex P1: the Risk tab's ECG panel spun forever). Any OTHER
    exception is a real bug and propagates. Returns a payload with `offline` set
    when serving the snapshot so callers can label a degraded state.
    """
    try:
        return build_data_payload(_watchlist if _watchlist is not None else DEFAULT_WATCHLIST)
    except RobinhoodOfflineError:
        return load_snapshot_payload()   # re-raises RobinhoodOfflineError only if NO snapshot exists


@app.get("/api/portfolio")
def get_portfolio():
    try:
        try:
            payload = build_data_payload(DEFAULT_WATCHLIST)
        except RobinhoodOfflineError as live_err:
            # ONLY a confirmed-offline session degrades to the MCP-built snapshot (Option A).
            # Any OTHER exception is a real bug — it must NOT be masked by serving stale data;
            # it falls through to _server_error (500) and reaches monitoring (Codex MEDIUM-4).
            payload = load_snapshot_payload()       # may re-raise RobinhoodOfflineError → 503
            logger.warning("api/portfolio: live RH offline (%s) — serving snapshot (age=%ss)",
                           type(live_err).__name__, payload.get("snapshot_age_seconds"))

        eq = payload["equity_summary"]
        prev = eq.get("adjusted_equity_previous_close", 0)
        curr = eq.get("portfolio_equity", 0)
        eq["day_change"]     = round(curr - prev, 2)
        eq["day_change_pct"] = round(((curr - prev) / prev * 100) if prev else 0, 2)

        # Log the equity snapshot for ECG only for LIVE data — never persist stale snapshot
        # values as if they were a fresh tick (would corrupt the equity curve).
        if not payload.get("offline"):
            _maybe_log_snapshot(payload["holdings"])

        return _sanitize({"status": "ok", "data": payload})
    except Exception as e:
        raise _server_error(e)


# ─── Quick AI analysis ────────────────────────────────────────────────────────

@app.post("/api/analyze", dependencies=[Depends(require_api_key)])
def run_analysis():
    try:
        payload  = build_data_payload(DEFAULT_WATCHLIST)
        analysis = analyze_portfolio(payload)
        return {"status": "ok", "analysis": analysis, "model": MODEL}
    except Exception as e:
        raise _server_error(e)


# ─── Deep AI analysis (with regime injection + DB logging) ───────────────────

@app.post("/api/deep-analysis", dependencies=[Depends(require_api_key)])
def run_deep_analysis():
    try:
        payload = build_data_payload(DEFAULT_WATCHLIST)
        eq      = payload["equity_summary"]
        prev    = eq.get("adjusted_equity_previous_close", 0)
        curr    = eq.get("portfolio_equity", 0)
        eq["day_change"]     = round(curr - prev, 2)
        eq["day_change_pct"] = round(((curr - prev) / prev * 100) if prev else 0, 2)

        symbols = [h["symbol"] for h in payload["holdings"]]

        # Regime detection injected into prompt
        regime_data    = detect_regimes(symbols)
        regime_context = regime_data.get("regime_summary")

        # Market intelligence (analyst targets, performance, insiders, ownership)
        mkt = get_all_market_data(symbols)

        analysis = deep_analyze_portfolio(payload, regime_context=regime_context)

        # Log to SQLite. Deep analysis is user-triggered (not a poll), so we
        # always write the snapshot here regardless of the throttle.
        try:
            run_id = log_analysis(MODEL, payload, analysis)
            log_equity_snapshot(payload["holdings"])
        except Exception as db_err:
            print(f"⚠  DB log failed: {db_err}")
            run_id = None

        resp = {
            "status":   "ok",
            "analysis": analysis,
            "model":    MODEL,
            "regimes":  regime_data.get("regimes", []),
            "market":   mkt,
            "run_id":   run_id,
        }
        _save_engine_result("ai_result", resp)
        return resp
    except Exception as e:
        raise _server_error(e)


# ─── Step 2 — Monte Carlo stress test ────────────────────────────────────────

@app.post("/api/monte-carlo", dependencies=[Depends(require_api_key)])
def monte_carlo():
    try:
        payload  = build_data_payload(DEFAULT_WATCHLIST)
        holdings = payload["holdings"]
        eq_sum   = payload["equity_summary"]
        result   = run_monte_carlo(holdings, eq_sum)
        resp = {"status": "ok", "data": result}
        _save_engine_result("monte_carlo", resp)
        return resp
    except Exception as e:
        raise _server_error(e)


# ─── Step 3 — Equity Curve Gate ──────────────────────────────────────────────

@app.get("/api/ecg")
def equity_curve_gate():
    try:
        try:
            payload = _resolved_payload()
        except RobinhoodOfflineError:
            # No live session AND no snapshot: degrade explicitly, never 503 into a
            # forever-spinner. ECG runs off the equity_snapshots table, so a snapshot
            # is normally present; this is the true-dark fallback.
            return {"status": "degraded",
                    "data": {"portfolio_gate": "UNKNOWN",
                             "portfolio_message": "Live data unavailable — ECG paused. Showing no live gate.",
                             "states": []}}
        holdings = payload["holdings"]

        def history_fn(symbol: str):
            return get_equity_history(symbol, days=30)

        result = compute_ecg(holdings, history_fn)
        result.setdefault("portfolio_gate", "UNKNOWN")
        result.setdefault("portfolio_message", "")
        if payload.get("offline"):
            result["degraded"] = True
            result["portfolio_message"] = (result.get("portfolio_message") or "") + " (cached snapshot)"
        return {"status": "ok", "data": result}
    except Exception as e:
        raise _server_error(e)


# ─── Step 4 — Regime detection standalone ────────────────────────────────────

@app.get("/api/regime")
def regime_endpoint(force_live: bool = False):
    """Market regime per holding. Serves the daily chain's cached regime when
    fresh (<4h) — the live path re-downloads 8 symbols of OHLCV + FRED on every
    dashboard load for data the 10:30 chain already computed. ?force_live=true
    bypasses the cache."""
    # Read whatever regime the chain cached (may be stale) — used both as the
    # fast path when fresh AND as the degraded fallback when live is offline.
    cached, ran = None, None
    try:
        eng = _load_engine_results()
        cached = eng.get("regime")
        ran = eng.get("regime_ran_at")
    except Exception:
        pass

    if not force_live and cached and cached.get("regimes") and ran:
        try:
            from datetime import datetime as _rdt, timezone as _rtz
            age_s = (_rdt.now(_rtz.utc) - _rdt.fromisoformat(str(ran))).total_seconds()
            if age_s < 4 * 3600:
                return {"status": "ok", "data": cached, "cached": True, "ran_at": ran}
        except Exception:
            pass

    try:
        payload  = _resolved_payload()
        symbols  = [h["symbol"] for h in payload["holdings"]]
        result   = detect_regimes(symbols)
        return {"status": "ok", "data": result}
    except RobinhoodOfflineError:
        # No live session AND (usually) a stale cache — serve the stale cache as a
        # degraded snapshot rather than 503-ing the whole Risk tab into an error.
        if cached and cached.get("regimes"):
            return {"status": "degraded", "data": cached, "cached": True, "ran_at": ran}
        return {"status": "degraded",
                "data": {"regimes": [], "regime_summary": "Live regime unavailable."}}
    except Exception as e:
        raise _server_error(e)


# ─── Step 1 — Analysis history ────────────────────────────────────────────────

@app.get("/api/history")
def analysis_history(limit: int = Query(20, ge=1, le=500)):
    try:
        rows = get_analysis_history(limit=limit)
        return {"status": "ok", "data": rows}
    except Exception as e:
        raise _server_error(e)


@app.get("/api/history/{run_id}")
def analysis_detail(run_id: int):
    try:
        row = get_analysis_by_id(run_id)
        if not row:
            raise HTTPException(status_code=404, detail="Run not found")
        return {"status": "ok", "data": row}
    except HTTPException:
        raise
    except Exception as e:
        raise _server_error(e)


# ─── Market Intelligence (SWS-style) ──────────────────────────────────────────────────

@app.get("/api/market-data")
def market_data_endpoint():
    try:
        payload = build_data_payload(DEFAULT_WATCHLIST)
        symbols = [h["symbol"] for h in payload["holdings"]]
        result  = get_all_market_data(symbols)
        return {"status": "ok", "data": result}
    except Exception as e:
        raise _server_error(e)


# ─── Company Intelligence Agents ─────────────────────────────────────────────

@app.post("/api/agents/sec", dependencies=[Depends(require_api_key)])
def agent_sec():
    """Watch SEC filings for current holdings (last 7 days)."""
    try:
        payload = build_data_payload(DEFAULT_WATCHLIST)
        symbols = [h["symbol"] for h in payload["holdings"]]
        result  = watch_filings(symbols, days_back=7)
        return {"status": "ok", "data": result}
    except Exception as e:
        raise _server_error(e)


@app.get("/api/agents/sec/history")
def agent_sec_history(days: int = 30):
    """Return cached SEC filing analyses from DB."""
    try:
        payload = build_data_payload(DEFAULT_WATCHLIST)
        symbols = [h["symbol"] for h in payload["holdings"]]
        result  = get_filing_history(symbols, days=days)
        return {"status": "ok", "data": result}
    except Exception as e:
        raise _server_error(e)


@app.post("/api/agents/news", dependencies=[Depends(require_api_key)])
def agent_news():
    """Scan news sentiment for current holdings + watchlist."""
    try:
        payload = build_data_payload(DEFAULT_WATCHLIST)
        symbols = list({h["symbol"] for h in payload["holdings"]} | {w["symbol"] for w in payload["watchlist"]})
        result  = scan_news(symbols, days_back=7)
        return {"status": "ok", "data": result}
    except Exception as e:
        raise _server_error(e)


@app.get("/api/agents/news/history")
def agent_news_history(days: int = 7):
    """Return cached news sentiment from DB."""
    try:
        payload = build_data_payload(DEFAULT_WATCHLIST)
        symbols = list({h["symbol"] for h in payload["holdings"]} | {w["symbol"] for w in payload["watchlist"]})
        result  = get_news_history(symbols, days=days)
        return {"status": "ok", "data": result}
    except Exception as e:
        raise _server_error(e)


@app.post("/api/agents/fundamentals", dependencies=[Depends(require_api_key)])
def agent_fundamentals():
    """Run full fundamental momentum scan (~3-5 min, uses Claude Sonnet)."""
    try:
        result = run_fundamental_scan()
        return {"status": "ok", "data": result}
    except Exception as e:
        raise _server_error(e)


@app.get("/api/agents/fundamentals/latest")
def agent_fundamentals_latest():
    """Return the latest cached fundamental scan from DB."""
    try:
        result = get_latest_fundamental_scan()
        if not result:
            return {"status": "ok", "data": None, "message": "No scan yet — run POST /api/agents/fundamentals first"}
        return {"status": "ok", "data": result}
    except Exception as e:
        raise _server_error(e)


# ─── Investment Screener ──────────────────────────────────────────────────────

@app.post("/api/screener", dependencies=[Depends(require_api_key)])
def run_screener():
    try:
        # Reduced from 20 to 10 to instantly resolve perceived hangs
        candidates = screen_market(top_n=10)
        if not candidates:
            raise HTTPException(status_code=503, detail="No screener data returned — market may be closed")
        result = analyze_screener(candidates)
        resp = {"status": "ok", "data": result, "candidates_screened": len(candidates), "as_of": __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat() + "Z"}
        _save_engine_result("screener", resp)
        return resp
    except HTTPException:
        raise
    except Exception as e:
        raise _server_error(e)


# ─── Kelly Criterion Position Sizer ──────────────────────────────────────────

@app.post("/api/kelly", dependencies=[Depends(require_api_key)])
def kelly_endpoint():
    try:
        payload        = build_data_payload(DEFAULT_WATCHLIST)
        holdings       = payload["holdings"]
        total_equity   = payload["equity_summary"].get("portfolio_equity", 0)
        symbols        = [h["symbol"] for h in holdings]
        analyst_data   = get_analyst_targets(symbols)
        analyst_upside = {s: {"upside_pct": analyst_data[s].get("upside_pct")} for s in analyst_data}
        
        ai_verdicts = {}
        try:
            from db import get_analysis_history, get_analysis_by_id
            history = get_analysis_history(limit=1)
            if history:
                latest_run = get_analysis_by_id(history[0]["id"])
                if latest_run and "result" in latest_run:
                    holdings_analysis = latest_run["result"].get("holdings_analysis", [])
                    for ha in holdings_analysis:
                        ai_verdicts[ha["symbol"]] = {
                            "verdict": ha.get("verdict"),
                            "pct_diff": ha.get("fair_value", {}).get("pct_diff", 0)
                        }
        except Exception as err:
            print(f"Failed to fetch AI verdicts for Kelly: {err}")

        result         = run_kelly_analysis(holdings, total_equity, analyst_upside, ai_verdicts)
        resp = {"status": "ok", "data": result, "total_equity": total_equity}
        _save_engine_result("kelly", resp)
        return resp
    except Exception as e:
        raise _server_error(e)


# ─── Price Chart ─────────────────────────────────────────────────────────────

@app.get("/api/chart/{symbol}")
def price_chart(symbol: str, period: str = "1mo"):
    # 30s TTL cache — dashboard polls this per holding; without the cache
    # every refresh would spawn a fresh yfinance session + crumb fetch +
    # tz-cache file open, which is the dominant FD-leak path.
    sym = _safe_symbol(symbol)
    key = (sym, period)
    now = time.monotonic()
    with _chart_cache_lock:
        hit = _chart_cache.get(key)
        if hit and (now - hit[0]) < CHART_TTL_SEC:
            return hit[1]

    try:
        # price_source, not raw yfinance: under the VPN finance-block Yahoo
        # returns nothing and the dashboard chart rendered BLANK. Daily ranges use
        # the RH-fallback-backed yf_download; intraday 1d/5d needs 5-minute bars,
        # which Yahoo/RH-unauth can't serve here — route those to Alpaca via
        # price_source.intraday_5m (key-gated; honest fallback when unconfigured).
        import price_source
        period_map = {"1d":"1d","5d":"5d","1w":"5d","1mo":"1mo","3mo":"3mo","6mo":"6mo","1y":"1y"}
        yf_period = period_map.get(period, "1mo")
        interval  = "5m" if period in ("1d","5d") else "1d"
        if period in ("1d", "5d"):
            raw = price_source.intraday_5m(sym, period)
            if raw is None or getattr(raw, "empty", True):
                # No intraday provider configured / no data → honest empty state;
                # the frontend renders "try a daily range". Cache it briefly too.
                result = {"symbol": sym, "period": period, "intraday_unavailable": True,
                          "dates": [], "prices": [], "pct_change": 0,
                          "current": None, "start": None}
                with _chart_cache_lock:
                    _chart_cache[key] = (now, result)
                return result
        else:
            raw = price_source.yf_download(sym, period=yf_period, interval=interval,
                                           auto_adjust=True, progress=False)
            if raw is None or raw.empty:
                raise HTTPException(status_code=404, detail="No data")
        closes = raw["Close"].squeeze().dropna()
        dates  = [str(d)[:16] if interval=="5m" else str(d)[:10] for d in closes.index]
        # .flatten() ensures 1D array if yf returns MultiIndex dataframe,
        # handle Series or DataFrame robustly
        import numpy as np
        prices = [round(float(v), 2) for v in np.array(closes).flatten()]
        pct_chg = round((prices[-1]/prices[0]-1)*100, 2) if len(prices)>1 else 0
        result = {"symbol":sym,"period":period,"dates":dates,"prices":prices,
                  "pct_change":pct_chg,"current":prices[-1],"start":prices[0]}
        with _chart_cache_lock:
            _chart_cache[key] = (now, result)
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise _server_error(e)


# ─── Consensus Engine ────────────────────────────────────────────────────────

@app.post("/api/consensus", dependencies=[Depends(require_api_key)])
def consensus_endpoint():
    try:
        payload      = build_data_payload(DEFAULT_WATCHLIST)
        holdings     = payload["holdings"]
        total_equity = payload["equity_summary"].get("portfolio_equity", 0)
        result       = run_consensus_portfolio(holdings, total_equity)
        resp = {"status": "ok", "data": result}
        _save_engine_result("consensus", resp)
        return resp
    except Exception as e:
        raise _server_error(e)


# ─── Cached engine results (power the dashboard's "never empty" run-tabs) ─────
def _cached_response(key: str) -> dict:
    """Normalize a stored engine result into a frontend-friendly envelope.

    Returns the same shape the matching POST returns, plus cached/ran_at/fresh.
    Handles both wrapped responses (this server) and raw results (run_engines2).
    """
    data = _load_engine_results()
    val = data.get(key)
    ran = data.get(f"{key}_ran_at")
    if val is None:
        return {"cached": False}
    if key in ("consensus", "kelly") and isinstance(val, dict) \
            and "holdings" not in val and "data" not in val:
        # The daily chain (run_engines2) stores these as per-symbol dicts;
        # the dashboard renderers expect the POST shape {holdings: [...]} —
        # normalize here so the Signals tab renders the 10:30 results.
        val = {"holdings": [{"symbol": s, **(v if isinstance(v, dict) else {})}
                            for s, v in val.items()]}
    out = dict(val) if isinstance(val, dict) and ("data" in val or "analysis" in val) else {"data": val}
    out["cached"] = True
    out["ran_at"] = ran
    out["fresh"] = _is_fresh(ran)
    return _sanitize(out)


@app.get("/api/deep-analysis/cached")
def deep_analysis_cached():
    return _cached_response("ai_result")


@app.get("/api/consensus/cached")
def consensus_cached():
    return _cached_response("consensus")


@app.get("/api/consensus/{symbol}")
def consensus_symbol(symbol: str):
    """Run the 11-node consensus engine on ANY ticker — not just held positions.

    This is the 'analyse any share' surface for customers: same engine, same
    weights, run stateless (no position context). Declared AFTER the literal
    /cached route so 'cached' never matches the path parameter. Guarded by the
    heavy-endpoint rate limiter like the portfolio consensus run."""
    sym = _safe_symbol(symbol)
    try:
        from consensus import run_consensus_holding
        row = run_consensus_holding(sym, equity=0.0, total_portfolio=1.0,
                                    avg_cost=0.0, current_price=0.0)
        return {"status": "ok", "data": row}
    except HTTPException:
        raise
    except Exception as e:
        raise _server_error(e)


@app.get("/api/kelly/cached")
def kelly_cached():
    return _cached_response("kelly")


@app.get("/api/monte-carlo/cached")
def monte_carlo_cached():
    return _cached_response("monte_carlo")


@app.get("/api/screener/cached")
def screener_cached():
    return _cached_response("screener")


@app.get("/api/portfolio/history")
def portfolio_history(points: int = 60):
    """Portfolio equity series for the KPI sparklines. Always returns a list."""
    try:
        from db import get_portfolio_equity_history
        hist = get_portfolio_equity_history(points)
        series = [round(float(h["total_equity"]), 2)
                  for h in hist if h.get("total_equity") is not None]
        return {"status": "ok", "series": series, "points": len(series)}
    except Exception as e:
        # Graceful — the sparkline draws nothing rather than erroring. Log the
        # detail server-side; NEVER return str(e) to the client — a DB failure
        # carries the sqlite path / "database disk image is malformed" / schema
        # (8th-pass, Fable audit: customer-reachable str(e) leak).
        logger.warning("portfolio_history failed: %s", e)
        return {"status": "error", "series": [], "error": "unavailable"}


@app.get("/api/sections/status")
def sections_status():
    """Per-section freshness for the sidebar badges. {tab:{ran_at,fresh}}."""
    data = _load_engine_results()

    def _stamp(key: str):
        ran = data.get(f"{key}_ran_at")
        if ran:
            return {"ran_at": ran, "fresh": _is_fresh(ran)}
        # cached data exists but age unknown (e.g. written by run_engines2) → stale badge
        if data.get(key) is not None:
            return {"ran_at": None, "fresh": False}
        return None

    cons, kelly = _stamp("consensus"), _stamp("kelly")
    # Signals tab is "fresh" if either engine has a fresh result; ran_at = newest.
    sig = None
    stamps = [s for s in (cons, kelly) if s]
    if stamps:
        newest = max(stamps, key=lambda s: s["ran_at"] or "")
        sig = {"ran_at": newest["ran_at"], "fresh": any(s["fresh"] for s in stamps)}

    out = {
        "overview":     {"ran_at": None, "fresh": True},   # always live
        "analysis":     _stamp("ai_result"),
        "daily":        _stamp("ai_result"),
        "risk":         _stamp("monte_carlo"),
        "radar":        _stamp("screener"),
        "signals":      sig,
    }
    return {k: v for k, v in out.items() if v is not None}


# ─── FFT Cycle Detection ──────────────────────────────────────────────────────

@app.get("/api/fft-cycles")
def fft_cycles_endpoint():
    """
    Detect dominant price cycles via FFT for all current holdings.
    Returns period, phase position, direction, and BULLISH/NEUTRAL/BEARISH signal
    per holding. Zero extra cost — pure math on yfinance price data.
    """
    try:
        payload = build_data_payload(DEFAULT_WATCHLIST)
        symbols = [h["symbol"] for h in payload["holdings"]]
        result  = batch_fft_cycles(symbols)
        return {"status": "ok", "data": result}
    except Exception as e:
        raise _server_error(e)


@app.get("/api/fft-cycles/{symbol}")
def fft_cycles_single(symbol: str):
    """FFT cycle analysis for a single symbol."""
    try:
        from fft_cycles import detect_fft_cycles
        result = detect_fft_cycles(_safe_symbol(symbol))
        return {"status": "ok", "data": result}
    except Exception as e:
        raise _server_error(e)


@app.get("/api/regime/{symbol}")
def regime_symbol(symbol: str):
    """Market regime (ADX/BB/RSI + FRED macro) for ANY ticker — the customer
    'analyse any share' regime surface. Public data; no owner/position context."""
    sym = _safe_symbol(symbol)
    cached = _ticker_get_cached("regime", sym)
    if cached:
        return cached
    try:
        import regime as _regime
        out = _regime.detect_regimes([sym])
        rows = out.get("regimes") or []
        if not rows:
            raise HTTPException(status_code=404, detail="No regime data")
        return _ticker_get_store("regime", sym, {"status": "ok", "data": rows[0]})
    except HTTPException:
        raise
    except Exception as e:
        raise _server_error(e)


@app.get("/api/fundamentals/{symbol}")
def fundamentals_symbol(symbol: str):
    """Company fundamentals for ANY ticker. Robinhood is PRIMARY (VPN-safe: PE / P/B
    / market-cap / 52-week range / dividend-yield / sector); yfinance momentum row is
    the fallback (its .info is category-blocked under the owner's VPN)."""
    sym = _safe_symbol(symbol)
    cached = _ticker_get_cached("fundamentals", sym)
    if cached:
        return cached
    try:
        import market_data
        row = market_data.rh_fundamentals(sym)
        if not row:
            import fundamental_agent
            rows = fundamental_agent._fetch_fundamentals_batch([sym])
            row = rows[0] if rows and not rows[0].get("error") else None
        if not row:
            raise HTTPException(status_code=404, detail="No fundamentals")
        return _ticker_get_store("fundamentals", sym, {"status": "ok", "data": row})
    except HTTPException:
        raise
    except Exception as e:
        raise _server_error(e)


@app.get("/api/analyst/{symbol}")
def analyst_symbol(symbol: str):
    """Analyst ratings for ANY ticker. Robinhood is PRIMARY (VPN-safe buy/hold/sell
    consensus + # analysts); yfinance price-targets are the fallback."""
    sym = _safe_symbol(symbol)
    cached = _ticker_get_cached("analyst", sym)
    if cached:
        return cached
    try:
        import market_data
        row = market_data.rh_analyst(sym)
        if not row:
            data = market_data.get_analyst_targets([sym]) or {}
            yf_row = data.get(sym)
            row = yf_row if (yf_row and not yf_row.get("error")) else None
        if not row:
            raise HTTPException(status_code=404, detail="No analyst data")
        return _ticker_get_store("analyst", sym, {"status": "ok", "data": row})
    except HTTPException:
        raise
    except Exception as e:
        raise _server_error(e)


@app.get("/api/news/{symbol}")
def news_symbol(symbol: str):
    """Recent news HEADLINES for ANY ticker (RSS, NO LLM — the sentiment *score*
    stays owner-only so the customer analyze path stays zero-cost). Returns the
    trimmed top-8 items (title/url/source/published); [] when none."""
    sym = _safe_symbol(symbol)
    cached = _ticker_get_cached("news", sym)
    if cached:
        return cached
    try:
        import news_agent
        # Alpaca first (VPN-safe, key-gated); RSS fallback (blocked on the owner's VPN).
        items = news_agent._fetch_alpaca_news(sym) or news_agent._fetch_all_news(sym) or []
        trimmed = [{"title": it.get("title", ""), "url": it.get("url", ""),
                    "source": it.get("source", ""), "published": it.get("published", "")}
                   for it in items[:8]]
        return _ticker_get_store("news", sym, {"status": "ok", "data": trimmed})
    except HTTPException:
        raise
    except Exception as e:
        raise _server_error(e)


def _latest_close(sym: str) -> float | None:
    """VPN-safe latest daily close for grounding the single-ticker research card —
    reuses the chart's RH-fallback-backed price_source path (not raw yfinance).
    Returns None when the ticker has no data, so the caller 404s rather than let
    the model fabricate a price."""
    try:
        import price_source
        raw = price_source.yf_download(sym, period="1mo", interval="1d",
                                       auto_adjust=True, progress=False)
        if raw is None or getattr(raw, "empty", True):
            return None
        closes = raw["Close"].squeeze().dropna()
        if len(closes) == 0:
            return None
        import numpy as np
        return round(float(np.array(closes).flatten()[-1]), 2)
    except Exception:
        return None


# Short-TTL shared cache for the single-ticker AI card. The card is PUBLIC per-ticker
# research (no per-customer data), so a process-wide cache is correct and stops a hot
# ticker from re-spending on Claude every request.
_TICKER_CARD_TTL = 900  # 15 min
_ticker_card_cache: dict[str, tuple[float, dict]] = {}
_ticker_card_lock = threading.Lock()
# Single-flight: per-symbol asyncio locks so N concurrent first requests for a hot
# ticker spend ONE Claude call, not N — the waiters find the cache filled. Also the
# eviction hook: expired cache entries (and their locks) are purged on every store,
# so neither dict grows past the set of symbols queried in the last TTL window.
_ticker_card_flight: dict[str, asyncio.Lock] = {}


def _ticker_card_get(sym: str) -> dict | None:
    with _ticker_card_lock:
        hit = _ticker_card_cache.get(sym)
    return hit[1] if hit and (time.monotonic() - hit[0]) < _TICKER_CARD_TTL else None


def _ticker_card_store(sym: str, card: dict) -> None:
    now = time.monotonic()
    with _ticker_card_lock:
        _ticker_card_cache[sym] = (now, card)
        for k in [k for k, (ts, _) in _ticker_card_cache.items() if (now - ts) >= _TICKER_CARD_TTL]:
            del _ticker_card_cache[k]
            lock = _ticker_card_flight.get(k)
            if lock is not None and not lock.locked():
                del _ticker_card_flight[k]


@app.post("/api/analyze-ticker", dependencies=[Depends(require_customer_or_owner)])
async def analyze_ticker_endpoint(request: Request):
    """Rich single-ticker AI research card for ANY ticker — the customer-facing,
    money-free subset of the deep portfolio analysis (VAL/GRO/HLT/MOM/DIV scores,
    fair-value pill, 3M/12M prediction, risks/rewards, AI verdict). PUBLIC
    single-name research: no portfolio, no positions, no owner dollars — exempt from
    money-masking (its public per-share prose must reach the customer raw).

    Gated to authenticated callers (require_customer_or_owner) so it is never an
    anonymous, uncapped LLM-spend surface; rate-limited via _HEAVY_PREFIXES; the
    optional LLM_DAILY_BUDGET_USD cap applies inside deep_analyze_ticker; results are
    cached per symbol for _TICKER_CARD_TTL. The blocking Anthropic call is offloaded
    to a thread so the single event loop is never frozen. Body: {"symbol": "AAPL"}."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    sym = _safe_symbol((body or {}).get("symbol", ""))
    card = _ticker_card_get(sym)
    if card is not None:
        auth_users.log_usage(_session_email(request), "ticker_card_cached", sym)
        return {"status": "ok", "data": card}
    try:
        # Single-flight per symbol: the ~15s LLM call means concurrent first
        # requests are the norm for a hot ticker — followers wait on the leader's
        # lock, then re-check the cache instead of spending again. setdefault is
        # safe here: this coroutine runs on the single event loop, unawaited.
        flight = _ticker_card_flight.setdefault(sym, asyncio.Lock())
        async with flight:
            card = _ticker_card_get(sym)
            if card is not None:
                auth_users.log_usage(_session_email(request), "ticker_card_cached", sym)
                return {"status": "ok", "data": card}
            # Ground the valuation on a REAL quote: no price = unknown ticker / data
            # outage → 404 rather than let the model fabricate a price and card.
            price = await asyncio.to_thread(_latest_close, sym)
            if price is None:
                raise HTTPException(status_code=404, detail="No market data for this ticker")
            card = await asyncio.to_thread(deep_analyze_ticker, sym, price)
            _ticker_card_store(sym, card)
            auth_users.log_usage(_session_email(request), "ticker_card", sym)
            return {"status": "ok", "data": card}
    except HTTPException:
        raise
    except Exception as e:
        raise _server_error(e)


# ─── Customer book — "Analyze MY portfolio" (v2 of the customer surface) ─────
# The customer enters their OWN positions (symbol + shares [+ avg cost]) and gets
# the full engine suite on THEIR book: deep AI analysis, Kelly sizing, Monte Carlo.
# Everything here is derived from customer_positions + public quotes — zero owner
# data — which is why /api/mybook is mask-exempt (masking would redact the
# customer's own dollars back at them).

def _cached_close(sym: str) -> float | None:
    """_latest_close with the shared 5-min per-ticker cache in front — valuing a
    book re-reads every symbol, so repeat views must not re-fetch upstream."""
    hit = _ticker_get_cached("close", sym)
    if hit:
        return hit.get("price")
    price = _latest_close(sym)
    if price is not None:
        _ticker_get_store("close", sym, {"price": price})
    return price


def _customer_book_payload(positions: list[dict]) -> dict:
    """Value the customer's positions live and shape them EXACTLY like the owner
    payload (build_data_payload) so the existing engines run unchanged. Symbols
    with no price data are excluded from the engines and reported in `unpriced`."""
    holdings, unpriced, total = [], [], 0.0
    for p in positions:
        price = _cached_close(p["symbol"])
        if price is None or price <= 0:
            unpriced.append(p["symbol"])
            continue
        equity = round(p["shares"] * price, 2)
        ac = p.get("avg_cost")
        pct_chg = round((price - ac) / ac * 100, 2) if ac else 0.0
        holdings.append({
            "symbol": p["symbol"], "quantity": p["shares"], "average_buy_price": ac or 0.0,
            "equity": equity, "percent_change": pct_chg,
            "equity_change": round(equity - (ac or price) * p["shares"], 2),
            "current_price": price, "type": "stock", "pe_ratio": None,
            "portfolio_percentage": 0.0,
        })
        total += equity
    for h in holdings:
        h["portfolio_percentage"] = round(h["equity"] / total * 100, 1) if total else 0.0
    equity_summary = {"portfolio_equity": round(total, 2), "cash": 0.0, "buying_power": 0.0,
                      "day_change": 0.0, "day_change_pct": 0.0}
    return {"as_of": _dt.now(_tz.utc).isoformat(), "equity_summary": equity_summary,
            "holdings": holdings, "watchlist": [], "unpriced": unpriced}


@app.get("/api/mybook")
def mybook_get(request: Request):
    """The current user's book, valued live (per-user, session-scoped)."""
    email = _session_email(request)
    if not email:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        payload = _customer_book_payload(auth_users.get_positions(email))
        return {"status": "ok", "data": {
            "positions": payload["holdings"],
            "total_equity": payload["equity_summary"]["portfolio_equity"],
            "unpriced": payload["unpriced"],
        }}
    except Exception as e:
        raise _server_error(e)


@app.post("/api/mybook")
async def mybook_set(request: Request):
    """Upsert one position: {"symbol", "shares", "avg_cost"?}. Returns the whole
    valued book; status "full" when the per-user position cap refuses a NEW symbol."""
    email = _session_email(request)
    if not email:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        body = await request.json()
    except Exception:
        body = {}
    sym = _safe_symbol((body or {}).get("symbol", ""))
    ok = auth_users.set_position(email, sym, (body or {}).get("shares"),
                                 (body or {}).get("avg_cost"))
    if not ok and len(auth_users.get_positions(email)) < auth_users._MAX_POSITIONS:
        raise HTTPException(status_code=400, detail="Invalid shares or avg_cost")
    return {"status": "ok" if ok else "full", "data": mybook_get(request)["data"]}


@app.delete("/api/mybook/{symbol}")
def mybook_remove(symbol: str, request: Request):
    email = _session_email(request)
    if not email:
        raise HTTPException(status_code=401, detail="Authentication required")
    auth_users.remove_position(email, _safe_symbol(symbol))
    return mybook_get(request)


# Per-user cache + single-flight for the book analysis. Keyed by (email) with the
# book fingerprint in the value: same book within the TTL = cached result; any
# position change = new fingerprint = fresh run. Same eviction/loop-affinity rules
# as the ticker card (see conftest note on _ticker_card_flight).
_MYBOOK_TTL = 900  # 15 min
_mybook_cache: dict[str, tuple[float, str, dict]] = {}
_mybook_lock = threading.Lock()
_mybook_flight: dict[str, asyncio.Lock] = {}


def _book_fingerprint(positions: list[dict]) -> str:
    return _json.dumps(sorted([(p["symbol"], p["shares"], p.get("avg_cost")) for p in positions]))


def _run_book_engines(payload: dict) -> dict:
    """Blocking engine suite on a customer payload — runs in a worker thread.
    Regime/Kelly/Monte-Carlo are best-effort (partial results beat a 500); the
    Claude deep analysis is the core deliverable and failures propagate.

    Order matters: the deep analysis runs BEFORE Kelly so its per-holding verdicts
    (verdict + fair-value gap) feed run_kelly_analysis exactly like the owner
    pipeline does (/api/kelly builds the same map from the latest analysis run) —
    without them Kelly sizes verdict-blind."""
    symbols = [h["symbol"] for h in payload["holdings"]]
    regime_context = None
    try:
        regime_context = detect_regimes(symbols).get("regime_summary")
    except Exception:
        pass
    analysis = deep_analyze_portfolio(payload, regime_context=regime_context)
    ai_verdicts = {}
    for ha in analysis.get("holdings_analysis") or []:
        if ha.get("symbol"):
            ai_verdicts[ha["symbol"]] = {
                "verdict": ha.get("verdict"),
                "pct_diff": (ha.get("fair_value") or {}).get("pct_diff", 0),
            }
    try:
        mc = run_monte_carlo(payload["holdings"], payload["equity_summary"])
    except Exception as e:
        mc = {"error": f"monte carlo unavailable: {type(e).__name__}"}
    try:
        analyst = get_analyst_targets(symbols) or {}
        upside = {s: {"upside_pct": analyst[s].get("upside_pct")} for s in analyst
                  if not analyst[s].get("error")}
        kelly = run_kelly_analysis(payload["holdings"],
                                   payload["equity_summary"]["portfolio_equity"],
                                   upside, ai_verdicts)
    except Exception as e:
        kelly = {"error": f"kelly unavailable: {type(e).__name__}"}
    return {"analysis": analysis, "kelly": kelly, "monte_carlo": mc,
            "book": {"positions": payload["holdings"],
                     "total_equity": payload["equity_summary"]["portfolio_equity"],
                     "unpriced": payload["unpriced"]}}


@app.post("/api/mybook/analyze", dependencies=[Depends(require_customer_or_owner)])
async def mybook_analyze(request: Request):
    """Full engine suite on the CUSTOMER's own book — deep AI analysis + Kelly +
    Monte Carlo, exactly the owner pipeline but fed from their entered positions.
    Auth-gated (LLM spend), heavy rate bucket, cached per (user, book fingerprint)
    for _MYBOOK_TTL, single-flight per user, engines offloaded to a thread. NOT
    logged to the owner's analysis history — this is the customer's data."""
    email = _session_email(request)
    if not email:
        raise HTTPException(status_code=401, detail="Authentication required")
    positions = auth_users.get_positions(email)
    if not positions:
        raise HTTPException(status_code=400, detail="Your book is empty — add positions first")
    fp = _book_fingerprint(positions)
    with _mybook_lock:
        hit = _mybook_cache.get(email)
    if hit and hit[1] == fp and (time.monotonic() - hit[0]) < _MYBOOK_TTL:
        auth_users.log_usage(email, "book_analysis_cached")
        return {"status": "ok", "cached": True, "data": hit[2]}
    try:
        flight = _mybook_flight.setdefault(email, asyncio.Lock())
        async with flight:
            with _mybook_lock:
                hit = _mybook_cache.get(email)
            if hit and hit[1] == fp and (time.monotonic() - hit[0]) < _MYBOOK_TTL:
                auth_users.log_usage(email, "book_analysis_cached")
                return {"status": "ok", "cached": True, "data": hit[2]}
            payload = await asyncio.to_thread(_customer_book_payload, positions)
            if not payload["holdings"]:
                raise HTTPException(status_code=404,
                                    detail="No market data for any symbol on your book")
            result = await asyncio.to_thread(_run_book_engines, payload)
            with _mybook_lock:
                _mybook_cache[email] = (time.monotonic(), fp, result)
                for k in [k for k, (ts, _, _) in _mybook_cache.items()
                          if (time.monotonic() - ts) >= _MYBOOK_TTL]:
                    del _mybook_cache[k]
            # Durable copy powers GET /mybook/report after the in-memory TTL/restart;
            # metering counts the live LLM run. Both best-effort by contract.
            try:
                auth_users.save_book_analysis(email, result)
            except Exception:
                pass
            auth_users.log_usage(email, "book_analysis")
            return {"status": "ok", "cached": False, "data": result}
    except HTTPException:
        raise
    except Exception as e:
        raise _server_error(e)


@app.post("/api/mybook/import-watchlist")
def mybook_import_watchlist(request: Request):
    """Seed the book from the user's saved watchlist: every watchlist symbol not
    already on the book is added with shares=1 (a placeholder the user edits —
    we cannot guess their position size). Existing positions are never touched."""
    email = _session_email(request)
    if not email:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        held = {p["symbol"] for p in auth_users.get_positions(email)}
        imported, skipped_cap = [], []
        for sym in auth_users.get_watch(email):
            if sym in held:
                continue
            (imported if auth_users.set_position(email, sym, 1.0) else skipped_cap).append(sym)
        book = mybook_get(request)["data"]
        return {"status": "ok", "imported": imported, "skipped_cap": skipped_cap, "data": book}
    except HTTPException:
        raise
    except Exception as e:
        raise _server_error(e)


def _render_book_report(email: str) -> str:
    """Standalone, print-friendly HTML report of the customer's book + their LAST
    persisted analysis. Server-side rendered with every dynamic string escaped —
    this page bypasses the dashboard's audited JS builders."""
    import html as _h
    book = _customer_book_payload(auth_users.get_positions(email))
    saved = auth_users.get_latest_book_analysis(email)
    e = _h.escape

    def row(label, val):
        return (f"<tr><td style='padding:6px 12px;color:#555'>{e(str(label))}</td>"
                f"<td style='padding:6px 12px;font-weight:600'>{e(str(val))}</td></tr>")

    pos_rows = "".join(
        f"<tr><td style='padding:6px 12px;font-weight:600'>{e(h['symbol'])}</td>"
        f"<td style='padding:6px 12px;text-align:right'>{h['quantity']:g}</td>"
        f"<td style='padding:6px 12px;text-align:right'>${h['current_price']:,.2f}</td>"
        f"<td style='padding:6px 12px;text-align:right'>${h['equity']:,.2f}</td>"
        f"<td style='padding:6px 12px;text-align:right'>{h['portfolio_percentage']:.1f}%</td></tr>"
        for h in book["holdings"])
    total = book["equity_summary"]["portfolio_equity"]

    body = ""
    if saved:
        d = saved.get("analysis") or {}
        mc, ky = saved.get("monte_carlo") or {}, saved.get("kelly") or {}
        risks = "".join(f"<li>{e(r.get('text', ''))} <em>({e(r.get('severity', ''))})</em></li>"
                        for r in (d.get("portfolio_risks") or []))
        cards = "".join(
            f"<h3 style='margin:18px 0 4px'>{e(h.get('symbol', ''))} — {e(h.get('verdict', ''))}</h3>"
            f"<p style='margin:4px 0;color:#444'>{e(h.get('snowflake_summary', ''))}</p>"
            f"<p style='margin:4px 0;color:#666;font-size:13px'>3M: {e((h.get('prediction') or {}).get('3m', ''))} · "
            f"12M: {e((h.get('prediction') or {}).get('12m', ''))}</p>"
            for h in (d.get("holdings_analysis") or []))
        kelly_rows = "".join(
            row(f"{k.get('symbol', '')} · {k.get('action', '')}",
                f"{k.get('current_pct', 0):.1f}% → {k.get('optimal_pct', 0):.1f}%")
            for k in (ky.get("holdings") or [])[:15])
        body = f"""
  <h2>AI Verdict: {e(str(d.get('portfolio_verdict', '—')))}</h2>
  <table>{row('Portfolio score', d.get('portfolio_score', '—'))}{row('Risk score', d.get('risk_score', '—'))}
  {row('Diversification', d.get('diversification_score', '—'))}{row('Portfolio type', d.get('portfolio_type', '—'))}</table>
  <p style='color:#444;line-height:1.6'>{e(str(d.get('market_context', '')))}</p>
  {('<h2>Key risks</h2><ul>' + risks + '</ul>') if risks else ''}
  <h2>Monte Carlo (1yr)</h2>
  <table>{row('Median outcome', '$' + format(mc.get('median', 0) or 0, ',.0f'))}
  {row('P5 – P95', '$' + format(mc.get('p5', 0) or 0, ',.0f') + ' – $' + format(mc.get('p95', 0) or 0, ',.0f'))}
  {row('VaR 95%', '$' + format(mc.get('var_95', 0) or 0, ',.0f'))}</table>
  <h2>Kelly sizing</h2><table>{kelly_rows or row('Status', 'unavailable')}</table>
  <h2>Per-holding research</h2>{cards}
  <p style='color:#888;font-size:12px'>Analysis generated {e(str(saved.get('_saved_at', ''))[:16])} UTC.</p>"""
    else:
        body = "<p><em>No analysis yet — run <b>Analyze My Portfolio</b> on the dashboard, then reload this report.</em></p>"

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>GESH — Portfolio Report</title>
<style>body{{font-family:-apple-system,Segoe UI,sans-serif;max-width:860px;margin:32px auto;padding:0 20px;color:#111}}
h1{{border-bottom:2px solid #111;padding-bottom:8px}}h2{{margin-top:28px}}table{{border-collapse:collapse}}
tr:nth-child(even){{background:#f6f6f6}}@media print{{a{{display:none}}}}</style></head><body>
<h1>GESH Portfolio Report</h1>
<p style='color:#666'>Prepared for {e(email)} · {_dt.now(_tz.utc).strftime('%Y-%m-%d %H:%M')} UTC</p>
<h2>Positions</h2>
<table><tr style='border-bottom:1px solid #ccc'><th style='padding:6px 12px;text-align:left'>Symbol</th>
<th style='padding:6px 12px'>Shares</th><th style='padding:6px 12px'>Price</th>
<th style='padding:6px 12px'>Value</th><th style='padding:6px 12px'>Weight</th></tr>{pos_rows}</table>
<p><b>Total: ${total:,.2f}</b>{(' · No price data: ' + e(', '.join(book['unpriced']))) if book['unpriced'] else ''}</p>
{body}
<hr><p style='color:#888;font-size:12px'>Informational and educational only — not financial advice.
All trading decisions remain your responsibility. Generated by GESH Financial Observability.</p>
</body></html>"""


@app.get("/mybook/report", response_class=HTMLResponse)
def mybook_report(request: Request):
    """Print-friendly (browser print → PDF) report of the customer's OWN book and
    their last persisted analysis. Session-gated; NOT a public route (the login
    wall covers it when enabled). Not under /api so the masking middleware never
    touches it — correct, since it carries only the customer's own data."""
    email = _session_email(request)
    if not email:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        auth_users.log_usage(email, "book_report")
        return HTMLResponse(_render_book_report(email))
    except HTTPException:
        raise
    except Exception as e:
        raise _server_error(e)


@app.get("/api/usage/summary")
def usage_summary_endpoint(request: Request, days: int = 30):
    """Per-customer usage counters (LLM cards, book analyses, reports) — the
    metering groundwork for billing tiers. OWNER-ONLY: 403 for customer sessions."""
    email = _session_email(request)
    if not email:
        raise HTTPException(status_code=401, detail="Authentication required")
    if not auth_users.is_owner(email):
        raise HTTPException(status_code=403, detail="Owner-only view")
    try:
        return {"status": "ok", "days": max(1, min(days, 365)),
                "data": auth_users.usage_summary(days=max(1, min(days, 365)))}
    except Exception as e:
        raise _server_error(e)


# ─── Ollama Local LLM — REMOVED ─────────────────────────────────────────────
# Ollama (llama3.2:3b) was removed: a 3B local model cannot challenge Sonnet.
# "Second opinion" from a weaker model is random disagreement, not independent signal.
# Use /api/gemini/second-opinion/{symbol} (Gemini 2.5 Pro) for genuine independent review.
#
# Legacy endpoints return 410 Gone with a redirect message.

@app.get("/api/ollama/status")
@app.post("/api/ollama/analyze", dependencies=[Depends(require_api_key)])
@app.post("/api/ollama/second-opinion/{symbol}", dependencies=[Depends(require_api_key)])
async def ollama_removed_endpoint(symbol: str = ""):
    raise HTTPException(
        status_code=410,
        detail="Ollama endpoints removed. Use /api/gemini/second-opinion/{symbol} instead."
    )


# ─── Gemini AI Endpoints ─────────────────────────────────────────────────────

@app.get("/api/gemini/status")
def gemini_status_endpoint():
    """Check Gemini API key, SDK install, and model availability."""
    try:
        result = get_gemini_status()
        return {"status": "ok", "data": result}
    except Exception as e:
        raise _server_error(e)


@app.post("/api/gemini/analyze", dependencies=[Depends(require_api_key)])
def gemini_analyze_endpoint():
    """
    Fast Gemini Flash portfolio commentary.
    Same output format as /api/ollama/analyze but uses Google Gemini API.
    Requires GEMINI_API_KEY in .env.
    """
    holdings, equity_summary = [], {}
    data_error: str | None = None
    try:
        payload        = build_data_payload(DEFAULT_WATCHLIST)
        holdings       = payload.get("holdings", [])
        equity_summary = payload.get("equity_summary", {})
    except Exception as de:
        data_error = str(de)

    if not holdings:
        summary = (
            "No live portfolio data available"
            + (f" ({data_error})" if data_error else "")
            + ". Provide general US equity market commentary."
        )
        result = gemini_quick_analysis(summary)
        result["note"] = "Portfolio data unavailable — general commentary only"
    else:
        total = equity_summary.get("portfolio_equity", 0)
        top   = sorted(holdings, key=lambda h: h.get("equity", 0), reverse=True)[:6]
        summary = (
            f"Total equity: ${total:,.0f}\n"
            + "\n".join(
                f"  {h['symbol']}: {h.get('portfolio_percentage', 0):.1f}% alloc, "
                f"{h.get('percent_change', 0):+.1f}% today"
                for h in top
            )
        )
        result = gemini_quick_analysis(summary)

    return {"status": "ok", "data": _sanitize(result)}


@app.post("/api/gemini/second-opinion/{symbol}", dependencies=[Depends(require_api_key)])
async def gemini_second_opinion_endpoint(symbol: str, request: Request):
    """
    Gemini 1.5 Pro contrarian second opinion on a symbol.
    Optional JSON body: {"analysis": "...", "verdict": "STRONG BUY"}
    Independent reasoning from Claude — surfaces missed risks.
    """
    try:
        symbol = _safe_symbol(symbol)
        try:
            body = await request.json()
        except Exception:
            body = {}
        analysis = "".join(c for c in str(body.get("analysis", f"Symbol: {symbol}")) if c.isprintable() or c in "\n\t")[:2000]
        verdict  = "".join(c for c in str(body.get("verdict", "")) if c.isprintable())[:64]
        result   = gemini_second_opinion(symbol.upper(), analysis, verdict)
        return {"status": "ok", "data": _sanitize(result)}
    except Exception as e:
        raise _server_error(e)


@app.post("/api/gemini/market-commentary", dependencies=[Depends(require_api_key)])
async def gemini_market_commentary_endpoint(request: Request):
    """
    Gemini Flash broad market commentary.
    Optional JSON body: {"include_regime": true, "include_macro": true}
    Combines regime detection + FRED macro context into a narrative.
    """
    try:
        try:
            body = await request.json()
        except Exception:
            body = {}

        regime_summary = ""
        macro_context  = ""

        if body.get("include_regime", True):
            try:
                regime_data    = detect_regimes(DEFAULT_WATCHLIST)
                regime_summary = regime_data.get("regime_summary", "")
                macro_ctx      = regime_data.get("macro", {})
                macro_context  = macro_ctx.get("macro_summary", "")
            except Exception:
                pass

        result = gemini_market_commentary(
            regime_summary=regime_summary,
            macro_context=macro_context,
        )
        return {"status": "ok", "data": _sanitize(result)}
    except Exception as e:
        raise _server_error(e)


# ─── Allocation Config ────────────────────────────────────────────────────────

@app.get("/api/config")
def get_config():
    """Return current allocation config (caps, gates, scanner mode)."""
    try:
        cfg = load_config()
        return {"status": "ok", "data": cfg}
    except Exception as e:
        raise _server_error(e)


@app.post("/api/config", dependencies=[Depends(require_api_key)])
async def set_config(request: Request):
    """Update one or more config keys. Body: {key: value, ...}"""
    try:
        body = await request.json()
        cfg = update_config(**body)
        return {"status": "ok", "data": cfg}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise _server_error(e)


# ─── Consolidation Zone + Sector Filter ──────────────────────────────────────

@app.get("/api/scanner/consolidation/{symbol}")
def consolidation_zone_single(symbol: str, interval: str = "5m", period: str = "1d"):
    """Detect consolidation zone for a single symbol."""
    try:
        _validate_timeframe(interval, period)
        result = detect_consolidation(_safe_symbol(symbol), interval=interval, period=period)
        return {"status": "ok", "data": result}
    except Exception as e:
        raise _server_error(e)


@app.post("/api/scanner/consolidation", dependencies=[Depends(require_api_key)])
async def consolidation_zone_batch(request: Request):
    """
    Batch consolidation zone scan. Body: {"symbols": [...], "interval": "5m", "period": "1d", "min_rr": 2.0}
    If symbols is empty, uses current holdings + watchlist.
    """
    try:
        body = await request.json()
        symbols = body.get("symbols", [])
        if not symbols:
            payload = build_data_payload(DEFAULT_WATCHLIST)
            symbols = list({h["symbol"] for h in payload["holdings"]} |
                           {w["symbol"] for w in payload["watchlist"]})
        _interval = body.get("interval", "5m")
        _period   = body.get("period", "1d")
        _validate_timeframe(_interval, _period)
        result = scan_consolidations(
            symbols,
            interval=_interval,
            period=_period,
            min_rr=float(body.get("min_rr", 2.0)),
        )
        return {"status": "ok", "data": result}
    except Exception as e:
        raise _server_error(e)


@app.get("/api/scanner/sector/{symbol}")
def sector_hot_single(symbol: str):
    """Check if the sector ETF for a symbol has unusual volume (≥1.2× 20d avg)."""
    try:
        result = is_sector_hot(_safe_symbol(symbol))
        return {"status": "ok", "data": result}
    except Exception as e:
        raise _server_error(e)


@app.post("/api/scanner/sector", dependencies=[Depends(require_api_key)])
async def sector_hot_batch(request: Request):
    """
    Batch sector volume pre-filter. Body: {"symbols": [...]}
    If empty, uses current holdings + watchlist.
    """
    try:
        body = await request.json()
        symbols = body.get("symbols", [])
        if not symbols:
            payload = build_data_payload(DEFAULT_WATCHLIST)
            symbols = list({h["symbol"] for h in payload["holdings"]} |
                           {w["symbol"] for w in payload["watchlist"]})
        result = batch_sector_check(symbols)
        return {"status": "ok", "data": result}
    except Exception as e:
        raise _server_error(e)


# ─── Trade Log ────────────────────────────────────────────────────────────────

@app.get("/api/trades")
def get_trades(symbol: str = None, limit: int = Query(50, ge=1, le=500), open_only: bool = False, paper: bool = False):
    """Return trade history. ?paper=true for paper trades."""
    try:
        rows = get_trade_history(symbol=symbol, limit=limit, open_only=open_only, is_paper=paper)
        return {"status": "ok", "data": rows}
    except Exception as e:
        raise _server_error(e)


@app.get("/api/trades/stats")
def trade_stats(days: int = 30, paper: bool = False):
    """Win rate, P&L stats, scanner performance for last N days."""
    try:
        summary = trade_summary_for_report(days=days)
        return {"status": "ok", "data": summary}
    except Exception as e:
        raise _server_error(e)


@app.post("/api/trades/log", dependencies=[Depends(require_api_key)])
async def log_trade_endpoint(request: Request):
    """
    Log a new trade entry.
    Body: {symbol, action, shares, entry_price, stop_loss?, target_price?,
           scanner_source?, signal_reason?, is_paper?}
    """
    try:
        body = await request.json()
        trade_id = log_trade(
            symbol=body["symbol"],
            action=body["action"],
            shares=float(body["shares"]),
            entry_price=float(body["entry_price"]),
            stop_loss=body.get("stop_loss"),
            target_price=body.get("target_price"),
            scanner_source=body.get("scanner_source"),
            signal_reason=body.get("signal_reason"),
            notes=body.get("notes"),
            is_paper=bool(body.get("is_paper", False)),
        )
        return {"status": "ok", "trade_id": trade_id}
    except KeyError as e:
        raise HTTPException(status_code=400, detail=f"Missing field: {e}")
    except Exception as e:
        raise _server_error(e)


@app.post("/api/trades/{trade_id}/close", dependencies=[Depends(require_api_key)])
async def close_trade_endpoint(trade_id: int, request: Request):
    """Close an open trade. Body: {exit_price, notes?, is_paper?}"""
    try:
        body = await request.json()
        result = close_trade(
            trade_id=trade_id,
            exit_price=float(body["exit_price"]),
            notes=body.get("notes"),
            is_paper=bool(body.get("is_paper", False)),
        )
        return {"status": "ok", "data": result}
    except Exception as e:
        raise _server_error(e)


# ─── Paper Trading ────────────────────────────────────────────────────────────

@app.post("/api/paper/run", dependencies=[Depends(require_api_key)])
async def paper_run_endpoint(request: Request):
    """
    Simulate an action plan in paper mode.
    Body: {action_plan: [{symbol, action, shares, current_price, signal_reason?},...],
           scanner_source?: str}
    """
    try:
        body = await request.json()
        payload = build_data_payload(DEFAULT_WATCHLIST)
        total = payload["equity_summary"].get("portfolio_equity", 0)
        results = run_paper_session(
            action_plan=body.get("action_plan", []),
            holdings=payload["holdings"],
            total_portfolio=total,
            scanner_source=body.get("scanner_source", "api"),
        )
        return {"status": "ok", "data": results, "total_portfolio": total}
    except Exception as e:
        raise _server_error(e)


@app.post("/api/paper/check-closes", dependencies=[Depends(require_api_key)])
def paper_check_closes():
    """Auto-close paper positions that hit stop or target."""
    try:
        closed = check_and_close_paper_positions()
        return {"status": "ok", "closed": closed, "count": len(closed)}
    except Exception as e:
        raise _server_error(e)


@app.get("/api/paper/summary")
def paper_summary_endpoint(days: int = 7):
    """Paper vs live performance summary for last N days."""
    try:
        result = get_paper_summary(days=days)
        return {"status": "ok", "data": result}
    except Exception as e:
        raise _server_error(e)


# ─── Trading Execution Layer (Risk Gate + Paper Execution) ───────────────────

@app.post("/api/trading/dry-run", dependencies=[Depends(require_api_key)])
async def trading_dry_run(request: Request):
    """
    Dry-run a trade intent through the risk gate. No order placed.
    Body: {symbol, action, confidence?, target_position_pct?, stop_loss?, take_profit?, rationale?}
    Returns gate verdict (APPROVED/REJECTED + per-gate breakdown).
    """
    try:
        body = await request.json()
        intent = manual_intent(
            symbol=body["symbol"],
            action=body["action"],
            confidence=float(body.get("confidence", 0.8)),
            target_position_pct=body.get("target_position_pct"),
            stop_loss=body.get("stop_loss"),
            take_profit=body.get("take_profit"),
            rationale=body.get("rationale"),
        )
        payload = build_data_payload(DEFAULT_WATCHLIST)
        verdict = risk_gate_evaluate(intent, payload)
        return {"status": "ok", "data": verdict.to_dict()}
    except KeyError as e:
        raise HTTPException(status_code=400, detail=f"Missing field: {e}")
    except Exception as e:
        raise _server_error(e)


@app.post("/api/trading/paper-order", dependencies=[Depends(require_api_key)])
async def trading_paper_order(request: Request):
    """
    Run intent through risk gate then paper-execute it.
    Body: same as /api/trading/dry-run
    Returns gate verdict + order result.
    """
    try:
        body = await request.json()
        intent = manual_intent(
            symbol=body["symbol"],
            action=body["action"],
            confidence=float(body.get("confidence", 0.8)),
            target_position_pct=body.get("target_position_pct"),
            stop_loss=body.get("stop_loss"),
            take_profit=body.get("take_profit"),
            rationale=body.get("rationale"),
        )
        payload = build_data_payload(DEFAULT_WATCHLIST)
        verdict = risk_gate_evaluate(intent, payload)
        result = execution_place(intent, verdict, payload)
        return {
            "status": "ok",
            "verdict": verdict.to_dict(),
            "order": result.to_dict(),
        }
    except KeyError as e:
        raise HTTPException(status_code=400, detail=f"Missing field: {e}")
    except Exception as e:
        raise _server_error(e)


@app.post("/api/trading/consensus-order", dependencies=[Depends(require_api_key)])
async def trading_consensus_order(request: Request):
    """
    Run full consensus for a symbol, build TradeIntent, push through risk gate, paper-execute.
    Body: {symbol: str}
    Full signal→gate→order pipeline in one call.
    """
    try:
        body = await request.json()
        symbol = body["symbol"].upper()
        payload = build_data_payload(DEFAULT_WATCHLIST)

        # Run consensus for symbol
        from consensus import run_consensus_holding
        consensus_row = run_consensus_holding(symbol, payload)

        # Build intent from consensus
        from trading.config import load_risk_config
        cfg = load_risk_config()
        intent = from_consensus(consensus_row, max_position_pct=cfg.max_position_pct)

        # Gate + execute
        verdict = risk_gate_evaluate(intent, payload)
        result = execution_place(intent, verdict, payload)

        return _sanitize({
            "status":    "ok",
            "symbol":    symbol,
            "consensus": consensus_row,
            "intent":    intent.to_dict(),
            "verdict":   verdict.to_dict(),
            "order":     result.to_dict(),
        })
    except KeyError as e:
        raise HTTPException(status_code=400, detail=f"Missing field: {e}")
    except Exception as e:
        raise _server_error(e)


@app.post("/api/trading/deep-debate/{symbol}", dependencies=[Depends(require_api_key)])
async def trading_deep_debate(symbol: str):
    """
    Bull / Bear debate engine: haiku x2 (bull + bear) → sonnet x1 (PM synthesis).
    Returns structured decision with price target, conviction, and catalysts.
    """
    try:
        symbol = _safe_symbol(symbol)
        payload = build_data_payload(DEFAULT_WATCHLIST)

        eq = cost = price = 0.0
        total_eq = float((payload.get("equity_summary") or {}).get("portfolio_equity") or 1.0)
        for h in payload.get("holdings", []):
            if h.get("symbol") == symbol:
                eq    = float(h.get("equity", 0) or 0)
                cost  = float(h.get("average_buy_price", 0) or 0)
                price = float(h.get("current_price", 0) or 0)
                break

        from consensus import run_consensus_holding
        consensus = run_consensus_holding(symbol, eq, total_eq, cost, price)

        from fundamentals_openbb import get_fundamentals_openbb
        fundamentals = get_fundamentals_openbb(symbol) or {}

        from bull_bear_debate import run_debate
        result = run_debate(symbol, consensus, fundamentals)

        return _sanitize({
            "status":            "ok",
            "symbol":            symbol,
            "debate":            result,
            "consensus_verdict": consensus.get("verdict"),
            "consensus_score":   consensus.get("raw_score"),
        })
    except Exception as e:
        raise _server_error(e)


# ─── Phase 1-3 Enhancement Endpoints ─────────────────────────────────────────

@app.get("/api/tax/harvest")
async def api_tax_harvest():
    """Tax-loss harvesting candidates + wash-sale window status."""
    try:
        from tax_harvester import get_harvest_summary
        payload = build_data_payload()
        positions = {h["symbol"]: h for h in payload.get("holdings", [])}
        cost_basis = {h["symbol"]: h.get("average_buy_price") for h in payload.get("holdings", [])}
        result = get_harvest_summary(positions, cost_basis)
        return _sanitize({"status": "ok", **result})
    except Exception as e:
        raise _server_error(e)


@app.get("/api/benchmarks/{period}")
async def api_benchmarks(period: str = "1mo"):
    """Thematic alpha benchmarking vs sector ETFs."""
    try:
        from benchmarks import batch_alpha
        payload = build_data_payload()
        tickers = [h["symbol"] for h in payload.get("holdings", [])]
        result  = batch_alpha(tickers, period)
        return _sanitize({"status": "ok", "period": period, "alpha_table": result})
    except Exception as e:
        raise _server_error(e)


@app.get("/api/explainability/{symbol}")
async def api_explainability(symbol: str):
    """Plain-English explanation of consensus verdict for a symbol."""
    try:
        from explainability import build_transparency_report
        from consensus import run_consensus_holding
        consensus  = run_consensus_holding(symbol.upper())
        regime_str = consensus.get("regime", "NEUTRAL")
        report = build_transparency_report(symbol.upper(), consensus, regime=regime_str)
        return _sanitize({"status": "ok", "symbol": symbol.upper(), **report})
    except Exception as e:
        raise _server_error(e)


@app.get("/api/crypto-correlation")
async def api_crypto_correlation():
    """Crypto-equity correlation risk summary for portfolio."""
    try:
        from crypto_correlation import crypto_risk_summary
        payload  = build_data_payload()
        holdings = payload.get("holdings", [])
        result   = crypto_risk_summary(holdings)
        return _sanitize({"status": "ok", **result})
    except Exception as e:
        raise _server_error(e)


@app.get("/api/yield/landscape")
async def api_yield_landscape():
    """DeFi & cash yield landscape for idle cash (informational)."""
    try:
        from defi_yield import get_yield_landscape
        payload  = build_data_payload()
        cash_usd = float(payload.get("equity_summary", {}).get("cash", 0) or 0)
        result   = get_yield_landscape(cash_usd)
        return _sanitize({"status": "ok", **result})
    except Exception as e:
        raise _server_error(e)


@app.get("/api/security/health")
async def api_security_health():
    """API health report — latency, rate-limit hits, last errors."""
    try:
        from security.api_verifier import all_api_health
        return _sanitize({"status": "ok", "sources": all_api_health()})
    except Exception as e:
        raise _server_error(e)


@app.get("/api/volatility/compare/{symbol}")
async def api_vol_compare(symbol: str):
    """Compare EWMA vs GARCH(1,1) volatility for a symbol."""
    try:
        import yfinance as yf
        import numpy as np
        from volatility_models import compare_vol_models
        data   = yf.download(symbol.upper(), period="1y", auto_adjust=True, progress=False)["Close"]
        if isinstance(data, __import__("pandas").DataFrame):
            data = data.iloc[:, 0]
        rets   = np.log(data / data.shift(1)).dropna()
        result = compare_vol_models(rets, symbol.upper())
        return _sanitize({"status": "ok", **result})
    except Exception as e:
        raise _server_error(e)


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 9000))
    # host=127.0.0.1 is intentional — never bind 0.0.0.0; the API has no network-level auth.
    uvicorn.run("server:app", host="127.0.0.1", port=port, reload=False)
