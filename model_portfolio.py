"""The published model portfolio — the ONLY portfolio shape a customer receives.

Four portfolios exist in this system and they were never separated in code:

    1. Owner brokerage portfolio   — real Robinhood positions, cash, buying power
    2. Internal execution book     — paper/autopilot state, risk-gate verdicts
    3. Published model portfolio   — THIS: the research product customers buy
    4. Individual customer book    — the customer's own entered positions

A customer session used to receive (1) directly: the raw broker payload with
`source: "robinhood_mcp"`, per-quote `session` identifiers, buying power, average
costs and drawdowns, passed through a middleware that rescaled the dollars. That
made owner account data customer-visible in transformed form, and it meant every
NEW field added to the broker payload leaked by default (audit A-01).

This module inverts that. `publish()` is a **build-up serializer**: it names the
fields the model portfolio consists of and copies only those. A field that is
not named here cannot reach a customer, whether it exists today or is added
tomorrow — the failure mode of a tear-down denylist is silent leakage, and the
failure mode of a build-up allowlist is a visibly missing field.

SCOPE (deliberate): this publishes a *derived view* of the current owner
snapshot. It is not yet a durable, versioned publication pipeline with its own
storage and an editorial publish step — see `docs/REMEDIATION_2026-07-23…` §9.
`version` is therefore a content hash: stable while the composition is stable,
and changing exactly when the published composition changes.

NOT SCALED HERE. The customer-view middleware still normalizes dollars to the
model-book notional, so this module must emit owner-denominated numbers and let
that single scaler run once. Scaling in both places would double-apply.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import portfolio_valuation
from portfolio_valuation import to_decimal

__all__ = [
    "MODEL_PORTFOLIO_ID", "SOURCE_LABEL", "METHODOLOGY",
    "PUBLISHED_HOLDING_FIELDS", "PUBLISHED_TOP_LEVEL_FIELDS",
    "publish", "content_version",
]

MODEL_PORTFOLIO_ID = "gesh-research-model-book"
SOURCE_LABEL = "GESH published model portfolio"
METHODOLOGY = (
    "A published research model portfolio, normalised to a fixed notional. It "
    "mirrors the composition and weights of the strategy GESH researches; it is "
    "not a brokerage account, not your account, and the dollar amounts are "
    "illustrative of the model rather than any real balance. Educational only — "
    "not investment advice."
)

# ── the allowlist ────────────────────────────────────────────────────────────
# Composition and weights only. Deliberately EXCLUDED, with reasons:
#   average_buy_price / percent_change / equity_change — the owner's personal
#       entry economics and unrealised P&L. The model publishes what it holds,
#       not what the operator paid.
#   buying_power / deployable / settled_cash / margin_* / withdrawable_amount —
#       private account capacity; no research value.
#   quote_provenance — carries per-quote `session` identifiers and the
#       `robinhood_mcp` provider tag, i.e. brokerage/session metadata.
#   ecg / drawdown / risk-gate / paper state — internal execution state.
PUBLISHED_HOLDING_FIELDS = ("symbol", "quantity", "current_price", "equity",
                            "portfolio_percentage", "type")
PUBLISHED_TOP_LEVEL_FIELDS = (
    "model_portfolio_id", "version", "published_at", "data_as_of", "holdings",
    "cash", "calculated_total_equity", "source_label", "methodology", "stale",
    "freshness", "positions_market_value", "day_change", "day_change_pct",
    "day_change_available", "reconciles", "valuation_status",
    "degraded_reason", "view",
)

# Anything matching these must never appear in a published payload. This is a
# TEST-FACING tripwire, not the mechanism — the allowlist above is the
# mechanism. Both exist so a mistake fails loudly instead of shipping.
FORBIDDEN_KEY_SUBSTRINGS = (
    "account_number", "account_id", "brokerage", "buying_power", "deployable",
    "settled_cash", "withdrawable", "margin", "pending_deposit",
    "uncleared_deposit", "session", "token", "cookie", "provenance",
    "average_buy_price", "avg_cost", "equity_change", "percent_change",
    "risk_gate", "gate_results", "paper_", "kill_switch", "reported_total",
    "robinhood", "mcp",
)


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def _canonical_decimal(value: object) -> str:
    """One textual form per numeric VALUE, so equal amounts hash equally.

    1, 1.0, "1.0000" and Decimal("1.0000") are the same quantity and must not
    produce different versions; -0 and 0 are the same value. Normalised through
    Decimal (never float) and rendered without an exponent, so 1E+2 and 100 also
    agree. Raises on a value that is not a finite number — a version computed
    over NaN is meaningless."""
    d = to_decimal(value)
    if d is None:
        raise ValueError(f"non-numeric value in model composition: {value!r}")
    d = d.normalize()
    if d == 0:
        return "0"                      # collapses -0, 0, 0.000
    sign, digits, exp = d.as_tuple()
    if isinstance(exp, int) and exp > 0:      # 1E+2 -> 100
        d = d.quantize(Decimal(1))
    return format(d, "f")


def content_version(holdings: list[dict], cash: object) -> str:
    """Content fingerprint of the published composition — NOT an approval.

    This identifies WHAT is published, not that anyone approved publishing it.
    It is deliberately not called `publication_version`: there is no editorial
    publish step yet (see the module docstring), and a name implying sign-off
    over a value that changes whenever a price moves would be a lie in the API.

    SCOPE: composition (symbol + quantity) AND cash. Cash is included because it
    is a published field that changes the portfolio a customer sees; a version
    that ignored it would report "unchanged" across a real change.

    Canonicalisation, so equivalent inputs give one version:
      * symbols trimmed and upper-cased  (`aapl`, ` AAPL ` -> AAPL)
      * quantities and cash through `_canonical_decimal`
      * holdings sorted by symbol (input order is not composition)
      * duplicate symbols SUMMED, then hashed once — two 5-share AAPL rows and
        one 10-share row are the same portfolio
      * a symbol whose summed quantity is zero is dropped, so "held nothing"
        hashes the same however it was expressed
      * negative or non-finite quantities raise: they are not a portfolio this
        module is willing to fingerprint silently
    """
    merged: dict[str, Decimal] = {}
    for h in holdings or []:
        sym = str(h.get("symbol") or "").strip().upper()
        if not sym:
            raise ValueError("holding without a symbol in model composition")
        qty = to_decimal(h.get("quantity"))
        if qty is None:
            raise ValueError(f"{sym}: non-numeric quantity in model composition")
        if qty < 0:
            raise ValueError(f"{sym}: negative quantity in model composition")
        merged[sym] = merged.get(sym, Decimal(0)) + qty
    basis = json.dumps(
        {"h": [[s, _canonical_decimal(q)] for s, q in sorted(merged.items()) if q != 0],
         "c": _canonical_decimal(cash if cash is not None else 0)},
        sort_keys=True, separators=(",", ":"))
    return "v" + hashlib.sha256(basis.encode()).hexdigest()[:12]


def _is_finite_number(x: object) -> bool:
    return (isinstance(x, (int, float)) and not isinstance(x, bool)
            and math.isfinite(x))


def _reconciliation_verdict(holdings: list[dict], equity_summary: dict) -> tuple[bool, str, str | None]:
    """COMPUTED reconciliation for the published model portfolio (GATE 3).

    The published total is the sum of the rows shown, so the equity identity
    (total == positions + cash) holds BY CONSTRUCTION for finite inputs — but a
    literal ``reconciles: True`` also claims it for NaN/inf and missing prices,
    which is exactly the case a customer must not be told reconciles. So the SAME
    holdings are run through the canonical valuation (portfolio_valuation), and
    the verdict is derived from real invariants:

      * non-finite quantity / price / equity / cash  → unavailable (fail closed)
      * any holding with no usable current price      → degraded (missing_price)
      * the canonical identity fails                  → degraded (invariant_failure)
      * otherwise                                     → reconciled

    Returns customer-safe codes only; detailed diagnostics stay in logs via the
    server's `_reconcile_equity_summary`. Never raises — a valuation that cannot
    be evaluated is itself an "unavailable" verdict, not a 500."""
    cash = equity_summary.get("cash")
    if not _is_finite_number(cash):
        return False, "unavailable", "non_finite_value"

    canonical: list[portfolio_valuation.Position] = []
    for h in holdings:
        q, price, eqv = h.get("quantity"), h.get("current_price"), h.get("equity")
        # NaN / inf anywhere → fail closed (unavailable). A value PRESENT but not
        # finite has been corrupted; the arithmetic cannot be trusted.
        for v in (q, price, eqv):
            if v is not None and not _is_finite_number(v):
                return False, "unavailable", "non_finite_value"
        # A missing (None) price/equity is "we don't have it yet", not corruption
        # → degraded/missing_price below, once the canonical valuation confirms it.
        try:
            canonical.append(portfolio_valuation.Position.build(
                h.get("symbol", ""), q, current_price=price))
        except Exception:  # noqa: BLE001 — an unbuildable value is fail-closed
            return False, "unavailable", "non_finite_value"

    try:
        val = portfolio_valuation.value_positions(canonical, cash=cash)
    except Exception:  # noqa: BLE001
        return False, "unavailable", "valuation_error"

    if val.unpriced_symbols:
        return False, "degraded", "missing_price"
    if not val.reconciles:
        return False, "degraded", "invariant_failure"
    return True, "reconciled", None


def publish(payload: dict, *, now: datetime | None = None) -> dict:
    """Build the customer-facing published model portfolio from an owner payload.

    Copies ONLY `PUBLISHED_HOLDING_FIELDS` / `PUBLISHED_TOP_LEVEL_FIELDS`. Uses
    the CANONICAL calculated equity (positions + cash) and never the broker's
    reported headline total — the two disagree by whatever the broker counts
    outside the itemised positions, and publishing a total that does not equal
    its own components is the defect this replaces (audit Q-03).
    """
    stamp = (now or datetime.now(timezone.utc)).isoformat()
    eq = payload.get("equity_summary") or {}
    fresh = payload.get("data_freshness") or {}

    holdings: list[dict] = []
    for h in (payload.get("holdings") or []):
        if not isinstance(h, dict):
            continue
        holdings.append({k: h.get(k) for k in PUBLISHED_HOLDING_FIELDS})

    positions_value = sum((_num(h.get("equity")) or 0.0) for h in holdings)
    cash = _num(eq.get("cash")) or 0.0
    calculated_total = round(positions_value + cash, 2)

    reconciles, valuation_status, degraded_reason = _reconciliation_verdict(holdings, eq)

    # content_version() rejects a non-numeric composition by design (a version
    # over garbage is meaningless). publish() is a customer-facing serializer,
    # though, so a corrupt holding must degrade to an unavailable verdict, never
    # 500 the /api/portfolio route.
    try:
        version = content_version(holdings, cash)
    except (ValueError, TypeError):
        version, reconciles, valuation_status, degraded_reason = (
            "vunavailable", False, "unavailable", "non_finite_value")

    return {
        "model_portfolio_id": MODEL_PORTFOLIO_ID,
        "version": version,
        "published_at": stamp,
        "data_as_of": payload.get("as_of") or fresh.get("data_as_of"),
        "holdings": holdings,
        "cash": round(cash, 2),
        "positions_market_value": round(positions_value, 2),
        "calculated_total_equity": calculated_total,
        "source_label": SOURCE_LABEL,
        "methodology": METHODOLOGY,
        "stale": bool(payload.get("stale_snapshot")) or fresh.get("state") == "stale",
        "freshness": {
            # Re-built field by field: the owner block carries `source`, which is
            # the brokerage provider tag.
            "state": fresh.get("state"),
            "label": fresh.get("label"),
            "age_seconds": fresh.get("age_seconds"),
            "max_expected_age_seconds": fresh.get("max_expected_age_seconds"),
        },
        "day_change": eq.get("day_change"),
        "day_change_pct": eq.get("day_change_pct"),
        "day_change_available": bool(eq.get("day_change_available")),
        "reconciles": reconciles,          # COMPUTED — see _reconciliation_verdict
        "valuation_status": valuation_status,   # reconciled | degraded | unavailable
        "degraded_reason": degraded_reason,     # customer-safe code, None when reconciled
        "view": "model_book",
    }


# ── customer analysis context ────────────────────────────────────────────────
# `publish()` shapes the model portfolio for a customer RESPONSE. Analysis
# routes need the same data in the OWNER PAYLOAD SHAPE, because the engines
# (ECG, regime, tax, benchmarks, FFT, market-data) read `holdings` /
# `equity_summary` directly.
#
# Ten customer-reachable GETs were calling `_resolved_payload()` and feeding the
# RAW owner payload to those engines. Scaling numbers downstream is not
# sanitisation: it leaves every non-numeric private field (source tags, quote
# sessions, execution state) and every NEW owner field untouched on the way IN.
# This builds the engine-shaped context from the SAME allowlist as publish(), so
# a customer analysis can only ever be computed from published facts.
CONTEXT_HOLDING_FIELDS = ("symbol", "quantity", "current_price", "equity",
                          "portfolio_percentage", "type")
CONTEXT_EQUITY_FIELDS = ("portfolio_equity", "cash", "total", "account_equity",
                         "adjusted_equity_previous_close")


def build_customer_model_context(payload: dict) -> dict:
    """Engine-shaped published context. Build-up allowlist, like publish().

    Deliberately omits `average_buy_price` / `percent_change` / `equity_change`
    (the operator's personal entry economics), `quote_provenance` (per-quote
    session ids + brokerage provider tag), `source`, buying power / margin /
    withdrawable / pending deposits, and all execution state. `total` is
    RECOMPUTED from positions + cash so a customer analysis can never be based
    on the broker headline the components do not support."""
    eq_in = payload.get("equity_summary") or {}
    holdings = [{k: h.get(k) for k in CONTEXT_HOLDING_FIELDS}
                for h in (payload.get("holdings") or []) if isinstance(h, dict)]
    positions_value = sum((_num(h.get("equity")) or 0.0) for h in holdings)
    cash = _num(eq_in.get("cash")) or 0.0
    equity_summary = {
        "portfolio_equity": round(positions_value, 2),
        "cash": round(cash, 2),
        "total": round(positions_value + cash, 2),
        "account_equity": round(positions_value + cash, 2),
    }
    prev = _num(eq_in.get("adjusted_equity_previous_close"))
    if prev is not None:
        equity_summary["adjusted_equity_previous_close"] = round(prev, 2)
    return {
        "as_of": payload.get("as_of"),
        "holdings": holdings,
        "equity_summary": equity_summary,
        "watchlist": list(payload.get("watchlist") or []),
        "offline": bool(payload.get("offline")),
        "model_context": True,          # tell for tests and logs
        "source_label": SOURCE_LABEL,
    }
