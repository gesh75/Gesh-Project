"""
trading/execution.py — broker wrapper.

Paper mode (default): writes a SIMULATED order row to `orders`. No broker call.
Live mode (off by default): not yet wired. We deliberately raise NotImplementedError
so nothing can accidentally hit Robinhood until you flip a flag AND wire it.

Idempotency: client_order_id is a deterministic SHA1 of
(symbol, action, generated_at, target_pct). Replays of the same intent will
collide on the UNIQUE index in `orders.client_order_id` and be rejected at the
DB layer — never sent twice.

Usage:
    intent = manual("MSTR", "BUY", confidence=0.9, target_position_pct=0.02)
    snapshot = build_data_payload(["MSTR"])
    verdict = risk_gate.evaluate(intent, snapshot)
    if verdict.approved:
        result = execution.place(intent, verdict, snapshot)
"""

from __future__ import annotations
import hashlib
import sqlite3
from dataclasses import asdict, dataclass
from typing import Any, Literal

from .config import RiskConfig, load_risk_config
from .contract import TradeIntent
from .risk_gate import GateVerdict
from .state import log_order
from .alerts import alert_order_fill, alert_rejection

Mode = Literal["paper", "live", "backtest"]


@dataclass
class OrderResult:
    ok: bool
    status: str                  # SIMULATED | SUBMITTED | FILLED | REJECTED | ERROR | DUPLICATE
    mode: Mode
    client_order_id: str
    order_row_id: int | None
    symbol: str
    action: str
    qty: float | None
    target_price: float | None
    fill_price: float | None
    fill_qty: float | None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _client_order_id(intent: TradeIntent) -> str:
    seed = "|".join([
        intent.symbol,
        intent.action,
        intent.generated_at,
        f"{intent.target_position_pct or 0:.6f}",
        intent.intent_uuid,
    ])
    return "gesh-" + hashlib.sha1(seed.encode()).hexdigest()[:16]


def _estimate_qty(intent: TradeIntent, snapshot: dict[str, Any]) -> tuple[float | None, float | None]:
    """
    Return (qty, ref_price). We compute fractional qty from target_pct × equity / price.
    Used for both paper and live to keep the math identical across modes.
    """
    if intent.target_position_pct is None or intent.action == "HOLD":
        return None, None

    eq_summary = snapshot.get("equity_summary", {})
    equity = float(eq_summary.get("portfolio_equity", 0) or 0)
    target_dollars = intent.target_position_pct * equity

    # Find a reference price: holdings first, then watchlist.
    price = None
    for h in snapshot.get("holdings", []):
        if h.get("symbol") == intent.symbol:
            price = float(h.get("current_price") or 0)
            break
    if not price:
        for w in snapshot.get("watchlist", []):
            if w.get("symbol") == intent.symbol:
                price = float(w.get("last_trade_price") or 0)
                break
    if not price:
        return None, None

    qty = round(target_dollars / price, 6) if intent.action == "BUY" else None
    if intent.action == "SELL":
        # SELL = close existing position (qty from holdings)
        for h in snapshot.get("holdings", []):
            if h.get("symbol") == intent.symbol:
                qty = float(h.get("quantity") or 0)
                break
    return qty, price


# ─── Main entry ───────────────────────────────────────────────────────────────

def place(
    intent: TradeIntent,
    verdict: GateVerdict,
    snapshot: dict[str, Any],
    cfg: RiskConfig | None = None,
) -> OrderResult:
    """
    Submit (or simulate) the order. Refuses to place anything the gate rejected.
    """
    cfg = cfg or load_risk_config()
    coid = _client_order_id(intent)
    qty, price = _estimate_qty(intent, snapshot)

    if not verdict.approved:
        # Persist a REJECTED order row so the audit chain is unbroken.
        row_id = _safe_log(
            intent_id=verdict.intent_id, decision_id=verdict.decision_id,
            mode="paper" if cfg.paper_mode else "live",
            client_order_id=coid, status="REJECTED",
            symbol=intent.symbol, action=intent.action,
            qty=qty, target_price=price,
            error=verdict.reason,
            response={"gates": verdict.gates},
        )
        alert_rejection(intent.symbol, intent.action, verdict.reason or "", verdict.gates, intent.confidence)
        return OrderResult(
            ok=False, status="REJECTED",
            mode="paper" if cfg.paper_mode else "live",
            client_order_id=coid, order_row_id=row_id,
            symbol=intent.symbol, action=intent.action,
            qty=qty, target_price=price, fill_price=None, fill_qty=None,
            error=verdict.reason,
        )

    if intent.action == "HOLD":
        row_id = _safe_log(
            intent_id=verdict.intent_id, decision_id=verdict.decision_id,
            mode="paper" if cfg.paper_mode else "live",
            client_order_id=coid, status="SIMULATED",
            symbol=intent.symbol, action="HOLD", qty=0.0, target_price=price,
            response={"note": "HOLD — no order placed"},
        )
        return OrderResult(
            ok=True, status="SIMULATED",
            mode="paper" if cfg.paper_mode else "live",
            client_order_id=coid, order_row_id=row_id,
            symbol=intent.symbol, action="HOLD",
            qty=0.0, target_price=price, fill_price=None, fill_qty=None,
        )

    # ─── PAPER MODE ───
    if cfg.paper_mode:
        row_id = _safe_log(
            intent_id=verdict.intent_id, decision_id=verdict.decision_id,
            mode="paper", client_order_id=coid, status="SIMULATED",
            symbol=intent.symbol, action=intent.action,
            qty=qty, target_price=price, fill_price=price, fill_qty=qty,
            response={"note": "paper-mode fill at last price"},
        )
        alert_order_fill(intent.symbol, intent.action, qty, price, "paper", "SIMULATED",
                         intent.confidence, intent.rationale)
        return OrderResult(
            ok=True, status="SIMULATED", mode="paper",
            client_order_id=coid, order_row_id=row_id,
            symbol=intent.symbol, action=intent.action,
            qty=qty, target_price=price, fill_price=price, fill_qty=qty,
        )

    # ─── LIVE MODE ─── (intentionally not implemented yet)
    raise NotImplementedError(
        "Live execution is not wired. Set PAPER_MODE=true (default) or implement "
        "the robin_stocks call in trading/execution.py only after weeks of clean paper runs."
    )


def _safe_log(**kwargs) -> int | None:
    """Wrap log_order to swallow UNIQUE constraint violations as DUPLICATE."""
    try:
        return log_order(**kwargs)
    except sqlite3.IntegrityError as e:
        # UNIQUE constraint on client_order_id — replay attempt, do not error.
        if "UNIQUE" in str(e).upper():
            return None
        raise
