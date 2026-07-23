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
from datetime import datetime, timezone
from typing import Any

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
    "day_change_available", "reconciles", "view",
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


def content_version(holdings: list[dict], cash: float | None) -> str:
    """Short, stable content hash of the published composition.

    Stands in for an editorial version number until a real publication pipeline
    exists: identical composition → identical version, any change to the
    published holdings or cash → a new version. Deterministic across processes
    (sorted keys, no clock)."""
    basis = json.dumps(
        {"h": sorted((str(h.get("symbol")), _num(h.get("quantity")))
                     for h in holdings),
         "c": _num(cash)},
        sort_keys=True, separators=(",", ":"))
    return "v" + hashlib.sha256(basis.encode()).hexdigest()[:12]


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

    return {
        "model_portfolio_id": MODEL_PORTFOLIO_ID,
        "version": content_version(holdings, cash),
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
        "reconciles": True,      # by construction: total IS positions + cash
        "view": "model_book",
    }
