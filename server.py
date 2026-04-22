from __future__ import annotations
from contextlib import asynccontextmanager
from pathlib import Path
import math, uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse


def _sanitize(obj):
    """Recursively replace NaN/Inf floats with None so JSON serialization never crashes."""
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    return obj

from ai_analyzer import MODEL, analyze_portfolio, deep_analyze_portfolio, analyze_screener
from auth import login, logout
from data_fetcher import DEFAULT_WATCHLIST, build_data_payload
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


# ─── Startup / Shutdown ───────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app):
    init_db()
    init_trading_tables()
    print("✓ SQLite DB initialised (core + trading tables)")
    try:
        login()
        print("✓ Robinhood session established")
    except Exception as e:
        print(f"⚠  Auth failed: {e}")
    yield
    try:
        logout()
    except Exception:
        pass


app = FastAPI(title="Gesh Financial Observability", lifespan=lifespan)
HTML = Path(__file__).parent / "templates" / "dashboard.html"


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

        # Log equity snapshot for ECG
        try:
            log_equity_snapshot(payload["holdings"])
        except Exception as snap_err:
            print(f"⚠  Snapshot log failed: {snap_err}")

        return _sanitize({"status": "ok", "data": payload})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── Quick AI analysis ────────────────────────────────────────────────────────

@app.post("/api/analyze")
def run_analysis():
    try:
        payload  = build_data_payload(DEFAULT_WATCHLIST)
        analysis = analyze_portfolio(payload)
        return {"status": "ok", "analysis": analysis, "model": MODEL}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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

        # Log to SQLite
        try:
            run_id = log_analysis(MODEL, payload, analysis)
            log_equity_snapshot(payload["holdings"])
        except Exception as db_err:
            print(f"⚠  DB log failed: {db_err}")
            run_id = None

        return {
            "status":   "ok",
            "analysis": analysis,
            "model":    MODEL,
            "regimes":  regime_data.get("regimes", []),
            "market":   mkt,
            "run_id":   run_id,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── Step 2 — Monte Carlo stress test ────────────────────────────────────────

@app.post("/api/monte-carlo")
def monte_carlo():
    try:
        payload  = build_data_payload(DEFAULT_WATCHLIST)
        holdings = payload["holdings"]
        eq_sum   = payload["equity_summary"]
        result   = run_monte_carlo(holdings, eq_sum)
        return {"status": "ok", "data": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
        raise HTTPException(status_code=500, detail=str(e))


# ─── Step 4 — Regime detection standalone ────────────────────────────────────

@app.get("/api/regime")
def regime_endpoint():
    try:
        payload  = build_data_payload(DEFAULT_WATCHLIST)
        symbols  = [h["symbol"] for h in payload["holdings"]]
        result   = detect_regimes(symbols)
        return {"status": "ok", "data": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── Step 1 — Analysis history ────────────────────────────────────────────────

@app.get("/api/history")
def analysis_history(limit: int = 20):
    try:
        rows = get_analysis_history(limit=limit)
        return {"status": "ok", "data": rows}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
        raise HTTPException(status_code=500, detail=str(e))


# ─── Market Intelligence (SWS-style) ──────────────────────────────────────────────────

@app.get("/api/market-data")
def market_data_endpoint():
    try:
        payload = build_data_payload(DEFAULT_WATCHLIST)
        symbols = [h["symbol"] for h in payload["holdings"]]
        result  = get_all_market_data(symbols)
        return {"status": "ok", "data": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/agents/sec/history")
def agent_sec_history(days: int = 30):
    """Return cached SEC filing analyses from DB."""
    try:
        payload = build_data_payload(DEFAULT_WATCHLIST)
        symbols = [h["symbol"] for h in payload["holdings"]]
        result  = get_filing_history(symbols, days=days)
        return {"status": "ok", "data": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/agents/news")
def agent_news():
    """Scan news sentiment for current holdings + watchlist."""
    try:
        payload = build_data_payload(DEFAULT_WATCHLIST)
        symbols = list({h["symbol"] for h in payload["holdings"]} | {w["symbol"] for w in payload["watchlist"]})
        result  = scan_news(symbols, days_back=7)
        return {"status": "ok", "data": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/agents/news/history")
def agent_news_history(days: int = 7):
    """Return cached news sentiment from DB."""
    try:
        payload = build_data_payload(DEFAULT_WATCHLIST)
        symbols = list({h["symbol"] for h in payload["holdings"]} | {w["symbol"] for w in payload["watchlist"]})
        result  = get_news_history(symbols, days=days)
        return {"status": "ok", "data": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/agents/fundamentals")
def agent_fundamentals():
    """Run full fundamental momentum scan (~3-5 min, uses Claude Sonnet)."""
    try:
        result = run_fundamental_scan()
        return {"status": "ok", "data": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/agents/fundamentals/latest")
def agent_fundamentals_latest():
    """Return the latest cached fundamental scan from DB."""
    try:
        result = get_latest_fundamental_scan()
        if not result:
            return {"status": "ok", "data": None, "message": "No scan yet — run POST /api/agents/fundamentals first"}
        return {"status": "ok", "data": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── Investment Screener ──────────────────────────────────────────────────────

@app.post("/api/screener")
def run_screener():
    try:
        # Reduced from 20 to 10 to instantly resolve perceived hangs
        candidates = screen_market(top_n=10)
        if not candidates:
            raise HTTPException(status_code=503, detail="No screener data returned — market may be closed")
        result = analyze_screener(candidates)
        return {"status": "ok", "data": result, "candidates_screened": len(candidates), "as_of": __import__('datetime').datetime.utcnow().isoformat() + "Z"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
        return {"status": "ok", "data": result, "total_equity": total_equity}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── Price Chart ─────────────────────────────────────────────────────────────

@app.get("/api/chart/{symbol}")
def price_chart(symbol: str, period: str = "1mo"):
    try:
        import yfinance as yf
        period_map = {"1d":"1d","5d":"5d","1w":"5d","1mo":"1mo","3mo":"3mo","6mo":"6mo","1y":"1y"}
        yf_period = period_map.get(period, "1mo")
        interval  = "5m" if period in ("1d","5d") else "1d"
        raw = yf.download(symbol, period=yf_period, interval=interval, auto_adjust=True, progress=False)
        if raw is None or raw.empty:
            raise HTTPException(status_code=404, detail="No data")
        closes = raw["Close"].squeeze().dropna()
        dates  = [str(d)[:16] if interval=="5m" else str(d)[:10] for d in closes.index]
        # .flatten() ensures 1D array if yf returns MultiIndex dataframe,
        # handle Series or DataFrame robustly
        import numpy as np
        prices = [round(float(v), 2) for v in np.array(closes).flatten()]
        pct_chg = round((prices[-1]/prices[0]-1)*100, 2) if len(prices)>1 else 0
        return {"symbol":symbol,"period":period,"dates":dates,"prices":prices,
                "pct_change":pct_chg,"current":prices[-1],"start":prices[0]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── Consensus Engine ────────────────────────────────────────────────────────

@app.post("/api/consensus")
def consensus_endpoint():
    try:
        payload      = build_data_payload(DEFAULT_WATCHLIST)
        holdings     = payload["holdings"]
        total_equity = payload["equity_summary"].get("portfolio_equity", 0)
        result       = run_consensus_portfolio(holdings, total_equity)
        return {"status": "ok", "data": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/fft-cycles/{symbol}")
def fft_cycles_single(symbol: str):
    """FFT cycle analysis for a single symbol."""
    try:
        from fft_cycles import detect_fft_cycles
        result = detect_fft_cycles(symbol.upper())
        return {"status": "ok", "data": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/gemini/analyze")
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


@app.post("/api/gemini/second-opinion/{symbol}")
async def gemini_second_opinion_endpoint(symbol: str, request: Request):
    """
    Gemini 1.5 Pro contrarian second opinion on a symbol.
    Optional JSON body: {"analysis": "...", "verdict": "STRONG BUY"}
    Independent reasoning from Claude — surfaces missed risks.
    """
    try:
        try:
            body = await request.json()
        except Exception:
            body = {}
        analysis = body.get("analysis", f"Symbol: {symbol.upper()}")
        verdict  = body.get("verdict", "")
        result   = gemini_second_opinion(symbol.upper(), analysis, verdict)
        return {"status": "ok", "data": _sanitize(result)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/gemini/market-commentary")
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
        raise HTTPException(status_code=500, detail=str(e))


# ─── Allocation Config ────────────────────────────────────────────────────────

@app.get("/api/config")
def get_config():
    """Return current allocation config (caps, gates, scanner mode)."""
    try:
        cfg = load_config()
        return {"status": "ok", "data": cfg}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/config")
async def set_config(request: Request):
    """Update one or more config keys. Body: {key: value, ...}"""
    try:
        body = await request.json()
        cfg = update_config(**body)
        return {"status": "ok", "data": cfg}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── Consolidation Zone + Sector Filter ──────────────────────────────────────

@app.get("/api/scanner/consolidation/{symbol}")
def consolidation_zone_single(symbol: str, interval: str = "5m", period: str = "1d"):
    """Detect consolidation zone for a single symbol."""
    try:
        result = detect_consolidation(symbol.upper(), interval=interval, period=period)
        return {"status": "ok", "data": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
        result = scan_consolidations(
            symbols,
            interval=body.get("interval", "5m"),
            period=body.get("period", "1d"),
            min_rr=float(body.get("min_rr", 2.0)),
        )
        return {"status": "ok", "data": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/scanner/sector/{symbol}")
def sector_hot_single(symbol: str):
    """Check if the sector ETF for a symbol has unusual volume (≥1.2× 20d avg)."""
    try:
        result = is_sector_hot(symbol.upper())
        return {"status": "ok", "data": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
        raise HTTPException(status_code=500, detail=str(e))


# ─── Trade Log ────────────────────────────────────────────────────────────────

@app.get("/api/trades")
def get_trades(symbol: str = None, limit: int = 50, open_only: bool = False, paper: bool = False):
    """Return trade history. ?paper=true for paper trades."""
    try:
        rows = get_trade_history(symbol=symbol, limit=limit, open_only=open_only, is_paper=paper)
        return {"status": "ok", "data": rows}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/trades/stats")
def trade_stats(days: int = 30, paper: bool = False):
    """Win rate, P&L stats, scanner performance for last N days."""
    try:
        summary = trade_summary_for_report(days=days)
        return {"status": "ok", "data": summary}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/trades/log")
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
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/trades/{trade_id}/close")
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
        raise HTTPException(status_code=500, detail=str(e))


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
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/paper/check-closes")
def paper_check_closes():
    """Auto-close paper positions that hit stop or target."""
    try:
        closed = check_and_close_paper_positions()
        return {"status": "ok", "closed": closed, "count": len(closed)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/paper/summary")
def paper_summary_endpoint(days: int = 7):
    """Paper vs live performance summary for last N days."""
    try:
        result = get_paper_summary(days=days)
        return {"status": "ok", "data": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── Trading Execution Layer (Risk Gate + Paper Execution) ───────────────────

@app.post("/api/trading/dry-run")
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
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/trading/paper-order")
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
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/trading/consensus-order")
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
        raise HTTPException(status_code=500, detail=str(e))


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 9000))
    uvicorn.run("server:app", host="127.0.0.1", port=port, reload=False)
