from __future__ import annotations
from contextlib import asynccontextmanager
from pathlib import Path
import math, time, threading, uvicorn, os, hmac
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


def require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> bool:
    """Auth gate for mutating endpoints (config changes + trade execution).

    Fails closed: if GESH_API_KEY is unset in the environment, the endpoint is
    blocked entirely (503) rather than left open. Set GESH_API_KEY in .env and
    send it as the `X-API-Key` request header.
    """
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
    "/api/deep-analysis", "/api/screener", "/api/consensus", "/api/monte-carlo",
    "/api/agents/", "/api/gemini/", "/api/trading/deep-debate",
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


from ai_analyzer import MODEL, analyze_portfolio, deep_analyze_portfolio, analyze_screener
from auth import login, logout
from data_fetcher import DEFAULT_WATCHLIST, build_data_payload, RobinhoodOfflineError
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


# ─── Startup / Shutdown ───────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app):
    init_db()
    init_trading_tables()
    print("✓ SQLite DB initialised (core + trading tables)")
    try:
        login()
        print("✓ Robinhood session established")
        # Write live cache immediately on startup so the daily-analysis
        # skill always has fresh data even before the first dashboard poll.
        try:
            from fetch_live_data import fetch_and_cache
            fetch_and_cache(logout_after=False)  # keep session alive for server
        except Exception as _ce:
            print(f"⚠  Startup cache write failed: {_ce}")
    except Exception as e:
        print(f"⚠  Auth failed: {e}")
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
<button type=submit>Sign in</button><div class=err id=e></div></form>
<script>document.getElementById('f').addEventListener('submit',async function(ev){ev.preventDefault();
var r=await fetch('/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({email:ev.target.email.value,password:ev.target.password.value})});
if(r.ok){location.href='/';}else{document.getElementById('e').textContent='Invalid email or password.';}});
</script></body></html>"""


@app.get("/healthz")
def healthz():
    return {"status": "ok", "auth": auth_users.auth_enabled()}


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
        secure=False,   # set True once :9000 is served over TLS
    )
    return resp


@app.post("/auth/logout")
def auth_logout():
    resp = JSONResponse(content={"ok": True})
    resp.delete_cookie(auth_users.COOKIE_NAME)
    return resp


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


# ─── Dashboard ────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def dashboard():
    return HTMLResponse(HTML.read_text().replace("{{ model }}", MODEL))


# ─── Portfolio data ───────────────────────────────────────────────────────────

@app.get("/api/portfolio")
def get_portfolio():
    try:
        payload = build_data_payload(DEFAULT_WATCHLIST)
        eq = payload["equity_summary"]
        prev = eq.get("adjusted_equity_previous_close", 0)
        curr = eq.get("portfolio_equity", 0)
        eq["day_change"]     = round(curr - prev, 2)
        eq["day_change_pct"] = round(((curr - prev) / prev * 100) if prev else 0, 2)

        # Log equity snapshot for ECG (rate-limited — see _maybe_log_snapshot)
        _maybe_log_snapshot(payload["holdings"])

        return _sanitize({"status": "ok", "data": payload})
    except Exception as e:
        raise _server_error(e)


# ─── Quick AI analysis ────────────────────────────────────────────────────────

@app.post("/api/analyze")
def run_analysis():
    try:
        payload  = build_data_payload(DEFAULT_WATCHLIST)
        analysis = analyze_portfolio(payload)
        return {"status": "ok", "analysis": analysis, "model": MODEL}
    except Exception as e:
        raise _server_error(e)


# ─── Deep AI analysis (with regime injection + DB logging) ───────────────────

@app.post("/api/deep-analysis")
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

@app.post("/api/monte-carlo")
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
        payload  = build_data_payload(DEFAULT_WATCHLIST)
        holdings = payload["holdings"]

        def history_fn(symbol: str):
            return get_equity_history(symbol, days=30)

        result = compute_ecg(holdings, history_fn)
        return {"status": "ok", "data": result}
    except Exception as e:
        raise _server_error(e)


# ─── Step 4 — Regime detection standalone ────────────────────────────────────

@app.get("/api/regime")
def regime_endpoint():
    try:
        payload  = build_data_payload(DEFAULT_WATCHLIST)
        symbols  = [h["symbol"] for h in payload["holdings"]]
        result   = detect_regimes(symbols)
        return {"status": "ok", "data": result}
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

@app.post("/api/agents/sec")
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


@app.post("/api/agents/news")
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


@app.post("/api/agents/fundamentals")
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

@app.post("/api/screener")
def run_screener():
    try:
        # Reduced from 20 to 10 to instantly resolve perceived hangs
        candidates = screen_market(top_n=10)
        if not candidates:
            raise HTTPException(status_code=503, detail="No screener data returned — market may be closed")
        result = analyze_screener(candidates)
        resp = {"status": "ok", "data": result, "candidates_screened": len(candidates), "as_of": __import__('datetime').datetime.utcnow().isoformat() + "Z"}
        _save_engine_result("screener", resp)
        return resp
    except HTTPException:
        raise
    except Exception as e:
        raise _server_error(e)


# ─── Kelly Criterion Position Sizer ──────────────────────────────────────────

@app.post("/api/kelly")
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
        import yfinance as yf
        period_map = {"1d":"1d","5d":"5d","1w":"5d","1mo":"1mo","3mo":"3mo","6mo":"6mo","1y":"1y"}
        yf_period = period_map.get(period, "1mo")
        interval  = "5m" if period in ("1d","5d") else "1d"
        raw = yf.download(sym, period=yf_period, interval=interval, auto_adjust=True, progress=False)
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

@app.post("/api/consensus")
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
        # Graceful — the sparkline draws nothing rather than erroring.
        return {"status": "error", "series": [], "error": str(e)}


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


# ─── Ollama Local LLM — REMOVED ─────────────────────────────────────────────
# Ollama (llama3.2:3b) was removed: a 3B local model cannot challenge Sonnet.
# "Second opinion" from a weaker model is random disagreement, not independent signal.
# Use /api/gemini/second-opinion/{symbol} (Gemini 2.5 Pro) for genuine independent review.
#
# Legacy endpoints return 410 Gone with a redirect message.

@app.get("/api/ollama/status")
@app.post("/api/ollama/analyze")
@app.post("/api/ollama/second-opinion/{symbol}")
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


@app.post("/api/scanner/consolidation")
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


@app.post("/api/scanner/sector")
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

@app.post("/api/paper/run")
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


@app.post("/api/paper/check-closes")
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
