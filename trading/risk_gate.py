"""
trading/risk_gate.py — the only path between a TradeIntent and execution.

8 gates in priority order:
  1. KILL_SWITCH       file at config.kill_switch_path → reject all
  2. MARKET_HOURS      outside 09:30-16:00 ET → reject
  3. CONFIDENCE_FLOOR  intent.confidence < min_confidence → reject
  4. POSITION_CAP      target_position_pct > max_position_pct → reject (do not silently truncate; explicit reject is safer)
  5. ORDER_RATE        orders submitted today >= max_orders_per_day → reject
  6. CASH_FLOOR        post-trade cash estimate < min_cash_buffer_usd → reject
  7. DAILY_LOSS        portfolio P&L today <= -max_daily_loss_pct → reject
  8. DATA_FRESH        no recent quote within max_price_age_seconds → reject

Every call writes ONE row to risk_decisions with the full per-gate map.
Pure: no broker I/O, no network calls. The portfolio snapshot must be passed in.
"""

from __future__ import annotations
import math
from dataclasses import asdict, dataclass
from datetime import datetime, time as dtime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .config import RiskConfig, load_risk_config
from . import extra_gates
from .contract import TradeIntent
from .state import (
    count_orders_today,
    log_decision,
    log_intent,
    paper_realized_pnl_usd_today,
)
from gesh_core.trading.state import LedgerUnavailable

# Shared seam-free risk-gate primitives — single source of truth in gesh-core
# (Phase 3.5). GateVerdict (the verdict type execution.py also imports),
# the kill-switch check, and the CASH_FLOOR math (rule #32). The time- and
# PnL-entangled helpers (_within_market_hours, _quote_age_seconds,
# _daily_pnl_pct) stay local because tests monkeypatch this module's datetime/
# paper-realized seams. This repo keeps its own evaluate() gate composition.
from gesh_core.trading.risk_gate_core import (  # noqa: F401
    GateVerdict,
    _kill_switch_present,
    _post_trade_cash_estimate,
)


# ─── Verdict shape ────────────────────────────────────────────────────────────


# ─── Helper checks ────────────────────────────────────────────────────────────


# Market-regime AVOID gate (synced with gesh-project-trading, rule #40):
# block new longs when SPY is below its trailing 200d MA. In-process 6h cache.
_SPY_TREND_CACHE: dict[str, Any] = {}
_SPY_TREND_TTL_S = 6 * 3600


def _spy_above_ma(ma_days: int = 200) -> bool | None:
    """True if SPY ≥ trailing ma_days SMA, False if below, None if data unknown
    (→ fail-open). Cached 6h so the gate never hammers Yahoo per intent."""
    import time as _t

    hit = _SPY_TREND_CACHE.get("v")
    if hit is not None and (_t.time() - hit[0]) <= _SPY_TREND_TTL_S:
        return hit[1]
    val: bool | None = None
    try:
        # price_source, not raw yfinance: this gate is fail-open, so a Yahoo-429
        # storm made it go blind exactly during stressed markets. The RH fallback
        # keeps it sighted.
        import numpy as _np
        import price_source as _ps

        raw = _ps.yf_download("SPY", period="2y", interval="1d", auto_adjust=True, progress=False)
        closes = raw["Close"].squeeze().dropna().values.astype(float)
        if len(closes) >= max(50, ma_days // 4):
            val = bool(closes[-1] >= float(_np.mean(closes[-ma_days:])))
    except Exception:
        val = None
    # Only cache successful results — caching a failed (None) fetch would pin the
    # gate blind for the full TTL after a transient Yahoo throttle. On failure,
    # fall through so the next call retries instead of serving stale None.
    if val is not None:
        _SPY_TREND_CACHE["v"] = (_t.time(), val)
    return val


def _market_regime_blocks_buy(intent: TradeIntent, cfg: RiskConfig) -> tuple[bool, str]:
    """AVOID gate — blocks BUY/ADD only, when enabled and SPY is in a downtrend.
    Fail-OPEN: unknown SPY data does NOT block (the other gates still apply)."""
    if not getattr(cfg, "market_regime_gate", False) or intent.action != "BUY":
        return False, ""
    if _spy_above_ma(getattr(cfg, "market_regime_ma_days", 200)) is False:
        return True, "Market regime AVOID: SPY below its 200d MA (downtrend) — no new longs"
    return False, ""


def _within_market_hours(cfg: RiskConfig) -> bool:
    try:
        tz = ZoneInfo(cfg.market_timezone)
    except Exception:
        tz = ZoneInfo("America/New_York")
    now = datetime.now(tz)
    if now.weekday() >= 5:  # Sat/Sun
        return False
    try:
        open_h, open_m = (int(p) for p in cfg.market_open_hhmm.split(":"))
        close_h, close_m = (int(p) for p in cfg.market_close_hhmm.split(":"))
    except (ValueError, AttributeError):
        return False  # malformed time config → fail closed (treat as outside hours)
    return dtime(open_h, open_m) <= now.time() <= dtime(close_h, close_m)


# Permitted future-quote clock skew. Code constant, not an env/deployment
# knob — changing it needs reviewer agreement (Astra R5).
MAX_QUOTE_FUTURE_SKEW_SECONDS = 60

_QUOTE_TS_FIELDS = (
    "quote_as_of",
    "updated_at",
    "venue_last_trade_time",
    "last_trade_time",
)


def _norm_symbol(value: Any) -> str:
    return str(value or "").strip().upper()


def _parse_quote_ts(raw: Any) -> datetime | None:
    """Parse an ISO quote timestamp. Naive values are UTC (same as snapshot age)."""
    if raw is None or raw == "":
        return None
    try:
        ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            # A naive ISO timestamp (legacy / hand-written snapshot) parses fine
            # but `now(utc) - naive` raises TypeError → previously swallowed to
            # None → DATA_FRESH rejected EVERY intent on an otherwise-fresh
            # snapshot. The rest of the codebase (server._is_fresh,
            # data_fetcher snapshot age) treats naive as UTC; do the same so the
            # freshness gate actually measures age instead of failing closed on a
            # format quirk. The staleness threshold itself is unchanged.
            #
            # MIRROR: the 9003 sidecar imports gesh-core's risk_gate copy — keep
            # the identical naive→UTC treatment there or autopilot will still
            # false-reject on fresh snapshots (deep-audit P0, PR #186).
            ts = ts.replace(tzinfo=timezone.utc)
        return ts
    except Exception:
        return None


def _row_for_symbol(rows: Any, symbol: str) -> dict[str, Any] | None:
    if not isinstance(rows, list):
        return None
    want = _norm_symbol(symbol)
    for row in rows:
        if isinstance(row, dict) and _norm_symbol(row.get("symbol")) == want:
            return row
    return None


def _mapping_record(mapping: Any, symbol: str) -> Any:
    if not isinstance(mapping, dict):
        return None
    if symbol in mapping:
        return mapping[symbol]
    want = _norm_symbol(symbol)
    if want in mapping:
        return mapping[want]
    for key, value in mapping.items():
        if _norm_symbol(key) == want:
            return value
    return None


def _ts_from_record(rec: dict[str, Any]) -> tuple[datetime | None, str | None]:
    """Return (parsed_ts, status) for a per-symbol quote record.

    status is None when no timestamp field is present, 'ok' when parsed,
    'malformed' when a field is present but unparseable.
    """
    for field in _QUOTE_TS_FIELDS:
        raw = rec.get(field)
        if raw in (None, ""):
            continue
        ts = _parse_quote_ts(raw)
        return (ts, "ok") if ts is not None else (None, "malformed")
    return None, None


def _lookup_symbol_quote(portfolio: dict[str, Any], symbol: str) -> tuple[datetime | None, str]:
    """Resolve the selected quote timestamp for ``symbol``.

    status: 'ok' | 'missing' | 'malformed' | 'no_row'

    A per-symbol row (provenance / holding / watchlist / quotes map) is never
    replaced by the container snapshot as_of — that was the laundering path.
    Container as_of is used only when no per-symbol row exists (daily-chain
    substitution onto payload.as_of, and unit snapshots with only as_of).
    """
    rec = _mapping_record(portfolio.get("quote_provenance"), symbol)
    if isinstance(rec, dict):
        raw = rec.get("quote_as_of")
        if raw in (None, ""):
            return None, "missing"
        ts = _parse_quote_ts(raw)
        return (ts, "ok") if ts is not None else (None, "malformed")

    row_without_ts = False
    holding = _row_for_symbol(portfolio.get("holdings"), symbol)
    if holding is not None:
        ts, status = _ts_from_record(holding)
        if status is not None:
            return ts, status
        row_without_ts = True

    watch = _row_for_symbol(portfolio.get("watchlist"), symbol)
    if watch is not None:
        ts, status = _ts_from_record(watch)
        if status is not None:
            return ts, status
        row_without_ts = True

    quotes = _mapping_record(portfolio.get("quotes"), symbol)
    if isinstance(quotes, dict):
        ts, status = _ts_from_record(quotes)
        if status is not None:
            return ts, status
        row_without_ts = True

    qmap = portfolio.get("quote_as_of")
    if isinstance(qmap, dict):
        want = _norm_symbol(symbol)
        key_hit = False
        raw: Any = None
        if symbol in qmap:
            key_hit, raw = True, qmap[symbol]
        else:
            for key, value in qmap.items():
                if _norm_symbol(key) == want:
                    key_hit, raw = True, value
                    break
        if key_hit:
            if raw in (None, ""):
                return None, "missing"
            ts = _parse_quote_ts(raw)
            return (ts, "ok") if ts is not None else (None, "malformed")

    if row_without_ts:
        return None, "missing"
    return None, "no_row"


def _quote_age_seconds(portfolio: dict[str, Any], symbol: str | None = None) -> float | None:
    """Age of the selected quote vs now, in seconds (negative = future).

    When ``symbol`` is omitted, ages the container ``as_of`` (legacy helper
    tests). When provided, ages that symbol's quote and never lets a fresh
    snapshot stamp stand in for a stale/missing per-symbol quote.
    """
    ts: datetime | None = None
    if symbol:
        ts, status = _lookup_symbol_quote(portfolio, symbol)
        if status == "no_row":
            ts = _parse_quote_ts(
                portfolio.get("as_of") or (portfolio.get("equity_summary") or {}).get("as_of")
            )
        elif status != "ok":
            return None
    else:
        ts = _parse_quote_ts(
            portfolio.get("as_of") or (portfolio.get("equity_summary") or {}).get("as_of")
        )
    if ts is None:
        return None
    return (datetime.now(timezone.utc) - ts).total_seconds()


def _positive_finite(value: Any) -> float | None:
    """A strictly positive, finite number, or None. bool/NaN/inf/str are 'unknown'.

    Producers use ``0.0`` as an "unavailable" sentinel for the previous-close
    equity, so a non-positive reading is treated as missing here rather than as
    a measured zero.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _daily_pnl_pct(portfolio: dict[str, Any], cfg: RiskConfig) -> float | None:
    """Daily P&L as a fraction of account base, for the daily-loss circuit breaker.

    Returns ``None`` when the day's P&L cannot be PROVEN (paper ledger unreadable,
    or LIVE previous-close / current equity missing or non-positive). The caller
    fails closed on ``None``; it must never read as a flat 0% day.

    In PAPER mode this is the paper book's OWN realized P&L today over the
    simulated base (cfg.paper_cash_usd) — NOT the real Robinhood account.
    Gating paper entries on the real account's intraday swing both starves the
    funnel and biases the paper track record toward real up-days. In LIVE mode
    the real account's intraday equity change is used so the breaker protects
    actual capital. (Ported from gesh-project-trading, rule #36.)

    LIVE-mode note: portfolio_equity and adjusted_equity_previous_close must be
    on the SAME basis (full account equity) — callers feeding snapshot data
    must remap (see run_paper_gate2.py), since the snapshot's portfolio_equity
    is positions-only.
    """
    if cfg.paper_mode:
        base = cfg.paper_cash_usd or 0.0
        if base <= 0:
            return 0.0
        try:
            return paper_realized_pnl_usd_today() / base
        except LedgerUnavailable:
            return None
    eq_summary = portfolio.get("equity_summary") or {}
    prev = _positive_finite(eq_summary.get("adjusted_equity_previous_close"))
    curr = _positive_finite(eq_summary.get("portfolio_equity"))
    if prev is None or curr is None:
        return None
    return (curr - prev) / prev


# ─── Main entry ───────────────────────────────────────────────────────────────


def evaluate(
    intent: TradeIntent,
    portfolio_snapshot: dict[str, Any],
    cfg: RiskConfig | None = None,
) -> GateVerdict:
    """
    Run all 8 gates. Persist intent + decision. Return GateVerdict.

    Even HOLD intents pass through and get logged — we want a complete audit
    of every signal the system considered.
    """
    cfg = cfg or load_risk_config()
    intent_id = log_intent(intent.to_dict())

    gates: dict[str, bool] = {}
    rejection_reason: str | None = None

    # HOLD shortcuts: log, no checks, approve trivially.
    if intent.action == "HOLD":
        gates["HOLD_SHORTCUT"] = True
        decision_id = log_decision(
            intent_id, "APPROVED", "HOLD requires no execution", gates, asdict(cfg)
        )
        return GateVerdict(True, None, gates, intent_id, decision_id)

    # Validation first — malformed intent should never even hit a gate.
    problems = intent.validate()
    if problems:
        gates["INTENT_VALID"] = False
        rejection_reason = "Malformed intent: " + "; ".join(problems)
        decision_id = log_decision(intent_id, "REJECTED", rejection_reason, gates, asdict(cfg))
        return GateVerdict(False, rejection_reason, gates, intent_id, decision_id)
    gates["INTENT_VALID"] = True

    # 1. Kill switch
    gates["KILL_SWITCH"] = not _kill_switch_present(cfg.kill_switch_path)
    if not gates["KILL_SWITCH"]:
        rejection_reason = "Kill switch active — trading suspended"

    # 2. Market hours
    if rejection_reason is None:
        gates["MARKET_HOURS"] = _within_market_hours(cfg)
        if not gates["MARKET_HOURS"]:
            rejection_reason = "Outside market hours"
    else:
        gates["MARKET_HOURS"] = False

    # 2b. Market-regime AVOID gate (SPY<200dMA → no new longs). BUY-only,
    # fail-open. Backtest-validated (docs/SIGNAL_PROFITABILITY_2026-06.md).
    if rejection_reason is None:
        _blk, _why = _market_regime_blocks_buy(intent, cfg)
        gates["MARKET_REGIME"] = not _blk
        if _blk:
            rejection_reason = _why
    else:
        gates["MARKET_REGIME"] = False

    # 3. Confidence floor
    if rejection_reason is None:
        gates["CONFIDENCE_FLOOR"] = intent.confidence >= cfg.min_confidence
        if not gates["CONFIDENCE_FLOOR"]:
            rejection_reason = (
                f"Confidence {intent.confidence:.2f} below floor {cfg.min_confidence:.2f}"
            )
    else:
        gates["CONFIDENCE_FLOOR"] = False

    # 3b. Entry-risk gates ported from the engine (audit M-02): earnings proximity, sector
    #     cap, consecutive-loss suspension, correlation heat. BUY only — an exit is never
    #     trapped. Inputs are snapshot data; a gate with no input passes and is recorded
    #     in the decision log as ``gates_unchecked`` (see trading/extra_gates.py).
    if intent.action == "BUY":
        extra_results, extra_reason = extra_gates.check_entry_gates(
            intent, portfolio_snapshot, cfg, blocked=rejection_reason is not None
        )
        gates.update(extra_results)
        if rejection_reason is None:
            rejection_reason = extra_reason
    else:
        gates.update({name: True for name in extra_gates.GATE_NAMES})

    # 4. Position cap
    if rejection_reason is None:
        target = intent.target_position_pct or 0.0
        gates["POSITION_CAP"] = target <= cfg.max_position_pct
        if not gates["POSITION_CAP"]:
            rejection_reason = (
                f"target_position_pct {target:.4f} exceeds cap {cfg.max_position_pct:.4f}"
            )
    else:
        gates["POSITION_CAP"] = False

    # 5. Order rate
    if rejection_reason is None:
        used = count_orders_today()
        gates["ORDER_RATE"] = used < cfg.max_orders_per_day
        if not gates["ORDER_RATE"]:
            rejection_reason = f"Daily order cap reached ({used}/{cfg.max_orders_per_day})"
    else:
        gates["ORDER_RATE"] = False

    # 6. Cash floor (only meaningful for BUY)
    if rejection_reason is None:
        post_cash = _post_trade_cash_estimate(portfolio_snapshot, intent, cfg)
        gates["CASH_FLOOR"] = post_cash >= cfg.min_cash_buffer_usd
        if not gates["CASH_FLOOR"]:
            rejection_reason = (
                f"Post-trade cash ${post_cash:,.2f} below floor ${cfg.min_cash_buffer_usd:,.2f}"
            )
    else:
        gates["CASH_FLOOR"] = False

    # 7. Daily loss
    if rejection_reason is None:
        pnl = _daily_pnl_pct(portfolio_snapshot, cfg)
        if pnl is None and intent.action == "SELL" and not cfg.paper_mode:
            # LIVE exit with no provable baseline: a sell adds no risk, so the
            # breaker must not trap the position. BUYs still fail closed below.
            gates["DAILY_LOSS"] = True
        elif pnl is None:
            gates["DAILY_LOSS"] = False
            rejection_reason = (
                "Paper ledger unavailable — cannot prove daily loss"
                if cfg.paper_mode
                else "Previous-close equity unavailable — cannot prove daily loss"
            )
        else:
            gates["DAILY_LOSS"] = pnl >= -cfg.max_daily_loss_pct
            if not gates["DAILY_LOSS"]:
                rejection_reason = (
                    f"Daily P&L {pnl:+.2%} exceeds max loss {-cfg.max_daily_loss_pct:+.2%}"
                )
    else:
        gates["DAILY_LOSS"] = False

    # 8. Data freshness — age the selected symbol's quote, not the snapshot.
    if rejection_reason is None:
        age = _quote_age_seconds(portfolio_snapshot, symbol=intent.symbol)
        # Mode-aware: LIVE keeps the tight 300s; PAPER tolerates the daily chain's
        # own multi-minute pipeline (still rejects a genuinely stale prior-day
        # snapshot). Missing timestamp still fails closed in both modes.
        max_age = cfg.max_price_age_seconds_paper if cfg.paper_mode else cfg.max_price_age_seconds
        if age is None:
            gates["DATA_FRESH"] = False  # fail closed: no timestamp → cannot prove freshness
            rejection_reason = (
                f"Quote age unknown — no parseable quote timestamp for {intent.symbol}"
            )
        elif age < -MAX_QUOTE_FUTURE_SKEW_SECONDS:
            gates["DATA_FRESH"] = False
            rejection_reason = (
                f"Quote timestamp is {abs(age):.0f}s in the future "
                f"(max skew {MAX_QUOTE_FUTURE_SKEW_SECONDS}s)"
            )
        else:
            effective_age = 0.0 if age < 0 else age
            gates["DATA_FRESH"] = effective_age <= max_age
            if not gates["DATA_FRESH"]:
                rejection_reason = f"Quote age {effective_age:.0f}s exceeds max {max_age}s"
    else:
        gates["DATA_FRESH"] = False

    verdict_label = "REJECTED" if rejection_reason else "APPROVED"
    config_snapshot = asdict(cfg)
    config_snapshot["gates_unchecked"] = extra_gates.unchecked_gates(intent, portfolio_snapshot)
    decision_id = log_decision(intent_id, verdict_label, rejection_reason, gates, config_snapshot)
    return GateVerdict(
        approved=(rejection_reason is None),
        reason=rejection_reason,
        gates=gates,
        intent_id=intent_id,
        decision_id=decision_id,
    )
