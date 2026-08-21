"""rh_order_review.py — broker-side preflight for a gate-approved intent.

What this is
------------
Stage 2 of the MCP integration. After the 9-gate `trading/risk_gate.py` approves an
intent, this asks Robinhood's `review_equity_order` tool to SIMULATE that order and
return the broker's own pre-trade alerts (tradability, buying power, collars, PDT,
price bands). It places nothing.

Why it earns its place
----------------------
Paper mode proves the engine's *arithmetic*. It cannot prove the engine's intents are
**executable at the broker** — paper mode knows nothing about halted symbols, collared
limit prices, settled-funds rules or per-account restrictions. Every paper fill in
`orders` is an assumption. This turns that assumption into recorded evidence, at zero
money risk, weeks before anything live is considered.

Safety
------
`_REVIEW_TOOL` is the only tool name this module will send, enforced in `_review_call`.
`place_equity_order` / `cancel_equity_order` appear nowhere in this file. Kept in its own
module (rather than folded into `rh_mcp_data.py`) so that data reads and order-shaped
calls never share an import surface.

Status taxonomy — the important part
------------------------------------
    OK            broker simulated the order; `alerts` may still be non-empty
    REJECTED      broker refused the simulated order outright
    UNAUTHORIZED  the agent token is not authorized to trade this account
    UNAVAILABLE   no MCP session (VPN, expired token, module import failure)
    ERROR         unexpected shape or exception

`UNAUTHORIZED` / `UNAVAILABLE` / `ERROR` are NOT approval. They mean "not checked".
Per rules #45/#59 a degraded state must announce itself rather than resemble a
measurement — never collapse these into a boolean that reads as a pass.

Current state (verified 2026-08-21): the agent OAuth client registered by
`fetch_snapshot_direct.py` holds scope `internal`, which grants the read tools but
NOT the order family. `review_equity_order` returns
``FORBIDDEN: agent not authorized to access this account``
(`rh_error_category: "unauthorized"`). So this module reports UNAUTHORIZED today and
activates by itself if that account authorization is ever granted. Granting it is an
explicit, owner-only decision — it widens what the stored token can do.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_REVIEW_TOOL = "review_equity_order"          # the ONLY tool this module may send

OK = "OK"
REJECTED = "REJECTED"
UNAUTHORIZED = "UNAUTHORIZED"
UNAVAILABLE = "UNAVAILABLE"
ERROR = "ERROR"

#: statuses that represent an actual broker verdict (as opposed to "not checked")
CONCLUSIVE = frozenset({OK, REJECTED})

_lock = threading.Lock()
_session: object | None = None
_failed_at: float = 0.0
_RETRY_S = 300.0

#: once the account returns UNAUTHORIZED there is no point retrying every order in
#: the batch — remember it for the process lifetime and short-circuit.
_unauthorized_seen = False


def reset() -> None:
    """Clear session + memoized authorization state (for tests)."""
    global _session, _failed_at, _unauthorized_seen
    with _lock:
        _session = None
        _failed_at = 0.0
        _unauthorized_seen = False


@dataclass(frozen=True, slots=True)
class ReviewResult:
    """Immutable broker preflight verdict."""

    symbol: str
    side: str
    order_type: str
    status: str
    quantity: str | None = None
    alerts: tuple[str, ...] = ()
    message: str | None = None
    quote: dict = field(default_factory=dict)

    @property
    def checked(self) -> bool:
        """True only when the broker actually rendered a verdict."""
        return self.status in CONCLUSIVE

    @property
    def executable(self) -> bool:
        """True when the broker simulated the order without refusing it.

        False for every non-conclusive status — 'we could not ask' must never
        read as 'the broker said yes'.
        """
        return self.status == OK

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "checked": self.checked,
            "executable": self.executable,
            "alerts": list(self.alerts),
            "message": self.message,
        }


def _direct():
    try:
        import fetch_snapshot_direct as F
        return F
    except BaseException as e:                        # SystemExit-safe, see rh_mcp_data
        logger.warning("rh_order_review: fetch_snapshot_direct unavailable (%s)", e)
        return None


def _get_session():
    global _session, _failed_at
    with _lock:
        if _session is not None:
            return _session
        if _failed_at and (time.time() - _failed_at) < _RETRY_S:
            return None
        F = _direct()
        if F is None:
            _failed_at = time.time()
            return None
        try:
            mcp = F.Mcp(F._access_token())
            mcp.call("initialize", {
                "protocolVersion": F.PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "gesh-order-review", "version": "1.0"},
            })
            mcp.notify_initialized()
            _session = mcp
            _failed_at = 0.0
            return mcp
        except BaseException as e:                    # noqa: BLE001
            logger.warning("rh_order_review: MCP session failed (%s)", e)
            _failed_at = time.time()
            return None


def _review_call(args: dict) -> tuple[str, dict]:
    """Send exactly one review_equity_order call. Returns (status, raw_result)."""
    mcp = _get_session()
    if mcp is None:
        return UNAVAILABLE, {}
    try:
        # Raw tools/call (not Mcp.tool) so the error envelope — isError and
        # _meta.rh_error_category — survives instead of becoming a RuntimeError string.
        result = mcp.call("tools/call", {"name": _REVIEW_TOOL, "arguments": args})
    except BaseException as e:                        # noqa: BLE001
        logger.warning("rh_order_review: review call failed (%s)", e)
        global _session
        with _lock:
            _session = None
        return ERROR, {"exception": str(e)}

    if not isinstance(result, dict):
        return ERROR, {}
    if result.get("isError"):
        category = ((result.get("_meta") or {}).get("rh_error_category") or "").lower()
        text = _first_text(result).lower()
        if category == "unauthorized" or "not authorized" in text or "forbidden" in text:
            return UNAUTHORIZED, result
        return REJECTED, result
    return OK, result


def _first_text(result: dict) -> str:
    for block in result.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            return str(block.get("text") or "")
    return ""


def _payload(result: dict) -> dict:
    """Structured body of a successful review, whatever envelope it arrived in."""
    if result.get("structuredContent"):
        sc = result["structuredContent"]
        return sc.get("data", sc) if isinstance(sc, dict) else {}
    import json
    text = _first_text(result)
    if text:
        try:
            body = json.loads(text)
            return body.get("data", body) if isinstance(body, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _extract_alerts(body: dict) -> tuple[str, ...]:
    """Normalize the broker's pre-trade alerts into plain strings."""
    out: list[str] = []
    for key in ("alerts", "pre_trade_alerts", "warnings", "checks"):
        for a in body.get(key) or []:
            if isinstance(a, str):
                out.append(a)
            elif isinstance(a, dict):
                msg = a.get("message") or a.get("text") or a.get("title") or a.get("type")
                if msg:
                    out.append(str(msg))
    return tuple(out)


def review(
    symbol: str,
    side: str,
    *,
    order_type: str = "market",
    quantity: str | float | None = None,
    limit_price: float | None = None,
    stop_price: float | None = None,
    time_in_force: str | None = None,
    market_hours: str | None = None,
    account: str | None = None,
) -> ReviewResult:
    """Simulate one equity order at the broker. Places nothing. Never raises."""
    global _unauthorized_seen
    side = (side or "").lower()
    order_type = (order_type or "market").lower()

    if side not in ("buy", "sell"):
        return ReviewResult(symbol, side, order_type, ERROR, message=f"bad side {side!r}")
    if not symbol:
        return ReviewResult(symbol, side, order_type, ERROR, message="missing symbol")

    if _unauthorized_seen:
        return ReviewResult(symbol, side, order_type, UNAUTHORIZED,
                            message="agent not authorized to trade this account")

    F = _direct()
    acct = account or (getattr(F, "ACCT", None) if F else None)
    if not acct:
        return ReviewResult(symbol, side, order_type, UNAVAILABLE,
                            message="no account number (GESH_RH_ACCOUNT unset)")

    qty_s = None if quantity is None else f"{float(quantity):.6f}".rstrip("0").rstrip(".")
    args: dict = {"account_number": acct, "symbol": symbol, "side": side, "type": order_type}
    if qty_s:
        args["quantity"] = qty_s
    if limit_price is not None:
        args["limit_price"] = f"{float(limit_price):.2f}"
    if stop_price is not None:
        args["stop_price"] = f"{float(stop_price):.2f}"
    if time_in_force:
        args["time_in_force"] = time_in_force
    if market_hours:
        args["market_hours"] = market_hours

    status, result = _review_call(args)
    if status == UNAUTHORIZED:
        with _lock:
            _unauthorized_seen = True
        return ReviewResult(symbol, side, order_type, UNAUTHORIZED, quantity=qty_s,
                            message=_first_text(result) or "agent not authorized")
    if status in (UNAVAILABLE, ERROR):
        return ReviewResult(symbol, side, order_type, status, quantity=qty_s,
                            message=_first_text(result) or result.get("exception"))
    if status == REJECTED:
        return ReviewResult(symbol, side, order_type, REJECTED, quantity=qty_s,
                            message=_first_text(result) or "broker rejected the simulated order")

    body = _payload(result)
    return ReviewResult(symbol, side, order_type, OK, quantity=qty_s,
                        alerts=_extract_alerts(body),
                        quote=body.get("quote") or {})


def review_action(action: dict) -> ReviewResult:
    """Adapter: review one row of the daily `action_plan`.

    TRIM/SELL → sell, BUY/ADD → buy. Anything else is not an order.
    """
    act = (action.get("action") or "").upper()
    side = {"BUY": "buy", "ADD": "buy", "TRIM": "sell", "SELL": "sell"}.get(act)
    symbol = action.get("symbol") or ""
    if side is None:
        return ReviewResult(symbol, "", "market", ERROR, message=f"not an order action: {act}")
    return review(symbol, side, order_type="market", quantity=action.get("shares"))


def enabled() -> bool:
    """Whether the daily chain should run the preflight (default ON — it is read-only)."""
    return os.getenv("GESH_BROKER_REVIEW", "1").strip().lower() not in ("0", "false", "no")


def blocking() -> bool:
    """Whether a REJECTED review should veto an approved intent.

    Default OFF. Stage 2 gathers evidence; turning this into a veto before the
    evidence exists would change gate behaviour on day one for no measured reason.
    """
    return os.getenv("GESH_REVIEW_BLOCKING", "0").strip().lower() in ("1", "true", "yes")


if __name__ == "__main__":                            # pragma: no cover
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else "NVDA"
    r = review(sym, "sell", quantity=1)
    print(f"{r.symbol} {r.side} x{r.quantity}: status={r.status} checked={r.checked} "
          f"executable={r.executable}")
    if r.message:
        print(f"  message: {r.message}")
    for a in r.alerts:
        print(f"  alert: {a}")
