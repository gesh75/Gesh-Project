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
    # Deterministic over (symbol, action, minute-bucket, target_pct) so an HTTP
    # retry of the SAME logical order collides on the UNIQUE index and is rejected
    # as a duplicate. intent_uuid is intentionally excluded — it is regenerated per
    # TradeIntent and would defeat retry idempotency.
    seed = "|".join([
        intent.symbol,
        intent.action,
        (intent.generated_at or "")[:16],   # YYYY-MM-DDTHH:MM — minute granularity
        f"{intent.target_position_pct or 0:.6f}",
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

    qty = None
    if intent.action == "BUY":
        # BUY: how many shares to reach target_dollars from current equity
        current_equity = 0.0
        for h in snapshot.get("holdings", []):
            if h.get("symbol") == intent.symbol:
                current_equity = float(h.get("equity") or 0)
                break
        delta_dollars = max(target_dollars - current_equity, 0.0)
        qty = round(delta_dollars / price, 6) if delta_dollars > 0 else 0.0

    elif intent.action == "SELL":
        # SELL: compute delta-to-target, NOT close entire position.
        # target_position_pct=0 means full close; otherwise trim to that %.
        current_qty    = 0.0
        current_equity = 0.0
        for h in snapshot.get("holdings", []):
            if h.get("symbol") == intent.symbol:
                current_qty    = float(h.get("quantity") or 0)
                current_equity = float(h.get("equity") or 0)
                break
        target_keep_dollars = target_dollars  # already computed from target_pct * portfolio
        sell_dollars = max(current_equity - target_keep_dollars, 0.0)
        # When already at/below target weight (sell_dollars==0) this must be a
        # no-op (qty 0), NOT a full-position close. A genuine full close passes
        # target_position_pct=0.0 -> target_keep_dollars=0 -> sell_dollars=equity.
        qty = round(sell_dollars / price, 6) if sell_dollars > 0 and price > 0 else 0.0

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

    # A priced BUY/SELL needs a reference price. Symbols neither held nor in the
    # snapshot watchlist resolve to None — fail SOFT with an ERROR row, never crash
    # the caller's per-symbol loop on `None * SLIP`.
    if price is None or qty is None:
        err = f"no reference price for {intent.symbol} (not in holdings/watchlist)"
        row_id = _safe_log(
            intent_id=verdict.intent_id, decision_id=verdict.decision_id,
            mode="paper" if cfg.paper_mode else "live",
            client_order_id=coid, status="ERROR",
            symbol=intent.symbol, action=intent.action,
            qty=qty, target_price=price, error=err,
            response={"note": "missing reference price — order not placed"},
        )
        return OrderResult(
            ok=False, status="ERROR",
            mode="paper" if cfg.paper_mode else "live",
            client_order_id=coid, order_row_id=row_id,
            symbol=intent.symbol, action=intent.action,
            qty=qty, target_price=price, fill_price=None, fill_qty=None,
            error=err,
        )

    # A BUY/SELL that sizes to (effectively) zero shares must NOT become a fill.
    # _estimate_qty returns 0.0 when the target allocation is already met or sub-share
    # rounding collapses to 0. Simulating it opens a PHANTOM paper position that books
    # $0 P&L yet still pads the closed-trade count toward the autonomy gate. Keep an
    # audit row but status='SKIPPED' (≠ SIMULATED) so it is never promoted to a fill.
    if qty <= 0:
        skip_note = (
            f"{intent.symbol} {intent.action} sizes to {qty} shares "
            "(target allocation already met) — skipped, no fill"
        )
        row_id = _safe_log(
            intent_id=verdict.intent_id, decision_id=verdict.decision_id,
            mode="paper" if cfg.paper_mode else "live",
            client_order_id=coid, status="SKIPPED",
            symbol=intent.symbol, action=intent.action,
            qty=qty, target_price=price,
            response={"note": skip_note},
        )
        return OrderResult(
            ok=False, status="SKIPPED",
            mode="paper" if cfg.paper_mode else "live",
            client_order_id=coid, order_row_id=row_id,
            symbol=intent.symbol, action=intent.action,
            qty=qty, target_price=price, fill_price=None, fill_qty=None,
            error=skip_note,
        )

    # ─── PAPER MODE ───
    if cfg.paper_mode:
        # Realistic slippage: 0.10%/side (bid-ask + impact); round-trip ≈ 0.20%.
        # Equities → $0 commission (slippage covers spread); crypto is NOT
        # fee-modeled here so crypto paper P&L is optimistic — don't gate live crypto on it.
        SLIP = 0.001
        fill_price = round(price * (1 + SLIP) if intent.action == "BUY" else price * (1 - SLIP), 4)
        row_id = _safe_log(
            intent_id=verdict.intent_id, decision_id=verdict.decision_id,
            mode="paper", client_order_id=coid, status="SIMULATED",
            symbol=intent.symbol, action=intent.action,
            qty=qty, target_price=price, fill_price=fill_price, fill_qty=qty,
            response={"note": f"paper-mode fill with 0.10% slippage (last={price} fill={fill_price})"},
        )
        alert_order_fill(intent.symbol, intent.action, qty, fill_price, "paper", "SIMULATED",
                         intent.confidence, intent.rationale)
        return OrderResult(
            ok=True, status="SIMULATED", mode="paper",
            client_order_id=coid, order_row_id=row_id,
            symbol=intent.symbol, action=intent.action,
            qty=qty, target_price=price, fill_price=fill_price, fill_qty=qty,
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
