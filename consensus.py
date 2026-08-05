"""
Multi-Node Consensus Engine
11 independent signal nodes, each using a DIFFERENT data source.
A stock only gets a strong signal when 5+ nodes agree.

Nodes (intentionally uncorrelated):
  1.  MOMENTUM      — RSI + MA crossover (price action)
  2.  TREND         — ADX + Bollinger regime
  3.  RELATIVE_STR  — Performance vs SPY (relative strength)
  4.  FUNDAMENTALS  — Revenue growth + margins + PE (quality)
  5.  ANALYST       — Wall St consensus + price target upside
  6.  NEWS          — Cached sentiment score from news_agent DB
  7.  KELLY         — Position sizing signal (ADD/HOLD/TRIM)
  8.  ECG           — Equity Curve Gate health (HEALTHY/SUSPENDED)
  9.  VOLUME        — Volume trend + liquidity (not wash-trade noise)
  10. FFT_CYCLE     — Dominant price cycle phase (trough vs peak)
  11. MACRO         — FRED/yfinance macro regime (FFR, yield curve, HY OAS, VIX)

Voting: BULLISH=+1, NEUTRAL=0, BEARISH=-1, ABSTAIN=None
Final: score = sum(votes) / len(voting nodes)
"""

from __future__ import annotations
import os
import sqlite3, json, numpy as np
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
import yfinance as yf

if TYPE_CHECKING:
    # Type-only: node_fundamentals/node_analyst annotate `obb_data` with this
    # TypedDict, but importing it at runtime would pull in fundamentals_openbb,
    # whose openbb import is deliberately guarded (openbb is not installed in
    # CI or on every host). Under `from __future__ import annotations` the
    # annotations stay strings, so this costs nothing at runtime and resolves
    # the name for type checkers / ruff F821.
    #
    # Trade-off, deliberately accepted: because the import is absent at runtime,
    # typing.get_type_hints() on these two nodes still raises NameError. That is
    # fine here — they are plain internal functions, not FastAPI handlers or
    # pydantic models, so nothing introspects their annotations at runtime. If
    # one ever becomes a route handler, promote this to a real import.
    from fundamentals_openbb import FundamentalsData
import price_source  # Yahoo primary, Robinhood fallback (price_source.py)
from agent_db import agent_db_path

DB_PATH = agent_db_path()
VOTE_BULLISH  =  1
VOTE_NEUTRAL  =  0
VOTE_BEARISH  = -1
ABSTAIN       = None

# ─── yfinance .info cache (15-minute TTL) ─────────────────────────────────────
# yf.Ticker.info makes a slow HTTP call. Caching for 15 min cuts redundant
# calls when Fundamentals + Analyst nodes both need the same symbol's info.
import time as _time
_INFO_CACHE: dict[str, tuple[dict, float]] = {}   # symbol → (info_dict, timestamp)
_INFO_TTL = 900  # 15 minutes

def _get_ticker_info(symbol: str) -> dict:
    """Return yfinance .info for symbol, cached for 15 minutes."""
    now = _time.monotonic()
    cached = _INFO_CACHE.get(symbol)
    if cached and (now - cached[1]) < _INFO_TTL:
        return cached[0]
    try:
        info = yf.Ticker(symbol).info or {}
    except Exception:
        info = {}
    _INFO_CACHE[symbol] = (info, now)
    return info


# ─── Regime-conditional NODE_WEIGHTS (Phase 4 — ported from the engine) ───────
# Backtest-validated across 6 assets / 24 months: the ADX/Trend filter is the edge
# in breakouts (HOOD pattern), RSI/Momentum dominates fast trends (NVDA pattern),
# and Fundamentals matter more in ranging markets. Cached 30 min per symbol so
# detect_regimes() isn't re-run on every consensus call.
_REGIME_LABEL_CACHE: dict[str, str] = {}
_REGIME_CACHE_STAMP: dict[str, float] = {}
_REGIME_CACHE_TTL_S = 30 * 60

_BASE_WEIGHTS = {
    "Rel.Strength":  2.0,
    "Trend":         2.0,
    "Volume":        1.5,
    "Momentum":      1.5,
    "Fundamentals":  0.75,
    "Macro":         0.75,
    "ECG":           0.5,
    "Analyst":       0.5,
    "LT_Macro":      0.5,   # long-term (1-3y) market-wide bias (VIX/F&G/margin debt)
    "News":          0.25,
    # Kelly: sizing output, not in directional vote
    # FFT_Cycle: removed from vote — spurious in non-stationary series
}


def _regime_cache_get(sym: str) -> str | None:
    stamp = _REGIME_CACHE_STAMP.get(sym)
    if stamp is None or (_time.time() - stamp) > _REGIME_CACHE_TTL_S:
        return None
    return _REGIME_LABEL_CACHE.get(sym)


def _regime_cache_put(sym: str, label: str) -> None:
    _REGIME_LABEL_CACHE[sym] = label
    _REGIME_CACHE_STAMP[sym] = _time.time()


def _regime_weights(sym: str) -> dict:
    """Return regime-adjusted NODE_WEIGHTS for sym.

    Calls detect_regimes() (single-symbol), cached 30 min. On ANY failure or when
    no regime is detected, falls back to the default balanced weights so consensus
    never crashes. Mirrors the engine's validated profiles exactly.
    """
    base = dict(_BASE_WEIGHTS)
    try:
        regime_label = _regime_cache_get(sym)
        if regime_label is None:
            from regime import detect_regimes
            result = detect_regimes([sym])
            regimes = result.get("regimes", [])
            if not regimes:
                return base
            regime_label = regimes[0].get("regime", "")
            _regime_cache_put(sym, regime_label)

        if regime_label == "BREAKOUT-READY":
            base["Trend"] = 2.5      # trend filter is the primary edge
            base["Momentum"] = 1.0   # RSI exits too early in breakouts
        elif regime_label in ("TRENDING", "MOMENTUM-HIGH"):
            base["Trend"] = 1.0      # ADX filter adds noise on fast trends
            base["Momentum"] = 2.5   # RSI dip entries are the real edge
        elif regime_label in ("MOMENTUM-LOW", "RANGING"):
            base["Momentum"] = 1.0
            base["Fundamentals"] = 1.25  # quality matters more when ranging
    except Exception:
        pass  # import/detection failure → balanced base
    return base


# ─── Node implementations ─────────────────────────────────────────────────────

def node_momentum(symbol: str, close: np.ndarray | None = None) -> tuple[int | None, str]:
    """RSI + 20/50 MA crossover on 90d daily data."""
    try:
        if close is None:
            raw = price_source.yf_download(symbol, period="90d", auto_adjust=True, progress=False)
            if raw is None or len(raw) < 20:
                return ABSTAIN, "Insufficient data"
            c = raw["Close"].squeeze().dropna().values.astype(float)
        else:
            c = close
            if len(c) < 20:
                return ABSTAIN, "Insufficient data"
        # RSI-14
        d = np.diff(c); gains = np.where(d>0,d,0); losses = np.where(d<0,-d,0)
        ag = np.convolve(gains,  np.ones(14)/14, 'valid')[-1]
        al = np.convolve(losses, np.ones(14)/14, 'valid')[-1]
        # Zero losses in the window = RSI saturates at 100 (max bullish), NOT a
        # neutral 50 — the old fallback muted this node in the strongest trends.
        if al > 0:
            rsi = 100 - (100 / (1 + ag / al))
        else:
            rsi = 100.0 if ag > 0 else 50.0   # 50 only when truly flat (no gains either)
        # MA crossover
        ma20 = np.mean(c[-20:]); ma50 = np.mean(c[-50:]) if len(c)>=50 else ma20
        # Price vs 52w high
        pct_from_high = (c[-1] - max(c)) / max(c) * 100
        signals = []
        if rsi > 55: signals.append(1)
        elif rsi < 45: signals.append(-1)
        else: signals.append(0)
        if ma20 > ma50 * 1.005: signals.append(1)
        elif ma20 < ma50 * 0.995: signals.append(-1)
        else: signals.append(0)
        score = sum(signals) / len(signals)
        detail = f"RSI={rsi:.0f} MA20={'>' if ma20>ma50 else '<'}MA50 HighGap={pct_from_high:.1f}%"
        if score >= 0.5: return VOTE_BULLISH, detail
        if score <= -0.5: return VOTE_BEARISH, detail
        return VOTE_NEUTRAL, detail
    except Exception as e:
        return ABSTAIN, str(e)


def node_trend(symbol: str, close: np.ndarray | None = None, high: np.ndarray | None = None, low: np.ndarray | None = None) -> tuple[int | None, str]:
    """ADX + Bollinger regime from existing regime module."""
    try:
        from regime import _rsi, _adx, _bollinger_width as _bollinger
        if close is None or high is None or low is None:
            raw = price_source.yf_download(symbol, period="60d", auto_adjust=True, progress=False)
            if raw is None or len(raw) < 20:
                return ABSTAIN, "Insufficient data"
            c = raw["Close"].squeeze().dropna().values.astype(float)
            h = raw["High"].squeeze().dropna().values.astype(float)
            l = raw["Low"].squeeze().dropna().values.astype(float)
        else:
            c, h, l = close, high, low
            if len(c) < 20:
                return ABSTAIN, "Insufficient data"
        adx = _adx(h, l, c)
        _, pct_b = _bollinger(c)
        rsi = _rsi(c)
        detail = f"ADX={adx:.0f} %B={pct_b:.0f} RSI={rsi:.0f}"
        # Strong trend + price above midline = bullish
        if adx > 25 and pct_b > 55 and rsi > 50: return VOTE_BULLISH, detail
        if adx > 25 and pct_b < 45 and rsi < 50: return VOTE_BEARISH, detail
        return VOTE_NEUTRAL, detail
    except Exception as e:
        return ABSTAIN, str(e)


def node_relative_strength(symbol: str, benchmark: str = "SPY",
                           sym_close: np.ndarray | None = None,
                           spy_close: np.ndarray | None = None) -> tuple[int | None, str]:
    """1-month performance vs SPY.

    Phase 2 (consensus convergence): accepts optional pre-fetched close arrays so a
    fetch-once run_consensus_holding can drop the separate [symbol, SPY] 35d batch
    (the last OHLCV node that still self-fetched). When arrays are NOT supplied it
    self-fetches exactly as before — the default call path is unchanged, so verdicts
    are identical until Phase 3 wires the arrays in. The array path mirrors the
    engine's logic (last min(n,22) bars) for parity once enabled.
    """
    try:
        if sym_close is not None and spy_close is not None:
            if len(sym_close) < 5 or len(spy_close) < 5:
                return ABSTAIN, "Too few rows"
            n = min(len(sym_close), len(spy_close), 22)
            s_arr, b_arr = sym_close[-n:], spy_close[-n:]
            s_ret = (s_arr[-1] / s_arr[0] - 1) * 100
            b_ret = (b_arr[-1] / b_arr[0] - 1) * 100
        else:
            raw = price_source.yf_download([symbol, benchmark], period="35d", auto_adjust=True, progress=False)
            if raw is None or raw.empty:
                return ABSTAIN, "No data"
            closes = raw["Close"] if "Close" in raw.columns else raw.xs("Close", axis=1, level=0)
            if hasattr(closes.columns, "__iter__") and symbol in closes.columns and benchmark in closes.columns:
                s = closes[symbol].dropna()
                b = closes[benchmark].dropna()
            else:
                return ABSTAIN, "Column mismatch"
            if len(s) < 5 or len(b) < 5:
                return ABSTAIN, "Too few rows"
            s_ret = (s.iloc[-1] / s.iloc[0] - 1) * 100
            b_ret = (b.iloc[-1] / b.iloc[0] - 1) * 100
        rs = float(s_ret) - float(b_ret)
        detail = f"1M: {float(s_ret):.1f}% vs SPY {float(b_ret):.1f}% → RS={rs:+.1f}%"
        if rs > 3: return VOTE_BULLISH, detail
        if rs < -3: return VOTE_BEARISH, detail
        return VOTE_NEUTRAL, detail
    except Exception as e:
        return ABSTAIN, str(e)


def node_fundamentals(
    symbol: str,
    obb_data: "FundamentalsData | None" = None,
) -> tuple[int | None, str]:
    """Revenue growth + profit margins + PE ratio. OpenBB primary, yfinance fallback.

    Args:
        obb_data: Pre-fetched OpenBB data from the caller; if None the function
                  fetches it itself (preserves backward compatibility).
    """
    try:
        if obb_data is None:
            from fundamentals_openbb import get_fundamentals_openbb
            obb_data = get_fundamentals_openbb(symbol)
        # The holding-level prefetch uses an empty mapping to mean "OpenBB was
        # attempted but unavailable." Only treat the response as usable for
        # this node when it contains a metric that can produce a fundamentals
        # vote; otherwise preserve the documented yfinance fallback.
        if obb_data and (
            obb_data.get("rev_growth") is not None or obb_data.get("pe") is not None
        ):
            rev_growth = obb_data.get("rev_growth")
            pe         = obb_data.get("pe")
            profit_m   = obb_data.get("profit_margin")
            eps_growth = obb_data.get("eps_growth")
            src        = "[OpenBB]"
        else:
            info       = _get_ticker_info(symbol)
            pe         = info.get("forwardPE") or info.get("trailingPE")
            rev_growth = info.get("revenueGrowth")
            profit_m   = info.get("profitMargins")
            eps_growth = info.get("earningsGrowth")
            src        = "[yf]"
        if rev_growth is None and pe is None:
            return ABSTAIN, "No fundamental data"
        score = 0; count = 0
        reasons = []
        if rev_growth is not None:
            count += 1
            if rev_growth > 0.10: score += 1; reasons.append(f"RevGrowth={rev_growth*100:.0f}%↑")
            elif rev_growth < 0: score -= 1;  reasons.append(f"RevGrowth={rev_growth*100:.0f}%↓")
            else: reasons.append(f"RevGrowth={rev_growth*100:.0f}%")
        if pe is not None:
            count += 1
            if 0 < pe < 20: score += 1;  reasons.append(f"PE={pe:.0f}✓")
            elif pe > 50:   score -= 1;  reasons.append(f"PE={pe:.0f}↑")
            elif pe < 0:    score -= 1;  reasons.append("PE<0")
            else: reasons.append(f"PE={pe:.0f}")
        if profit_m is not None:
            count += 1
            if profit_m > 0.15: score += 1; reasons.append(f"Margin={profit_m*100:.0f}%✓")
            elif profit_m < 0:  score -= 1; reasons.append(f"Margin={profit_m*100:.0f}%✗")
        if eps_growth is not None:
            count += 1
            if eps_growth > 0.10: score += 1; reasons.append(f"EPS↑{eps_growth*100:.0f}%")
            elif eps_growth < 0:  score -= 1; reasons.append(f"EPS↓{eps_growth*100:.0f}%")
        if count == 0: return ABSTAIN, "No usable data"
        norm = score / count
        detail = f"{src} " + " · ".join(reasons)
        if norm >= 0.4: return VOTE_BULLISH, detail
        if norm <= -0.4: return VOTE_BEARISH, detail
        return VOTE_NEUTRAL, detail
    except Exception as e:
        return ABSTAIN, str(e)


def node_analyst(
    symbol: str,
    obb_data: "FundamentalsData | None" = None,
) -> tuple[int | None, str]:
    """Analyst recommendation + price target upside. OpenBB primary, yfinance fallback.

    Args:
        obb_data: Pre-fetched OpenBB data from the caller; if None the function
                  fetches it itself (preserves backward compatibility).
    """
    try:
        if obb_data is None:
            from fundamentals_openbb import get_fundamentals_openbb
            obb_data = get_fundamentals_openbb(symbol)
        if obb_data is not None and obb_data.get("rec_key") is not None:
            rec         = (obb_data.get("rec_key") or "").lower()
            mean_target = obb_data.get("target_mean_price")
            current     = obb_data.get("current_price")
            src         = "[OpenBB]"
        else:
            info        = _get_ticker_info(symbol)
            rec         = (info.get("recommendationKey") or "").lower()
            mean_target = info.get("targetMeanPrice")
            current     = info.get("currentPrice") or info.get("regularMarketPrice")
            src         = "[yf]"
        upside = ((mean_target - current) / current * 100) if mean_target and current else None
        detail = f"{src} Rec={rec} Target={mean_target} Upside={upside:.1f}%" if upside else f"{src} Rec={rec}"
        if rec in ("strong_buy", "buy") and (upside is None or upside > 5):
            return VOTE_BULLISH, detail
        if rec in ("sell", "strong_sell") or (upside is not None and upside < -5):
            return VOTE_BEARISH, detail
        if rec == "hold" or (upside is not None and -5 <= upside <= 5):
            return VOTE_NEUTRAL, detail
        return ABSTAIN, "No analyst data"
    except Exception as e:
        return ABSTAIN, str(e)


def node_news(symbol: str) -> tuple[int | None, str]:
    """News sentiment — live Reddit (Phase 5) with cached-DB fallback.

    Converges toward the engine's Node 6: try live crowd sentiment via
    sentiment_connector.node_sentiment (praw → Reddit JSON API → keyword scoring,
    90-min cached). If it ABSTAINs (Reddit unavailable / no creds) or the import
    fails, fall back to the cached DB news_sentiment row (the prior behavior), then
    ABSTAIN. Graceful end-to-end — never raises out of the node.
    """
    # 1. Live Reddit sentiment (preferred)
    try:
        from sentiment_connector import node_sentiment
        vote, detail = node_sentiment(symbol)
        if vote is not None:
            return vote, detail   # live signal available
    except Exception:
        pass  # connector missing / network error → fall through to cached DB

    # 2. Cached DB fallback (news_agent-populated)
    try:
        cutoff = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=7)).isoformat()
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT sentiment_score, overall_sentiment FROM news_sentiment "
                "WHERE symbol=? AND created_at>=? ORDER BY created_at DESC LIMIT 1",
                (symbol, cutoff)
            ).fetchone()
        if not row:
            return ABSTAIN, "No live Reddit + no cached news"
        score, sentiment = row
        detail = f"Cached: Sentiment={sentiment} Score={score}"
        if score >= 30:  return VOTE_BULLISH, detail
        if score <= -30: return VOTE_BEARISH, detail
        return VOTE_NEUTRAL, detail
    except Exception as e:
        return ABSTAIN, str(e)


def node_kelly(symbol: str, equity: float, total_portfolio: float,
               avg_cost: float, current_price: float, returns: np.ndarray | None = None) -> tuple[int | None, str]:
    """Kelly position sizing signal."""
    try:
        from kelly import kelly_size_holding
        k = kelly_size_holding(symbol, equity, total_portfolio, avg_cost, current_price, returns=returns)
        if k.get("error"):
            return ABSTAIN, k["error"]
        action = k.get("action", "HOLD")
        detail = f"Action={action} Optimal={k.get('optimal_pct',0)}% Current={k.get('current_pct',0)}% Sharpe={k.get('sharpe',0)}"
        if action == "ADD":   return VOTE_BULLISH, detail
        if action == "TRIM":  return VOTE_BEARISH, detail
        if action == "AVOID": return VOTE_BEARISH, detail
        return VOTE_NEUTRAL, detail
    except Exception as e:
        return ABSTAIN, str(e)


def node_ecg(symbol: str) -> tuple[int | None, str]:
    """Equity Curve Gate health for this holding."""
    try:
        # Phase 3.2 renamed the ecg helpers (_drawdown/_streak/_gate_status →
        # below); this node kept the old imports and ABSTAINed on ImportError
        # every run. Mirrors compute_ecg exactly: history arrives newest-first
        # (ORDER BY snapshot_at DESC) so reverse for the running-peak math, and
        # _loss_streak takes per-snapshot % CHANGES derived from the equity
        # series — never raw equity levels (always positive → streak stuck at 0).
        from db import get_equity_history
        from ecg import _classify, _compute_drawdown_from_peak, _loss_streak

        history = get_equity_history(symbol, days=30)
        if not history or len(history) < 3:
            return ABSTAIN, "Insufficient ECG history"
        prices = [h["equity"] for h in reversed(history)]  # oldest → newest
        chgs = [
            (prices[i] / prices[i - 1] - 1.0) * 100 if prices[i - 1] else 0.0
            for i in range(1, len(prices))
        ]
        dd = _compute_drawdown_from_peak(prices)
        streak = _loss_streak(chgs)
        status = _classify(dd, streak)
        detail = f"ECG={status} DD={dd:.1f}% Streak={streak}"
        if status == "HEALTHY":
            return VOTE_BULLISH, detail
        if status == "SUSPENDED":
            return VOTE_BEARISH, detail
        return VOTE_NEUTRAL, detail
    except Exception as e:
        return ABSTAIN, str(e)


def node_volume(symbol: str, close: np.ndarray | None = None, volume: np.ndarray | None = None) -> tuple[int | None, str]:
    """Volume trend: 10d avg vs 30d avg. Rising volume = conviction."""
    try:
        if close is None or volume is None:
            raw = price_source.yf_download(symbol, period="45d", auto_adjust=True, progress=False)
            if raw is None or len(raw) < 15:
                return ABSTAIN, "Insufficient data"
            vol = raw["Volume"].squeeze().dropna().values.astype(float)
            prices = raw["Close"].squeeze().dropna().values.astype(float)
        else:
            vol = volume
            prices = close
            
        if len(vol) < 15 or len(prices) < 15:
            return ABSTAIN, "Too few rows"
        avg10 = np.mean(vol[-10:])
        avg30 = np.mean(vol[-30:]) if len(vol) >= 30 else np.mean(vol)
        ratio = avg10 / avg30 if avg30 > 0 else 1.0
        # Also check: volume increasing while price increasing = bullish
        price_up = prices[-1] > prices[-10]
        detail = f"Vol10/30={ratio:.2f}x PriceDir={'↑' if price_up else '↓'}"
        if ratio > 1.2 and price_up:  return VOTE_BULLISH, detail
        if ratio > 1.3 and not price_up: return VOTE_BEARISH, detail  # high vol, falling = distribution
        if ratio < 0.7:               return VOTE_NEUTRAL, detail    # drying up volume = wait
        return VOTE_NEUTRAL, detail
    except Exception as e:
        return ABSTAIN, str(e)


# ─── Main consensus runner ────────────────────────────────────────────────────

def run_consensus_holding(
    symbol: str,
    equity: float = 0,
    total_portfolio: float = 1,
    avg_cost: float = 0,
    current_price: float = 0,
) -> dict:
    """Run all 9 nodes for one holding, return consensus verdict."""
    nodes = {}

    try:
        raw_252 = price_source.yf_download(symbol, period="252d", auto_adjust=True, progress=False)
        # Row-aligned OHLCV: per-column .dropna() let Close/High/Low drift out of
        # time alignment when a NaN hit only one column (ADX on shifted bars).
        # Join on the index and drop rows missing any PRICE field instead.
        import pandas as pd
        fields = {f: raw_252[f].squeeze() for f in ("Close", "High", "Low", "Volume") if f in raw_252}
        price_cols = [f for f in ("Close", "High", "Low") if f in fields]
        aligned = pd.DataFrame(fields).dropna(subset=price_cols) if price_cols else pd.DataFrame()
        cache = {
            "close": aligned["Close"].values.astype(float) if "Close" in aligned else np.array([]),
            "high":  aligned["High"].values.astype(float) if "High" in aligned else np.array([]),
            "low":   aligned["Low"].values.astype(float) if "Low" in aligned else np.array([]),
            "volume": aligned["Volume"].fillna(0.0).values.astype(float) if "Volume" in aligned else np.array([]),
        }
        if len(cache["close"]) > 0:
            # SIMPLE returns — matches gesh_core.kelly's pct_change() convention.
            # Log returns understate mu by ~sigma^2/2, which skewed the Kelly
            # node bearish vs the dashboard Kelly on volatile names.
            c91 = cache["close"][-91:]
            # Guard the denominator: a halted/delisted bar at 0.0 would make simple
            # returns inf, poisoning mu/sigma → NaN Sharpe into the Kelly node. Drop
            # non-finite rows so a bad price degrades to a shorter (or empty) series.
            with np.errstate(divide="ignore", invalid="ignore"):
                returns = np.diff(c91) / c91[:-1]
            returns = returns[np.isfinite(returns)]
            if returns.size == 0:
                returns = None
        else:
            returns = None
    except Exception:
        cache = {"close": None, "high": None, "low": None, "volume": None}
        returns = None

    c, h, l, v = cache["close"], cache["high"], cache["low"], cache["volume"]

    nodes["Momentum"]       = node_momentum(symbol, close=c[-90:] if c is not None and len(c) > 0 else None)
    nodes["Trend"]          = node_trend(symbol, close=c[-60:] if c is not None and len(c) > 0 else None, high=h[-60:] if h is not None and len(h) > 0 else None, low=l[-60:] if l is not None and len(l) > 0 else None)
    # Phase 3 wiring: feed Rel.Strength the already-fetched sym close + a
    # once-per-process SPY close (gesh_core.market_fetch caches SPY 30min) so it
    # stops self-fetching its own [symbol, SPY] batch — the last redundant fetch.
    # Aligns to the engine's validated 22-bar RS window. Falls back to self-fetch
    # if the prefetch is empty or SPY is unavailable.
    try:
        from gesh_core.market_fetch import fetch_spy_close as _fetch_spy_close
        _spy_close = _fetch_spy_close() if c is not None and len(c) > 0 else None
    except Exception:
        _spy_close = None
    nodes["Rel.Strength"]   = node_relative_strength(
        symbol,
        sym_close=c if c is not None and len(c) > 0 else None,
        spy_close=_spy_close,
    )
    # Prefetch OpenBB fundamentals once — shared by node_fundamentals and node_analyst
    # so neither function needs to make its own HTTP request (saves one round-trip +
    # the 0.3 s stagger sleep in get_fundamentals_openbb per holding per run).
    try:
        from fundamentals_openbb import get_fundamentals_openbb as _get_obb
        _obb_data = _get_obb(symbol) or {}
    except Exception:
        _obb_data = {}  # attempted prefetch but got no usable data; prevents per-node refetch
    nodes["Fundamentals"]   = node_fundamentals(symbol, obb_data=_obb_data)
    nodes["Analyst"]        = node_analyst(symbol, obb_data=_obb_data)
    nodes["News"]           = node_news(symbol)
    nodes["Kelly"]          = node_kelly(symbol, equity, total_portfolio, avg_cost, current_price, returns=returns)
    nodes["ECG"]            = node_ecg(symbol)
    nodes["Volume"]         = node_volume(symbol, close=c[-45:] if c is not None and len(c) > 0 else None, volume=v[-45:] if v is not None and len(v) > 0 else None)
    # Node 10: FFT Cycle phase (spectral analysis)
    try:
        from fft_cycles import node_fft_cycle
        nodes["FFT_Cycle"] = node_fft_cycle(symbol, prices=c if c is not None and len(c) > 0 else None)
    except Exception as _fft_err:
        nodes["FFT_Cycle"] = (None, f"FFT unavailable: {_fft_err}")

    # Node 11: Macro regime (FRED/yfinance — FFR, yield curve, HY OAS, VIX)
    try:
        from macro_connector import node_macro
        nodes["Macro"] = node_macro()
    except Exception as _macro_err:
        nodes["Macro"] = (None, f"Macro unavailable: {_macro_err}")

    # Node 12: Long-term macro bias (VIX + Fear&Greed + FINRA margin debt → 1-3y
    # tilt). Distinct from the short-term Macro node above — a slow-moving,
    # market-WIDE bias (same signal for every holding), internally cached 6h/24h so
    # per-symbol calls in a portfolio loop don't re-fetch. Graceful ABSTAIN on
    # import/call failure (the except below), exactly like the other lazily-imported
    # nodes; a pure data outage instead degrades to a neutral vote inside the node.
    try:
        from long_term_macro import node_long_term_macro
        nodes["LT_Macro"] = node_long_term_macro(symbol)
    except Exception as _ltm_err:
        nodes["LT_Macro"] = (None, f"LT-Macro unavailable: {_ltm_err}")

    votes   = {k: v[0] for k, v in nodes.items()}
    details = {k: v[1] for k, v in nodes.items()}

    # Weighted tally — regime-conditional NODE_WEIGHTS (Phase 4, engine parity).
    # Trend/Momentum/Fundamentals are re-weighted per the symbol's detected regime;
    # falls back to balanced base weights on any detection failure.
    NODE_WEIGHTS = _regime_weights(symbol)
    regime_label = _regime_cache_get(symbol) or "DEFAULT"   # Phase 7: surface for the dashboard

    # Extract Kelly separately for sizing display
    kelly_vote   = votes.get("Kelly")
    kelly_detail = details.get("Kelly", "")

    weighted_sum = 0.0
    weight_total = 0.0
    bullish = bearish = neutral = 0
    active_count = 0
    abstains = 0

    # LT_Macro is DISPLAY-ONLY by default (Codex #5 / decision doc): it applies a
    # market-wide tilt to every ticker and has no walk-forward validation yet, so it
    # is excluded from the weighted tally + confidence denominator unless explicitly
    # enabled via GESH_LT_MACRO_VOTING. Its vote is still computed and returned in
    # `votes` for the dashboard card. Flip the env flag on only after validation.
    _skip = {"Kelly", "FFT_Cycle"}
    if os.getenv("GESH_LT_MACRO_VOTING", "").strip().lower() not in ("1", "true", "yes", "on"):
        _skip.add("LT_Macro")
    for node, vote in votes.items():
        if node in _skip:
            continue
        w = NODE_WEIGHTS.get(node, 1.0)
        if vote is None:
            abstains += 1
            continue
        active_count += 1
        weight_total += w
        weighted_sum += vote * w
        if vote == VOTE_BULLISH:   bullish += 1
        elif vote == VOTE_BEARISH: bearish += 1
        else:                      neutral += 1

    n         = active_count
    raw_score = weighted_sum / weight_total if weight_total > 0 else 0
    votable = sum(1 for k in votes if k not in _skip)
    confidence = n / max(votable, 1)
    # `confidence` above is DATA COVERAGE (share of nodes with live data), not the
    # strength of the call — surfacing it as "confidence" made a near-neutral HOLD read
    # as "80% confident" (GESH-3). Expose honest, separate provenance metrics:
    #  • data_coverage — coverage against the VOTABLE universe (can reach 100%)
    #  • conviction    — how far the weighted vote sits from neutral (|raw_score| vs the
    #                    0.55 STRONG threshold); invariant-free, tracks verdict strength
    #  • tilt          — for a HOLD, whether the discarded count leans bearish/bullish
    data_coverage = round(n / max(votable, 1) * 100, 0)
    _av = abs(raw_score)
    conviction = round(min(_av / 0.55, 1.0) * 100, 0)
    conviction_label = "STRONG" if _av >= 0.55 else "MODERATE" if _av >= 0.25 else "WEAK"
    tilt = (("bearish" if bearish > bullish else "bullish" if bullish > bearish else "balanced")
            if _av < 0.25 else None)

    if n < 4:
        verdict = "INSUFFICIENT DATA"
        verdict_color = "muted"
    elif raw_score >= 0.55:
        verdict = "STRONG BUY"
        verdict_color = "green"
    elif raw_score >= 0.25:
        verdict = "BUY"
        verdict_color = "green"
    elif raw_score <= -0.55:
        verdict = "STRONG SELL"
        verdict_color = "red"
    elif raw_score <= -0.25:
        verdict = "SELL"
        verdict_color = "red"
    else:
        verdict = "HOLD"
        verdict_color = "cyan"

    return {
        "symbol":        symbol,
        "verdict":       verdict,
        "verdict_color": verdict_color,
        "raw_score":     round(raw_score, 3),
        "confidence":    round(confidence * 100, 0),   # back-compat alias for data coverage
        "data_coverage": data_coverage,                # coverage vs the votable universe (GESH-3)
        "conviction":    conviction,                   # call strength from |raw_score| (GESH-3)
        "conviction_label": conviction_label,
        "tilt":          tilt,                         # for a HOLD: bearish/bullish/balanced lean
        "bullish":       bullish,
        "bearish":       bearish,
        "neutral":       neutral,
        "abstains":      abstains,
        "total_nodes":   len(nodes),
        "active_nodes":  n,
        "votes":         votes,    # {node: +1/0/-1/None}
        "details":       details,  # {node: reasoning string}
        "regime":        regime_label,        # Phase 7: detected regime driving the weights
        "node_weights":  NODE_WEIGHTS,        # Phase 7: regime-adjusted weights per node
        "kelly_vote":    kelly_vote,
        "kelly_detail":  kelly_detail,
        "as_of":         datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
    }


def run_consensus_portfolio(holdings: list[dict], total_portfolio: float) -> dict:
    """Run consensus on all holdings, return ranked results."""
    import concurrent.futures

    def process_holding(h):
        sym   = h.get("symbol", "")
        eq    = float(h.get("equity", 0))
        cost  = float(h.get("average_buy_price", 0))
        price = float(h.get("current_price", 0))
        print(f"  CONSENSUS: {sym}...")
        return run_consensus_holding(sym, eq, total_portfolio, cost, price)

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(holdings) or 1, 8)) as executor:
        results = list(executor.map(process_holding, holdings))

    # Sort: strong buy first, strong sell last
    order = {"STRONG BUY": 0, "BUY": 1, "HOLD": 2, "SELL": 3, "STRONG SELL": 4, "INSUFFICIENT DATA": 5}
    results.sort(key=lambda x: (order.get(x["verdict"], 5), -x["raw_score"]))

    strong_buys  = [r["symbol"] for r in results if r["verdict"] == "STRONG BUY"]
    buys         = [r["symbol"] for r in results if r["verdict"] == "BUY"]
    sells        = [r["symbol"] for r in results if r["verdict"] in ("SELL", "STRONG SELL")]
    avg_conf     = round(sum(r["confidence"] for r in results) / len(results), 1) if results else 0

    return {
        "holdings":      results,
        "strong_buys":   strong_buys,
        "buys":          buys,
        "sells":         sells,
        "avg_confidence": avg_conf,
        "total_analyzed": len(results),
        "as_of":         datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
    }
