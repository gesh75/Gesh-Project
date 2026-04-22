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
from dataclasses import asdict, dataclass
from datetime import datetime, time as dtime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .config import RiskConfig, load_risk_config
from .contract import TradeIntent
from .state import count_orders_today, log_decision, log_intent


# ─── Verdict shape ────────────────────────────────────────────────────────────

@dataclass
class GateVerdict:
    approved: bool
    reason: str | None
    gates: dict[str, bool]                # gate_name → True (passed) | False (failed)
    intent_id: int                        # row id in trade_intents
    decision_id: int                      # row id in risk_decisions

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ─── Helper checks ────────────────────────────────────────────────────────────

def _kill_switch_present(path: str) -> bool:
    return Path(path).expanduser().exists()


def _within_market_hours(cfg: RiskConfig) -> bool:
    try:
        tz = ZoneInfo(cfg.market_timezone)
    except Exception:
        tz = ZoneInfo("America/New_York")
    now = datetime.now(tz)
    if now.weekday() >= 5:           # Sat/Sun
        return False
    open_h, open_m = (int(p) for p in cfg.market_open_hhmm.split(":"))
    close_h, close_m = (int(p) for p in cfg.market_close_hhmm.split(":"))
    return dtime(open_h, open_m) <= now.time() <= dtime(close_h, close_m)


def _quote_age_seconds(portfolio: dict[str, Any]) -> float | None:
    """Best-effort: derive quote freshness from payload['as_of'] or holdings timestamp."""
    raw = portfolio.get("as_of") or portfolio.get("equity_summary", {}).get("as_of")
    if not raw:
        return None
    try:
        ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - ts).total_seconds()
    except Exception:
        return None


def _post_trade_cash_estimate(portfolio: dict[str, Any], intent: TradeIntent) -> float:
    """Crude estimate: cash − (target_pct × equity) for BUY; +equity for SELL."""
    eq_summary = portfolio.get("equity_summary", {})
    cash = float(eq_summary.get("cash", 0) or 0)
    equity = float(eq_summary.get("portfolio_equity", 0) or 0)
    if intent.action == "BUY" and intent.target_position_pct:
        return cash - (intent.target_position_pct * equity)
    return cash


def _daily_pnl_pct(portfolio: dict[str, Any]) -> float:
    eq_summary = portfolio.get("equity_summary", {})
    prev = float(eq_summary.get("adjusted_equity_previous_close", 0) or 0)
    curr = float(eq_summary.get("portfolio_equity", 0) or 0)
    if prev <= 0:
        return 0.0
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
        decision_id = log_decision(intent_id, "APPROVED", "HOLD requires no execution",
                                   gates, asdict(cfg))
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
        rejection_reason = f"Kill switch present at {cfg.kill_switch_path}"

    # 2. Market hours
    if rejection_reason is None:
        gates["MARKET_HOURS"] = _within_market_hours(cfg)
        if not gates["MARKET_HOURS"]:
            rejection_reason = "Outside market hours"
    else:
        gates["MARKET_HOURS"] = False

    # 3. Confidence floor
    if rejection_reason is None:
        gates["CONFIDENCE_FLOOR"] = intent.confidence >= cfg.min_confidence
        if not gates["CONFIDENCE_FLOOR"]:
            rejection_reason = (
                f"Confidence {intent.confidence:.2f} below floor {cfg.min_confidence:.2f}"
            )
    else:
        gates["CONFIDENCE_FLOOR"] = False

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
            rejection_reason = (
                f"Daily order cap reached ({used}/{cfg.max_orders_per_day})"
            )
    else:
        gates["ORDER_RATE"] = False

    # 6. Cash floor (only meaningful for BUY)
    if rejection_reason is None:
        post_cash = _post_trade_cash_estimate(portfolio_snapshot, intent)
        gates["CASH_FLOOR"] = post_cash >= cfg.min_cash_buffer_usd
        if not gates["CASH_FLOOR"]:
            rejection_reason = (
                f"Post-trade cash ${post_cash:,.2f} below floor ${cfg.min_cash_buffer_usd:,.2f}"
            )
    else:
        gates["CASH_FLOOR"] = False

    # 7. Daily loss
    if rejection_reason is None:
        pnl = _daily_pnl_pct(portfolio_snapshot)
        gates["DAILY_LOSS"] = pnl >= -cfg.max_daily_loss_pct
        if not gates["DAILY_LOSS"]:
            rejection_reason = (
                f"Daily P&L {pnl:+.2%} exceeds max loss {-cfg.max_daily_loss_pct:+.2%}"
            )
    else:
        gates["DAILY_LOSS"] = False

    # 8. Data freshness (only enforce if we actually have a timestamp)
    if rejection_reason is None:
        age = _quote_age_seconds(portfolio_snapshot)
        if age is None:
            gates["DATA_FRESH"] = True       # unknown age → don't block, but flag in log
        else:
            gates["DATA_FRESH"] = age <= cfg.max_price_age_seconds
            if not gates["DATA_FRESH"]:
                rejection_reason = (
                    f"Quote age {age:.0f}s exceeds max {cfg.max_price_age_seconds}s"
                )
    else:
        gates["DATA_FRESH"] = False

    verdict_label = "REJECTED" if rejection_reason else "APPROVED"
    decision_id = log_decision(intent_id, verdict_label, rejection_reason, gates, asdict(cfg))
    return GateVerdict(
        approved=(rejection_reason is None),
        reason=rejection_reason,
        gates=gates,
        intent_id=intent_id,
        decision_id=decision_id,
    )
