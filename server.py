from __future__ import annotations

import asyncio
import hmac
import logging
import math
import os
import re
import threading
import time
import uuid
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

import price_source  # locked yf.download adapter (thread-safety)
from run_manifest import atomic_write_json

# ─── FD-leak safety net: raise per-process file descriptor limit ──────────────
# Long-running uvicorn + yfinance + sqlite + robin_stocks tends to leak FDs
# (HTTP keep-alive sockets, yfinance crumb/tz cache opens, sqlite handles).
# macOS default soft limit is 256/1024 — bump to 10240 so a slow leak takes
# days, not hours, to bite. Also enforced via launchd plist as defense-in-depth.
try:
    import resource

    _soft, _hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    _target = 10240
    resource.setrlimit(resource.RLIMIT_NOFILE, (max(_soft, _target), max(_hard, _target)))
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
        raise HTTPException(
            status_code=503, detail="Endpoint disabled: GESH_API_KEY not configured"
        )
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
        raise HTTPException(
            status_code=503, detail="Endpoint disabled: GESH_API_KEY not configured"
        )
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return True


logger = logging.getLogger("gesh.server")

# ─── repeating-log throttle ───────────────────────────────────────────────────
# A retry that can never succeed (the reauth-dead Robinhood session, retried on
# every /api/portfolio poll) logged one WARNING per attempt: 26,445 identical
# lines, ~6% of a 21MB /tmp/gesh_main.log, and off-hours it cannot possibly
# recover. Nothing was broken — but that volume is exactly what buries a REAL
# error, so the fix is volume, not severity. First occurrence still logs
# immediately (never delay a new signal); repeats collapse into a periodic line
# that reports how many were swallowed, so an outage is never understated.
_THROTTLED_LOG_INTERVAL_S = 300.0
_throttled_log_last: dict[str, tuple[float, int]] = {}
_throttled_log_lock = threading.Lock()


def _throttled_log_due(key: str, interval: float = _THROTTLED_LOG_INTERVAL_S) -> int | None:
    """Rate-limit a repeating log line, keyed by call site.

    Returns the number of repeats SUPPRESSED since the last emission when the
    line is due (0 on the first ever call), or None while still throttled.
    Callers must append the count so a quiet log never implies a quiet system.
    """
    now = time.monotonic()
    with _throttled_log_lock:
        last, suppressed = _throttled_log_last.get(key, (None, 0))
        if last is not None and (now - last) < interval:
            _throttled_log_last[key] = (last, suppressed + 1)
            return None
        _throttled_log_last[key] = (now, 0)
        return suppressed


def _signups_paused() -> bool:
    """Is the paid checkout closed to NEW subscribers? (Legal Hub L5.)

    Read live from the env on every request so the flag can be flipped with a
    restart and no code change. Defaults to OFF: pausing revenue is the owner's
    call, not a default this code should impose — it only provides the switch.
    """
    return os.getenv("GESH_SIGNUPS_PAUSED", "").strip().lower() in ("1", "true", "yes", "on")


def _reset_throttled_logs() -> None:
    """Test seam — throttle state is process-global and would leak across tests."""
    with _throttled_log_lock:
        _throttled_log_last.clear()


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
        return HTTPException(status_code=503, detail="Broker data temporarily unavailable")

    # Typed LLM outcomes — fixed safe copy only; never echo exception args.
    try:
        import llm_contracts
        import llm_gateway

        if isinstance(exc, (llm_gateway.LLMRefusalError, llm_contracts.LLMContractError)):
            logger.warning("LLM contract/refusal: kind=%s", type(exc).__name__)
            return HTTPException(
                status_code=422,
                detail="AI research could not be completed for this request",
            )
        if isinstance(exc, llm_gateway.LLMTruncatedError):
            logger.warning("LLM truncated: kind=%s", type(exc).__name__)
            return HTTPException(
                status_code=503,
                detail="AI response exceeded its configured output limit",
            )
        if isinstance(
            exc,
            (llm_gateway.LLMUnavailableError, llm_gateway.LLMBudgetExceededError),
        ):
            logger.warning("LLM unavailable: kind=%s", type(exc).__name__)
            return HTTPException(
                status_code=503,
                detail="AI provider temporarily unavailable",
            )
        if isinstance(exc, llm_gateway.LLMConfigurationError):
            logger.warning("LLM configuration: kind=%s", type(exc).__name__)
            return HTTPException(
                status_code=503,
                detail="AI provider temporarily unavailable",
            )
    except ImportError:
        pass

    upstream = _upstream_unavailable_error(exc)
    if upstream is not None:
        logger.warning("Upstream unavailable: kind=%s", type(exc).__name__)
        return upstream
    logger.exception("Unhandled server error: kind=%s", type(exc).__name__)
    return HTTPException(status_code=500, detail="Internal server error")


def _upstream_unavailable_error(exc: Exception) -> HTTPException | None:
    """Map known network / LLM-provider outages to a user-actionable 503."""
    try:
        import anthropic

        if isinstance(exc, (anthropic.APIConnectionError, anthropic.APITimeoutError)):
            return HTTPException(
                status_code=503,
                detail="Claude API unreachable — check network connection and retry",
            )
        if isinstance(exc, anthropic.RateLimitError):
            return HTTPException(
                status_code=503,
                detail="Claude API rate limit — wait a minute and retry",
            )
    except ImportError:
        pass
    if isinstance(exc, EnvironmentError) and "ANTHROPIC" in str(exc).upper():
        return HTTPException(status_code=503, detail="AI service temporarily unavailable")
    cause = exc.__cause__ or exc.__context__
    if cause is not None and cause is not exc:
        nested = _upstream_unavailable_error(cause)
        if nested is not None:
            return nested
    err_name = type(exc).__name__
    if err_name in ("ConnectError", "ConnectionError", "TimeoutError", "gaierror"):
        return HTTPException(
            status_code=503,
            detail="Network unreachable — check connection and retry",
        )
    return None


_ALLOWED_INTERVALS = {
    "1m",
    "2m",
    "5m",
    "15m",
    "30m",
    "60m",
    "90m",
    "1h",
    "1d",
    "5d",
    "1wk",
    "1mo",
    "3mo",
}
_ALLOWED_PERIODS = {
    "1d",
    "5d",
    "1mo",
    "3mo",
    "6mo",
    "1y",
    "2y",
    "5y",
    "10y",
    "ytd",
    "max",
    "10d",
    "30d",
    "60d",
}


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


# Equal-weight (privacy) book: when a customer imports symbols only, every name is
# valued at an equal slice of this notional so weights are 1/N and no real size is
# needed or faked into the weighting. The absolute figure is illustrative only.
_EQUAL_WEIGHT_NOTIONAL = 100_000.0

_BULK_HEADER_WORDS = {
    "SYMBOL",
    "TICKER",
    "SHARES",
    "QTY",
    "QUANTITY",
    "COST",
    "PRICE",
    "AVGCOST",
    "TOTAL",
}


def _parse_pos_float(tok: str | None) -> float | None:
    """A positive float from a token, tolerating $ and thousands commas; else None."""
    try:
        v = float(str(tok).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _parse_bulk_book(text: str) -> tuple[list[dict], list[str]]:
    """Parse a pasted block of positions — one per line — for bulk book import.

    Accepts ``SYMBOL`` (privacy path — no size), ``SYMBOL shares``, or
    ``SYMBOL shares avg_cost`` with comma / whitespace / tab delimiters. A leading
    header row and unparseable-symbol lines are collected in ``invalid`` (never
    raises). Symbols are uppercased and de-duplicated (first occurrence wins).
    Returns ``(items, invalid)`` where each item is
    ``{"symbol", "shares"|None, "avg_cost"|None}``."""
    items: list[dict] = []
    invalid: list[str] = []
    seen: set[str] = set()
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = [t for t in re.split(r"[\s,]+", line) if t]
        if not parts:
            continue
        sym = parts[0].strip().upper()
        if sym in _BULK_HEADER_WORDS or not _SYMBOL_RE.match(sym):
            invalid.append(line[:40])
            continue
        if sym in seen:
            continue
        seen.add(sym)
        items.append(
            {
                "symbol": sym,
                "shares": _parse_pos_float(parts[1]) if len(parts) > 1 else None,
                "avg_cost": _parse_pos_float(parts[2]) if len(parts) > 2 else None,
            }
        )
    return items, invalid


# ── Lightweight per-IP rate limiting (dependency-free, single-process) ────────
_RATE_LOCK = threading.Lock()
_RATE_HITS: dict[tuple, deque] = defaultdict(deque)
_HEAVY_PREFIXES = (
    "/api/deep-analysis",
    "/api/analyze-ticker",
    "/api/screener",
    "/api/consensus",
    "/api/monte-carlo",
    "/api/agents/",
    "/api/gemini/",
    "/api/trading/deep-debate",
    # Per-ticker analyze GETs: each cache miss is 1-2 live upstream fetches
    # (yfinance+FRED / Robinhood / RSS), so an authed customer cycling symbols at
    # the normal 300/min cap could hammer the providers. Trailing slash on purpose:
    # the exact "/api/regime" (dashboard-polled, chain-cached) stays in the normal
    # bucket — only the /{symbol} variants are heavy.
    "/api/regime/",
    "/api/fundamentals/",
    "/api/analyst/",
    "/api/news/",
    "/api/momentum/",
    "/api/levels/",
    "/api/earnings/",
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


# ─── Login brute-force throttle (GPT-5.6 audit item 8) ───────────────────────
# Counts only FAILED /auth/login attempts, per IP and per (IP, email). A correct
# login clears both buckets so a legitimate owner is never locked out by earlier
# fat-fingered attempts. We key on the IP too (not email alone) so an attacker
# can't lock a victim out of their own account by spraying failures at it.
LOGIN_FAIL_LIMIT = 8  # blocked after this many failures in the window
LOGIN_FAIL_WINDOW = 300  # 5-minute sliding window
_LOGIN_FAILS: dict[str, deque] = defaultdict(deque)
_LOGIN_LOCK = threading.Lock()


def _login_keys(ip: str, email: str) -> tuple[str, str]:
    return (f"ip:{ip}", f"ip:{ip}|em:{(email or '').strip().lower()}")


def _login_blocked(ip: str, email: str) -> bool:
    now = time.time()
    with _LOGIN_LOCK:
        for key in _login_keys(ip, email):
            d = _LOGIN_FAILS[key]
            while d and d[0] <= now - LOGIN_FAIL_WINDOW:
                d.popleft()
            if len(d) >= LOGIN_FAIL_LIMIT:
                return True
        return False


def _login_record_fail(ip: str, email: str) -> None:
    now = time.time()
    with _LOGIN_LOCK:
        for key in _login_keys(ip, email):
            _LOGIN_FAILS[key].append(now)


def _login_clear(ip: str, email: str) -> None:
    with _LOGIN_LOCK:
        for key in _login_keys(ip, email):
            _LOGIN_FAILS.pop(key, None)


from urllib.parse import urlparse as _urlparse  # CSRF same-origin check

from starlette.exceptions import HTTPException as StarletteHTTPException

import auth_users  # multi-user login wall (OFF unless GESH_AUTH_ENABLED is set)
import billing  # Lemon Squeezy payment webhook → provision/revoke auth_users
import decision_quality  # unified degraded-decision block (Codex #6)
import email_sender  # transactional email (magic-link recovery via /auth/request-link)
import fair_value  # canonical fair-value % (Codex #2)
import model_portfolio  # the ONLY portfolio shape a customer receives
import portfolio_valuation  # CANONICAL money arithmetic — see module docstring
import price_cache  # shared close-price cache (also used by cron digest)
import price_sanity  # cross-source price quality + strict mode (Codex #3)
import verdict_reconciliation  # ONE stance from the mechanical + valuation layers
from ai_analyzer import (
    MODEL,
    analyze_portfolio,
    analyze_screener,
    deep_analyze_portfolio,
    deep_analyze_ticker,
)
from allocation_config import (
    load_config,
    update_config,
)
from auth import login, logout
from consensus import run_consensus_portfolio
from consolidation_zone import detect_consolidation, scan_consolidations
from data_fetcher import (
    DEFAULT_WATCHLIST,
    RobinhoodOfflineError,
    build_data_payload,
    load_snapshot_payload,
)
from db import (
    get_analysis_by_id,
    get_analysis_history,
    get_equity_history,
    init_db,
    log_analysis,
    log_equity_snapshot,
)
from ecg import compute_ecg
from fft_cycles import batch_fft_cycles
from fundamental_agent import get_latest_fundamental_scan, run_fundamental_scan

# Ollama removed — 3B local model provides no signal value vs Sonnet. Use Gemini for second opinions.
from gemini_agent import (
    gemini_market_commentary,
    gemini_quick_analysis,
    gemini_second_opinion,
    get_gemini_status,
)
from kelly import run_kelly_analysis
from market_data import get_all_market_data, get_analyst_targets, screen_market
from monte_carlo import run_monte_carlo
from news_agent import get_news_history, scan_news
from paper_trading import check_and_close_paper_positions, get_paper_summary, run_paper_session
from regime import detect_regimes
from sec_agent import get_filing_history, watch_filings
from sector_filter import batch_sector_check, is_sector_hot
from trade_log import (
    close_trade,
    get_trade_history,
    log_trade,
    trade_summary_for_report,
)
from trading.contract import from_consensus
from trading.contract import manual as manual_intent
from trading.execution import place as execution_place
from trading.risk_gate import evaluate as risk_gate_evaluate
from trading.state import init_trading_tables

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
    # /congress-watch is the only public HTML page that triggers upstream fetches —
    # it must be throttled too, not just /api/*, or anonymous cold-cache traffic can
    # exhaust workers and the upstream quota.
    path = request.url.path
    if path.startswith("/api/") or path == "/congress-watch":
        ip = request.client.host if request.client else "unknown"
        heavy = path.startswith(_HEAVY_PREFIXES)
        if not _rate_ok(ip, heavy):
            return JSONResponse(
                status_code=429, content={"detail": "Rate limit exceeded — slow down"}
            )
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
            return JSONResponse(
                status_code=401, content={"detail": "Authentication required — sign in"}
            )
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
_OWNER_ONLY_PATHS = ("/report", "/invest", "/progress", "/watch", "/usage")
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
    # PUBLIC market news — RSS/Alpaca HEADLINES only (title/url/source/published).
    # The mask redacts by SEMANTIC PATH, and "title" was never enrolled as a KEEP
    # key, so public analyst price targets ("...raises PT to $290") were redacted to
    # "$•••" — pure loss with nothing protected, since the customer reads the same PT
    # on Yahoo (GESH-7). The sentiment SCORE stays owner-only and is never in this body.
    "/api/news",
    # The caller's OWN account data (GDPR/CCPA access + portability, Privacy §8).
    # `export_user_data` keys strictly on the caller's user_id and holds zero owner
    # data. Masking it was a REAL DEFECT, not a missing nicety: `shares`/`quantity`
    # are in _QTY_KEYS, so the export multiplied the customer's own share counts by
    # the owner's model-book factor (audit 2026-07-22: submit 1 → export 2.7971,
    # = 100000/35750.87). `avg_cost` is not a masked key, so cost basis stayed raw
    # while quantity moved — an export that no longer reconciled with itself.
    # A portability artifact must be a byte-faithful copy of what we store.
    "/api/account",
)
_MONEY_KEYS = {
    "equity",
    "cash",
    "deployable",
    "settled_cash",
    "buying_power",
    "buying_power_leveraged",
    "margin_buying_power",
    "withdrawable_amount",
    "pending_deposits",
    "uncleared_deposits",
    "total",
    "account_equity",
    "portfolio_equity",
    "adjusted_equity_previous_close",
    "equity_value",
    "market_value",
    "recommended_usd",
    "delta_usd",
    "cost_basis",
    "total_equity",
    "day_change",
    "total_value",
    # Monte-Carlo / distribution outcomes (Codex P0: these leaked raw dollars)
    "mean",
    "median",
    "p5",
    "p25",
    "p75",
    "p95",
    "var_95",
    "cvar_95",
    "current_equity",
    "center",
    # 8th-pass (Fable audit): owner-$ keys the recursion previously passed through
    # RAW — same leak class as the ECG array / sparkline `series`. `equity_change`
    # = per-position unrealized $ P&L (data_fetcher), `extended_hours_equity` =
    # account equity (data_fetcher), `realized_pnl`/`total_realized_pnl` = trade $
    # P&L (trade_log, incl. nested best/worst_trade rows — scaled recursively).
    "equity_change",
    "extended_hours_equity",
    "realized_pnl",
    "total_realized_pnl",
    # Reconciliation block (_reconcile_equity_summary): the broker's own account
    # total and its delta against positions+cash. Both are OWNER DOLLARS — the
    # leak sweep caught them passing through raw the moment they were added.
    "reported_total",
    "reported_total_delta",
    # Canonical valuation block (portfolio_valuation.as_dict) — every one of these
    # is a dollar amount on whichever book the payload describes.
    "positions_market_value",
    "prior_close_positions_value",
    "prior_close_equity",
    "day_change_amount",
    "total_cost_basis",
    "unrealized_pnl",
    "prior_close",
    # Published model portfolio (model_portfolio.publish): owner-denominated on
    # the way out of the handler, scaled to the model notional here — exactly once.
    "calculated_total_equity",
}
_QTY_KEYS = {
    "quantity",
    "qty",
    "shares",
    "fill_qty",
    "delta_shares",
    "shares_to_buy",
    "shares_to_trim",
}

# PUBLIC market-data subtrees kept VERBATIM (never scaled). The key `shares`
# scales owner sizing (fetch_live_data / paper_trading holdings), but the same key
# under `insider_trades` is a PUBLIC transaction count — scaling it by the
# model-book factor shows the customer a corrupt number (8th-pass, Fable audit).
# Semantic-path rule: decide by subtree, not by the bare key.
_PUBLIC_RAW_KEYS = {"insider_trades"}

# Bare-number arrays that carry OWNER DOLLARS (no dict key on the elements, so
# the recursive scaler would return them unchanged and leak — Codex P0 on
# /api/ecg's equity_history). Listed keys have their numeric elements scaled.
_MONEY_LIST_KEYS = {
    "equity_history",
    "equity_series",
    "equity_curve",
    "series",
}  # /api/portfolio/history KPI-sparkline equity (owner $)

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
_KEEP_STRING_KEYS = {
    "3m",
    "12m",
    "prediction",
    "fair_value",
    "estimate",
    "target",
    "current",
    "price",
    "forecast",
    "analyst_target",
}


def _scale_label(text: str, factor: float) -> str:
    return _NUM_RE.sub(lambda m: f"{float(m.group(0).replace(',', '')) * factor:,.0f}", text)


def _redact_prose(text: str) -> str:
    text = _MONEY_RE.sub("$•••", text)  # owner $-amounts / ranges
    text = _SHARES_RE.sub("a position", text)  # share counts / ranges
    return text


def _customer_scale_factor() -> float:
    """Fallback model-book factor, from the on-disk snapshot.

    Used for owner-derived customer routes that are NOT the published portfolio
    response (which carries its own exact basis — see the middleware). The
    denominator is the canonical basis (Σ itemised holdings + cash), never a
    summary field: the raw snapshot FILE stores positions in `portfolio_equity`
    while the normalized payload stores ACCOUNT equity there, so any code that
    reads that key without knowing which side of normalization it is on will get
    it wrong half the time. Reading the holdings avoids the question entirely.

    Fails CLOSED: an undeterminable basis returns 0.0 so dollars zero out."""
    try:
        snap = _json.loads((Path(__file__).parent / "live_portfolio_snapshot.json").read_text())
        return _model_scale_factor(model_basis_from_payload(snap))
    except Exception:
        return 0.0


# Raw snapshot writers (build_snapshot_from_mcp.py, fetch_live_snapshot.py) emit
# `price`; only data_fetcher.load_snapshot_payload() renames it to
# `current_price`. `_customer_scale_factor()` reads the FILE directly and so
# never sees that rename — which silently classified every raw holding as
# unpriced and collapsed the scaling basis to cash alone (on the real snapshot:
# a factor of 11.17 instead of 2.95, inflating every fallback-route dollar ~3.8x).
#
# Normalizing here, at the raw boundary, is deliberate. `canonical_holdings()`
# is RIGHT to require the normalized `current_price`: a global `price` fallback
# inside it would also hide a genuine normalization regression on the live path,
# where `current_price` is the contract.
_RAW_PRICE_KEYS = ("current_price", "price")


def _basis_rows(holdings: list) -> list[dict] | None:
    """Minimal normalized rows for a basis calculation, or None if unusable.

    Accepts both the raw snapshot shape (`price`) and the normalized payload
    shape (`current_price`). Source `equity` is carried through only as evidence
    for canonical_holdings' mismatch check — never as the calculation."""
    rows: list[dict] = []
    for h in holdings or []:
        if not isinstance(h, dict):
            return None
        price = next((h[k] for k in _RAW_PRICE_KEYS if h.get(k) is not None), None)
        qty = h.get("quantity")
        # Holdings are PRESENT but unusable -> refuse. Falling through to a
        # cash-only basis would publish a fabricated stock-portfolio factor.
        if portfolio_valuation.to_decimal(qty) is None:
            return None
        price_d = portfolio_valuation.to_decimal(price)
        if price_d is None or price_d <= 0:
            return None
        rows.append(
            {
                "symbol": h.get("symbol"),
                "quantity": qty,
                "current_price": price,
                "equity": h.get("equity"),
            }
        )
    return rows


def model_basis_from_payload(payload: dict) -> float | None:
    """Canonical model-book basis: Σ canonical holding equity + cash.

    Returns None when the basis cannot be determined, so an unavailable basis is
    never confused with a genuine cash-only book of 0.0 — `_model_scale_factor`
    turns None into a fail-closed 0.0.

    Replaces `_scale_factor_from_summary`, whose bare-`equity_summary` signature
    is what allowed the account-vs-positions error to recur: summary fields alone
    cannot say which side of normalization they came from. The basis is read from
    the ITEMISED holdings — the only unambiguous source of positions — plus cash.
    """
    if not isinstance(payload, dict):
        return None
    eq = payload.get("equity_summary") or {}
    cash = portfolio_valuation.to_decimal(eq.get("cash"))
    if cash is None:
        return None  # non-finite / missing / non-numeric cash
    rows = _basis_rows(payload.get("holdings") or [])
    if rows is None:
        return None
    _published, report = model_portfolio.canonical_holdings(
        rows, model_portfolio.PUBLISHED_HOLDING_FIELDS
    )
    if report.get("non_finite") or report.get("unpriced"):
        return None
    return float(report["positions_value"]) + float(cash)


def _model_scale_factor(basis: float | None) -> float:
    """model notional / basis. Fails CLOSED (0.0) on an unusable or absent basis,
    so dollars zero out rather than leak. `None` means "could not be determined"
    — kept distinct from a real 0.0 so the two are never conflated."""
    try:
        return _MODEL_BOOK_USD / basis if basis and basis > 0 else 0.0
    except (TypeError, ZeroDivisionError):
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
                out[k] = v  # public market data — never scale
            elif (
                isinstance(v, (int, float))
                and not isinstance(v, bool)
                and (lk in _MONEY_KEYS or lk.endswith("_usd") or lk.endswith("_dollars"))
            ):
                out[k] = round(v * factor, 2)
            elif isinstance(v, (int, float)) and not isinstance(v, bool) and lk in _QTY_KEYS:
                out[k] = round(v * factor, 4)
            elif isinstance(v, list) and lk in _MONEY_LIST_KEYS:
                out[k] = [
                    round(x * factor, 2)
                    if isinstance(x, (int, float)) and not isinstance(x, bool)
                    else _scale_money(x, factor, lk)
                    for x in v
                ]
            elif isinstance(v, str) and lk == "label" and parent_key == "buckets":
                out[k] = _scale_label(v, factor)  # MC histogram label = portfolio $
            elif isinstance(v, str) and lk in _KEEP_STRING_KEYS:
                out[k] = v  # per-share price/forecast — keep raw
            elif isinstance(v, str) and ("$" in v or "share" in v.lower()):
                out[k] = _redact_prose(v)  # owner sizing prose — redact
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


# ─── Unavailable scaling basis: refuse, never publish a fabricated $0.00 ───────
# `_customer_scale_factor()` fails closed to 0.0, which is fail-closed against a
# LEAK but fail-OPEN against a FABRICATION: the factor is a multiplier, so 0.0
# does not withhold a number, it asserts one. A customer saw `"cash": 0.0` in an
# HTTP 200 and could not tell "your model book is empty" from "we could not
# determine the basis" — and $0.00 is the more alarming reading of the two.
#
# The distinction that matters is NOT "does this body contain money" but "does
# this body's output DEPEND ON the factor". `_scale_money` does two independent
# jobs: it SCALES numbers (needs a factor) and it REDACTS owner sizing prose
# (does not). A response of per-share prices and percentages is fully correct
# with no factor at all, so withholding it would be a self-inflicted outage;
# conversely a body of pure prose must still be masked even when no factor
# exists, or a correctness fix becomes a disclosure bug.


def _requires_scaling_factor(obj) -> bool:
    """True when any part of this response would change if the factor changed.

    Defined by DIFFERENTIAL EVALUATION of `_scale_money` itself rather than by a
    second list of money keys. A parallel list would silently rot the first time
    someone added a key to `_MONEY_KEYS` and not here; this cannot, because it
    asks the scaler what it actually does. An exact 0 is correctly reported as
    factor-independent — 0 x anything is 0, so serving it fabricates nothing."""
    return _scale_money(obj, 1.0) != _scale_money(obj, 2.0)


def _usable_factor(factor: object) -> bool:
    """A factor must be finite and strictly positive. Zero erases the book and a
    negative inverts it; neither can be presented as a customer's holdings."""
    return (
        isinstance(factor, (int, float))
        and not isinstance(factor, bool)
        and math.isfinite(factor)
        and factor > 0
    )


def _model_unavailable_response() -> JSONResponse:
    """Typed, retryable, uncacheable refusal carrying NO financial value.

    `detail` is deliberately generic: the cause (missing snapshot, unpriced
    holding, malformed source) is operator diagnostics and belongs in the log,
    not in a customer response."""
    return JSONResponse(
        status_code=503,
        headers={"Cache-Control": "no-store"},
        content={
            "status": "unavailable",
            "reason_code": "model_scaling_unavailable",
            "detail": "Model portfolio data is temporarily unavailable.",
            "model_scaling_available": False,
            "data_as_of": None,
            "retryable": True,
            "view": "model_book",
        },
    )


# ─── Human-readable refusals for browser navigations ──────────────────────────
# A customer who typed /invest saw the raw body `{"detail":"Owner-only view"}`,
# and an unknown path rendered `{"detail":"Not Found","view":"model_book"}` —
# framework internals presented as the product (audit BUG-08). API clients still
# get JSON; only requests that ASK for HTML get a page.


def _wants_html(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return "text/html" in accept and not request.url.path.startswith("/api/")


_ERROR_PAGE = """<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} · GESH Financial</title>
<style>
 body{{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
   background:#0b0f14;color:#e6edf3;font-family:ui-sans-serif,-apple-system,Segoe UI,Roboto,sans-serif}}
 main{{max-width:32rem;padding:2rem;text-align:center}}
 h1{{font-size:1.35rem;margin:0 0 .6rem}}
 p{{color:#8fa3bf;line-height:1.55;margin:0 0 1.4rem}}
 a{{display:inline-block;padding:.6rem 1.1rem;border:1px solid #1f6feb;border-radius:6px;
   color:#58a6ff;text-decoration:none}}
</style>
<main><h1>{title}</h1><p>{body}</p><a href="/">Back to your dashboard</a></main>
"""


def _refusal_response(request: Request, status: int, title: str, body: str = ""):
    """One refusal, rendered for whoever asked: HTML for a browser, JSON for an API."""
    if _wants_html(request):
        import html as _h

        return HTMLResponse(
            status_code=status,
            content=_ERROR_PAGE.format(title=_h.escape(title), body=_h.escape(body or title)),
        )
    return JSONResponse(status_code=status, content={"detail": title})


@app.exception_handler(StarletteHTTPException)
async def _http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Give browser navigations a page instead of a JSON fragment."""
    if _wants_html(request):
        titles = {
            404: "Page not found",
            403: "Not available on your account",
            401: "Please sign in",
            405: "Action not allowed",
        }
        title = titles.get(exc.status_code, "Something went wrong")
        detail = exc.detail if isinstance(exc.detail, str) else ""
        # 5xx detail can carry internal context; never render it.
        body = (
            detail
            if (exc.status_code < 500 and detail and detail != "Not Found")
            else "The page you asked for isn't here."
        )
        return _refusal_response(request, exc.status_code, title, body)
    # API clients keep the exact JSON contract they had before.
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=getattr(exc, "headers", None),
    )


# ─── CSRF / content-type hardening on cookie-authenticated mutations ──────────
# The session cookie is HttpOnly + SameSite=Lax, which already withholds it from
# cross-site POSTs — but the API also ACCEPTED `Content-Type: text/plain` on
# JSON endpoints (audit CSRF finding), and text/plain is precisely the
# content-type that makes a cross-origin POST a "simple request" with no
# preflight. That combination is one SameSite regression away from exploitable,
# so both halves are closed here:
#
#   * a mutation carrying a body must declare application/json;
#   * a mutation whose Origin/Referer names a DIFFERENT host is refused.
#
# Absent Origin is allowed on purpose: browsers always send it on cross-origin
# mutations, while legitimate non-browser clients (cron, the smoke script) send
# none — and those authenticate with X-API-Key, not with an ambient cookie.
_MUTATING_METHODS = frozenset(("POST", "PUT", "PATCH", "DELETE"))
# HMAC-authenticated third-party callbacks: cross-origin by definition, never
# cookie-authenticated, and their content-type is the sender's choice.
_CSRF_EXEMPT_PREFIXES = ("/api/billing/webhook",)


def _trusted_origins(request: Request) -> set[str]:
    """Canonical scheme://host[:port] origins this deployment answers for.

    The request's own Host header is NOT authoritative — it is attacker-supplied
    and, with an origin check keyed off it, "Origin: evil.test" + "Host:
    evil.test" would validate against itself. Configuration is authoritative:
    APP_BASE_URL (the deployment's public origin) plus any GESH_ALLOWED_ORIGINS
    entries. The Host header is admitted ONLY for loopback development, where
    there is no proxy to assert a public origin and no cross-site attacker to
    benefit."""
    out: set[str] = set()

    def _add(raw: str) -> None:
        raw = (raw or "").strip()
        if not raw:
            return
        if "//" not in raw:
            raw = "https://" + raw
        out.add(_canonical_origin(raw))

    _add(os.getenv("APP_BASE_URL", ""))
    for entry in os.getenv("GESH_ALLOWED_ORIGINS", "").split(","):
        _add(entry)
    host = (request.headers.get("host") or "").strip().lower()
    if host.split(":")[0] in ("localhost", "127.0.0.1", "[::1]", "::1"):
        _add(f"http://{host}")
        _add(f"https://{host}")
    if not out:
        # No deployment origin configured: fall back to the request's own origin
        # so a single-tenant local install is not bricked, and say so once.
        _add(f"{request.url.scheme}://{request.headers.get('host') or request.url.netloc}")
    out.discard("")
    return out


_DEFAULT_PORTS = {"http": "80", "https": "443"}


def _canonical_origin(raw: str) -> str:
    """scheme://host:port with the default port made explicit, lowercased.

    Comparing raw strings would treat https://x.test and https://x.test:443 as
    different origins; comparing bare netloc would treat http:// and https://
    as the same one. Both are wrong.

    PR105-SEC-01: EVERY parse AND property access happens inside the exception
    boundary. `urlparse()` itself rarely raises, but `u.port` raises ValueError on
    a non-numeric port ("https://x:notaport") or one out of range
    ("https://x:65536") — and that access used to sit OUTSIDE the try, so a
    malformed Origin header produced a pre-auth HTTP 500 with a traceback instead
    of a controlled refusal. Fail closed: anything unparseable is "" (invalid),
    which the caller treats as a refusal, never as a match.
    """
    try:
        u = _urlparse(raw.strip())
        scheme = (u.scheme or "").lower()
        # Only web origins can carry an ambient browser cookie; ftp://, file://,
        # data:// and friends are never a legitimate same-origin mutation source.
        if scheme not in ("http", "https"):
            return ""
        # userinfo in an Origin is malformed per the Origin grammar and is a
        # classic spoofing vector ("https://evil@trusted.test"). Refuse it rather
        # than silently discarding the credentials portion.
        if u.username is not None or u.password is not None:
            return ""
        host = (u.hostname or "").lower()
        port = str(u.port) if u.port else _DEFAULT_PORTS.get(scheme, "")
    except Exception:  # noqa: BLE001 — malformed host/port/syntax => invalid origin
        return ""
    if not host:
        return ""
    return f"{scheme}://{host}:{port}"


def _raw_header_values(request: Request, name: str) -> list[str]:
    """Every raw ASGI value for `name`, lowercased-key matched.

    `request.headers.get()` collapses duplicates to the first occurrence, which
    hides a conflicting second value — exactly the ambiguity a security decision
    must not resolve by luck (PR105-SEC-02b). Reading the raw scope is the only
    way to see that a singleton header arrived twice."""
    want = name.lower().encode()
    try:
        pairs = request.scope.get("headers") or []
    except Exception:  # noqa: BLE001 — a malformed scope must not 500 the guard
        return []
    return [v.decode("latin-1").strip().lower() for k, v in pairs if k.lower() == want]


def _is_api_key_authenticated(request: Request) -> bool:
    """True when the caller proves itself with the service key rather than an
    ambient cookie. CSRF is a cookie problem: a request that carries no cookie
    authority cannot be forged by a third-party page."""
    expected = os.getenv("GESH_API_KEY", "")
    supplied = request.headers.get("x-api-key") or ""
    return bool(expected and supplied and hmac.compare_digest(supplied, expected))


@app.middleware("http")
async def _csrf_guard_middleware(request: Request, call_next):
    """Origin enforcement for cookie-authenticated mutations.

    Policy by authentication mode, because the two have different threat models:

      COOKIE-authenticated  — Origin (or Referer only when Origin is genuinely
          absent) is REQUIRED and must canonically match a configured origin.
          Absent/null/malformed is a refusal. This is the CSRF-relevant case:
          the browser attaches the cookie whether or not the user meant to.
      API-KEY authenticated — Origin is optional. A cron job or CLI has no
          ambient authority for a hostile page to borrow.
      UNAUTHENTICATED       — passes through to the route's own auth, which
          refuses it. Blocking here would only turn 401s into 403s.

    Applies to POST/PUT/PATCH/DELETE regardless of whether a body is present:
    a bodyless DELETE is exactly as forgeable as a POST."""
    path = request.url.path
    if (
        request.method not in _MUTATING_METHODS
        or not path.startswith("/api/")
        or any(path.startswith(p) for p in _CSRF_EXEMPT_PREFIXES)
    ):
        return await call_next(request)

    # Method override would let a GET-shaped request become a DELETE and skip
    # the check above. The app does not support it; refuse rather than ignore.
    for hdr in ("x-http-method-override", "x-method-override", "x-http-method"):
        if request.headers.get(hdr):
            return JSONResponse(
                status_code=400, content={"detail": "Method override is not supported."}
            )

    # PR105-SEC-02b: a SINGLETON security header appearing twice is ambiguous —
    # `request.headers.get()` silently returns the first, so a proxy or raw client
    # could present one value to us and another downstream. Read the raw ASGI
    # headers and refuse the ambiguity rather than pick a winner.
    for singleton in ("origin", "content-type"):
        values = _raw_header_values(request, singleton)
        if len(values) > 1 and len(set(values)) > 1:
            return JSONResponse(
                status_code=400, content={"detail": f"Ambiguous duplicate {singleton} header."}
            )

    ctype = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    has_body = bool(
        request.headers.get("content-length", "").strip("0 ")
        or request.headers.get("transfer-encoding")
    )
    # PR105-SEC-02: a body-bearing mutation must DECLARE application/json. The old
    # `and ctype` short-circuit meant an ABSENT Content-Type skipped the check
    # entirely, so raw JSON bytes with no declared type bypassed the JSON-only
    # contract. Parameters after the media type (charset=UTF-8) are stripped above,
    # so only a genuinely different — or missing — media type is refused.
    if has_body and ctype != "application/json":
        return JSONResponse(
            status_code=415, content={"detail": "This endpoint accepts application/json only."}
        )

    if _is_api_key_authenticated(request):
        return await call_next(request)

    cookie_authenticated = bool(request.cookies.get(auth_users.COOKIE_NAME))
    if not cookie_authenticated:
        return await call_next(request)  # no ambient authority to forge

    raw = (request.headers.get("origin") or "").strip()
    source = "Origin"
    if not raw or raw.lower() == "null":
        if raw.lower() == "null":
            return _csrf_refusal(request, "opaque origin")
        raw = (request.headers.get("referer") or "").strip()
        source = "Referer"
    if not raw:
        return _csrf_refusal(request, "no Origin or Referer")
    candidate = _canonical_origin(raw)
    if not candidate or candidate not in _trusted_origins(request):
        return _csrf_refusal(request, f"{source} not trusted")
    return await call_next(request)


def _csrf_refusal(request: Request, why: str):
    logger.warning(
        "cross-origin mutation refused (%s): %s %s", why, request.method, request.url.path
    )
    return JSONResponse(status_code=403, content={"detail": "Cross-origin request refused."})


@app.middleware("http")
async def _customer_view_middleware(request: Request, call_next):
    if not auth_users.auth_enabled():
        return await call_next(request)
    email = auth_users.authenticate_session(request.cookies.get(auth_users.COOKIE_NAME))
    if not email or auth_users.is_owner(email):
        return await call_next(request)

    path = request.url.path
    if path == "/report" or any(path == p or path.startswith(p + "/") for p in _OWNER_ONLY_PATHS):
        return _refusal_response(
            request, 403, "Owner-only view", "This page is part of the operator console."
        )

    # Public single-ticker research cards carry no owner dollars — serve RAW so their
    # per-share price prose is not redacted into uselessness for the customer.
    if any(path == p or path.startswith(p + "/") for p in _MASK_EXEMPT_PREFIXES):
        return await call_next(request)

    resp = await call_next(request)
    if not path.startswith("/api/") or "application/json" not in resp.headers.get(
        "content-type", ""
    ):
        return resp
    body = b"".join([chunk async for chunk in resp.body_iterator])
    try:
        decoded = _json.loads(body)
        # A published model-portfolio response states its own component total, so
        # scale by THAT rather than by a separately-read snapshot: the two can
        # describe different moments, and only the response's own basis makes
        # "Σ scaled holdings + scaled cash == the model notional" exact.
        factor = None
        if isinstance(decoded, dict):
            data = decoded.get("data")
            if (
                isinstance(data, dict)
                and data.get("model_portfolio_id") == model_portfolio.MODEL_PORTFOLIO_ID
            ):
                factor = _model_scale_factor(data.get("calculated_total_equity"))
        if factor is None:
            factor = _customer_scale_factor()

        if not _usable_factor(factor):
            if _requires_scaling_factor(decoded):
                # Refuse rather than publish a book of fabricated zeros.
                #
                # THROTTLED. An unavailable basis is a standing condition, not an
                # event: it persists until the snapshot returns, while the open
                # dashboard re-polls every money route on a timer. Unthrottled,
                # one cause would emit a line per route per poll per tab — the
                # same runaway that once put 26k duplicate lines in one log.
                # Keyed by FAILURE CLASS rather than by path, because there is
                # one cause; the most recent path is carried in the message so an
                # operator still gets a concrete example to reproduce with.
                suppressed = _throttled_log_due("model_scaling_unavailable")
                if suppressed is not None:
                    logger.warning(
                        "model scaling unavailable — refusing customer financial "
                        "responses (most recent: %s %s)%s",
                        request.method,
                        path,
                        f" [+{suppressed} suppressed in the last {int(_THROTTLED_LOG_INTERVAL_S)}s]"
                        if suppressed
                        else "",
                    )
                # The refusal itself is NOT conditional on the log: throttling is a
                # logging concern and must never change what the customer receives.
                return _model_unavailable_response()
            # Nothing in this body depends on the factor, but prose redaction and
            # every other factor-independent protection still must run — so mask
            # with a NEUTRAL factor instead of short-circuiting to the raw body,
            # and keep the handler's own status code.
            factor = 1.0

        masked = _scale_money(decoded, factor)
        if isinstance(masked, dict):
            masked["view"] = "model_book"  # UI/debug tell — customer-scaled response
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


def _preferences_payload(email: str) -> dict:
    return {
        "alerts_opt_out": auth_users.get_alerts_opt_out(email),
        "digest_opt_out": auth_users.get_digest_opt_out(email),
    }


@app.get("/api/preferences")
def preferences_get(request: Request):
    """The 're-enable from the dashboard' path the unsubscribe pages promise.
    Returns only the caller's own two email-preference booleans — no owner or
    other-customer data (leak sweep: see EXCLUDED in
    tests/test_customer_leak_sweep.py)."""
    email = _session_email(request)
    if not email:
        raise HTTPException(status_code=401, detail="Authentication required")
    return {"status": "ok", "data": _preferences_payload(email)}


@app.post("/api/preferences")
async def preferences_set(request: Request):
    """Flip alerts_opt_out / digest_opt_out for the caller. Only keys present in
    the body are changed — an absent key leaves that flag untouched."""
    email = _session_email(request)
    if not email:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        body = await request.json()
    except Exception:
        body = {}
    if "alerts_opt_out" in body:
        auth_users.set_alerts_opt_out(email, bool(body["alerts_opt_out"]))
    if "digest_opt_out" in body:
        auth_users.set_digest_opt_out(email, bool(body["digest_opt_out"]))
    return {"status": "ok", "data": _preferences_payload(email)}


def _cookie_secure(request: Request | None = None) -> bool:
    """Whether to set the Secure flag on the session cookie.

    Resolution order:
      1. Explicit ``GESH_COOKIE_SECURE`` env override, when set to a non-empty value
         (``0/false/no/off`` → insecure; anything else → secure). Reserve this for a
         deliberate override; it forces the same answer for every request.
      2. Otherwise fail closed for every non-loopback host.  A plain-HTTP request to
         ``localhost``, ``127.0.0.1``, or ``::1`` may omit Secure; HTTPS (direct or via
         ``X-Forwarded-Proto: https``) still gets it. This secures real TLS users WITHOUT
         locking the owner out of ``http://localhost`` — the exact tradeoff that previously
         forced a blanket ``GESH_COOKIE_SECURE=0`` and shipped session cookies in clear to
         paying users (M2).
      3. No request context and no override → default secure (fail safe on a TLS deploy).

    A forwarded ``http`` value never overrides an HTTPS ASGI scheme, and forwarded headers
    never permit a non-loopback host to downgrade its cookie."""
    override = os.getenv("GESH_COOKIE_SECURE", "").strip()
    if override:
        return override.lower() not in ("0", "false", "no", "off")
    if request is not None:
        host = (request.url.hostname or "").rstrip(".").lower()
        if host not in ("localhost", "127.0.0.1", "::1"):
            return True
        proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
        if proto:
            return request.url.scheme == "https" or proto != "http"
        return request.url.scheme == "https"
    return True


def _secure_cookie_config_ready() -> bool:
    """Fail closed when an HTTPS customer URL explicitly disables Secure cookies."""
    base_url = os.getenv("APP_BASE_URL", "").strip().lower()
    override = os.getenv("GESH_COOKIE_SECURE", "").strip().lower()
    explicitly_insecure = override in ("0", "false", "no", "off")
    return not (base_url.startswith("https://") and explicitly_insecure)


def _require_secure_cookie_config() -> None:
    if not _secure_cookie_config_ready():
        logger.error("Authentication disabled: HTTPS APP_BASE_URL with insecure cookie override")
        raise HTTPException(status_code=503, detail="Authentication temporarily unavailable")


@app.get("/login", response_class=HTMLResponse)
def login_page():
    return _LOGIN_PAGE


@app.post("/auth/login")
async def auth_login(request: Request):
    """JSON login → sets an httpOnly session cookie. (JSON, not form, to avoid a
    python-multipart dependency on the running server.)

    Brute-force hardened (audit item 8): failed attempts are throttled per IP and
    per (IP, email); the 200k-round PBKDF2 verify runs in a worker thread so it
    never blocks the event loop."""
    ip = request.client.host if request.client else "unknown"
    body = await _read_json_capped(request)  # [CLAUDE V1] capped public body
    email = (body.get("email") or "").strip()
    password = body.get("password") or ""
    if _login_blocked(ip, email):
        # [CLAUDE Task3 2026-07-18] tell well-behaved clients when to come back
        return JSONResponse(
            status_code=429,
            content={"ok": False, "detail": "Too many login attempts — try again shortly"},
            headers={"Retry-After": str(LOGIN_FAIL_WINDOW)},
        )
    _require_secure_cookie_config()
    # PBKDF2 is CPU-bound (~200k rounds); off-load so concurrent requests keep flowing.
    ok = await asyncio.to_thread(auth_users.verify_credentials_constant_time, email, password)
    if not ok:
        _login_record_fail(ip, email)
        return JSONResponse(
            status_code=401, content={"ok": False, "detail": "Invalid email or password"}
        )
    _login_clear(ip, email)
    auth_users.touch_last_login(email)
    resp = JSONResponse(content={"ok": True})
    resp.set_cookie(
        auth_users.COOKIE_NAME,
        auth_users.issue_token(email),
        httponly=True,
        samesite="lax",
        max_age=auth_users.TOKEN_TTL_SECONDS,
        secure=_cookie_secure(request),
    )
    return resp


@app.post("/auth/logout")
def auth_logout():
    resp = JSONResponse(content={"ok": True})
    resp.delete_cookie(auth_users.COOKIE_NAME)
    return resp


@app.get("/auth/magic")
def auth_magic(request: Request, token: str = ""):
    """Passwordless login from the onboarding email's magic link.

    The URL token is SINGLE-USE: consume_magic_token validates + atomically spends it, so
    a leaked link (history/Referer/logs) can't be replayed. We then mint a SEPARATE fresh
    session token for the cookie — the short-TTL URL token is never reused as the session.
    Redirect drops the token from the URL and sets Referrer-Policy: no-referrer so the
    landing nav can't leak it onward."""
    _require_secure_cookie_config()
    # [CLAUDE Task3 2026-07-18] session-conflict guard: when the browser already
    # holds a VALID session for a DIFFERENT user, refuse (409) WITHOUT consuming
    # the magic token — clicking someone else's link must not silently swap the
    # signed-in identity (and the link stays valid for its rightful owner).
    _current = auth_users.authenticate_session(request.cookies.get(auth_users.COOKIE_NAME))
    if _current:
        _subject_uid = auth_users.verify_token(token, expected_typ="magic")
        _subject = auth_users.get_user_by_id(_subject_uid) if _subject_uid else None
        if _subject and _subject["email"] != _current:
            return HTMLResponse(
                status_code=409,
                content=(
                    "<h2>Already signed in</h2><p>This browser is signed in as a different "
                    "user. Sign out first, then open the link again.</p>"
                ),
            )
    email = auth_users.consume_magic_token(token)
    if not email:
        return RedirectResponse(url="/login?e=expired", status_code=302)
    u = auth_users.get_user(email)
    if not u or not u["is_active"]:
        return RedirectResponse(url="/login?e=expired", status_code=302)
    auth_users.touch_last_login(email)
    resp = RedirectResponse(url="/", status_code=303)  # 303 See Other after consuming the token
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.set_cookie(
        auth_users.COOKIE_NAME,
        auth_users.issue_token(email, typ="session"),  # fresh SESSION token (not the magic one)
        httponly=True,
        samesite="lax",
        max_age=auth_users.TOKEN_TTL_SECONDS,
        secure=_cookie_secure(request),
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
    body = await _read_json_capped(request)  # [CLAUDE V1] capped public body
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
    feats = [
        "Signal-accuracy proof (live track record)",
        "Daily AI portfolio report",
        "Watch dashboard",
        "Diversification radar",
    ]
    items = "".join(f"<li>{f}</li>" for f in feats)
    if _signups_paused():
        # The Legal Hub's L5 recommends pausing net-new paid signups until the ToS
        # is finalised and counsel has classified "My Book". Whether to actually
        # stop taking money is a BUSINESS decision, so this is an explicit env
        # flag defaulting to OFF — the mechanism exists, the choice stays human.
        card = (
            '<div style="border:1px solid #6b5410;border-radius:12px;padding:28px;'
            'background:#1a1405;max-width:460px;color:#e8d9b0">'
            '<h2 style="color:#f0b429;margin:0 0 10px">Not accepting new subscribers</h2>'
            '<p style="margin:0 0 10px">GESH is in beta and we have paused new paid '
            "signups while our terms and regulatory position are being finalised with "
            "counsel. Existing subscribers are unaffected.</p>"
            '<p style="margin:0">The <a href="/track-record" style="color:#00e87a">'
            "track record</a> and research pages remain open to read.</p></div>"
        )
    else:
        card = (
            '<div style="border:1px solid #1a2840;border-radius:12px;padding:28px;'
            'background:#0d1520;max-width:360px">'
            f'<h2 style="color:#00d4ff;margin:0">{name}</h2>'
            f'<p style="font-size:34px;margin:8px 0"><strong>${price}</strong>'
            '<span style="color:#667;font-size:14px">/mo</span></p>'
            f'<ul style="color:#aeb9c7;line-height:1.8">{items}</ul>'
            f'<a href="{url}" style="display:block;text-align:center;background:#00d4ff;'
            "color:#001018;padding:12px;border-radius:8px;text-decoration:none;"
            'font-weight:600;margin-top:12px">Subscribe</a></div>'
        )
    return (
        "<!doctype html><html><head><meta charset=utf-8><title>GESH — Pricing</title>"
        '<meta name=viewport content="width=device-width,initial-scale=1"></head>'
        '<body style="font-family:system-ui,sans-serif;background:#080d14;color:#e6edf3;'
        'max-width:900px;margin:auto;padding:32px">'
        '<h1 style="color:#00d4ff">GESH Financial — Access</h1>'
        # L5 beta framing, shown where money changes hands. The ToS is still a
        # DRAFT and counsel has not classified "My Book", so the product must
        # present itself as beta research/education rather than as advice.
        '<div style="background:#1a1405;border:1px solid #6b5410;border-left:3px solid #f0b429;'
        'border-radius:6px;padding:12px 16px;margin:0 0 20px;color:#e8d9b0;font-size:13px">'
        "<b>BETA · educational use.</b> GESH is a research and education tool in beta. "
        "It is <b>not investment advice</b> and does not manage money or place trades. "
        "Analytics are AI-generated and may be wrong; all decisions remain yours.</div>"
        '<p style="color:#aeb9c7">AI research tooling for your portfolio. '
        'See the <a href="/track-record" style="color:#00e87a">track record</a> first — '
        "results shown are hypothetical/simulated.</p>"
        f'<div style="margin:24px 0">{card}</div>'
        '<p style="color:#667;font-size:12px">By subscribing you agree to the '
        '<a href="/terms" style="color:#889">Terms</a>, '
        '<a href="/privacy" style="color:#889">Privacy Policy</a> &amp; '
        '<a href="/disclaimer" style="color:#889">Disclaimer</a>. '
        "NOT FINANCIAL ADVICE — FOR INFORMATIONAL AND EDUCATIONAL PURPOSES ONLY."
        "</p></body></html>"
    )


@app.get("/billing/success", response_class=HTMLResponse)
def billing_success():
    """Post-checkout landing — the magic-link email lands separately."""
    return (
        "<!doctype html><html><head><meta charset=utf-8><title>Payment received</title></head>"
        '<body style="font-family:system-ui,sans-serif;background:#080d14;color:#e6edf3;'
        'max-width:560px;margin:auto;padding:48px;text-align:center">'
        '<h1 style="color:#00e87a">✓ Payment received</h1>'
        "<p>Check your email for a sign-in link to access GESH. "
        "It can take a minute to arrive.</p>"
        '<p><a href="/login" style="color:#00d4ff">Go to login</a></p></body></html>'
    )


WEBHOOK_MAX_BYTES = 262_144  # 256 KiB — real LS payloads are a few KiB


async def _read_limited_body(request: Request, limit: int) -> bytes:
    """[CLAUDE Task3 2026-07-18] Read a request body with a hard byte cap.

    Rejects oversized payloads with 413 BEFORE any signature check or JSON parse
    so an attacker cannot make the public webhook endpoint buffer arbitrary data."""
    declared = request.headers.get("content-length")
    try:
        if declared is not None and int(declared) > limit:
            raise HTTPException(status_code=413, detail="Request too large")
    except ValueError:
        raise HTTPException(status_code=411, detail="Invalid Content-Length")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            raise HTTPException(status_code=413, detail="Request too large")
    return bytes(body)


PUBLIC_BODY_MAX_BYTES = 65_536  # 64 KiB — email+password / token payloads are tiny


async def _read_json_capped(request: Request, limit: int = PUBLIC_BODY_MAX_BYTES) -> dict:
    """[CLAUDE V1 2026-07-19] Read + parse a JSON body under a hard byte cap.

    Public POST routes (login, request-link, unsubscribe) must not buffer an
    unbounded unauthenticated body — the 413 fires before any parse/auth work.
    Returns {} on empty/invalid JSON (callers already treat that as no-op)."""
    raw = await _read_limited_body(request, limit)
    if not raw:
        return {}
    try:
        data = _json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


@app.post("/api/billing/webhook")
async def billing_webhook(request: Request):
    """Lemon Squeezy webhook — signature-verified + idempotent. Provisions/revokes the
    auth_users account. Public (in PUBLIC_PREFIXES) because it carries its own HMAC auth."""
    raw = await _read_limited_body(request, WEBHOOK_MAX_BYTES)
    sig = request.headers.get("X-Signature")
    try:
        payload = _json.loads(raw)
        if not isinstance(payload, dict):
            payload = {}
    except Exception:
        payload = {}
    try:
        result = billing.handle_webhook(raw, sig, payload)
        return JSONResponse(content=result)
    except PermissionError:
        return JSONResponse(status_code=401, content={"ok": False, "detail": "bad signature"})
    except ValueError as exc:
        logger.warning("Invalid webhook payload: %s", exc)
        return JSONResponse(
            status_code=400,
            content={"ok": False, "detail": "Invalid webhook payload"},
        )
    except billing.RetryableError as exc:
        # Non-2xx so Lemon Squeezy RETRIES (it only retries non-2xx) — e.g. the onboarding
        # email could not be sent yet (#1). The dedup claim was already released.
        logger.warning("Retryable webhook failure: %s", exc)
        return JSONResponse(
            status_code=503,
            content={
                "ok": False,
                "detail": "Webhook processing temporarily unavailable",
                "retry": True,
            },
        )
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


@app.get("/congress-watch", response_class=HTMLResponse)
def congress_watch_page():
    """Public, shareable Congress-Trade-Watch page — the acquisition magnet extracted
    from Autopilot's viral hook, but INFORMATIONAL only (public STOCK Act disclosures,
    no execution, no advice; publisher-lane). Offline-safe empty state."""
    try:
        import congress_agent

        return congress_agent.render_watch_html(congress_agent.load_watch_data())
    except Exception as exc:
        raise _server_error(exc)


@app.get("/api/congress")
def api_congress():
    """Recent congressional disclosures + most-bought leaderboard (public data).

    Includes source provenance (chambers, fetched-at, degraded flag) so an
    upstream outage is reported as degraded — never as successful empty data."""
    import congress_agent

    lb = congress_agent.leaderboard()
    return {
        "status": "ok",
        "data": {
            "leaderboard": lb.get("top_bought", []),
            "window": lb.get("window"),
            "recent": congress_agent.recent_activity(limit=40).get("trades", []),
            "disclaimer": congress_agent.DISCLOSURE_LAG_NOTE,
            "attribution": congress_agent.FILING_ATTRIBUTION_NOTE,
            "source": congress_agent.source_status(),
        },
    }


@app.get("/api/congress/{symbol}")
def api_congress_symbol(symbol: str):
    """Congressional trades for one ticker — informational, attaches to the research card."""
    import congress_agent

    return {"status": "ok", "data": congress_agent.for_symbol(_safe_symbol(symbol))}


@app.get("/terms", response_class=HTMLResponse)
@app.get("/disclaimer", response_class=HTMLResponse)
def terms_page():
    """Public Terms of Service & Disclaimer (internal compliance notes stripped)."""
    import legal_pages

    return legal_pages.render_terms_html()


@app.get("/api/account/export")
def account_export(request: Request):
    """Data-subject ACCESS/PORTABILITY right (Privacy Policy §8).

    Returns everything stored about the caller as a downloadable JSON file.
    `export_user_data` keys strictly on the caller's user_id, so it cannot reach
    another account, and it never includes the password hash."""
    email = _session_email(request)
    if not email:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        data = auth_users.export_user_data(email)
        auth_users.log_usage(email, "data_export")
        stamp = _dt.now(_tz.utc).strftime("%Y%m%d")
        return JSONResponse(
            content=_jsonable(data),
            headers={"Content-Disposition": f'attachment; filename="gesh-my-data-{stamp}.json"'},
        )
    except HTTPException:
        raise
    except Exception as e:
        raise _server_error(e)


@app.post("/api/account/delete")
async def account_delete(request: Request):
    """Data-subject ERASURE right (Privacy Policy §8) — a real purge, not a
    deactivation.

    Requires the literal string "DELETE": this is irreversible, and an
    irreversible action behind a single click is an accident waiting to happen.
    The owner account is refused — deleting it would lock the operator out of
    their own product. If a live subscription exists we say so: deleting here
    does NOT cancel Lemon Squeezy, and letting someone keep paying for an
    account that no longer exists would be indefensible."""
    email = _session_email(request)
    if not email:
        raise HTTPException(status_code=401, detail="Authentication required")
    if auth_users.is_owner(email):
        raise HTTPException(
            status_code=403, detail="The owner account cannot be deleted from the app."
        )
    try:
        body = await request.json()
    except Exception:
        body = {}
    if (body or {}).get("confirm") != "DELETE":
        raise HTTPException(
            status_code=400,
            detail='Confirmation required — send {"confirm": "DELETE"} to erase your account.',
        )
    try:
        sub_id, sub_status = auth_users.billing_subscription_status(email)
        report = auth_users.delete_user_data(email)
        notice = ""
        if sub_id and (sub_status or "").lower() in ("active", "past_due", "paused"):
            notice = (
                "Your account data is deleted, but your subscription is billed by "
                "Lemon Squeezy and is NOT cancelled by this action — cancel it from "
                "the billing-portal link in your receipt email to stop future charges."
            )
        resp = JSONResponse({"status": "ok", **report, "notice": notice})
        resp.delete_cookie(auth_users.COOKIE_NAME, path="/")
        return resp
    except HTTPException:
        raise
    except Exception as e:
        raise _server_error(e)


@app.get("/privacy", response_class=HTMLResponse)
def privacy_page():
    """Public Privacy Policy (hub item L1).

    Published as an honest DRAFT pending counsel review: the product already
    collects emails, customer-entered positions, and usage metering, and until
    now had NO published policy at all. Public (in PUBLIC_PREFIXES) because a
    policy nobody can read before signing up is not a published policy."""
    import legal_pages

    return legal_pages.render_privacy_html()


# ── Unsubscribe: GET confirms, POST mutates (Codex P1, 2026-07-17) ────────────
# Email security scanners and link-preview bots routinely FETCH every URL in an
# email — a GET that mutates silently unsubscribed customers who never clicked.
# Contract: GET = confirmation page only (idempotent, safe); POST = the actual
# opt-out. POST accepts the token via query string, JSON {token}, or an RFC 8058
# one-click body (mail providers POST "List-Unsubscribe=One-Click" with the token
# still in the URL). The jobs' emails carry List-Unsubscribe[-Post].
# [CLAUDE 2026-07-18] immutable-user-ID migration: verify_token returns the UUID
# subject — resolve it to the user's CURRENT email before flipping preference flags.

_UNSUB_KINDS = {
    "alerts": ("alerts_unsub", "GESH signal-change alerts", "set_alerts_opt_out"),
    "digest": ("digest_unsub", "the GESH weekly digest", "set_digest_opt_out"),
}


def _unsub_user(kind: str, token: str) -> dict | None:
    """Validate the typed token and resolve its immutable-UUID subject to the
    current user record (or None) — one place for the v2-token -> email hop so the
    confirm page and the mutate path stay consistent."""
    typ, _label, _setter = _UNSUB_KINDS[kind]
    uid = auth_users.verify_token(token, expected_typ=typ)
    return auth_users.get_user_by_id(uid) if uid else None


def _unsub_confirm_page(kind: str, token: str) -> HTMLResponse:
    import json

    _typ, label, _setter = _UNSUB_KINDS[kind]
    if not _unsub_user(kind, token):
        raise HTTPException(status_code=400, detail="Invalid or expired unsubscribe link")
    tok_js = json.dumps(token)  # JSON-string embedding — token is ours, escape anyway
    return HTMLResponse(
        f"<div style='font-family:system-ui,sans-serif;max-width:480px;margin:40px auto'>"
        f"<h2>Unsubscribe from {label}?</h2>"
        f"<p>Click confirm to stop these emails. You can re-enable them any time "
        f"from your dashboard.</p>"
        f"<button id='go' style='padding:10px 18px;font-size:15px'>Confirm unsubscribe</button>"
        f"<p id='done' style='display:none;color:#0a7'>Unsubscribed. You can close this page.</p>"
        f"<noscript><p>JavaScript is required to confirm — or use your email client's "
        f"built-in unsubscribe button.</p></noscript>"
        f"<script>document.getElementById('go').onclick=async()=>{{"
        f"const r=await fetch(location.pathname,{{method:'POST',"
        f"headers:{{'Content-Type':'application/json'}},"
        f"body:JSON.stringify({{token:{tok_js}}})}});"
        f"if(r.ok){{document.getElementById('go').style.display='none';"
        f"document.getElementById('done').style.display='block';}}}};</script></div>"
    )


async def _unsub_perform(kind: str, request: Request, token_qs: str) -> JSONResponse:
    _typ, _label, setter_name = _UNSUB_KINDS[kind]
    token = (token_qs or "").strip()
    if not token:  # JSON or urlencoded body (both capped — [CLAUDE V1])
        try:
            raw = await _read_limited_body(request, PUBLIC_BODY_MAX_BYTES)
        except HTTPException:
            raise
        except Exception:
            raw = b""
        if raw:
            try:
                _b = _json.loads(raw)
                token = str((_b or {}).get("token") or "").strip() if isinstance(_b, dict) else ""
            except Exception:
                token = ""
            if not token:  # RFC 8058 one-click: urlencoded fallback
                from urllib.parse import parse_qsl

                token = dict(parse_qsl(raw.decode("utf-8", "replace"))).get("token", "").strip()
    user = _unsub_user(kind, token)
    if not user:
        return JSONResponse(
            status_code=400, content={"ok": False, "detail": "Invalid or expired unsubscribe link"}
        )
    getattr(auth_users, setter_name)(user["email"], True)
    return JSONResponse(content={"ok": True})


@app.get("/alerts/unsubscribe", response_class=HTMLResponse)
def alerts_unsubscribe_confirm(token: str = ""):
    """Confirmation page ONLY — never mutates (scanner/preview-safe)."""
    return _unsub_confirm_page("alerts", token)


@app.post("/alerts/unsubscribe")
async def alerts_unsubscribe_do(request: Request, token: str = ""):
    """The actual opt-out. Typed-token gated; accepts JSON / query / one-click bodies."""
    return await _unsub_perform("alerts", request, token)


@app.get("/digest/unsubscribe", response_class=HTMLResponse)
def digest_unsubscribe_confirm(token: str = ""):
    """Confirmation page ONLY — never mutates (scanner/preview-safe)."""
    return _unsub_confirm_page("digest", token)


@app.post("/digest/unsubscribe")
async def digest_unsubscribe_do(request: Request, token: str = ""):
    """The actual opt-out. Typed-token gated; accepts JSON / query / one-click bodies."""
    return await _unsub_perform("digest", request, token)


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
        raise HTTPException(
            status_code=404, detail="No daily report yet — the 10:30 chain has not produced one."
        )
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
from datetime import datetime as _dt
from datetime import timezone as _tz

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
            # The daily chain writes this file too, so every writer must use a
            # unique sibling temp file before the atomic replace.
            atomic_write_json(ENGINE_RESULTS, _sanitize(data))
    except Exception as _e:
        print(f"⚠  engine-result cache write failed for {key}: {_e}")


def _cached_engine(key: str) -> dict:
    """Return the cached result for an engine key, or {cached:false} if absent."""
    data = _load_engine_results()
    val = data.get(key)
    if val is not None:
        return {"status": "ok", "cached": True, "data": val, "ran_at": data.get(f"{key}_ran_at")}
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


def _holding_consensus_args(symbol: str, payload: dict) -> dict[str, float]:
    """Derive run_consensus_holding() kwargs from a portfolio snapshot."""
    symbol = symbol.upper()
    total_portfolio = (
        float((payload.get("equity_summary") or {}).get("portfolio_equity", 0) or 0) or 1.0
    )
    holding = next(
        (h for h in payload.get("holdings", []) if (h.get("symbol") or "").upper() == symbol),
        None,
    )
    if holding is not None:
        return {
            "equity": float(holding.get("equity", 0) or 0),
            "total_portfolio": total_portfolio,
            "avg_cost": float(holding.get("average_buy_price", 0) or 0),
            "current_price": float(holding.get("current_price", 0) or 0),
        }
    quote = next(
        (w for w in payload.get("watchlist", []) if (w.get("symbol") or "").upper() == symbol),
        None,
    )
    current_price = float((quote or {}).get("last_trade_price", 0) or 0)
    return {
        "equity": 0.0,
        "total_portfolio": total_portfolio,
        "avg_cost": 0.0,
        "current_price": current_price,
    }


def _cached_consensus_row(symbol: str) -> dict | None:
    """Return a fresh cached consensus row for one symbol, if available."""
    symbol = symbol.upper()
    data = _load_engine_results()
    raw = data.get("consensus")
    if not raw or not _is_fresh(data.get("consensus_ran_at")):
        return None

    def _match(rows: list) -> dict | None:
        for row in rows:
            if isinstance(row, dict) and (row.get("symbol") or "").upper() == symbol:
                out = dict(row)
                out.setdefault("symbol", symbol)
                return out
        return None

    if isinstance(raw, dict):
        if "data" in raw and isinstance(raw["data"], dict):
            inner = raw["data"]
            if "holdings" in inner and isinstance(inner["holdings"], list):
                hit = _match(inner["holdings"])
                if hit:
                    return hit
            if symbol in inner and isinstance(inner[symbol], dict):
                out = dict(inner[symbol])
                out.setdefault("symbol", symbol)
                return out
        if "holdings" in raw and isinstance(raw["holdings"], list):
            hit = _match(raw["holdings"])
            if hit:
                return hit
        if symbol in raw and isinstance(raw[symbol], dict):
            out = dict(raw[symbol])
            out.setdefault("symbol", symbol)
            return out
    return None


def _resolve_consensus_row(symbol: str, payload: dict, *, use_cached: bool = True) -> dict:
    """Use cached daily consensus when fresh; otherwise run the full engine."""
    symbol = symbol.upper()
    if use_cached:
        cached = _cached_consensus_row(symbol)
        if cached:
            return cached
    from consensus import run_consensus_holding

    args = _holding_consensus_args(symbol, payload)
    row = run_consensus_holding(symbol, **args)
    row.setdefault("symbol", symbol)
    return row


# ─── In-process throttles / caches (FD-leak mitigation) ──────────────────────
# Dashboard polls /api/portfolio every few seconds. Logging an equity snapshot
# on *every* poll is pointless for ECG (which only needs minute-level cadence)
# and was the source of "Snapshot log failed: unable to open database file"
# spam once FDs got tight. Throttle to one write per SNAPSHOT_INTERVAL_SEC.
SNAPSHOT_INTERVAL_SEC = 300  # 5 minutes — plenty for ECG
_last_snapshot_at: float = 0.0
_snapshot_lock = threading.Lock()


def _is_real_observation(h: dict) -> bool:
    """A holding is a REAL equity observation only if BOTH price and equity are finite
    and > 0. A degraded/offline snapshot can carry current_price=0 and a stale constant
    equity; persisting those poisons the ECG equity curve — a sentinel-heavy series reads
    as a -96% crash and fires false "SUSPENDED — consider exiting" alarms (external audit
    2026-07-18). Missing data must stay MISSING, never a loss."""
    price, equity = h.get("current_price"), h.get("equity")
    return (
        isinstance(price, (int, float))
        and not isinstance(price, bool)
        and math.isfinite(price)
        and price > 0
        and isinstance(equity, (int, float))
        and not isinstance(equity, bool)
        and math.isfinite(equity)
        and equity > 0
    )


def _maybe_log_snapshot(holdings: list[dict]) -> None:
    """Rate-limited equity snapshot + live cache write. Skips if last write < interval ago."""
    global _last_snapshot_at
    now = time.monotonic()
    with _snapshot_lock:
        if now - _last_snapshot_at < SNAPSHOT_INTERVAL_SEC:
            return
        _last_snapshot_at = now
    # Only persist real observations — never write price=0 / sentinel rows that would
    # later be read back as a catastrophic drawdown.
    valid = [h for h in holdings if _is_real_observation(h)]
    if not valid:
        return
    try:
        log_equity_snapshot(valid)
    except Exception as snap_err:
        print(f"⚠  Snapshot log failed: {snap_err}")
    # Refresh live cache so the daily-analysis skill always gets current data
    try:
        import threading as _t

        from fetch_live_data import fetch_and_cache

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
    # Stamp the fetch time so each Analyze widget can label its OWN age rather than the
    # page making one global LIVE-vs-CACHED claim (GESH-4). A cache HIT returns this
    # same dict, so `as_of` keeps reflecting when the data was actually fetched — the
    # widget derives live-vs-cached from its age.
    #
    # `source` is NOT defaulted here. It previously defaulted to the constant "live",
    # which — because a cache hit returns the stored dict verbatim — claimed "live" even
    # on a 5-minute-old cached serve and could never say otherwise. A constant is not an
    # observation. Endpoints that KNOW which feed served the row set `source` explicitly
    # (setdefault yields to them); the rest omit it rather than assert something
    # unverified (GESH-8).
    if isinstance(resp, dict) and "price" not in resp:  # skip tiny price-only blobs
        resp.setdefault("as_of", _dt.now(_tz.utc).isoformat())
    with _ticker_get_lock:
        _ticker_get_cache[(bucket, sym)] = (time.monotonic(), resp)
    return resp


# ─── Dashboard ────────────────────────────────────────────────────────────────

# Elements that never close (HTML void elements) — they can't wrap a subtree, so
# the balancer must not look for a closing tag for them.
_VOID_TAGS = frozenset(
    (
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    )
)
_TAG_OPEN_RE = re.compile(
    r"<(?P<close>/?)(?P<tag>[a-zA-Z][\w-]*)(?P<attrs>[^>]*?)(?P<selfclose>/?)>"
)
_OWNER_CLASS_RE = re.compile(r"""\bclass\s*=\s*(?P<q>["'])(?P<val>[^"']*)(?P=q)""")
_RAWTEXT_TAGS = ("script", "style")


def _has_owner_only_class(attrs: str) -> bool:
    """True when the tag carries the exact `owner-only` class TOKEN.

    Token-exact on purpose: a substring test would also strip a hypothetical
    `owner-only-note` or `not-owner-only`, and silently deleting the wrong
    subtree is worse than not stripping at all."""
    m = _OWNER_CLASS_RE.search(attrs)
    return bool(m) and "owner-only" in m.group("val").split()


def _strip_owner_only_html(html: str) -> str:
    """Remove every element marked `class="… owner-only …"` from a document.

    This is the SERVER-SIDE half of customer/owner separation. The dashboard
    already hid these blocks with `body.customer .owner-only{display:none}`, but
    CSS is a rendering hint, not an authorization boundary: the operator console
    (owner positions, Buying Power, Equity-Curve Gate, Scanner config, Paper
    Trade Log, Risk-Gate dry-run, /watch · /invest · /usage) was still delivered
    to every customer and readable in one devtools toggle (audit A-01).

    Written as an explicit scanner rather than a regex sweep because the template
    contains JS template literals that themselves build `owner-only` markup — a
    regex would cut into a backtick string and take the whole customer app down
    with it. `<script>`/`<style>` bodies are therefore copied VERBATIM, and a tag
    whose matching close cannot be found is left alone (fail-safe: an unstripped
    element is a bug, a truncated document is an outage)."""
    out: list[str] = []
    i, n = 0, len(html)
    while i < n:
        lt = html.find("<", i)
        if lt == -1:
            out.append(html[i:])
            break
        out.append(html[i:lt])

        if html.startswith("<!--", lt):  # comment — copy verbatim
            end = html.find("-->", lt)
            end = n if end == -1 else end + 3
            out.append(html[lt:end])
            i = end
            continue

        m = _TAG_OPEN_RE.match(html, lt)
        if not m:  # stray "<" — literal text
            out.append(html[lt])
            i = lt + 1
            continue

        tag = m.group("tag").lower()
        if not m.group("close") and tag in _RAWTEXT_TAGS:  # raw text — copy verbatim
            close = html.lower().find(f"</{tag}>", m.end())
            end = n if close == -1 else close + len(tag) + 3
            out.append(html[lt:end])
            i = end
            continue

        if (
            m.group("close")
            or tag in _VOID_TAGS
            or m.group("selfclose")
            or not _has_owner_only_class(m.group("attrs"))
        ):
            out.append(m.group(0))
            i = m.end()
            continue

        # Owner-only subtree: balance same-name tags to find its real end.
        depth, j, end = 1, m.end(), None
        while depth and j < n:
            inner = _TAG_OPEN_RE.search(html, j)
            if not inner:
                break
            if inner.group("tag").lower() == tag and not inner.group("selfclose"):
                depth += -1 if inner.group("close") else 1
                if depth == 0:
                    end = inner.end()
                    break
            j = inner.end()
        if end is None:  # malformed — keep as-is
            out.append(m.group(0))
            i = m.end()
            continue
        i = end  # drop [lt, end)
    return "".join(out)


# ─── Role-composed dashboard ──────────────────────────────────────────────────
# The operator console lives in its OWN partial (templates/owner_console.html)
# and is spliced into named slots in the shell. The customer document is
# ASSEMBLED without it — it is never built and then cut down, which is what the
# audit found (markup delivered, hidden with `body.customer .owner-only{...}`).
#
# Build-up, not tear-down: a new operator widget added to the partial can never
# reach a customer, because customer rendering only ever fills slots with "".
# `_strip_owner_only_html` remains as a SECONDARY net for any owner-only markup
# a future edit puts directly in the shell; it is no longer the mechanism.
OWNER_PARTIAL = Path(__file__).parent / "templates" / "owner_console.html"
_SLOT_RE = re.compile(r"<!--GESH_SLOT:(?P<name>[A-Za-z0-9_]+)-->")
_BLOCK_RE = re.compile(
    r"<!--GESH_BLOCK:(?P<name>[A-Za-z0-9_]+)-->\n(?P<body>.*?)\n<!--/GESH_BLOCK:(?P=name)-->", re.S
)


def _owner_blocks() -> dict[str, str]:
    """Owner console blocks by slot name. Read per call so a template edit shows
    up without a restart, matching how the shell is already read."""
    try:
        return {
            m.group("name"): m.group("body") for m in _BLOCK_RE.finditer(OWNER_PARTIAL.read_text())
        }
    except OSError:
        logger.error("owner console partial missing at %s — rendering without it", OWNER_PARTIAL)
        return {}


def _render_dashboard(role: str) -> str:
    """Compose the dashboard for `role`. Customer slots are filled with nothing."""
    html = HTML.read_text()
    blocks = _owner_blocks() if role == "owner" else {}
    missing: list[str] = []

    def fill(m: re.Match) -> str:
        name = m.group("name")
        if role != "owner":
            return ""
        if name not in blocks:
            missing.append(name)
            return ""
        return blocks[name]

    html = _SLOT_RE.sub(fill, html)
    if missing:
        # Never silently serve the owner a half-console.
        logger.error("owner console slots unresolved: %s", ", ".join(missing))
    if role != "owner":
        # Secondary net only — the shell should contain no owner-only markup at
        # all now, so this is expected to be a no-op (asserted by tests).
        html = _strip_owner_only_html(html)
        # `tab-overview` carries the initial `active` class and lives in the
        # owner partial, so a customer document would open with NO active pane
        # until applyRoleNav's fetch resolved. Promote the landing pane instead.
        html = html.replace(
            '<div class="tab-pane" id="tab-home">', '<div class="tab-pane active" id="tab-home">', 1
        )
    return html


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    if auth_users.auth_enabled():
        email = auth_users.authenticate_session(request.cookies.get(auth_users.COOKIE_NAME))
        role = "owner" if (email and auth_users.is_owner(email)) else "customer"
    else:
        role = "owner"
    html = _render_dashboard(role)
    # Inject initial role synchronously so customer UI (hiding owner-only nav) applies
    # before first paint. This eliminates the 1-2s flash of full owner menu on refresh.
    html = html.replace("<body", f'<body data-initial-role="{role}"', 1)
    html = html.replace("{{ model }}", MODEL)
    return HTMLResponse(html)


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
        return load_snapshot_payload()  # re-raises RobinhoodOfflineError only if NO snapshot exists


def _context_payload(request: Request | None) -> dict:
    """Portfolio context for an analysis route, scoped to the caller's role.

    The OWNER gets the real payload. A CUSTOMER gets the PUBLISHED model
    context — the same allowlist `model_portfolio.publish()` uses, in the
    engine-facing shape.

    Ten customer-reachable GETs (/api/ecg, /api/regime, /api/market-data,
    /api/fft-cycles, /api/tax/harvest, /api/benchmarks, /api/crypto-correlation,
    /api/yield/landscape and the two agent-history routes) fed `_resolved_payload()`
    straight into their engines. The response mask scales NUMBERS on the way out;
    it does nothing about private non-numeric fields on the way IN, and nothing at
    all about a field added to the owner payload next month. Sanitising at the
    boundary — not after the computation — is the difference between a filtered
    answer and an answer that was never allowed to see the data.
    """
    payload = _resolved_payload()
    if _is_owner_request(request):
        return payload
    return model_portfolio.build_customer_model_context(payload)


def _regime_for_symbols(symbols: list[str], *, offline: bool = False) -> dict:
    """Regime bundle for analysis endpoints. Prefers engine_results cache when the
    portfolio payload is offline or when a fresh (<4h) cache exists."""
    cached, ran = None, None
    try:
        eng = _load_engine_results()
        cached = eng.get("regime")
        ran = eng.get("regime_ran_at")
    except Exception:
        pass

    if offline and cached and cached.get("regimes"):
        return cached

    if not offline and cached and cached.get("regimes") and ran:
        try:
            age_s = (_dt.now(_tz.utc) - _dt.fromisoformat(str(ran))).total_seconds()
            if age_s < 4 * 3600:
                return cached
        except Exception:
            pass

    try:
        return detect_regimes(symbols)
    except Exception:
        if cached and cached.get("regimes"):
            return cached
        return {"regimes": [], "regime_summary": "Live regime unavailable."}


# How old a snapshot may be before the label changes. LIVE means a broker tick
# now; CACHED means a stored snapshot inside the window; STALE means beyond it.
_FRESHNESS_MAX_AGE_S = 3600


def _attach_freshness(payload: dict) -> dict:
    """One freshness model for the whole response.

    The response used to carry `offline: true` next to `stale_snapshot: false`
    while the UI printed "LIVE MARKET DATA" and a card said "just now" — three
    independent notions of freshness that could, and did, contradict each other
    in the same viewport (audit BUG-07). They are collapsed into ONE block with a
    single `state` a caller can render directly, and the legacy booleans are
    derived from it so nothing can drift apart again."""
    age = payload.get("snapshot_age_seconds")
    offline = bool(payload.get("offline"))
    if not offline:
        state, label = "live", "Live market data"
    elif age is None:
        state, label = "unknown", "Age of this data is unknown"
    elif age > _FRESHNESS_MAX_AGE_S:
        state, label = "stale", "Stored snapshot — older than an hour"
    else:
        state, label = "cached", "Stored snapshot — not live prices"
    payload["data_freshness"] = {
        "state": state,  # live | cached | stale | unknown
        "label": label,
        "data_as_of": payload.get("as_of"),
        "age_seconds": age,
        "max_expected_age_seconds": _FRESHNESS_MAX_AGE_S,
        "source": payload.get("source"),
        "is_live": state == "live",
    }
    # Legacy keys stay, but are now DERIVED from the same state so a client
    # reading either one cannot be told two different things.
    payload["offline"] = state != "live"
    payload["stale_snapshot"] = state == "stale"
    return payload


def _positive_finite(value: object) -> float | None:
    """A usable prior-close reference, or None.

    The snapshot loader emits 0.0 for an ABSENT previous-close field, so zero
    here means "we were not told", not "the book was worth nothing" — and a
    zero/negative denominator cannot produce a return at all. Non-finite is
    corruption. All of them are the same answer: unavailable."""
    d = portfolio_valuation.to_decimal(value)
    return float(d) if (d is not None and d > 0) else None


def _attach_account_day_change(eq: dict) -> dict:
    """Owner ACCOUNT-level day change, in place. Account minus account."""
    prior = _positive_finite(eq.get("adjusted_equity_previous_close"))
    current = portfolio_valuation.to_decimal(eq.get("broker_account_equity"))
    if current is None:
        current = portfolio_valuation.to_decimal(eq.get("calculated_total_equity"))
    if prior is None or current is None:
        # Report unavailable rather than a 0.0 that renders as a flat market.
        eq["day_change"] = None
        eq["day_change_pct"] = None
        eq["day_change_available"] = False
        eq["prior_close_equity"] = None
        return eq
    amount = float(current) - prior
    eq["day_change"] = round(amount, 2)
    eq["day_change_pct"] = round(amount / prior * 100, 2)
    eq["day_change_available"] = True
    eq["prior_close_equity"] = round(prior, 2)
    return eq


def _reconcile_equity_summary(eq: dict, holdings: list | None = None) -> dict:
    """Make the headline total equal its own ITEMISED components, in place.

    FIELD SEMANTICS — verified against both production normalizers, because
    getting this backwards is precisely the bug this function once had:

      equity_summary.portfolio_equity   ACCOUNT-LEVEL equity (positions + cash).
          Live: `get_total_equity()` -> `profile["equity"]`. Snapshot:
          `load_snapshot_payload()` -> `account_equity or total or …`, which its
          own comment documents as account-level.
      equity_summary.cash               uninvested cash, SEPARATE and already
          included in the account figure above.
      holdings[].equity                 the itemised positions.

    So `portfolio_equity + cash` DOUBLE-COUNTS cash: a 108,000 account with
    20,000 cash reconciled to 128,000. Positions come from the holdings — the
    only place they are itemised — via the canonical helper, never by
    reconstructing them from an overloaded summary field.

    Emits, under unambiguous names:
      positions_market_value   Σ canonical holding equity
      calculated_total_equity  positions_market_value + cash   (the display total)
      broker_account_equity    what the broker said, kept for owner diagnostics
      reported_total_delta     broker - calculated (unmodelled sleeves)

    `total` / `account_equity` stay as compatibility aliases of the calculated
    total for existing owner consumers."""
    cash_d = portfolio_valuation.to_decimal(eq.get("cash"))
    broker = portfolio_valuation.to_decimal(
        eq.get("account_equity")
        if eq.get("account_equity") is not None
        else (
            eq.get("portfolio_equity")
            if eq.get("portfolio_equity") is not None
            else eq.get("total")
        )
    )

    rows, report = model_portfolio.canonical_holdings(
        holdings or [], model_portfolio.PUBLISHED_HOLDING_FIELDS
    )
    positions = report["positions_value"]

    if cash_d is None or report.get("non_finite"):
        # A corrupt input cannot produce a reconciled book; fail closed rather
        # than substitute a zero.
        eq["positions_market_value"] = float(positions)
        eq["calculated_total_equity"] = None
        eq["broker_account_equity"] = float(broker) if broker is not None else None
        eq["reported_total_delta"] = None
        eq["reconciles"] = False
        eq["valuation_source_state"] = "unavailable"
        return eq

    derived = float(positions) + float(cash_d)
    eq["positions_market_value"] = round(float(positions), 2)
    eq["calculated_total_equity"] = round(derived, 2)
    # Compatibility aliases — owner consumers (Kelly/consensus sizing) read these.
    eq["total"] = round(derived, 2)
    eq["account_equity"] = round(derived, 2)
    eq["broker_account_equity"] = round(float(broker), 2) if broker is not None else None

    if broker is None:
        eq["reported_total"] = None
        eq["reported_total_delta"] = None
        eq["reconciles"] = True
        eq["valuation_source_state"] = "calculated"
        return eq

    delta = round(float(broker) - derived, 2)
    eq["reported_total"] = round(float(broker), 2)
    eq["reported_total_delta"] = delta
    eq["reconciles"] = abs(delta) <= float(portfolio_valuation.MONEY_TOLERANCE)
    eq["valuation_source_state"] = "calculated" if eq["reconciles"] else "degraded"
    if not eq["reconciles"]:
        # Log the variance WITHOUT dollar figures — the alert must not become the
        # leak. A broker headline legitimately covers sleeves we do not itemise
        # (options, crypto, unsettled activity); the itemised total is what we show.
        logger.warning(
            "equity variance: broker account equity disagrees with positions+cash "
            "by %.4f%% — serving the itemised total, source marked degraded",
            abs(delta) / derived * 100 if derived else 0.0,
        )
    return eq


def _is_owner_request(request: Request | None) -> bool:
    """True only for a confirmed owner session. Fails CLOSED: with the wall on
    and no valid session, the caller is treated as a customer."""
    if request is None or not auth_users.auth_enabled():
        return True  # single-tenant / wall-off = owner mode
    email = auth_users.authenticate_session(request.cookies.get(auth_users.COOKIE_NAME))
    return bool(email and auth_users.is_owner(email))


@app.get("/api/portfolio")
def get_portfolio(request: Request = None):  # noqa: B008 — Optional for direct calls
    try:
        try:
            payload = build_data_payload(DEFAULT_WATCHLIST)
        except RobinhoodOfflineError as live_err:
            # ONLY a confirmed-offline session degrades to the MCP-built snapshot (Option A).
            # Any OTHER exception is a real bug — it must NOT be masked by serving stale data;
            # it falls through to _server_error (500) and reaches monitoring (Codex MEDIUM-4).
            payload = load_snapshot_payload()  # may re-raise RobinhoodOfflineError → 503
            # Throttled: this fires on EVERY poll while the RH session is dead and
            # cannot self-heal off-hours. First hit is immediate; repeats collapse.
            suppressed = _throttled_log_due("api_portfolio_offline")
            if suppressed is not None:
                logger.warning(
                    "api/portfolio: live RH offline (%s) — serving snapshot (age=%ss)%s",
                    type(live_err).__name__,
                    payload.get("snapshot_age_seconds"),
                    f" [+{suppressed} suppressed in the last {int(_THROTTLED_LOG_INTERVAL_S)}s]"
                    if suppressed
                    else "",
                )

        eq = payload["equity_summary"]
        _reconcile_equity_summary(eq, payload.get("holdings"))
        # OWNER day change is ACCOUNT vs ACCOUNT: `adjusted_equity_previous_close`
        # is the broker's previous ACCOUNT equity, and today's account equity is
        # the broker headline. Cash sits inside both sides already, so adding it
        # to either would double-count it (and adding it to only one produces the
        # 6.67% artefact). Comparing today's ITEMISED total against yesterday's
        # ACCOUNT total would be the same category error in reverse.
        _attach_account_day_change(eq)

        # Surface snapshot age for the dashboard staleness badge (>24h → amber banner).
        raw_as_of = (payload.get("as_of") or "").strip()
        if raw_as_of:
            try:
                ts = _dt.fromisoformat(raw_as_of.replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=_tz.utc)
                age_s = (_dt.now(_tz.utc) - ts).total_seconds()
                payload.setdefault("snapshot_age_seconds", round(age_s))
                payload["stale_snapshot"] = age_s > 86400
            except Exception:
                pass

        _attach_freshness(payload)

        # Log the equity snapshot for ECG only for LIVE data — never persist stale snapshot
        # values as if they were a fresh tick (would corrupt the equity curve).
        if not payload.get("offline"):
            _maybe_log_snapshot(payload["holdings"])

        if not _is_owner_request(request):
            # A customer never receives the owner brokerage payload — not even a
            # rescaled one. `publish` is a build-up allowlist, so brokerage
            # identifiers, quote-session metadata, buying power, cost basis and
            # execution state cannot reach this response even if they are added
            # to the owner payload later (audit A-01). Dollars are still
            # normalised to the model-book notional by the masking middleware,
            # which runs exactly once, after this.
            return _sanitize(
                {"status": "ok", "data": model_portfolio.publish(payload, prior_close=_prior_close)}
            )

        return _sanitize({"status": "ok", "data": payload})
    except Exception as e:
        raise _server_error(e)


# ─── Quick AI analysis ────────────────────────────────────────────────────────


@app.post("/api/analyze", dependencies=[Depends(require_api_key)])
def run_analysis():
    try:
        payload = _resolved_payload()
        analysis = analyze_portfolio(payload)
        resp = {"status": "ok", "analysis": analysis, "model": MODEL}
        if payload.get("offline"):
            resp["offline"] = True
        return resp
    except Exception as e:
        raise _server_error(e)


# ─── Deep AI analysis (with regime injection + DB logging) ───────────────────


@app.post("/api/deep-analysis", dependencies=[Depends(require_api_key)])
def run_deep_analysis():
    try:
        payload = _resolved_payload()
        offline = bool(payload.get("offline"))
        eq = payload["equity_summary"]
        prev = eq.get("adjusted_equity_previous_close", 0)
        curr = eq.get("portfolio_equity", 0)
        eq["day_change"] = round(curr - prev, 2)
        eq["day_change_pct"] = round(((curr - prev) / prev * 100) if prev else 0, 2)

        symbols = [h["symbol"] for h in payload["holdings"]]

        # Regime detection injected into prompt (cached when portfolio is offline)
        regime_data = _regime_for_symbols(symbols, offline=offline)
        regime_context = regime_data.get("regime_summary")

        # Market intelligence (analyst targets, performance, insiders, ownership)
        mkt = get_all_market_data(symbols)

        analysis = deep_analyze_portfolio(payload, regime_context=regime_context)
        # Canonicalize fair-value % BEFORE it is persisted/returned (Codex #4), so the
        # stored history and the response carry the same convention Kelly recomputes on
        # read — otherwise old and new conventions coexist in reports and history.
        analysis = fair_value.canonicalize_analysis(analysis)

        # Log to SQLite. Deep analysis is user-triggered (not a poll), so we
        # always write the analysis here regardless of the throttle. But NEVER
        # persist a snapshot-sourced (offline) equity value as a live tick — that
        # would corrupt the equity curve / ECG with stale data (mirrors /api/portfolio).
        try:
            run_id = log_analysis(MODEL, payload, analysis)
            if not payload.get("offline"):
                log_equity_snapshot(payload["holdings"])
        except Exception as db_err:
            print(f"⚠  DB log failed: {db_err}")
            run_id = None

        resp = {
            "status": "ok",
            "analysis": analysis,
            "model": MODEL,
            "regimes": regime_data.get("regimes", []),
            "market": mkt,
            "run_id": run_id,
        }
        if offline:
            resp["offline"] = True
            resp["degraded"] = True
        _save_engine_result("ai_result", resp)
        return resp
    except Exception as e:
        raise _server_error(e)


# ─── Step 2 — Monte Carlo stress test ────────────────────────────────────────


@app.post("/api/monte-carlo", dependencies=[Depends(require_api_key)])
def monte_carlo():
    try:
        payload = _resolved_payload()
        holdings = payload["holdings"]
        eq_sum = payload["equity_summary"]
        result = run_monte_carlo(holdings, eq_sum)
        # _jsonable: same numpy-scalar hazard as /api/kelly (GBM percentiles are
        # np.float64) — coerce before FastAPI's encoder sees it.
        resp = {"status": "ok", "data": _jsonable(result)}
        if payload.get("offline"):
            resp["offline"] = True
        _save_engine_result("monte_carlo", resp)
        return resp
    except Exception as e:
        raise _server_error(e)


# ─── Step 3 — Equity Curve Gate ──────────────────────────────────────────────


@app.get("/api/ecg")
def equity_curve_gate(request: Request = None):  # noqa: B008
    try:
        try:
            payload = _context_payload(request)
        except RobinhoodOfflineError:
            # No live session AND no snapshot: degrade explicitly, never 503 into a
            # forever-spinner. ECG runs off the equity_snapshots table, so a snapshot
            # is normally present; this is the true-dark fallback.
            return {
                "status": "degraded",
                "data": {
                    "portfolio_gate": "UNKNOWN",
                    "portfolio_message": "Live data unavailable — ECG paused. Showing no live gate.",
                    "states": [],
                },
            }
        holdings = payload["holdings"]

        def history_fn(symbol: str):
            # Defense in depth (external audit 2026-07-18): the equity_snapshots table
            # accumulated corrupt rows (current_price=0, stale constant equity) from
            # degraded snapshots. compute_ecg reads MAX(series) as the peak, so one real
            # value among sentinels reads as a ~-96% drawdown → a false "SUSPENDED —
            # consider exiting" panic alarm. Drop non-observations here so a corrupt
            # symbol degrades to "insufficient history / HEALTHY", never a fake crash.
            return [r for r in get_equity_history(symbol, days=30) if _is_real_observation(r)]

        result = compute_ecg(holdings, history_fn)
        result.setdefault("portfolio_gate", "UNKNOWN")
        result.setdefault("portfolio_message", "")
        if payload.get("offline"):
            result["degraded"] = True
            result["portfolio_message"] = (
                result.get("portfolio_message") or ""
            ) + " (cached snapshot)"
        return {"status": "ok", "data": result}
    except Exception as e:
        raise _server_error(e)


# ─── Step 4 — Regime detection standalone ────────────────────────────────────


@app.get("/api/regime")
def regime_endpoint(force_live: bool = False, request: Request = None):  # noqa: B008
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
            from datetime import datetime as _rdt
            from datetime import timezone as _rtz

            age_s = (_rdt.now(_rtz.utc) - _rdt.fromisoformat(str(ran))).total_seconds()
            if age_s < 4 * 3600:
                return {"status": "ok", "data": cached, "cached": True, "ran_at": ran}
        except Exception:
            pass

    try:
        payload = _context_payload(request)
        symbols = [h["symbol"] for h in payload["holdings"]]
        result = detect_regimes(symbols)
        return {"status": "ok", "data": result}
    except RobinhoodOfflineError:
        # No live session AND (usually) a stale cache — serve the stale cache as a
        # degraded snapshot rather than 503-ing the whole Risk tab into an error.
        if cached and cached.get("regimes"):
            return {"status": "degraded", "data": cached, "cached": True, "ran_at": ran}
        return {
            "status": "degraded",
            "data": {"regimes": [], "regime_summary": "Live regime unavailable."},
        }
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
def market_data_endpoint(request: Request = None):  # noqa: B008
    try:
        payload = _context_payload(request)
        symbols = [h["symbol"] for h in payload["holdings"]]
        result = get_all_market_data(symbols)
        return {"status": "ok", "data": result}
    except Exception as e:
        raise _server_error(e)


# ─── Company Intelligence Agents ─────────────────────────────────────────────


@app.post("/api/agents/sec", dependencies=[Depends(require_api_key)])
def agent_sec():
    """Watch SEC filings for current holdings (last 7 days)."""
    try:
        payload = _resolved_payload()
        symbols = [h["symbol"] for h in payload["holdings"]]
        result = watch_filings(symbols, days_back=7)
        return {"status": "ok", "data": result}
    except Exception as e:
        raise _server_error(e)


@app.get("/api/agents/sec/history")
def agent_sec_history(days: int = 30, request: Request = None):  # noqa: B008
    """Return cached SEC filing analyses from DB."""
    try:
        payload = _context_payload(request)
        symbols = [h["symbol"] for h in payload["holdings"]]
        result = get_filing_history(symbols, days=days)
        return {"status": "ok", "data": result}
    except Exception as e:
        raise _server_error(e)


@app.post("/api/agents/news", dependencies=[Depends(require_api_key)])
def agent_news():
    """Scan news sentiment for current holdings + watchlist."""
    try:
        payload = _resolved_payload()
        symbols = list(
            {h["symbol"] for h in payload["holdings"]} | {w["symbol"] for w in payload["watchlist"]}
        )
        result = scan_news(symbols, days_back=7)
        return {"status": "ok", "data": result}
    except Exception as e:
        raise _server_error(e)


@app.get("/api/agents/news/history")
def agent_news_history(days: int = 7, request: Request = None):  # noqa: B008
    """Return cached news sentiment from DB."""
    try:
        payload = _context_payload(request)
        symbols = list(
            {h["symbol"] for h in payload["holdings"]} | {w["symbol"] for w in payload["watchlist"]}
        )
        rows = get_news_history(symbols, days=days)
        scores = [
            r["sentiment_score"] for r in rows if isinstance(r.get("sentiment_score"), (int, float))
        ]
        avg = round(sum(scores) / len(scores), 1) if scores else 0.0
        mood = "RISK_ON" if avg > 20 else "RISK_OFF" if avg < -20 else "NEUTRAL"
        return {
            "status": "ok",
            "data": {
                "results": rows,
                "avg_score": avg,
                "market_mood": mood,
                "cached": True,
            },
        }
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
            return {
                "status": "ok",
                "data": None,
                "message": "No scan yet — run POST /api/agents/fundamentals first",
            }
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
            raise HTTPException(
                status_code=503, detail="No screener data returned — market may be closed"
            )
        result = analyze_screener(candidates)
        resp = {
            "status": "ok",
            "data": result,
            "candidates_screened": len(candidates),
            "as_of": __import__("datetime")
            .datetime.now(__import__("datetime").timezone.utc)
            .isoformat(),
        }
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
        payload = _resolved_payload()
        holdings = payload["holdings"]
        total_equity = payload["equity_summary"].get("portfolio_equity", 0)
        symbols = [h["symbol"] for h in holdings]
        analyst_data = get_analyst_targets(symbols)
        analyst_upside = {
            s: {"upside_pct": analyst_data[s].get("upside_pct")} for s in analyst_data
        }

        ai_verdicts = {}
        try:
            from db import get_analysis_by_id, get_analysis_history

            history = get_analysis_history(limit=1)
            if history:
                latest_run = get_analysis_by_id(history[0]["id"])
                if latest_run and "result" in latest_run:
                    holdings_analysis = latest_run["result"].get("holdings_analysis", [])
                    for ha in holdings_analysis:
                        ai_verdicts[ha["symbol"]] = {
                            "verdict": ha.get("verdict"),
                            "pct_diff": fair_value.canonical_pct_diff(
                                ha.get("fair_value")
                            ),  # canonical, not the model pct_diff (Codex #2)
                        }
        except Exception as err:
            print(f"Failed to fetch AI verdicts for Kelly: {err}")

        result = run_kelly_analysis(holdings, total_equity, analyst_upside, ai_verdicts)
        # _jsonable: kelly leans on numpy and can emit np.bool_/np.float64 scalars
        # that FastAPI's encoder rejects ("'numpy.bool' object is not iterable" —
        # the QA-observed 500 of 2026-07-17). The customer path already coerces;
        # the owner endpoint must too.
        resp = {"status": "ok", "data": _jsonable(result), "total_equity": total_equity}
        if payload.get("offline"):
            resp["offline"] = True
        _save_engine_result("kelly", resp)
        return resp
    except Exception as e:
        raise _server_error(e)


# ─── Price Chart ─────────────────────────────────────────────────────────────

# Supported chart windows. Keys accept both the UI's yfinance-style spellings
# ("1mo") and the conventional UI labels ("1M", "1d"/"1D") — matching is
# case-insensitive. Anything not listed is a 400, never a silent substitution.
_CHART_RANGES: dict[str, dict] = {
    "1d": {"period": "1d", "yf_period": "1d", "interval": "5m"},
    "5d": {"period": "5d", "yf_period": "5d", "interval": "5m"},
    "1w": {"period": "5d", "yf_period": "5d", "interval": "5m"},
    "1mo": {"period": "1mo", "yf_period": "1mo", "interval": "1d"},
    "1m": {"period": "1mo", "yf_period": "1mo", "interval": "1d"},
    "3mo": {"period": "3mo", "yf_period": "3mo", "interval": "1d"},
    "3m": {"period": "3mo", "yf_period": "3mo", "interval": "1d"},
    "6mo": {"period": "6mo", "yf_period": "6mo", "interval": "1d"},
    "6m": {"period": "6mo", "yf_period": "6mo", "interval": "1d"},
    "1y": {"period": "1y", "yf_period": "1y", "interval": "1d"},
    "ytd": {"period": "ytd", "yf_period": "ytd", "interval": "1d"},
    "5y": {"period": "5y", "yf_period": "5y", "interval": "1wk"},
    "max": {"period": "max", "yf_period": "max", "interval": "1wk"},
}


@app.get("/api/chart/{symbol}")
def price_chart(symbol: str, period: str = "1mo", range: str | None = None):  # noqa: A002
    # 30s TTL cache — dashboard polls this per holding; without the cache
    # every refresh would spawn a fresh yfinance session + crumb fetch +
    # tz-cache file open, which is the dominant FD-leak path.
    sym = _safe_symbol(symbol)
    # `range` is the REST-conventional spelling and what external clients reach
    # for first. It used to be an unknown query param: FastAPI dropped it, the
    # default period served, and every one of 1D/5D/1M/3M/6M/1Y/max came back as
    # the same 21-point month (audit F-02). Unrecognized values are now a 400 —
    # `period_map.get(period, "1mo")` silently answered a DIFFERENT question than
    # the one asked, which for a price chart is a correctness bug, not leniency.
    requested = range if range is not None else period
    resolved = _CHART_RANGES.get(str(requested).strip().lower())
    if resolved is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Unsupported range '"
                + str(requested)[:16]
                + "'. Supported: "
                + ", ".join(sorted(_CHART_RANGES))
            ),
        )
    period = resolved["period"]
    key = (sym, period)
    now = time.monotonic()
    with _chart_cache_lock:
        hit = _chart_cache.get(key)
        if hit and (now - hit[0]) < CHART_TTL_SEC:
            return {**hit[1], "requested_range": str(requested)}

    try:
        # price_source, not raw yfinance: under the VPN finance-block Yahoo
        # returns nothing and the dashboard chart rendered BLANK. Daily ranges use
        # the RH-fallback-backed yf_download; intraday 1d/5d needs 5-minute bars,
        # which Yahoo/RH-unauth can't serve here — route those to Alpaca via
        # price_source.intraday_5m (key-gated; honest fallback when unconfigured).
        import price_source

        yf_period = resolved["yf_period"]
        interval = resolved["interval"]
        if period in ("1d", "5d"):
            raw = price_source.intraday_5m(sym, period)
            if raw is None or getattr(raw, "empty", True):
                # No intraday provider configured / no data → honest empty state;
                # the frontend renders "try a daily range". Cache it briefly too.
                result = {
                    "symbol": sym,
                    "period": period,
                    "intraday_unavailable": True,
                    "dates": [],
                    "prices": [],
                    "pct_change": 0,
                    "current": None,
                    "start": None,
                    "resolved_period": yf_period,
                    "resolved_interval": interval,
                    "point_count": 0,
                    "first_timestamp": None,
                    "last_timestamp": None,
                    "source": None,
                }
                with _chart_cache_lock:
                    _chart_cache[key] = (now, result)
                return {**result, "requested_range": str(requested)}
        else:
            raw = price_source.yf_download(
                sym, period=yf_period, interval=interval, auto_adjust=True, progress=False
            )
            if raw is None or raw.empty:
                raise HTTPException(status_code=404, detail="No data")
        closes = raw["Close"].squeeze().dropna()
        dates = [str(d)[:16] if interval == "5m" else str(d)[:10] for d in closes.index]
        # .flatten() ensures 1D array if yf returns MultiIndex dataframe,
        # handle Series or DataFrame robustly
        import numpy as np

        prices = [round(float(v), 2) for v in np.array(closes).flatten()]
        pct_chg = round((prices[-1] / prices[0] - 1) * 100, 2) if len(prices) > 1 else 0
        try:
            source = price_source.frame_source(raw)
        except Exception:  # noqa: BLE001 — provenance is metadata, never a 500
            source = None
        result = {
            "symbol": sym,
            "period": period,
            "dates": dates,
            "prices": prices,
            "pct_change": pct_chg,
            "current": prices[-1],
            "start": prices[0],
            # Contract metadata: a client must be able to VERIFY it got the
            # window it asked for instead of trusting that it did.
            "resolved_period": yf_period,
            "resolved_interval": interval,
            "point_count": len(prices),
            "first_timestamp": dates[0] if dates else None,
            "last_timestamp": dates[-1] if dates else None,
            "source": source,
            "data_as_of": _dt.now(_tz.utc).isoformat(),
        }
        with _chart_cache_lock:
            _chart_cache[key] = (now, result)
        return {**result, "requested_range": str(requested)}
    except HTTPException:
        raise
    except Exception as e:
        raise _server_error(e)


# ─── Consensus Engine ────────────────────────────────────────────────────────


@app.post("/api/consensus", dependencies=[Depends(require_api_key)])
def consensus_endpoint():
    try:
        payload = _resolved_payload()
        holdings = payload["holdings"]
        total_equity = payload["equity_summary"].get("portfolio_equity", 0)
        result = run_consensus_portfolio(holdings, total_equity)
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
    if (
        key in ("consensus", "kelly")
        and isinstance(val, dict)
        and "holdings" not in val
        and "data" not in val
    ):
        # The daily chain (run_engines2) stores these as per-symbol dicts;
        # the dashboard renderers expect the POST shape {holdings: [...]} —
        # normalize here so the Signals tab renders the 10:30 results.
        val = {
            "holdings": [
                {"symbol": s, **(v if isinstance(v, dict) else {})} for s, v in val.items()
            ]
        }
    out = (
        dict(val)
        if isinstance(val, dict) and ("data" in val or "analysis" in val)
        else {"data": val}
    )
    out["cached"] = True
    out["ran_at"] = ran
    out["fresh"] = _is_fresh(ran)
    return _sanitize(out)


@app.get("/api/deep-analysis/cached", dependencies=[Depends(require_customer_or_owner)])
def deep_analysis_cached():
    return _cached_response("ai_result")


@app.get("/api/consensus/cached", dependencies=[Depends(require_customer_or_owner)])
def consensus_cached():
    return _cached_response("consensus")


# ── Unified unknown / unavailable-symbol contract (GATE 2) ───────────────────
# Per-symbol engines never emit a fabricated success for a symbol with no data.
# Two explicit shapes, and only two:
#   * HTTP 404                       — no such instrument / no coverage
#                                      (analyst, fundamentals, chart, analyze-ticker)
#   * {"status": "unavailable", …}   — a well-formed request with no substantive
#                                      data to compute from (consensus, regime,
#                                      momentum, fft-cycles, levels, earnings, and
#                                      news for a symbol with no market identity)
# `reason_code` separates the two failure modes the backend must distinguish:
#   symbol_not_found  — the symbol has no market identity (no price anywhere)
#   data_unavailable  — the symbol is real, but THIS engine's inputs are missing
#                       right now (a provider outage — CASE B, not a bad ticker)
# The envelope never carries status "ok" and never stamps a timestamp onto data
# that does not exist. A genuinely NEUTRAL result for a real symbol (CASE C) has
# real values and does not trip any engine's unavailability marker, so it flows
# through untouched as status "ok".


def _symbol_has_market_identity(sym: str) -> bool:
    """Cheap cross-engine "does this ticker exist" probe: a real symbol has a
    price. Cached, so the unavailable branch adds at most one lookup (usually a
    cache hit from the engine's own failed attempt). Fail-closed — any error is
    treated as no identity, yielding the more conservative symbol_not_found."""
    try:
        return bool(_cached_close(sym))
    except Exception:  # noqa: BLE001
        return False


def _unavailable_response(sym: str, *, data: object = None) -> dict:
    """Canonical unavailable envelope (never status "ok", never a fake timestamp).
    The engine's own unavailability payload is retained under `data` for
    back-compat, so existing readers of data.available / data.error / data.regime
    keep working and the frontend degrades exactly as before."""
    reason = "data_unavailable" if _symbol_has_market_identity(sym) else "symbol_not_found"
    body = {
        "status": "unavailable",
        "reason_code": reason,
        "symbol": sym,
        "data_as_of": None,
        "source": None,
    }
    if data is not None:
        body["data"] = data
    return body


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

        row = run_consensus_holding(
            sym, equity=0.0, total_portfolio=1.0, avg_cost=0.0, current_price=0.0
        )
        # No node could vote (no price/history) → INSUFFICIENT DATA, not a
        # fabricated balanced consensus.
        if (
            not isinstance(row, dict)
            or row.get("verdict") == "INSUFFICIENT DATA"
            or (row.get("active_nodes") or 0) == 0
        ):
            return _unavailable_response(sym, data=row)
        return {"status": "ok", "data": row}
    except HTTPException:
        raise
    except Exception as e:
        raise _server_error(e)


@app.get("/api/kelly/cached", dependencies=[Depends(require_customer_or_owner)])
def kelly_cached():
    return _cached_response("kelly")


@app.get("/api/monte-carlo/cached", dependencies=[Depends(require_customer_or_owner)])
def monte_carlo_cached():
    return _cached_response("monte_carlo")


@app.get("/api/screener/cached", dependencies=[Depends(require_customer_or_owner)])
def screener_cached():
    return _cached_response("screener")


# ─── Long-Term Macro Overlay (VIX + Fear & Greed + Margin Debt) ───────────────
# Visible surface for Proposal 1 long-term (1-3y) sentiment bias features.
# Two layers: long_term_macro caches the raw upstream fetches (6h/24h files); the
# serve-side cache below lets /cached repaint INSTANTLY from memory without
# re-entering the module, while the live route always recomputes + refreshes it.
_LTM_TTL_SEC = 900  # 15-min serve cache
_ltm_cache: dict[str, tuple[float, dict]] = {}
_ltm_lock = threading.Lock()


def _ltm_compute() -> dict:
    """Compute the overlay (module-cached upstream) and refresh the serve cache."""
    from long_term_macro import get_long_term_macro_sentiment

    data = _sanitize(get_long_term_macro_sentiment())
    with _ltm_lock:
        _ltm_cache["v"] = (time.monotonic(), data)
    return data


@app.get("/api/long-term-macro")
def long_term_macro():
    """Live compute — always refreshes the serve cache."""
    try:
        return {"status": "ok", "data": _ltm_compute()}
    except Exception as e:
        raise _server_error(e)


@app.get("/api/long-term-macro/cached")
def long_term_macro_cached():
    """Instant paint from the serve cache; computes once on a cold/expired entry."""
    try:
        with _ltm_lock:
            hit = _ltm_cache.get("v")
        cached = bool(hit and (time.monotonic() - hit[0]) < _LTM_TTL_SEC)
        data = hit[1] if cached else _ltm_compute()
        out = {"status": "ok", "data": data, "cached": cached}
        if isinstance(data, dict) and data.get("fetched_at"):
            out["ran_at"] = data["fetched_at"]
        return out
    except Exception as e:
        raise _server_error(e)


@app.get("/api/portfolio/history", dependencies=[Depends(require_api_key)])
def portfolio_history(points: int = Query(default=60, ge=1, le=365)):
    """Portfolio equity series for the KPI sparklines. Always returns a list."""
    try:
        from db import get_portfolio_equity_history

        hist = get_portfolio_equity_history(points)
        series = [
            round(float(h["total_equity"]), 2) for h in hist if h.get("total_equity") is not None
        ]
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

    snap = data.get("snapshot") if isinstance(data.get("snapshot"), dict) else {}
    snap_at = snap.get("fetched_at")
    overview = (
        {"ran_at": snap_at, "fresh": _is_fresh(snap_at)}
        if snap_at
        else {"ran_at": None, "fresh": False}
    )

    out = {
        "overview": overview,
        "analysis": _stamp("ai_result"),
        "daily": _stamp("ai_result"),
        "risk": _stamp("monte_carlo"),
        "radar": _stamp("screener"),
        "intelligence": _stamp("regime"),
        "signals": sig,
    }
    return {k: v for k, v in out.items() if v is not None}


# ─── FFT Cycle Detection ──────────────────────────────────────────────────────


@app.get("/api/fft-cycles")
def fft_cycles_endpoint(request: Request = None):  # noqa: B008
    """
    Detect dominant price cycles via FFT for all current holdings.
    Returns period, phase position, direction, and BULLISH/NEUTRAL/BEARISH signal
    per holding. Zero extra cost — pure math on yfinance price data.
    """
    try:
        payload = _context_payload(request)
        symbols = [h["symbol"] for h in payload["holdings"]]
        result = batch_fft_cycles(symbols)
        return {"status": "ok", "data": result}
    except Exception as e:
        raise _server_error(e)


@app.get("/api/fft-cycles/{symbol}")
def fft_cycles_single(symbol: str):
    """FFT cycle analysis for a single symbol."""
    try:
        from fft_cycles import detect_fft_cycles

        sym = _safe_symbol(symbol)
        result = detect_fft_cycles(sym)
        if not isinstance(result, dict) or result.get("error"):
            return _unavailable_response(sym, data=result)
        return {"status": "ok", "data": result}
    except Exception as e:
        raise _server_error(e)


@app.get("/api/symbol-search")
def symbol_search(q: str = "", limit: int = 10):
    """Company-name / ticker typeahead for the Analyze box. Returns [{symbol,name}]
    matches (public reference data — no LLM, no owner data). Universe loaded from
    Alpaca once and cached 24h in-memory, so this is a cheap in-memory match."""
    try:
        import symbol_search as _ss

        return {"status": "ok", "data": _ss.search(q, limit)}
    except Exception as e:
        raise _server_error(e)


@app.get("/api/momentum/{symbol}")
def momentum_symbol(symbol: str):
    """Momentum buy/sell signal (RSI + MACD + Volume, combined) for ANY ticker.
    Pure price math on public daily OHLCV — no LLM, no owner data."""
    sym = _safe_symbol(symbol)
    cached = _ticker_get_cached("momentum", sym)
    if cached:
        return cached
    try:
        import momentum_signal

        mom = momentum_signal.compute(sym)
        if isinstance(mom, dict) and mom.get("available") is False:
            return _unavailable_response(sym, data=mom)
        return _ticker_get_store("momentum", sym, {"status": "ok", "data": mom})
    except Exception as e:
        raise _server_error(e)


@app.get("/api/levels/{symbol}")
def levels_symbol(symbol: str):
    """Support/resistance levels + a long trade-setup (entry/stop/target, R:R) for
    ANY ticker. Pure price math on public daily OHLCV — no LLM, no owner data."""
    sym = _safe_symbol(symbol)
    cached = _ticker_get_cached("levels", sym)
    if cached:
        return cached
    try:
        import trade_levels

        # Bind the trade-setup ENTRY (and the header quote) to the SAME canonical
        # quote the chart / AI card / portfolio table use (_latest_close — the
        # bad-tick-guarded 1mo last close), instead of trade_levels' independent 6mo
        # last bar. This eliminates the "$396.18 vs $394.76" divergence (GESH-1).
        # On a data outage _latest_close is None → compute falls back to its own series.
        px, pmeta = _latest_close_meta(sym)
        data = trade_levels.compute(sym, current_price=px)
        # No price → no support/resistance to compute. Return unavailable rather
        # than a shaped card of nulls (audit: no fabricated levels).
        if isinstance(data, dict) and data.get("available") is False:
            return _unavailable_response(sym, data=data)
        # A setup grounded on a cross-source-suspect quote must never look tradeable
        # (Codex #3 / GESH-9). Strict mode refuses outright; the warn-only default
        # returns the levels but flags the setup non-actionable, which the dashboard
        # already renders as a red "no actionable setup" banner (GESH-2).
        suspect, reason = _price_quality(sym, px) if px is not None else (False, "")
        if suspect:
            if price_sanity.strict_reject_enabled():
                raise HTTPException(
                    status_code=422,
                    detail=f"Price rejected as unreliable — {reason}. "
                    "Strict price-quality mode is on (GESH_REJECT_SUSPECT_PRICES).",
                )
            if isinstance(data, dict):
                data["price_suspect"] = True
                data["price_suspect_reason"] = reason
                if isinstance(data.get("setup"), dict):
                    data["setup"]["actionable"] = False
                    data["setup"]["warning"] = f"Quote quality is suspect — {reason}"
        # Unified degraded-decision block (Codex #6): one consistent shape for the
        # dashboard, logs, and audit — no data (px None) is itself a degraded input.
        # data_sources carries the feed that ACTUALLY served the bars (read off the
        # frame), not a constant — see _decision_sources.
        decision_quality.attach(
            data,
            _quality_block(pmeta, suspect, reason, degraded=px is None),
            context=f"levels:{sym}",
        )
        # The levels card's feed is the one that served the quote — already observed
        # off the frame (GESH-8 uses the same truth as decision_quality, never a label).
        resp = {"status": "ok", "data": data}
        if pmeta.get("source"):
            resp["source"] = pmeta["source"]
        return _ticker_get_store("levels", sym, resp)
    except HTTPException:
        raise
    except Exception as e:
        raise _server_error(e)


@app.get("/api/earnings/{symbol}")
def earnings_symbol(symbol: str):
    """Earnings history (EPS actual vs estimate, beat/miss) for ANY ticker — the
    visual deep-analysis data. Finnhub primary (VPN-safe), yfinance fallback. No
    LLM, no owner data. Cached 5 min."""
    sym = _safe_symbol(symbol)
    cached = _ticker_get_cached("earnings", sym)
    if cached:
        return cached
    try:
        import earnings_viz

        data = earnings_viz.get_earnings(sym)
        # `diagnostic` names providers and env vars — operator detail. Log it,
        # never ship it (audit BUG-05: internal config surfaced in customer copy).
        diag = data.pop("diagnostic", None)
        if diag:
            logger.info("earnings unavailable for %s: %s", sym, diag)
        if isinstance(data, dict) and data.get("available") is False:
            return _unavailable_response(sym, data=data)
        return _ticker_get_store("earnings", sym, {"status": "ok", "data": data})
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
        row0 = rows[0]
        if not isinstance(row0, dict) or str(row0.get("regime", "")).upper() == "UNKNOWN":
            return _unavailable_response(sym, data=row0)
        return _ticker_get_store("regime", sym, {"status": "ok", "data": row0})
    except HTTPException:
        raise
    except Exception as e:
        raise _server_error(e)


# Keys that merely ECHO the request rather than describe the company. yfinance
# fills `company_name` with the ticker itself for symbols it has never heard of,
# so these carry no evidence that the vendor knows the security.
_FUNDAMENTAL_IDENTITY_KEYS = frozenset(
    ("symbol", "company_name", "name", "sector_source", "error", "source", "as_of")
)

_FUNDAMENTAL_ZERO_DEFAULT_KEYS = frozenset(("insider_buys", "insider_sells", "num_analysts"))


def _fundamentals_value_is_empty(key: str, value: object) -> bool:
    """True for absent values and the adapter's known no-coverage defaults."""
    if value in (None, "", [], {}):
        return True
    if isinstance(value, str):
        normalized = value.strip().lower()
        return (key == "recommendation" and normalized == "hold") or (
            key == "insider_signal" and normalized == "neutral"
        )
    return key in _FUNDAMENTAL_ZERO_DEFAULT_KEYS and not isinstance(value, bool) and value == 0


def _fundamentals_row_is_empty(row: dict) -> bool:
    """True when a vendor answered with a shape but no facts.

    Deliberately structural rather than an allow-list of metric names: vendors
    disagree on field names (`pe_ratio` vs `trailing_pe`) and add new ones, and
    a name list that falls behind starts 404-ing real companies. Any field with
    a value that is neither request-echo nor a known adapter default counts as
    knowledge."""
    if not isinstance(row, dict):
        return True
    return not any(
        not _fundamentals_value_is_empty(k, v)
        for k, v in row.items()
        if k not in _FUNDAMENTAL_IDENTITY_KEYS
    )


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

        # Track WHICH vendor actually served this row. `sector` is a vendor
        # CLASSIFICATION from a defined taxonomy (Robinhood's is non-GICS — it calls
        # TSLA "Consumer Durables"), not an absolute truth, and the two vendors do not
        # agree. The branch is known right here, so the source is reported truthfully
        # rather than guessed (GESH-6).
        row = market_data.rh_fundamentals(sym)
        vendor = "robinhood"
        if not row:
            import fundamental_agent

            rows = fundamental_agent._fetch_fundamentals_batch([sym])
            row = rows[0] if rows and not rows[0].get("error") else None
            vendor = "yfinance"
        if not row or _fundamentals_row_is_empty(row):
            # yfinance echoes the ticker back as `company_name` with every metric
            # null for symbols it does not know, which rendered as a real-looking
            # fundamentals card sourced "yfinance · just now" (audit BUG-05 / J).
            raise HTTPException(status_code=404, detail="No fundamentals for this symbol")
        if row.get("sector"):
            row["sector_source"] = vendor
        # Cross-source sanity: the 52-wk range here is RH-sourced; compare it to the
        # (differently-sourced) price feed. A price implausibly outside the band flags
        # a whole-series feed error the median bad-tick guard can't see (e.g. NFLX
        # ~$73 vs a ~$500+ low). _cached_close is the shared 5-min cache — cheap.
        try:
            import price_sanity

            px = _cached_close(sym)
            suspect, reason = price_sanity.is_price_suspect(
                px, row.get("low_52"), row.get("high_52")
            )
            if suspect:
                row["price_suspect"] = True
                row["price_suspect_reason"] = reason
        except Exception:  # noqa: BLE001 — advisory flag must never break the card
            pass
        # Real feed, not a constant: `vendor` is the branch that actually served (GESH-8).
        return _ticker_get_store(
            "fundamentals", sym, {"status": "ok", "data": row, "source": vendor}
        )
    except HTTPException:
        raise
    except Exception as e:
        raise _server_error(e)


def _reconcile_card(sym: str, card: dict) -> dict | None:
    """Combine this card's valuation opinion with the latest MECHANICAL verdict.

    The mechanical reading is taken from the recorded signal history rather than
    by re-running the 11-node consensus: the engine run costs seconds per symbol
    and this endpoint is already the expensive one. A symbol with no recorded
    verdict simply reconciles on the valuation side alone — which is honest, and
    still better than printing two unrelated conclusions.

    Fail-soft by contract: this block is presentation, so a missing table or a
    malformed row must degrade to `None`, never to a 500 on a paid LLM call."""
    try:
        mech_verdict = mech_conf = None
        try:
            import signal_history

            hist = signal_history.get_history(sym, days=1)
            if hist:
                mech_verdict = hist[-1].get("verdict")
                mech_conf = hist[-1].get("confidence")
        except Exception:  # noqa: BLE001 — optional context
            pass
        fv = card.get("fair_value") or {}
        return verdict_reconciliation.reconcile(
            verdict_reconciliation.Inputs(
                mechanical_verdict=mech_verdict,
                mechanical_confidence=(float(mech_conf) if mech_conf is not None else None),
                valuation_status=fv.get("status"),
                valuation_pct_diff=fair_value.canonical_pct_diff(fv) if fv else None,
                ai_verdict=card.get("verdict"),
            )
        ).as_dict()
    except Exception:  # noqa: BLE001
        return None


def _analyst_row_is_empty(row: dict) -> bool:
    """True when a provider answered but carried NO actual coverage.

    yfinance returns a fully-shaped row for symbols it has never heard of, with
    `recommendation` defaulted to "Hold" and every number null. Rendered, that
    reads exactly like a real Hold consensus. A row counts as coverage only if
    at least one analyst or one price target is actually present."""
    if not isinstance(row, dict):
        return True
    if row.get("num_analysts"):
        return False
    return not any(
        row.get(k) is not None
        for k in ("target_mean", "target_median", "target_high", "target_low")
    )


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
        vendor = "robinhood"
        if not row:
            data = market_data.get_analyst_targets([sym]) or {}
            yf_row = data.get(sym)
            row = yf_row if (yf_row and not yf_row.get("error")) else None
            vendor = "yfinance"
        if not row or _analyst_row_is_empty(row):
            # An unknown/uncovered ticker used to come back 200 with
            # `recommendation: "Hold", num_analysts: 0` — a fabricated neutral
            # consensus indistinguishable from a real one (audit BUG-05 / J).
            # No coverage is a 404, not a Hold.
            raise HTTPException(status_code=404, detail="No analyst coverage for this symbol")
        # Real feed, not a constant (GESH-8).
        return _ticker_get_store("analyst", sym, {"status": "ok", "data": row, "source": vendor})
    except HTTPException:
        raise
    except Exception as e:
        raise _server_error(e)


@app.get("/api/news/{symbol}")
def news_symbol(symbol: str):
    """Recent news HEADLINES for ANY ticker (RSS/Alpaca, NO LLM — the customer
    path never triggers a live sentiment run, so it stays zero-cost). Returns the
    trimmed top-8 items (title/url/source/published); [] when none.

    Since plan T4 the response also carries an intentionally CUSTOMER-VISIBLE
    `sentiment` chip block — read from the cached news_sentiment table only
    (never a live LLM call), allow-listed to score/mood/article_count/as_of,
    gated server-side on article_count>0 (never fabricate sentiment over zero
    articles), and never shipping the raw analysis text."""
    sym = _safe_symbol(symbol)
    cached = _ticker_get_cached("news", sym)
    if cached:
        return cached
    try:
        import news_agent

        # Alpaca first (VPN-safe, key-gated); RSS fallback (blocked on the owner's VPN).
        # Split the `or` chain so the feed that ACTUALLY served is observable (GESH-8)
        # rather than inferred — Alpaca first (VPN-safe), RSS fallback.
        items = news_agent._fetch_alpaca_news(sym)
        feed = "alpaca" if items else None
        if not items:
            items = news_agent._fetch_all_news(sym)
            feed = "rss" if items else None
        if not items:
            # Cached-article fallback (Recent News card fix): both live feeds are
            # routinely empty on the owner box (Alpaca is key-gated, the RSS feeds
            # are VPN-blocked), which left the card permanently blank. Serve the
            # news agent's stored articles instead — a read-only DB lookup, no LLM,
            # already allow-listed to the same 4 public headline fields.
            try:
                items = news_agent.get_cached_articles(sym)
                feed = "cache" if items else None
            except Exception:  # noqa: BLE001 — fallback is best-effort, never a 500
                items = []
                feed = None
        items = items or []
        trimmed = [
            {
                "title": it.get("title", ""),
                "url": it.get("url", ""),
                "source": it.get("source", ""),
                "published": it.get("published", ""),
            }
            for it in items[:8]
        ]
        # Empty news for a REAL symbol is legitimate ("no recent news"); empty
        # news for a symbol with no market identity is an unknown ticker. Only the
        # latter is unavailable — news availability is not proof a symbol exists.
        if not trimmed and not _symbol_has_market_identity(sym):
            return _unavailable_response(sym, data=[])
        resp = {"status": "ok", "data": trimmed}
        if feed:  # omitted entirely when nothing served
            resp["source"] = feed
        # News-as-sentiment chip (plan T4): attach the CACHED sentiment row — a
        # zero-cost DB read, never a live LLM call, so the customer path stays
        # free. Allow-listed sub-keys only (see tests/test_news_public_contract).
        # Shipped ONLY when the cached row analyzed at least one article:
        # sentiment over zero articles is fabricated by definition (project
        # rule "never fabricate sentiment when article_count=0").
        try:
            cached_row = news_agent._fresh_sentiment(sym)
            if cached_row and (cached_row.get("article_count") or 0) > 0:
                resp["sentiment"] = {
                    "score": cached_row.get("sentiment_score", 0),
                    "mood": cached_row.get("overall_sentiment", "NEUTRAL"),
                    "article_count": cached_row.get("article_count", 0),
                    "as_of": cached_row.get("as_of"),
                }
        except Exception:  # noqa: BLE001 — the chip is optional context, never a 500
            pass
        return _ticker_get_store("news", sym, resp)
    except HTTPException:
        raise
    except Exception as e:
        raise _server_error(e)


@app.get("/api/signals/history/{symbol}", dependencies=[Depends(require_customer_or_owner)])
def signals_history(symbol: str, request: Request):
    """30-day consensus verdict/score series for the card sparkline (feature 3).
    Mechanical read of signal_history — no LLM, no quota. Session-gated (same
    require_customer_or_owner as the ticker card) since it is customer-facing.
    Customers may only read symbols in their own watchlist or book; owners retain
    access to the shared history. Empty history for an entitled symbol is a normal
    answer, never a 500."""
    sym = _safe_symbol(symbol)
    if auth_users.auth_enabled():
        email = _session_email(request)
        if not email:
            raise HTTPException(status_code=401, detail="Authentication required")
        if not auth_users.is_owner(email):
            own_symbols = set(auth_users.get_watch(email))
            own_symbols.update(position["symbol"] for position in auth_users.get_positions(email))
            if sym not in own_symbols:
                raise HTTPException(
                    status_code=403, detail="Symbol is not in your watchlist or book"
                )
    cached = _ticker_get_cached("sighist", sym)
    if cached:
        return cached
    try:
        import signal_history

        data = {"symbol": sym, "history": signal_history.get_history(sym, days=30)}
        return _ticker_get_store("sighist", sym, {"status": "ok", "data": data})
    except HTTPException:
        raise
    except Exception as e:
        raise _server_error(e)


# Close-price lookup lives in price_cache.py so cron consumers (run_weekly_digest)
# can price a book without importing this app. Re-exported here — with thin
# module-global wrappers below — so the historical monkeypatch seams
# (server._latest_close_meta / server._latest_close / server._cached_close)
# keep steering behavior exactly as before the extraction.
_latest_close_meta = price_cache._latest_close_meta


def _latest_close(sym: str) -> float | None:
    """VPN-safe latest daily close for grounding the single-ticker research card —
    reuses the chart's RH-fallback-backed price_source path (not raw yfinance).
    Returns None when the ticker has no data, so the caller 404s rather than let
    the model fabricate a price. Resolves the module-global _latest_close_meta at
    call time so patching that seam still steers this wrapper."""
    return _latest_close_meta(sym)[0]


# ── Per-customer daily quotas on the LLM-spend surfaces ────────────────────────
# Metering (#22) counts; this enforces. Rolling-24h caps on LIVE Claude calls only
# — cached hits are free and never consume quota. The OWNER is exempt. 0 disables
# a cap. The base caps are env-tunable; the billing tier (set on provision by the
# Lemon Squeezy webhook) multiplies them — the $399 Trading Desk tier gets 5×
# without touching env or code. Unknown/NULL tier = base (the alpha default).
_QUOTA_DEFAULTS = {"ticker_card": 25, "book_analysis": 10}
_TIER_QUOTA_MULT = {"observer": 1, "trader": 2, "desk": 5}


def _customer_quota_limit(email: str | None, event: str) -> int:
    """Return the tier-scaled limit; zero means unlimited/exempt."""
    if not email or auth_users.is_owner(email):
        return 0
    base = int(os.getenv(f"QUOTA_DAILY_{event.upper()}", _QUOTA_DEFAULTS.get(event, 0)))
    if base <= 0:
        return 0
    return base * _TIER_QUOTA_MULT.get(auth_users.get_tier(email), 1)


def _check_customer_quota(email: str | None, event: str) -> None:
    """Raise 429 when the user's rolling-24h count of `event` has hit their
    tier-scaled cap.

    FAILS CLOSED (503) on a metering read error: this gates PAID Claude spend, so
    an unverifiable quota must DENY the spend, never grant an uncapped one — the
    old fail-open let a locked/corrupt usage table turn into unbounded API cost
    (the only other backstop, LLM_DAILY_BUDGET_USD, is off by default). Cached
    results still serve free, so a transient 503 does not blank the product."""
    try:
        limit = _customer_quota_limit(email, event)
        if limit <= 0:
            return
        used = auth_users.usage_count_24h(email, event)
    except Exception:
        logger.warning(
            "quota metering unavailable (%s/%s) — denying spend (fail-closed)", email, event
        )
        raise HTTPException(
            status_code=503, detail="Usage metering temporarily unavailable — please retry shortly."
        )
    if used >= limit:
        raise HTTPException(
            status_code=429,
            detail=f"Daily AI analysis limit reached ({limit}/24h) — cached results "
            f"remain free; the limit resets on a rolling 24-hour window.",
        )


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


def _price_quality(sym: str, price) -> tuple[bool, str]:
    """(suspect, reason) — cross-check a grounding price against the symbol's 52-wk
    range (RH fundamentals — a DIFFERENT feed than the price). Codex #3: so a
    whole-series feed error doesn't silently drive the AI card / valuation while it's
    only flagged for display on the fundamentals card. Advisory; (False, "") on error."""
    try:
        import price_sanity

        cached = _ticker_get_cached("fundamentals", sym)
        row = (cached or {}).get("data") if cached else None
        if not row:
            import market_data

            row = market_data.rh_fundamentals(sym) or {}
        return price_sanity.is_price_suspect(price, row.get("low_52"), row.get("high_52"))
    except Exception:  # noqa: BLE001 — advisory flag, never break the card
        return False, ""


def _decision_sources(quote_source: str | None, *extra: str) -> list[str]:
    """The feeds that DEMONSTRABLY served this decision — never a guess.

    `quote_source` is read off the frame that produced the quote
    (price_source.frame_source), so it names the feed that actually ran: "yahoo",
    "robinhood", or both on a splice. An UNATTRIBUTABLE quote contributes nothing
    rather than a default feed name — naming a feed we didn't observe would be exactly
    the fabrication this field exists to surface.

    Note: distinct from `_ticker_get_store`'s `source`, which is FRESHNESS
    (live-vs-cached, GESH-4), not feed identity. Do not conflate the two.
    """
    feeds: list[str] = [f for f in (quote_source or "").split("+") if f]
    for e in extra:
        if e and e not in feeds:
            feeds.append(e)
    return feeds


def _quality_block(
    pmeta: dict, suspect: bool, reason: str, *, degraded: bool = False, as_of=None
) -> dict:
    """decision_quality.build() fed with REAL provenance (Codex #6).

    A decision is degraded when we have a concrete reason to distrust the number:
    a cross-source-suspect quote, a silent bad-tick substitution, an unattributable
    source, or the caller's own reason. Being served by the Robinhood FALLBACK is NOT
    degraded on its own — RH is a legitimate feed, not a defect.
    """
    src = pmeta.get("source")
    substituted = bool(pmeta.get("substituted"))
    # rh_fundamentals_52w is named only when the 52-wk cross-check actually ran.
    sources = _decision_sources(src, "rh_fundamentals_52w" if reason or suspect else "")
    block = decision_quality.build(
        price_suspect=suspect,
        reason=reason,
        degraded=bool(degraded or substituted or not src),
        data_sources=sources,
        as_of=as_of,
    )
    if substituted:
        block["reasons"].append(
            "latest quote rejected as a bad tick — a prior in-band close was "
            "substituted, so this is not the last traded price"
        )
    if not src:
        block["reasons"].append("price feed unattributable — source could not be verified")
    return block


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
        return {"status": "ok", "data": card, "cached": True}
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
                return {"status": "ok", "data": card, "cached": True}
            # Quota gate sits AFTER the cache re-check (cached hits are free) and
            # BEFORE any spend — a capped user costs nothing, not even the quote.
            _check_customer_quota(_session_email(request), "ticker_card")
            # Ground the valuation on a REAL quote: no price = unknown ticker / data
            # outage → 404 rather than let the model fabricate a price and card.
            price, pmeta = await asyncio.to_thread(_latest_close_meta, sym)
            if price is None:
                raise HTTPException(status_code=404, detail="No market data for this ticker")
            # Cross-source price quality is checked BEFORE the spend so strict mode can
            # refuse to build a decision on a broken feed at zero LLM cost (Codex #3).
            suspect, reason = await asyncio.to_thread(_price_quality, sym, price)
            if suspect and price_sanity.strict_reject_enabled():
                raise HTTPException(
                    status_code=422,
                    detail=f"Price rejected as unreliable — {reason}. "
                    "Strict price-quality mode is on (GESH_REJECT_SUSPECT_PRICES).",
                )
            card = await asyncio.to_thread(deep_analyze_ticker, sym, price)
            # Warn-only default: mark the DECISION output when the grounding quote is
            # cross-source-suspect — the card was built on this price, so the user must see it.
            if suspect and isinstance(card, dict):
                card["price_suspect"] = True
                card["price_suspect_reason"] = reason
            # Stamp when the AI card was generated so the UI can tag it "AI-GENERATED
            # · <time>" and never let it hide under a global "LIVE" banner (GESH-4/5).
            if isinstance(card, dict):
                card["generated_at"] = _dt.now(_tz.utc).isoformat()
                # Unified degraded-decision block (Codex #6): same shape the /api/levels
                # setup and the dashboard/audit read, plus a structured degraded log.
                decision_quality.attach(
                    card,
                    _quality_block(pmeta, suspect, reason, as_of=card["generated_at"]),
                    context=f"analyze-ticker:{sym}",
                )
                # Provenance strip (feature 2): a compact, ALWAYS-present block the
                # UI renders under every card — timestamp, model, price feed, and
                # the suspect flag so trust signals never depend on parsing prose.
                card["provenance"] = {
                    "generated_at": card.get("generated_at"),
                    "price_source": pmeta.get("source"),
                    "price_as_of": card.get("generated_at"),
                    "model": MODEL,
                    "suspect": bool(suspect),
                    "suspect_reason": reason if suspect else None,
                }
                # ONE stance, with the disagreement stated. The mechanical verdict
                # and this card's valuation opinion used to be rendered side by
                # side with equal weight — "STRONG BUY" next to "HOLD, overvalued
                # −18.1%" for the same symbol off the same snapshot (audit BUG-03).
                card["reconciliation"] = _reconcile_card(sym, card)
            _ticker_card_store(sym, card)
            auth_users.log_usage(_session_email(request), "ticker_card", sym)
            return {"status": "ok", "data": card, "cached": False}
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
    book re-reads every symbol, so repeat views must not re-fetch upstream.
    Delegates to price_cache but passes THIS module's _latest_close, resolved at
    call time, so tests monkeypatching server._latest_close keep steering it."""
    return price_cache._cached_close(sym, fetch=_latest_close)


def _prior_close(sym: str) -> float | None:
    """Previous-session close — the denominator of day change. Module-level so
    tests can stub it the same way they stub _latest_close. Fail-soft: None
    disables day change for the book (never silently reports a flat day)."""
    try:
        return price_cache.prior_close(sym)
    except Exception:  # noqa: BLE001 — a pricing hiccup must not 500 the book
        return None


def _customer_book_payload(positions: list[dict], equal_weight: bool = False) -> dict:
    """Value the customer's positions live and shape them EXACTLY like the owner
    payload (build_data_payload) so the existing engines run unchanged. Symbols
    with no price data are excluded from the engines and reported in `unpriced`.

    When ``equal_weight`` is set (the sizes-private path), stored share counts are
    ignored and every priced name is given an equal slice of a fixed notional — so
    weights are 1/N and the customer's real position sizes are never needed nor
    faked into the weighting. The absolute dollar figures are illustrative only."""
    # Warm the close cache with ONE batched download: a cold 40-position book
    # valued per-symbol is 40 upstream fetches serialized by the yfinance
    # download lock — batched it's one round-trip. Anything the batch misses
    # falls through to the per-symbol path in the loop below.
    uncached = [p["symbol"] for p in positions if not price_cache.has_cached_close(p["symbol"])]
    if len(uncached) > 1:
        price_cache.warm_close_cache_batch(uncached)
    priced, unpriced = [], []
    for p in positions:
        price = _cached_close(p["symbol"])
        (unpriced.append(p["symbol"]) if price is None or price <= 0 else priced.append((p, price)))
    eq_each = (_EQUAL_WEIGHT_NOTIONAL / len(priced)) if (equal_weight and priced) else None

    # Build canonical Positions, then let portfolio_valuation do ALL the money
    # arithmetic. Nothing downstream may re-derive equity, day change or P&L —
    # every previous divergence (audit Q-02/Q-03) came from a second formula.
    canonical: list[portfolio_valuation.Position] = []
    for p, price in priced:
        if equal_weight:
            # Sizes-private mode: real share counts are deliberately NOT used, so
            # the dollars here are illustrative by construction and are labeled as
            # such. Cost basis and prior close are meaningless against a synthetic
            # quantity, so both stay absent and day change/P&L report unavailable.
            canonical.append(
                portfolio_valuation.Position.build(
                    p["symbol"], eq_each / price, current_price=price
                )
            )
        else:
            canonical.append(
                portfolio_valuation.Position.build(
                    p["symbol"],
                    p["shares"],
                    average_cost=p.get("avg_cost"),
                    current_price=price,
                    prior_close=_prior_close(p["symbol"]),
                )
            )

    val = portfolio_valuation.value_positions(canonical, cash=0)

    holdings = []
    for pos in canonical:
        mv = pos.market_value
        holdings.append(
            {
                "symbol": pos.symbol,
                "quantity": float(pos.quantity),
                "average_buy_price": float(pos.average_cost) if pos.average_cost else 0.0,
                "equity": float(mv) if mv is not None else 0.0,
                # `percent_change` is the LIFETIME return vs cost. It is None — not 0.0 —
                # when there is no cost basis: the dashboard used to read a 0 here as a
                # real "flat" reading and, worse, multiplied it into a "Day change".
                "percent_change": (
                    round(float((pos.current_price - pos.average_cost) / pos.average_cost * 100), 2)
                    if pos.average_cost
                    else None
                ),
                "day_change_pct": (
                    round(float((pos.current_price - pos.prior_close) / pos.prior_close * 100), 2)
                    if pos.prior_close
                    else None
                ),
                "equity_change": (
                    float(mv - pos.cost_basis)
                    if (mv is not None and pos.cost_basis is not None)
                    else None
                ),
                "current_price": float(pos.current_price),
                "prior_close": float(pos.prior_close) if pos.prior_close else None,
                "type": "stock",
                "pe_ratio": None,
                "portfolio_percentage": 0.0,
            }
        )
    total = float(val.positions_market_value)
    for h in holdings:
        h["portfolio_percentage"] = round(h["equity"] / total * 100, 1) if total else 0.0

    valuation = val.as_dict()
    equity_summary = {
        "portfolio_equity": total,
        "cash": 0.0,
        "buying_power": 0.0,
        # Canonical, and None when the inputs don't support it. Callers MUST check
        # day_change_available instead of treating a missing value as zero.
        "day_change": valuation["day_change_amount"],
        "day_change_pct": valuation["day_change_percent"],
        "day_change_available": valuation["day_change_available"] and not equal_weight,
        "cost_basis_available": valuation["cost_basis_available"],
        "total_cost_basis": valuation["total_cost_basis"],
        "unrealized_pnl": valuation["unrealized_pnl"],
        "unrealized_pnl_percent": valuation["unrealized_pnl_percent"],
    }
    if equal_weight:  # illustrative dollars — no real day change to report
        equity_summary["day_change"] = None
        equity_summary["day_change_pct"] = None
    return {
        "as_of": _dt.now(_tz.utc).isoformat(),
        "equity_summary": equity_summary,
        "holdings": holdings,
        "watchlist": [],
        "unpriced": unpriced,
        "equal_weight": bool(equal_weight),
        "valuation": valuation,
    }


@app.get("/api/mybook")
def mybook_get(request: Request):
    """The current user's book, valued live (per-user, session-scoped)."""
    email = _session_email(request)
    if not email:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        ew = auth_users.get_equal_weight(email)
        payload = _customer_book_payload(auth_users.get_positions(email), equal_weight=ew)
        # The book table earns its place with signal columns GESH alone can
        # provide (research: brokerages already do plain holdings tracking
        # better). Both maps are fail-soft: a missing signal_history table or
        # absent analysis renders an empty state, never a 500 (Task 5).
        syms = [h["symbol"] for h in payload["holdings"]] + payload["unpriced"]
        signals: dict[str, dict] = {}
        try:
            import signal_history

            for s in syms:
                hist = signal_history.get_history(s, days=1)
                if hist:
                    signals[s] = {"verdict": hist[-1]["verdict"], "date": hist[-1]["date"]}
        except Exception:  # noqa: BLE001 — the table is optional context, never a 500
            pass
        saved = auth_users.get_latest_book_analysis(email)
        kelly_deltas: dict[str, dict] = {}
        risky_symbols: list[str] = []
        stale = _analysis_stale_for(email, saved)
        if saved:
            for k in (saved.get("kelly") or {}).get("holdings") or []:
                if k.get("symbol") in syms and k.get("action"):
                    kelly_deltas[k["symbol"]] = {
                        "action": k["action"],
                        "delta_pp": round(
                            (k.get("optimal_pct") or 0) - (k.get("current_pct") or 0), 1
                        ),
                    }
            risk_syms: set[str] = set()
            for r in (saved.get("analysis") or {}).get("portfolio_risks") or []:
                # Whole-word AND case-sensitive against the ORIGINAL text: real
                # tickers are English words (ON, SO, ALL, NOW, IT, T), so a
                # substring test — or even a word-boundary match on uppercased
                # text — would flag an ON holding on "based on weakening
                # margins". Risk prose writes tickers uppercase; the lowercase
                # English word never matches the uppercase symbol.
                txt = str(r.get("text") or "")
                for s in syms:
                    if re.search(rf"\b{re.escape(s)}\b", txt):
                        risk_syms.add(s)
            risky_symbols = sorted(risk_syms)
        return {
            "status": "ok",
            "data": {
                "positions": payload["holdings"],
                "total_equity": payload["equity_summary"]["portfolio_equity"],
                # Canonical valuation block — the ONLY source the UI may use for
                # day change / P&L. It previously recomputed "Day change" in the
                # browser from lifetime-vs-cost returns and rendered
                # "+$271,639 (5920.4%)" on a real book (audit Q-02).
                "valuation": payload["valuation"],
                "unpriced": payload["unpriced"],
                "equal_weight": ew,
                "signals": signals,
                "kelly_deltas": kelly_deltas,
                "analysis_stale": stale,
                "risky_symbols": risky_symbols,
            },
        }
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
    shares_input = (body or {}).get("shares")
    ok = auth_users.set_position(email, sym, shares_input, (body or {}).get("avg_cost"))
    if not ok and len(auth_users.get_positions(email)) < auth_users._MAX_POSITIONS:
        raise HTTPException(status_code=400, detail="Invalid shares or avg_cost")
    # If the user supplied a real positive share count via the form, exit privacy/equal-weight mode.
    # (Only bulk symbols-only sets the flag on; single adds with sizes should turn it off.)
    try:
        sh = float(shares_input) if shares_input not in (None, "") else 0
        if ok and sh > 0:
            auth_users.set_equal_weight(email, False)
    except Exception:
        pass
    return {"status": "ok" if ok else "full", "data": mybook_get(request)["data"]}


@app.delete("/api/mybook/{symbol}")
def mybook_remove(symbol: str, request: Request):
    email = _session_email(request)
    if not email:
        raise HTTPException(status_code=401, detail="Authentication required")
    auth_users.remove_position(email, _safe_symbol(symbol))
    return mybook_get(request)


@app.post("/api/mybook/clear")
def mybook_clear(request: Request):
    """Completely clear the user's book (all positions) and exit equal-weight mode.
    Intended for users who did a symbols-only bulk import and now want to start over
    with real share counts (or a corrected bulk list)."""
    email = _session_email(request)
    if not email:
        raise HTTPException(status_code=401, detail="Authentication required")
    auth_users.clear_positions(email)
    return {"status": "ok", "data": mybook_get(request)["data"]}


# Per-user cache + single-flight for the book analysis. Keyed by (email) with the
# book fingerprint in the value: same book within the TTL = cached result; any
# position change = new fingerprint = fresh run. Same eviction/loop-affinity rules
# as the ticker card (see conftest note on _ticker_card_flight).
_MYBOOK_TTL = 900  # 15 min
_mybook_cache: dict[str, tuple[float, str, dict]] = {}
_mybook_lock = threading.Lock()


def _positive_env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


_MYBOOK_WORKERS = _positive_env_int("GESH_MYBOOK_WORKERS", 2)
_MYBOOK_QUEUE = _positive_env_int("GESH_MYBOOK_QUEUE", _MYBOOK_WORKERS * 2)
_mybook_executor = ThreadPoolExecutor(max_workers=_MYBOOK_WORKERS, thread_name_prefix="gesh-mybook")
_mybook_slots = threading.BoundedSemaphore(_MYBOOK_WORKERS + _MYBOOK_QUEUE)


def _submit_mybook_job(*args: object) -> bool:
    """Submit without allowing an unbounded executor queue."""
    if not _mybook_slots.acquire(blocking=False):
        return False
    try:
        future = _mybook_executor.submit(_mybook_analyze_worker, *args)
    except BaseException:
        _mybook_slots.release()
        raise
    future.add_done_callback(lambda _future: _mybook_slots.release())
    return True


def _load_mybook_job_state(user_id: str) -> tuple[dict | None, dict | None]:
    """Read durable state fail-closed without exposing database details."""
    try:
        auth_users.expire_stale_mybook_jobs(
            _positive_env_int(
                "GESH_MYBOOK_STALE_SECONDS", 900
            )  # [CLAUDE V3] runs are minutes; 15m orphan expiry
        )
        return (
            auth_users.get_active_mybook_job(user_id),
            auth_users.get_latest_mybook_job(user_id),
        )
    except Exception as exc:
        logger.warning("mybook durable state unavailable for %s: %s", user_id, exc)
        raise HTTPException(
            status_code=503,
            detail="Analysis state temporarily unavailable — please retry shortly.",
        )


class ValuationIntegrityError(RuntimeError):
    """The book's own arithmetic does not reconcile — refuse to build on it."""


def _assert_valuation_sound(payload: dict, *, where: str) -> None:
    """Gate every downstream engine on a self-consistent valuation.

    Kelly sizing, Monte-Carlo VaR and the AI narrative all take equity and
    weights as ground truth. Running them on a book whose total does not equal
    its own components produces confident, precise, wrong advice — which is
    strictly worse than an error message. Structured-logged without any dollar
    figure so the alert itself never becomes the leak."""
    val = payload.get("valuation") or {}
    failures = list(val.get("issues") or [])
    if val.get("reconciles") is False or any("!=" in f for f in failures):
        logger.error(
            "valuation integrity failure (%s): %d issue(s), %d position(s)",
            where,
            len(failures),
            len(payload.get("holdings") or []),
        )
        raise ValuationIntegrityError(
            "We could not reconcile this portfolio's value, so the analysis was "
            "stopped rather than run on inconsistent numbers. Please re-check the "
            "share counts on your positions."
        )


def _book_fingerprint(positions: list[dict]) -> str:
    return _json.dumps(sorted([(p["symbol"], p["shares"], p.get("avg_cost")) for p in positions]))


# The deep analysis emits one detailed prose card per holding, so the LLM's OUTPUT
# (and thus its runtime, ~70 tok/s) grows with the book. Cap DETAILED cards to the
# largest-weight names; Monte Carlo + Kelly still cover the FULL book. Customer
# books always use concise cards + a retry ladder so we stay under max_tokens and
# the ~100s Cloudflare tunnel limit (see async mybook_analyze polling).
_BOOK_ANALYSIS_MAX_HOLDINGS = 8
_BOOK_ANALYSIS_RETRY_CAPS = (6, 4)


def _deep_analyze_for_book(
    analysis_payload: dict,
    regime_context: str | None,
    truncated: dict | None,
) -> dict:
    """Customer-book deep analysis: concise output, shrink on truncation."""
    holdings = analysis_payload.get("holdings") or []
    caps = [len(holdings)]
    if truncated:
        caps.extend(c for c in _BOOK_ANALYSIS_RETRY_CAPS if c < len(holdings))
    last_err: Exception | None = None
    for cap in caps:
        payload = analysis_payload
        if cap < len(holdings):
            top = sorted(holdings, key=lambda h: h.get("equity") or 0, reverse=True)[:cap]
            payload = {**analysis_payload, "holdings": top}
        try:
            analysis = deep_analyze_portfolio(
                payload,
                regime_context=regime_context,
                concise=True,
                max_tokens=12000,
            )
            if truncated and cap < truncated["analyzed"]:
                truncated["analyzed"] = cap
            # Canonicalize on the way out (Codex #4) — this result is persisted via
            # save_book_analysis and rendered in /mybook/report, so it must carry the
            # same FV convention Kelly recomputes on read.
            return fair_value.canonicalize_analysis(analysis)
        except RuntimeError as e:
            if "truncated" in str(e).lower() and cap != caps[-1]:
                logger.warning(
                    "mybook deep analysis truncated at %d holdings — retrying smaller",
                    cap,
                )
                last_err = e
                continue
            raise
    raise last_err or RuntimeError("mybook deep analysis failed")


def _jsonable(o):
    """Recursively coerce engine output to JSON-native types. Monte Carlo / Kelly /
    regime lean on numpy and can hand back numpy scalars (np.bool_, np.float64) that
    FastAPI's encoder chokes on ("'numpy.bool' object is not iterable") — turning a
    completed analysis into a 500. numpy scalars/arrays expose .tolist()."""
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if type(o).__module__.startswith("numpy"):
        try:
            return _jsonable(o.tolist())
        except Exception:
            return str(o)
    return o


def _run_book_engines(payload: dict) -> dict:
    """Blocking engine suite on a customer payload — runs in a worker thread.
    Regime/Kelly/Monte-Carlo are best-effort (partial results beat a 500); the
    Claude deep analysis is the core deliverable and failures propagate.

    Order matters: the deep analysis runs BEFORE Kelly so its per-holding verdicts
    (verdict + fair-value gap) feed run_kelly_analysis exactly like the owner
    pipeline does (/api/kelly builds the same map from the latest analysis run) —
    without them Kelly sizes verdict-blind."""
    # Cap the DETAILED analysis first; its regime + analyst enrichment then runs only
    # over the analyzed names (the yfinance pulls scale with symbol count too).
    holdings = payload["holdings"]
    coverage = {
        "analyzed": min(len(holdings), _BOOK_ANALYSIS_MAX_HOLDINGS),
        "total": len(holdings),
    }
    all_holdings_summary = [
        {
            "symbol": h.get("symbol"),
            "equity": h.get("equity"),
            "portfolio_percentage": h.get("portfolio_percentage"),
        }
        for h in holdings
    ]
    analysis_payload = {
        **payload,
        "all_holdings_summary": all_holdings_summary,
        "detailed_coverage": coverage,
    }
    truncated = None
    if len(holdings) > _BOOK_ANALYSIS_MAX_HOLDINGS:
        top = sorted(holdings, key=lambda h: h.get("equity") or 0, reverse=True)[
            :_BOOK_ANALYSIS_MAX_HOLDINGS
        ]
        analysis_payload = {
            **analysis_payload,
            "holdings": top,
            "book_meta": {
                "total_holdings": len(holdings),
                "all_symbols": [h["symbol"] for h in holdings],
                "analyzed_symbols": [h["symbol"] for h in top],
                "portfolio_scope_source": "all_holdings_summary",
                "note": (
                    f"Emit detailed per-holding cards for analyzed_symbols only "
                    f"({len(top)} of {len(holdings)}). Portfolio verdict, sector mix, "
                    f"and diversification must be derived from all_holdings_summary."
                ),
            },
        }
        truncated = {"analyzed": len(top), "total": len(holdings)}
    analyzed_symbols = [h["symbol"] for h in analysis_payload["holdings"]]
    regime_context = None
    try:
        regime_context = detect_regimes(analyzed_symbols).get("regime_summary")
    except Exception:
        pass
    analysis = _deep_analyze_for_book(analysis_payload, regime_context, truncated)
    if truncated:
        analysis["holdings_truncated"] = truncated
    ai_verdicts = {}
    for ha in analysis.get("holdings_analysis") or []:
        if ha.get("symbol"):
            ai_verdicts[ha["symbol"]] = {
                "verdict": ha.get("verdict"),
                "pct_diff": fair_value.canonical_pct_diff(
                    ha.get("fair_value")
                ),  # canonical (Codex #2)
            }
    try:
        mc = run_monte_carlo(payload["holdings"], payload["equity_summary"])
    except Exception as e:
        mc = {"error": f"monte carlo unavailable: {type(e).__name__}"}
    try:
        analyst = get_analyst_targets(analyzed_symbols) or {}
        upside = {
            s: {"upside_pct": analyst[s].get("upside_pct")}
            for s in analyst
            if not analyst[s].get("error")
        }
        kelly = run_kelly_analysis(
            payload["holdings"], payload["equity_summary"]["portfolio_equity"], upside, ai_verdicts
        )
    except Exception as e:
        kelly = {"error": f"kelly unavailable: {type(e).__name__}"}
    return _jsonable(
        {
            "analysis": analysis,
            "kelly": kelly,
            "monte_carlo": mc,
            "detailed_coverage": coverage,
            "book": {
                "positions": payload["holdings"],
                "total_equity": payload["equity_summary"]["portfolio_equity"],
                "unpriced": payload["unpriced"],
            },
        }
    )


def _mybook_analyze_worker(
    email: str,
    user_id: str,
    job_id: str,
    fp: str,
    positions: list[dict],
    ew: bool,
) -> None:
    """Run one durably identified generation in the bounded worker pool."""
    if not auth_users.mark_mybook_job_running(user_id, job_id, fp):
        auth_users.release_usage(job_id, user_id)  # [CLAUDE V5] tenant-scoped
        return
    try:
        payload = _customer_book_payload(positions, ew)
        if not payload["holdings"]:
            raise HTTPException(
                status_code=404, detail="No market data for any symbol on your book"
            )
        _assert_valuation_sound(payload, where=f"mybook job {job_id}")
        result = _run_book_engines(payload)
        # Stamp the book fingerprint this analysis was computed FOR — persisted via
        # save_book_analysis so /mybook/report can detect that the book has since
        # changed and flag the analysis as stale instead of silently pairing new
        # positions with old scores (audit 2026-07-15 Test 4).
        result["_fp"] = fp
        result["_job_id"] = job_id

        # A book edit while this generation was running makes the result audit-only.
        # The paid reservation is completed, but no cache/analysis publication occurs.
        current_positions = auth_users.get_positions(email)
        current_ew = auth_users.get_equal_weight(email)
        current_fp = _book_fingerprint(current_positions) + ("|ew" if current_ew else "")
        if current_fp != fp:
            auth_users.finish_mybook_job(user_id, job_id, fp, "done")
            return

        # finish_mybook_job compares the durable latest generation and persists the
        # analysis in the same transaction before it releases the active-job slot.
        if not auth_users.finish_mybook_job(user_id, job_id, fp, "done", result=result):
            return
        with _mybook_lock:
            latest = auth_users.get_latest_mybook_job(user_id)
            if not latest or latest.get("job_id") != job_id:
                return
            _mybook_cache[user_id] = (time.monotonic(), fp, result)
            for k in [
                k
                for k, (ts, _, _) in _mybook_cache.items()
                if (time.monotonic() - ts) >= _MYBOOK_TTL
            ]:
                del _mybook_cache[k]
    except BaseException as exc:
        code = f"HTTP_{exc.status_code}" if isinstance(exc, HTTPException) else type(exc).__name__
        auth_users.finish_mybook_job(user_id, job_id, fp, "failed", error_code=code)


@app.post("/api/mybook/analyze", dependencies=[Depends(require_customer_or_owner)])
async def mybook_analyze(request: Request):
    """Full engine suite on the CUSTOMER's own book — deep AI analysis + Kelly +
    Monte Carlo, exactly the owner pipeline but fed from their entered positions.
    Auth-gated (LLM spend), heavy rate bucket, cached per (user, book fingerprint)
    for _MYBOOK_TTL, single-flight per user. NOT logged to the owner's analysis
    history — this is the customer's data.

    The engine suite can run for minutes, so it runs in a bounded executor: the first
    call returns HTTP 202 {status: running} immediately and the client polls this
    same endpoint every few seconds until 200 (outlasting the Cloudflare tunnel's
    ~100s limit); a completed run warms the per-book cache so the poll returns it."""
    email = _session_email(request)
    if not email:
        raise HTTPException(status_code=401, detail="Authentication required")
    positions = auth_users.get_positions(email)
    if not positions:
        raise HTTPException(status_code=400, detail="Your book is empty — add positions first")
    ew = auth_users.get_equal_weight(email)
    fp = _book_fingerprint(positions) + ("|ew" if ew else "")
    user_id = auth_users.resolve_user_id(email)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    active, latest = _load_mybook_job_state(user_id)
    if active:
        return JSONResponse(
            status_code=202,
            content={"status": "running", "job_id": active["job_id"]},
        )

    if latest and latest.get("fingerprint") == fp and not latest.get("delivered_at"):
        if latest.get("status") == "failed":
            auth_users.mark_mybook_job_delivered(user_id, latest["job_id"])
            if latest.get("error_code") == "HTTP_404":
                raise HTTPException(
                    status_code=404, detail="No market data for any symbol on your book"
                )
            raise HTTPException(status_code=500, detail="Book analysis failed — please retry.")
        if latest.get("status") == "done" and latest.get("result") is not None:
            auth_users.mark_mybook_job_delivered(user_id, latest["job_id"])
            return {"status": "ok", "cached": False, "data": latest["result"]}

    with _mybook_lock:
        hit = _mybook_cache.get(user_id)
        if hit and hit[1] == fp and (time.monotonic() - hit[0]) < _MYBOOK_TTL:
            auth_users.log_usage(email, "book_analysis_cached")
            return {"status": "ok", "cached": True, "data": hit[2]}

    try:
        limit = _customer_quota_limit(email, "book_analysis")
        job_id = str(uuid.uuid4())
        started = auth_users.start_mybook_job(user_id, job_id, fp, "book_analysis", limit)
    except Exception:
        logger.warning("mybook job reservation unavailable for %s", user_id)
        raise HTTPException(
            status_code=503,
            detail="Usage metering temporarily unavailable — please retry shortly.",
        )
    if started["quota_exceeded"]:
        raise HTTPException(
            status_code=429,
            detail=f"Daily AI analysis limit reached ({limit}/24h) — cached results remain free.",
        )
    if not started["accepted"]:
        job = started["job"] or {}
        return JSONResponse(
            status_code=202,
            content={"status": "running", "job_id": job.get("job_id")},
        )
    if not _submit_mybook_job(email, user_id, job_id, fp, positions, ew):
        auth_users.finish_mybook_job(user_id, job_id, fp, "failed", error_code="queue_full")
        raise HTTPException(status_code=503, detail="Analysis capacity is busy — retry shortly.")
    return JSONResponse(
        status_code=202,
        content={
            "status": "running",
            "job_id": job_id,
            "message": "Analysis started — poll every few seconds",
        },
    )


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


@app.post("/api/mybook/bulk")
async def mybook_bulk(request: Request):
    """Bulk-import a whole book from one pasted block — the answer to "I have 85
    positions, not one at a time". Body: {"text": "<lines>", "replace": true?} or {"symbols": [...]}.

    - If "replace": true, clears the existing book first (perfect after a bad symbols-only bulk).
    - Privacy path: paste TICKERS ONLY (no share counts) → sets equal-weight mode (sizes private, 1/N weighting).
    - Paste with counts (e.g. "AAPL 120") → honors real shares and turns equal-weight off.
    - Existing positions are upserted unless replace=true; the per-user cap (100) still applies (overflow → skipped_cap).
    """
    email = _session_email(request)
    if not email:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        body = await request.json()
    except Exception:
        body = {}
    text = (body or {}).get("text") or ""
    syms = (body or {}).get("symbols")
    if not text and isinstance(syms, list):
        text = "\n".join(str(s) for s in syms)
    replace = bool((body or {}).get("replace"))
    if replace:
        auth_users.clear_positions(email)
    items, invalid = _parse_bulk_book(text)
    any_sized = any(it["shares"] is not None for it in items)
    imported, skipped_cap = [], []
    for it in items:
        shares = it["shares"] if it["shares"] is not None else 1.0  # placeholder for symbols-only
        ok = auth_users.set_position(email, it["symbol"], shares, it["avg_cost"])
        (imported if ok else skipped_cap).append(it["symbol"])
    if imported:
        # Symbols-only paste → equal-weight (sizes private); any explicit size → real weights.
        auth_users.set_equal_weight(email, not any_sized)
    return {
        "status": "ok",
        "imported": imported,
        "skipped_cap": skipped_cap,
        "invalid": invalid,
        "equal_weight": auth_users.get_equal_weight(email),
        "replaced": replace,
        "data": mybook_get(request)["data"],
    }


def _analysis_stale_for(email: str, saved: dict | None) -> bool:
    """Has the book changed since the LAST persisted analysis was computed?
    New analyses carry the exact book fingerprint (`_fp`). Legacy rows (saved
    before the fingerprint existed) are ALWAYS stale: a symbol-set comparison
    was tried first, but it reads a same-symbols/different-shares book as fresh
    — without the fingerprint the share counts are unverifiable, and "can't
    verify" must surface as stale, not fresh (external review 2026-07-17).
    Self-clearing: one re-run mints a fingerprinted row. This is the SINGLE
    source of truth for staleness — /api/mybook/summary, /api/mybook/deploy-cash,
    and the /mybook/report page all call this (audit finding #2)."""
    if not saved:
        return False
    saved_fp = saved.get("_fp")
    if not saved_fp:
        return True
    positions = auth_users.get_positions(email)
    ew = auth_users.get_equal_weight(email)
    return saved_fp != _book_fingerprint(positions) + ("|ew" if ew else "")


def _ready_analysis_pending(email: str) -> bool:
    """Is a COMPLETED analysis for the CURRENT book sitting undelivered?

    The async job hands its result to the next POST /api/mybook/analyze, which
    marks it delivered. A customer who navigates away mid-run therefore leaves a
    finished result held on disk with nothing telling them so — /mybook/report
    showed "Analysis out of date" while a matching fresh analysis existed, which
    reads as "it failed" (QA audit 2026-07-19: an expert tester concluded the
    daily rate limit was exhausted; the run had in fact completed).

    Fingerprint-matched on purpose: a held result for a DIFFERENT book must keep
    the honest stale warning rather than promise a fresh one. Read-only and
    fail-quiet — this only upgrades a banner on a print-friendly page, so a
    metering hiccup must never turn it into a 500.
    """
    try:
        user_id = auth_users.resolve_user_id(email)
        if not user_id:
            return False
        latest = auth_users.get_latest_mybook_job(user_id)
        if not latest or latest.get("status") != "done" or latest.get("delivered_at"):
            return False
        ew = auth_users.get_equal_weight(email)
        current_fp = _book_fingerprint(auth_users.get_positions(email)) + ("|ew" if ew else "")
        return latest.get("fingerprint") == current_fp
    except Exception:  # noqa: BLE001 — advisory banner, never break the report
        return False


@app.post("/api/mybook/deploy-cash")
async def mybook_deploy_cash(request: Request):
    """'Where does new cash go?' — ranks the customer's LAST persisted Kelly ADD
    gaps against a cash amount. Pure arithmetic on cached data: no LLM, no quota.
    Advisory only; mirrors the report's staleness fingerprint so out-of-date
    sizing is labeled, never silently served."""
    email = _session_email(request)
    if not email:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        cash = float((body or {}).get("cash"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="cash must be a number") from None
    if not (0 < cash <= 10_000_000):
        raise HTTPException(status_code=400, detail="cash out of range")
    try:
        saved = auth_users.get_latest_book_analysis(email)
        if not saved:
            raise HTTPException(
                status_code=404, detail="No analysis yet — run Analyze My Portfolio first"
            )
        stale = _analysis_stale_for(email, saved)
        # Normalize once: legacy persisted rows can carry current_pct/optimal_pct
        # as None or omit them entirely — `or 0` covers both (dict.get defaults
        # only cover absent keys, not explicit None).
        adds = []
        for k in (saved.get("kelly") or {}).get("holdings") or []:
            cur = float(k.get("current_pct") or 0)
            opt = float(k.get("optimal_pct") or 0)
            if k.get("action") == "ADD" and opt > cur:
                adds.append({"symbol": k["symbol"], "current_pct": cur, "optimal_pct": opt})
        gaps = {k["symbol"]: k["optimal_pct"] - k["current_pct"] for k in adds}
        total_gap = sum(gaps.values())
        allocations, spent = [], 0.0
        for k in sorted(adds, key=lambda x: gaps[x["symbol"]], reverse=True):
            sym = k["symbol"]
            price = _cached_close(sym)
            if not price or price <= 0 or total_gap <= 0:
                continue
            dollars = round(cash * gaps[sym] / total_gap, 2)
            shares = int(dollars // price)
            if shares < 1:
                continue
            allocations.append(
                {
                    "symbol": sym,
                    "weight_pct": round(gaps[sym] / total_gap * 100, 1),
                    "dollars": dollars,
                    "shares": shares,
                    "price": price,
                    "rationale": f"Kelly gap {k['current_pct']:.1f}% → {k['optimal_pct']:.1f}%",
                }
            )
            spent += shares * price
        return {
            "status": "ok",
            "data": {
                "cash": cash,
                "stale": stale,
                "allocations": allocations,
                "leftover": round(cash - spent, 2),
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        raise _server_error(e)


@app.get("/api/mybook/summary")
def mybook_summary(request: Request):
    """Hero payload for the customer Home pane: value, what changed, one next
    action. Cached/DB reads only — this endpoint must stay instant and free."""
    email = _session_email(request)
    if not email:
        raise HTTPException(status_code=401, detail="Authentication required")
    positions = auth_users.get_positions(email)
    watch = auth_users.get_watch(email)
    ew = auth_users.get_equal_weight(email)
    # Equal-weight books never expose real dollars (sizes are private by design) —
    # the hero shows position count prominently instead (see buildHomeHero).
    total = 0.0 if ew else sum(p["shares"] * (_cached_close(p["symbol"]) or 0) for p in positions)
    saved = auth_users.get_latest_book_analysis(email)
    stale = _analysis_stale_for(email, saved)
    top_changes: list[dict] = []
    try:
        import signal_history

        syms = sorted({p["symbol"] for p in positions} | set(watch))
        flips = signal_history.detect_confirmed_flips(syms, persistence_days=2)
        top_changes = [
            {
                "symbol": f["symbol"],
                "prior_verdict": f["prior_verdict"],
                "new_verdict": f["new_verdict"],
                "date": f["confirmed_since"],
            }
            for f in flips[:3]
        ]
    except Exception:  # noqa: BLE001 — hero must render without signal history
        pass
    if not positions:
        action = {"label": "Add your holdings", "target": "book"}
    elif not saved:
        action = {"label": "Analyze my portfolio", "target": "analyze"}
    elif stale:
        action = {"label": "Re-run analysis — your book changed", "target": "analyze"}
    else:
        action = {"label": "View your report", "target": "report"}
    return {
        "status": "ok",
        "data": {
            "total_equity": round(total, 2),
            "positions_count": len(positions),
            "watch_count": len(watch),
            "last_verdict": ((saved or {}).get("analysis") or {}).get("portfolio_verdict"),
            "analysis_stale": stale,
            "analyzed_at": (saved or {}).get("_saved_at"),
            "top_changes": top_changes,
            "next_action": action,
        },
    }


def _lead_and_fold(text: str, max_lead: int = 220) -> tuple[str, str]:
    """First-sentence(s) lead + folded remainder, split on a sentence boundary.
    Customer surfaces show structured leads, not walls of model prose (plan T3).
    Returns (lead, rest); rest is "" when the text is short or has no boundary."""
    t = " ".join(str(text or "").split())
    if len(t) <= max_lead:
        return t, ""
    cut = t.find(". ", max_lead)
    if cut == -1:
        return t, ""
    return t[: cut + 1], t[cut + 2 :]


def _render_book_report(email: str) -> str:
    """Standalone, print-friendly HTML report of the customer's book + their LAST
    persisted analysis. Server-side rendered with every dynamic string escaped —
    this page bypasses the dashboard's audited JS builders."""
    import html as _h

    ew = auth_users.get_equal_weight(email)
    positions = auth_users.get_positions(email)
    book = _customer_book_payload(positions, equal_weight=ew)
    saved = auth_users.get_latest_book_analysis(email)
    e = _h.escape

    # Staleness check: the Positions table above is always CURRENT, but the saved
    # analysis may predate a book edit — rendering them side by side unflagged made
    # every portfolio-level number silently wrong (audit 2026-07-15 Test 4). Shared
    # with /api/mybook/summary and /api/mybook/deploy-cash via _analysis_stale_for
    # so a legacy (pre-fingerprint) analysis can't read fresh on the hero while the
    # report flags it stale for the identical data (audit finding #2).
    stale = _analysis_stale_for(email, saved)

    def row(label, val):
        return (
            f"<tr><td style='padding:6px 12px;color:#555'>{e(str(label))}</td>"
            f"<td style='padding:6px 12px;font-weight:600'>{e(str(val))}</td></tr>"
        )

    pos_rows = "".join(
        f"<tr><td style='padding:6px 12px;font-weight:600'>{e(h['symbol'])}</td>"
        f"<td style='padding:6px 12px;text-align:right'>{('—' if ew else format(h['quantity'], 'g'))}</td>"
        f"<td style='padding:6px 12px;text-align:right'>${h['current_price']:,.2f}</td>"
        f"<td style='padding:6px 12px;text-align:right'>{('—' if ew else '$' + format(h['equity'], ',.2f'))}</td>"
        f"<td style='padding:6px 12px;text-align:right'>{h['portfolio_percentage']:.1f}%</td></tr>"
        for h in book["holdings"]
    )
    total = book["equity_summary"]["portfolio_equity"]
    total_html = (
        (
            f"<b>Equal-weight basis</b> — position sizes are private, so each of the "
            f"{len(book['holdings'])} priced names is weighted equally (1/N). "
            f"Dollar values are illustrative, not real holdings."
        )
        if ew
        else f"<b>Total: ${total:,.2f}</b>"
    )

    stale_banner = ""
    if stale and _ready_analysis_pending(email):
        # A finished analysis for THESE positions is held, undelivered — the run
        # completed after the customer navigated away. Telling them it's "out of
        # date" here would be true of what's rendered but wrong about the system.
        stale_banner = (
            "<div style='background:#d1e7dd;border:1px solid #0f5132;border-radius:6px;"
            "padding:12px 16px;margin:18px 0'><b>&#10003; A newer analysis is ready</b> — "
            "your last run finished after you left the page, and it matches the positions "
            "listed above. Open <b>Analyze My Portfolio</b> on the dashboard to load it "
            "instantly (it is already computed — no new AI usage), then reload this report. "
            "Everything below is the PREVIOUS analysis"
            f" ({e(str(saved.get('_saved_at', ''))[:16])} UTC).</div>"
        )
    elif stale:
        stale_banner = (
            "<div style='background:#fff3cd;border:1px solid #d39e00;border-radius:6px;"
            "padding:12px 16px;margin:18px 0'><b>&#9888; Analysis out of date</b> — your "
            "positions changed after this analysis was generated"
            f" ({e(str(saved.get('_saved_at', ''))[:16])} UTC). The scores, Monte Carlo, "
            "Kelly sizing and per-holding research below reflect the OLD portfolio, not the "
            "positions listed above. Re-run <b>Analyze My Portfolio</b> on the dashboard, "
            "then reload this report.</div>"
        )

    body = ""
    if saved:
        d = saved.get("analysis") or {}
        mc, ky = saved.get("monte_carlo") or {}, saved.get("kelly") or {}
        risks = "".join(
            f"<li>{e(r.get('text', ''))} <em>({e(r.get('severity', ''))})</em></li>"
            for r in (d.get("portfolio_risks") or [])
        )
        cards = "".join(
            f"<h3 style='margin:18px 0 4px'>{e(h.get('symbol', ''))} — {e(h.get('verdict', ''))}</h3>"
            f"<p style='margin:4px 0;color:#444'>{e(h.get('snowflake_summary', ''))}</p>"
            f"<p style='margin:4px 0;color:#666;font-size:13px'>3M: {e((h.get('prediction') or {}).get('3m', ''))} · "
            f"12M: {e((h.get('prediction') or {}).get('12m', ''))}</p>"
            for h in (d.get("holdings_analysis") or [])
        )
        # `or 0` (not just a .get default) — a persisted row can carry an
        # explicit None, which the :.1f format would crash on.
        kelly_rows = "".join(
            row(
                f"{k.get('symbol', '')} · {k.get('action', '')}",
                f"{(k.get('current_pct') or 0):.1f}% → {(k.get('optimal_pct') or 0):.1f}%",
            )
            for k in (ky.get("holdings") or [])[:15]
        )
        sectors = "".join(
            f"<tr><td style='padding:6px 12px'>{e(s.get('sector', ''))}</td>"
            f"<td style='padding:6px 12px;text-align:right;font-weight:600'>{float(s.get('allocation_pct', 0) or 0):.1f}%</td>"
            f"<td style='padding:6px 12px;color:#555'>{e(s.get('assessment', ''))}</td></tr>"
            for s in (d.get("sector_breakdown") or [])
        )
        sector_block = (f"<h2>Sector breakdown</h2><table>{sectors}</table>") if sectors else ""
        # Conclusions first, mechanics folded (plan T2): each engine section LEADS
        # with one plain-language line; the raw tables stay complete but move
        # behind <details>. Nothing is deleted — fold, don't remove.
        adjustments = [k for k in (ky.get("holdings") or []) if k.get("action") in ("ADD", "TRIM")]
        # "largest" = biggest |optimal - current| gap. The holdings list arrives
        # sorted by optimal_pct DESC, so taking the first row would headline a
        # 0.1pp trim over a 14pp add (PR #79 review, finding 1).
        top_adj = max(
            adjustments,
            key=lambda k: abs((k.get("optimal_pct") or 0) - (k.get("current_pct") or 0)),
            default=None,
        )
        n_adj = len(adjustments)
        kelly_conclusion = f"{n_adj} sizing adjustment{'s' if n_adj != 1 else ''} suggested" + (
            f" — largest: {e(str(top_adj.get('symbol', '')))} {e(str(top_adj.get('action', '')))}."
            if top_adj
            else "."
        )
        mc_var = mc.get("var_95", 0) or 0
        # The headline pairs the SAVED analysis's VaR dollars with TODAY'S live
        # book total — when the analysis is stale that mix understates risk, so
        # the caveat rides on the conclusion line itself (the banner above is
        # skippable; the headline is not — finding 3).
        mc_stale_note = " (from your last analysis — re-run for current positions)" if stale else ""
        mc_conclusion = (
            f"Worst plausible {e(str(mc.get('horizon_days') or 21))}-day stretch "
            f"(95% confidence): &minus;${mc_var:,.0f} on your ${total:,.0f} book."
            f"{mc_stale_note}"
        )
        # Structured-only AI output (plan T3): one bold takeaway sentence, the
        # rest of the model prose folded — never a free-standing wall of text.
        ctx_lead, ctx_rest = _lead_and_fold(str(d.get("market_context", "")))
        ctx_html = f"<p style='color:#444;line-height:1.6'><b>{e(ctx_lead)}</b></p>" + (
            f"<details><summary>Full context</summary>"
            f"<p style='color:#444;line-height:1.6'>{e(ctx_rest)}</p></details>"
            if ctx_rest
            else ""
        )
        body = f"""
  <h2>AI Verdict: {e(str(d.get("portfolio_verdict", "—")))}</h2>
  <table>{row("Portfolio score", d.get("portfolio_score", "—"))}{row("Risk score", d.get("risk_score", "—"))}
  {row("Diversification", d.get("diversification_score", "—"))}{row("Portfolio type", d.get("portfolio_type", "—"))}</table>
  {ctx_html}
  {("<h2>Key risks</h2><ul>" + risks + "</ul>") if risks else ""}
  {sector_block}
  <h2>Risk check</h2><p style='color:#111;line-height:1.6'><b>{mc_conclusion}</b></p>
  <details><summary>How this was computed — Monte Carlo ({e(str(mc.get("horizon_days") or 21))} trading days)</summary>
  <table>{row("Median outcome", "$" + format(mc.get("median", 0) or 0, ",.0f"))}
  {row("P5 – P95", "$" + format(mc.get("p5", 0) or 0, ",.0f") + " – $" + format(mc.get("p95", 0) or 0, ",.0f"))}
  {row("VaR 95%", "$" + format(mc_var, ",.0f"))}</table></details>
  <h2>Position sizing</h2><p style='color:#111;line-height:1.6'><b>{kelly_conclusion}</b></p>
  <details><summary>How this was computed — Kelly sizing</summary>
  <table>{kelly_rows or row("Status", "unavailable")}</table></details>
  <h2>Per-holding research</h2>{cards}
  <p style='color:#888;font-size:12px'>Analysis generated {e(str(saved.get("_saved_at", ""))[:16])} UTC.</p>"""
    else:
        body = "<p><em>No analysis yet — run <b>Analyze My Portfolio</b> on the dashboard, then reload this report.</em></p>"

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>GESH — Portfolio Report</title>
<style>body{{font-family:-apple-system,Segoe UI,sans-serif;max-width:860px;margin:32px auto;padding:0 20px;color:#111}}
h1{{margin:0}}h2{{margin-top:28px}}table{{border-collapse:collapse}}
tr:nth-child(even){{background:#f6f6f6}}
details{{margin:8px 0;page-break-inside:avoid}}details summary{{cursor:pointer;color:#555;font-size:13px}}
@media print{{a{{display:none}}details{{display:block}}details>*{{display:block}}}}</style></head><body>
<div style='border-bottom:3px solid #111;padding-bottom:10px;display:flex;justify-content:space-between;align-items:baseline'>
<h1>GESH <span style='font-weight:300'>Portfolio Report</span></h1>
<span style='color:#666;font-size:13px'>AI portfolio intelligence · 11-node consensus · verified signals</span></div>
<p style='color:#666'>Prepared for {e(email)} · {_dt.now(_tz.utc).strftime("%Y-%m-%d %H:%M")} UTC</p>
<h2>Positions</h2>
<table><tr style='border-bottom:1px solid #ccc'><th style='padding:6px 12px;text-align:left'>Symbol</th>
<th style='padding:6px 12px'>Shares</th><th style='padding:6px 12px'>Price</th>
<th style='padding:6px 12px'>Value</th><th style='padding:6px 12px'>Weight</th></tr>{pos_rows}</table>
<p>{total_html}{(" · No price data: " + e(", ".join(book["unpriced"]))) if book["unpriced"] else ""}</p>
{stale_banner}
{body}
<hr><p style='color:#888;font-size:12px'>Informational and educational only — not financial advice.
All trading decisions remain your responsibility. Generated by GESH Financial Observability ·
<a href='/track-record'>see the track record</a> (hypothetical/simulated).</p>
<p style='color:#888;font-size:12px'><a href='/privacy'>Privacy Policy</a> ·
<a href='/terms'>Terms of Service</a> · <a href='/disclaimer'>Disclaimer</a></p>
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


@app.get("/usage", response_class=HTMLResponse)
def usage_page(request: Request, days: int = 30):
    """Owner cockpit: per-customer usage counters as a page instead of raw JSON.
    In _OWNER_ONLY_PATHS (customer sessions 403 at the middleware) plus a
    defense-in-depth owner check here. Server-rendered, everything escaped."""
    import html as _h

    email = _session_email(request)
    if not email:
        raise HTTPException(status_code=401, detail="Authentication required")
    if not auth_users.is_owner(email):
        raise HTTPException(status_code=403, detail="Owner-only view")
    try:
        days = max(1, min(days, 365))
        rows = auth_users.usage_summary(days=days)
        e = _h.escape
        tiers: dict[str, str] = {}
        body = "".join(
            f"<tr><td style='padding:6px 12px'>{e(r['email'])}</td>"
            f"<td style='padding:6px 12px'>{e(tiers.setdefault(r['email'], auth_users.get_tier(r['email']) or '—'))}</td>"
            f"<td style='padding:6px 12px'>{e(r['event'])}</td>"
            f"<td style='padding:6px 12px;text-align:right;font-weight:600'>{r['count']}</td>"
            f"<td style='padding:6px 12px;color:#666'>{e(str(r['last_at'])[:16])}</td></tr>"
            for r in rows
        )
        live_llm = sum(r["count"] for r in rows if r["event"] in ("ticker_card", "book_analysis"))
        # Provider-side spend/telemetry (owner-only; never on customer routes).
        import llm_gateway

        try:
            llm_provider = llm_gateway.daily_usage_summary(days=days)
        except Exception:
            llm_provider = {
                "attempt_count": 0,
                "total_estimated_spend_usd": 0.0,
                "error": "unavailable",
            }
        # Job Health — launchd jobs print their JSON summary only to /tmp logs, so a
        # failing/misconfigured job is invisible without this (Grok review 2026-07-17).
        import job_health

        job_runs = job_health.latest_runs()
        job_rows = []
        for label in ("com.gesh.signal-alerts", "com.gesh.weekly-digest"):
            info = job_runs.get(label)
            ran_at = info["ran_at"] if info else None
            summary = info["summary"] if info else {}
            flags = []
            # Staleness only for the daily job — a >25h-old weekly digest is normal.
            if label == "com.gesh.signal-alerts" and job_health.is_stale(ran_at):
                flags.append("STALE")
            if "DISABLED" in str(summary.get("email_delivery", "")):
                flags.append("EMAIL DISABLED")
            status = " · ".join(flags) if flags else ("OK" if info else "—")
            color = "#c00" if flags else ("#2a7" if info else "#888")
            # quote=False keeps the JSON readable in element content (never an attribute)
            summary_html = e(_json.dumps(summary), quote=False) if info else "—"
            job_rows.append(
                f"<tr><td style='padding:6px 12px;font-weight:600'>{e(label)}</td>"
                f"<td style='padding:6px 12px;color:{color};font-weight:700'>{e(status)}</td>"
                f"<td style='padding:6px 12px;color:#666'>{e(str(ran_at)[:16]) if ran_at else 'never'}</td>"
                f"<td style='padding:6px 12px;font-family:monospace;font-size:12px'>{summary_html}</td></tr>"
            )
        provider_html = e(_json.dumps(llm_provider), quote=False)
        return HTMLResponse(f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>GESH — Usage</title><style>body{{font-family:-apple-system,Segoe UI,sans-serif;max-width:900px;
margin:32px auto;padding:0 20px;color:#111}}table{{border-collapse:collapse;width:100%}}
th{{text-align:left;padding:6px 12px;border-bottom:2px solid #111;font-size:12px;letter-spacing:.06em}}
tr:nth-child(even){{background:#f6f6f6}}</style></head><body>
<h1>Customer Usage — last {days} days</h1>
<p style='color:#666'>{live_llm} live LLM calls billed across all customers ·
window <a href='/usage?days=7'>7d</a> · <a href='/usage?days=30'>30d</a> · <a href='/usage?days=90'>90d</a></p>
<h2>Job Health</h2>
<table><tr><th>JOB</th><th>STATUS</th><th>LAST RUN (UTC)</th><th>LAST SUMMARY</th></tr>
{"".join(job_rows)}</table>
<h2>LLM Provider</h2>
<pre style='background:#f6f6f6;padding:12px;overflow:auto;font-size:12px'>{provider_html}</pre>
<h2>Usage</h2>
<table><tr><th>EMAIL</th><th>TIER</th><th>EVENT</th><th>COUNT</th><th>LAST</th></tr>
{body or "<tr><td colspan='5' style='padding:16px;color:#888'><em>No usage recorded yet.</em></td></tr>"}</table>
</body></html>""")
    except HTTPException:
        raise
    except Exception as e2:
        raise _server_error(e2)


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
        days_n = max(1, min(days, 365))
        import llm_gateway

        try:
            llm_provider = llm_gateway.daily_usage_summary(days=days_n)
        except Exception:
            llm_provider = {
                "attempt_count": 0,
                "total_estimated_spend_usd": 0.0,
                "error": "unavailable",
            }
        return {
            "status": "ok",
            "days": days_n,
            "data": auth_users.usage_summary(days=days_n),
            "llm_provider": llm_provider,
        }
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
        detail="Ollama endpoints removed. Use /api/gemini/second-opinion/{symbol} instead.",
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
        payload = _resolved_payload()
        holdings = payload.get("holdings", [])
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
        top = sorted(holdings, key=lambda h: h.get("equity", 0), reverse=True)[:6]
        summary = f"Total equity: ${total:,.0f}\n" + "\n".join(
            f"  {h['symbol']}: {h.get('portfolio_percentage', 0):.1f}% alloc, "
            f"{h.get('percent_change', 0):+.1f}% today"
            for h in top
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
        analysis = "".join(
            c
            for c in str(body.get("analysis", f"Symbol: {symbol}"))
            if c.isprintable() or c in "\n\t"
        )[:2000]
        verdict = "".join(c for c in str(body.get("verdict", "")) if c.isprintable())[:64]
        result = gemini_second_opinion(symbol.upper(), analysis, verdict)
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
        macro_context = ""

        if body.get("include_regime", True):
            try:
                regime_data = detect_regimes(DEFAULT_WATCHLIST)
                regime_summary = regime_data.get("regime_summary", "")
                macro_ctx = regime_data.get("macro", {})
                macro_context = macro_ctx.get("macro_summary", "")
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


@app.get("/api/config", dependencies=[Depends(require_api_key)])
def get_config():
    """Return current allocation config (caps, gates, scanner mode).

    OWNER-ONLY — same gate as the write below. This is the operator's live
    trading-engine configuration: allocation percentages, per-trade dollar caps,
    max open positions/contracts, cash buffer, scanner parameters, and the
    auto_trade / paper_mode kill switches. It was readable by any customer
    session (audit A-02) purely because the read had no dependency while the
    write did. There is no customer feature that consumes it."""
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
            payload = _resolved_payload()
            symbols = list(
                {h["symbol"] for h in payload["holdings"]}
                | {w["symbol"] for w in payload["watchlist"]}
            )
        _interval = body.get("interval", "5m")
        _period = body.get("period", "1d")
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
            payload = _resolved_payload()
            symbols = list(
                {h["symbol"] for h in payload["holdings"]}
                | {w["symbol"] for w in payload["watchlist"]}
            )
        result = batch_sector_check(symbols)
        return {"status": "ok", "data": result}
    except Exception as e:
        raise _server_error(e)


# ─── Trade Log ────────────────────────────────────────────────────────────────


@app.get("/api/trades")
def get_trades(
    symbol: str = None,
    limit: int = Query(50, ge=1, le=500),
    open_only: bool = False,
    paper: bool = False,
):
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
        payload = _resolved_payload()
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
        payload = _resolved_payload()
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
        payload = _resolved_payload()
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
    Run consensus for a symbol, build TradeIntent, push through risk gate, paper-execute.
    Body: {symbol: str, use_cached?: bool}
    Full signal→gate→order pipeline in one call.
    """
    try:
        body = await request.json()
        symbol = body["symbol"].upper()
        use_cached = bool(body.get("use_cached", True))
        payload = _resolved_payload()

        consensus_row = _resolve_consensus_row(symbol, payload, use_cached=use_cached)

        from trading.config import load_risk_config

        cfg = load_risk_config()
        intent = from_consensus(consensus_row, max_position_pct=cfg.max_position_pct)

        verdict = risk_gate_evaluate(intent, payload)
        result = execution_place(intent, verdict, payload)

        return _sanitize(
            {
                "status": "ok",
                "symbol": symbol,
                "cached": use_cached and _cached_consensus_row(symbol) is not None,
                "consensus": consensus_row,
                "intent": intent.to_dict(),
                "verdict": verdict.to_dict(),
                "order": result.to_dict(),
            }
        )
    except KeyError as e:
        raise HTTPException(status_code=400, detail=f"Missing field: {e}")
    except Exception as e:
        raise _server_error(e)


@app.get("/api/trading/gate-status", dependencies=[Depends(require_api_key)])
def trading_gate_status():
    """Orders used today vs daily cap — for paper-sim UI early-stop."""
    try:
        from trading.config import load_risk_config
        from trading.state import count_orders_today

        cfg = load_risk_config()
        used = count_orders_today()
        return _sanitize(
            {
                "status": "ok",
                "orders_today": used,
                "max_orders_per_day": cfg.max_orders_per_day,
                "cap_reached": used >= cfg.max_orders_per_day,
            }
        )
    except Exception as e:
        raise _server_error(e)


@app.post("/api/trading/simulate-session", dependencies=[Depends(require_api_key)])
async def trading_simulate_session(request: Request):
    """
    Batch paper simulation for all holdings: cached consensus → gate → paper execute.
    Body: {use_cached?: bool, symbols?: [str]}
    One round-trip instead of N sequential consensus-order calls.
    """
    try:
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        use_cached = bool((body or {}).get("use_cached", True))
        only = {(s or "").upper() for s in (body or {}).get("symbols") or []}
        payload = _resolved_payload()
        holdings = payload.get("holdings", [])
        if only:
            holdings = [h for h in holdings if (h.get("symbol") or "").upper() in only]

        from trading.config import load_risk_config

        cfg = load_risk_config()
        results: list[dict] = []
        cap_hit = False
        cap_reason = ""

        for h in holdings:
            sym = (h.get("symbol") or "").upper()
            if not sym:
                continue
            if cap_hit:
                results.append(
                    {
                        "symbol": sym,
                        "skipped": True,
                        "reason": cap_reason or "Daily order cap already reached",
                    }
                )
                continue
            try:
                consensus_row = _resolve_consensus_row(sym, payload, use_cached=use_cached)
                intent = from_consensus(consensus_row, max_position_pct=cfg.max_position_pct)
                verdict = risk_gate_evaluate(intent, payload)
                result = execution_place(intent, verdict, payload)
                vd = verdict.to_dict()
                results.append(
                    {
                        "symbol": sym,
                        "cached": use_cached and _cached_consensus_row(sym) is not None,
                        "consensus_verdict": consensus_row.get("verdict"),
                        "intent": intent.to_dict(),
                        "verdict": vd,
                        "order": result.to_dict(),
                    }
                )
                reason = vd.get("reason") or ""
                if not vd.get("approved") and "Daily order cap" in reason:
                    cap_hit = True
                    cap_reason = reason
            except Exception as exc:
                results.append({"symbol": sym, "error": str(exc)})

        return _sanitize(
            {
                "status": "ok",
                "count": len(results),
                "cap_reached": cap_hit,
                "cap_reason": cap_reason or None,
                "use_cached": use_cached,
                "results": results,
            }
        )
    except Exception as e:
        raise _server_error(e)


@app.post("/api/trading/deep-debate/{symbol}", dependencies=[Depends(require_api_key)])
async def trading_deep_debate(symbol: str, request: Request):
    """
    Bull / Bear debate engine: haiku x2 (bull + bear) → sonnet x1 (PM synthesis).
    Body: {use_cached?: bool} — default true, uses today's engine_results consensus.
    Returns structured decision with price target, conviction, and catalysts.
    """
    try:
        symbol = _safe_symbol(symbol)
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        use_cached = bool(body.get("use_cached", True))
        payload = _resolved_payload()

        consensus = _resolve_consensus_row(symbol, payload, use_cached=use_cached)

        from fundamentals_openbb import get_fundamentals_openbb

        fundamentals = get_fundamentals_openbb(symbol) or {}

        from bull_bear_debate import run_debate

        result = run_debate(symbol, consensus, fundamentals)

        return _sanitize(
            {
                "status": "ok",
                "symbol": symbol,
                "cached_consensus": use_cached and _cached_consensus_row(symbol) is not None,
                "debate": result,
                "consensus_verdict": consensus.get("verdict"),
                "consensus_score": consensus.get("raw_score"),
            }
        )
    except Exception as e:
        raise _server_error(e)


# ─── Phase 1-3 Enhancement Endpoints ─────────────────────────────────────────


@app.get("/api/tax/harvest")
async def api_tax_harvest(request: Request = None):  # noqa: B008
    """Tax-loss harvesting candidates + wash-sale window status."""
    try:
        from tax_harvester import get_harvest_summary

        payload = _context_payload(request)
        positions = {h["symbol"]: h for h in payload.get("holdings", [])}
        cost_basis = {h["symbol"]: h.get("average_buy_price") for h in payload.get("holdings", [])}
        result = get_harvest_summary(positions, cost_basis)
        return _sanitize({"status": "ok", **result})
    except Exception as e:
        raise _server_error(e)


@app.get("/api/benchmarks/{period}")
async def api_benchmarks(period: str = "1mo", request: Request = None):  # noqa: B008
    """Thematic alpha benchmarking vs sector ETFs."""
    try:
        from benchmarks import batch_alpha

        payload = _context_payload(request)
        tickers = [h["symbol"] for h in payload.get("holdings", [])]
        result = batch_alpha(tickers, period)
        return _sanitize({"status": "ok", "period": period, "alpha_table": result})
    except Exception as e:
        raise _server_error(e)


@app.get("/api/explainability/{symbol}")
async def api_explainability(symbol: str):
    """Plain-English explanation of consensus verdict for a symbol."""
    try:
        from consensus import run_consensus_holding
        from explainability import build_transparency_report

        consensus = run_consensus_holding(symbol.upper())
        regime_str = consensus.get("regime", "NEUTRAL")
        report = build_transparency_report(symbol.upper(), consensus, regime=regime_str)
        return _sanitize({"status": "ok", "symbol": symbol.upper(), **report})
    except Exception as e:
        raise _server_error(e)


@app.get("/api/crypto-correlation")
async def api_crypto_correlation(request: Request = None):  # noqa: B008
    """Crypto-equity correlation risk summary for portfolio."""
    try:
        from crypto_correlation import crypto_risk_summary

        payload = _context_payload(request)
        holdings = payload.get("holdings", [])
        result = crypto_risk_summary(holdings)
        return _sanitize({"status": "ok", **result})
    except Exception as e:
        raise _server_error(e)


@app.get("/api/yield/landscape")
async def api_yield_landscape(request: Request = None):  # noqa: B008
    """DeFi & cash yield landscape for idle cash (informational)."""
    try:
        from defi_yield import get_yield_landscape

        payload = _context_payload(request)
        cash_usd = float(payload.get("equity_summary", {}).get("cash", 0) or 0)
        result = get_yield_landscape(cash_usd)
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
        import numpy as np

        from volatility_models import compare_vol_models

        data = price_source.locked_yf_download(
            symbol.upper(), period="1y", auto_adjust=True, progress=False
        )["Close"]
        if isinstance(data, __import__("pandas").DataFrame):
            data = data.iloc[:, 0]
        rets = np.log(data / data.shift(1)).dropna()
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
