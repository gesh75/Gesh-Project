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


# ── canonical published holdings (the single holding-level calculation) ──────
# Both customer serializers (publish + build_customer_model_context) MUST derive
# holding equity the same way, from the same numbers. They previously each summed
# the SOURCE `equity` field while the reconciliation verdict was computed from
# quantity × current_price, so the two were never compared: a row carrying
# quantity=10, current_price=100, equity=900 published a 1,000 total as
# `reconciles: true` while the canonical total was 1,100.

# Displayed prices are rounded to the cent, so `quantity × displayed_price` can
# differ legitimately from a broker equity computed at full precision by up to
# half a cent per share. The allowance is therefore proportional to size, not a
# flat percentage: a blanket percentage would wave through a real 5% error on a
# large position while flagging honest rounding on a small one.
_DISPLAYED_PRICE_UNIT = Decimal("0.01")


def _equity_tolerance(quantity: Decimal) -> Decimal:
    """max(MONEY_TOLERANCE, |quantity| × half-a-cent + MONEY_TOLERANCE)."""
    half_unit = _DISPLAYED_PRICE_UNIT / Decimal(2)
    return max(portfolio_valuation.MONEY_TOLERANCE,
               abs(quantity) * half_unit + portfolio_valuation.MONEY_TOLERANCE)


def canonical_holdings(raw: list, fields: tuple) -> tuple[list[dict], dict]:
    """Published rows built from CANONICAL equity, plus what the check found.

    Returns (rows, report). Every priced row carries
    ``equity = quantity × current_price`` — the source `equity` is evidence to
    be checked, never the authoritative calculation — and
    ``portfolio_percentage`` is recomputed from those canonical equities so the
    weights always describe the numbers actually shown.

    `current_price` is the normalized key: data_fetcher emits it on BOTH the
    live and snapshot paths (it maps the raw snapshot's `price` at load), so a
    row without it here is genuinely unpriced rather than differently spelled.

    report keys:
      non_finite     — a present-but-corrupt quantity/price/equity (fail closed)
      unpriced       — symbols with no usable current price
      mismatch       — priced rows whose source equity is outside tolerance
      positions_value(Decimal) — Σ canonical equity over priced rows
    """
    rows: list[dict] = []
    report = {"non_finite": False, "unpriced": [], "mismatch": [],
              "positions_value": Decimal(0)}

    for h in raw:
        if not isinstance(h, dict):
            continue
        row = {k: h.get(k) for k in fields}
        q_raw, p_raw, e_raw = h.get("quantity"), h.get("current_price"), h.get("equity")
        for v in (q_raw, p_raw, e_raw):
            if v is not None and not _is_finite_number(v):
                report["non_finite"] = True
        qty, price, src_equity = to_decimal(q_raw), to_decimal(p_raw), to_decimal(e_raw)
        if qty is None and q_raw is not None:
            report["non_finite"] = True

        if qty is None or price is None or price <= 0:
            report["unpriced"].append(str(h.get("symbol") or ""))
            if "equity" in row:
                row["equity"] = None            # never invent a value for an unpriced row
        else:
            canonical = (qty * price).quantize(Decimal("0.01"))
            report["positions_value"] += canonical
            if src_equity is not None and abs(src_equity - canonical) > _equity_tolerance(qty):
                report["mismatch"].append(str(h.get("symbol") or ""))
            if "equity" in row:
                row["equity"] = float(canonical)
        rows.append(row)

    total = report["positions_value"]
    for row in rows:
        if "portfolio_percentage" not in row:
            continue
        eqv = to_decimal(row.get("equity"))
        row["portfolio_percentage"] = (
            round(float(eqv / total * 100), 1) if (eqv is not None and total > 0) else 0.0)
    return rows, report


def _reconciliation_verdict(holdings: list[dict], equity_summary: dict,
                            *, report: dict | None = None) -> tuple[bool, str, str | None]:
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
    # The holding-level findings come from canonical_holdings, which already
    # compared each source equity against quantity × current_price. Ranked
    # worst-first: corruption fails closed before anything else is considered.
    report = report or {}
    if report.get("non_finite"):
        return False, "unavailable", "non_finite_value"

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

    if val.unpriced_symbols or report.get("unpriced"):
        return False, "degraded", "missing_price"
    if report.get("mismatch"):
        # Source equity disagreed with quantity × price beyond the price-display
        # tolerance. The customer output uses the canonical figure; the row-level
        # detail stays in server logs, so the code shipped here is generic.
        return False, "degraded", "holding_equity_mismatch"
    if not val.reconciles:
        return False, "degraded", "invariant_failure"
    return True, "reconciled", None


def _model_day_change(holdings: list[dict], cash: float, calculated_total: float,
                      prior_close: object) -> dict:
    """Day change for the PUBLISHED model — component prior vs component current.

    The owner payload's `adjusted_equity_previous_close` is the broker's previous
    ACCOUNT equity, which includes sleeves the published model does not itemise.
    Comparing today's component total against that yesterday-account total mixes
    two different books and yields a percentage that describes neither, so it is
    deliberately NOT used here.

    Instead the prior is rebuilt from the SAME components: Σ quantity × previous
    session close, plus prior-close cash (current cash, matching
    value_positions' policy — intraday cash moves only on a trade or transfer).

    `prior_close` is a callable(symbol) -> price | None. If it is absent, or any
    published holding has no usable previous close, the day change is reported
    UNAVAILABLE. An honest null beats a percentage built on a mismatched basis."""
    unavailable = {"day_change": None, "day_change_pct": None,
                   "day_change_available": False}
    if prior_close is None or not holdings:
        return unavailable

    prior_positions = Decimal(0)
    for h in holdings:
        qty = to_decimal(h.get("quantity"))
        if qty is None:
            return unavailable
        try:
            prev = to_decimal(prior_close(h.get("symbol")))
        except Exception:  # noqa: BLE001 — a pricing hiccup is "unavailable"
            return unavailable
        if prev is None or prev <= 0:
            return unavailable
        prior_positions += (qty * prev).quantize(Decimal("0.01"))

    prior_cash = to_decimal(cash) or Decimal(0)
    prior_total = prior_positions + prior_cash
    if prior_total <= 0:
        return unavailable
    current = to_decimal(calculated_total)
    if current is None:
        return unavailable
    amount = current - prior_total
    return {"day_change": round(float(amount), 2),
            "day_change_pct": round(float(amount / prior_total * Decimal(100)), 2),
            "day_change_available": True}


def publish(payload: dict, *, now: datetime | None = None,
            prior_close: object = None) -> dict:
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

    # Canonical rows: equity = quantity × current_price, weights recomputed from
    # those equities. The source `equity` is CHECKED against this, never trusted
    # as the calculation (see canonical_holdings).
    holdings, report = canonical_holdings(payload.get("holdings") or [],
                                          PUBLISHED_HOLDING_FIELDS)
    positions_value = float(report["positions_value"])
    cash = _num(eq.get("cash")) or 0.0
    calculated_total = round(positions_value + cash, 2)

    reconciles, valuation_status, degraded_reason = _reconciliation_verdict(
        holdings, eq, report=report)

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
        **_model_day_change(holdings, cash, calculated_total, prior_close),
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
    # SAME canonical holding calculation as publish() — the response serializer
    # and the analysis-context serializer must never compute equity differently.
    holdings, report = canonical_holdings(payload.get("holdings") or [],
                                          CONTEXT_HOLDING_FIELDS)
    positions_value = float(report["positions_value"])
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
        # The owner's Robinhood watchlist is NOT a published fact — symbols the
        # operator merely watches (but does not hold) are private. It used to be
        # copied verbatim here, so a customer hitting an engine that unions
        # watchlist symbols (e.g. /api/agents/news/history) received owner-only
        # tickers that dollar-masking does not redact. Customers have their own
        # per-user watchlist in gesh_auth.db; this model context exposes none.
        "watchlist": [],
        "offline": bool(payload.get("offline")),
        "model_context": True,          # tell for tests and logs
        "source_label": SOURCE_LABEL,
    }
