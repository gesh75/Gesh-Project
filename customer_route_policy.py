"""Stage A — Customer Route Policy Declaration (declaration-only).

This module is behavior-neutral declaration code. It defines exact string Enums
and frozen dataclasses for AccessPolicy, InputContext, OutputPolicy,
MissingContextPolicy, FieldKind, FinancialField, and RoutePolicy.

It provides get_all_route_policies() returning an immutable tuple and
get_route_policy(method, path) performing exact key lookup (KeyError on unknown;
no normalization, no prefixes, no fallback).

Duplicate (method, path) entries are validated at import time.

Stage A is declaration-only: no server import, no runtime enforcement,
no side effects. All 95 current runtime /api routes are explicitly declared
with their exact handler names from server.py.

No DATASET_SCALED routes exist in Stage A.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import FrozenSet, Tuple


class AccessPolicy(str, Enum):
    PUBLIC = "PUBLIC"
    CUSTOMER = "CUSTOMER"
    OWNER_ONLY = "OWNER_ONLY"


class InputContext(str, Enum):
    NONE = "NONE"
    CUSTOMER_BOOK = "CUSTOMER_BOOK"
    CURRENT_MODEL = "CURRENT_MODEL"
    HISTORICAL_DATASET = "HISTORICAL_DATASET"


class OutputPolicy(str, Enum):
    RAW_SAFE = "RAW_SAFE"
    PUBLISHED_PORTFOLIO = "PUBLISHED_PORTFOLIO"
    REQUEST_SCALED = "REQUEST_SCALED"
    DATASET_SCALED = "DATASET_SCALED"
    SANITIZE_NO_SCALE = "SANITIZE_NO_SCALE"
    BLOCK = "BLOCK"


class MissingContextPolicy(str, Enum):
    PASS_THROUGH = "PASS_THROUGH"
    TYPED_503 = "TYPED_503"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    OWNER_ONLY = "OWNER_ONLY"


class FieldKind(str, Enum):
    PORTFOLIO_DOLLARS = "portfolio_dollars"
    PORTFOLIO_QUANTITIES = "portfolio_quantities"
    PUBLIC_PRICE = "public_price"
    PUBLIC_EXTERNAL_USD = "public_external_usd"
    PERCENTAGE = "percentage"
    MONEY_ARRAY = "money_array"
    PROSE_REDACTION = "prose_redaction"


@dataclass(frozen=True)
class FinancialField:
    name: str
    kind: FieldKind
    description: str = ""


@dataclass(frozen=True)
class RoutePolicy:
    method: str
    path: str
    handler_name: str
    access_policy: AccessPolicy
    input_context: InputContext
    output_policy: OutputPolicy
    missing_context_policy: MissingContextPolicy
    financial_fields: Tuple[FinancialField, ...] = field(default_factory=tuple)
    scaling_basis_contract: str = ""
    rationale: str = ""
    forbidden_private_fields: FrozenSet[str] = field(default_factory=frozenset)
    schema_proof: str = ""


def _rp(
    method: str,
    path: str,
    handler_name: str,
    access_policy: AccessPolicy,
    input_context: InputContext,
    output_policy: OutputPolicy,
    missing_context_policy: MissingContextPolicy,
    financial_fields: Tuple[FinancialField, ...] = (),
    scaling_basis_contract: str = "",
    rationale: str = "",
    forbidden_private_fields: FrozenSet[str] = frozenset(),
    schema_proof: str = "",
) -> RoutePolicy:
    return RoutePolicy(
        method=method,
        path=path,
        handler_name=handler_name,
        access_policy=access_policy,
        input_context=input_context,
        output_policy=output_policy,
        missing_context_policy=missing_context_policy,
        financial_fields=financial_fields,
        scaling_basis_contract=scaling_basis_contract,
        rationale=rationale,
        forbidden_private_fields=forbidden_private_fields,
        schema_proof=schema_proof,
    )


_COMMON_FORBIDDEN: FrozenSet[str] = frozenset(
    {
        "cash",
        "equity",
        "total_equity",
        "calculated_total_equity",
        "buying_power",
        "margin_buying_power",
        "portfolio_cash",
        "settled_cash",
        "pending_deposits",
        "cost_basis",
        "weights",
        "quantity",
        "shares",
        "deployable",
        "withdrawable_amount",
        "weight",
        "average_buy_price",
        "portfolio_percentage",
        "account_number",
        "account_id",
        "brokerage_account_id",
        "account_equity",
        "portfolio_equity",
        "extended_hours_equity",
    }
)

_MARKET_DATA_FORBIDDEN: FrozenSet[str] = frozenset(
    s for s in _COMMON_FORBIDDEN if s != "shares"
)

_UNPROVEN_HISTORICAL_BASIS: str = (
    "requires an immutable record with original composition or total basis, "
    "source timestamp, stable dataset/composition ID, and one dataset-level "
    "normalization anchor"
)

_POLICIES: Tuple[RoutePolicy, ...] = (
    # =====================================================================
    # OWNER_ONLY routes (require_api_key protected) — input NONE, BLOCK, OWNER_ONLY
    # =====================================================================
    _rp("POST", "/api/analyze", "run_analysis", AccessPolicy.OWNER_ONLY, InputContext.NONE, OutputPolicy.BLOCK, MissingContextPolicy.OWNER_ONLY, rationale="Protected by require_api_key; mutates shared engine and spends owner LLM quota."),
    _rp("POST", "/api/deep-analysis", "run_deep_analysis", AccessPolicy.OWNER_ONLY, InputContext.NONE, OutputPolicy.BLOCK, MissingContextPolicy.OWNER_ONLY, rationale="Protected by require_api_key; mutates shared engine and spends owner LLM quota."),
    _rp("POST", "/api/monte-carlo", "monte_carlo", AccessPolicy.OWNER_ONLY, InputContext.NONE, OutputPolicy.BLOCK, MissingContextPolicy.OWNER_ONLY, rationale="Protected by require_api_key; mutates shared engine and spends owner LLM quota."),
    _rp("POST", "/api/agents/sec", "agent_sec", AccessPolicy.OWNER_ONLY, InputContext.NONE, OutputPolicy.BLOCK, MissingContextPolicy.OWNER_ONLY, rationale="Protected by require_api_key; mutates shared engine and spends owner LLM quota."),
    _rp("POST", "/api/agents/news", "agent_news", AccessPolicy.OWNER_ONLY, InputContext.NONE, OutputPolicy.BLOCK, MissingContextPolicy.OWNER_ONLY, rationale="Protected by require_api_key; mutates shared engine and spends owner LLM quota."),
    _rp("POST", "/api/agents/fundamentals", "agent_fundamentals", AccessPolicy.OWNER_ONLY, InputContext.NONE, OutputPolicy.BLOCK, MissingContextPolicy.OWNER_ONLY, rationale="Protected by require_api_key; mutates shared engine and spends owner LLM quota."),
    _rp("POST", "/api/screener", "run_screener", AccessPolicy.OWNER_ONLY, InputContext.NONE, OutputPolicy.BLOCK, MissingContextPolicy.OWNER_ONLY, rationale="Protected by require_api_key; mutates shared engine and spends owner LLM quota."),
    _rp("POST", "/api/kelly", "kelly_endpoint", AccessPolicy.OWNER_ONLY, InputContext.NONE, OutputPolicy.BLOCK, MissingContextPolicy.OWNER_ONLY, rationale="Protected by require_api_key; mutates shared engine and spends owner LLM quota."),
    _rp("POST", "/api/consensus", "consensus_endpoint", AccessPolicy.OWNER_ONLY, InputContext.NONE, OutputPolicy.BLOCK, MissingContextPolicy.OWNER_ONLY, rationale="Protected by require_api_key; mutates shared engine and spends owner LLM quota."),
    _rp("GET", "/api/config", "get_config", AccessPolicy.OWNER_ONLY, InputContext.NONE, OutputPolicy.BLOCK, MissingContextPolicy.OWNER_ONLY, rationale="Protected by require_api_key; owner config mutation surface."),
    _rp("POST", "/api/config", "set_config", AccessPolicy.OWNER_ONLY, InputContext.NONE, OutputPolicy.BLOCK, MissingContextPolicy.OWNER_ONLY, rationale="Protected by require_api_key; owner config mutation surface."),
    _rp("POST", "/api/scanner/consolidation", "consolidation_zone_batch", AccessPolicy.OWNER_ONLY, InputContext.NONE, OutputPolicy.BLOCK, MissingContextPolicy.OWNER_ONLY, rationale="Protected by require_api_key; scanner batch uses owner engine."),
    _rp("POST", "/api/scanner/sector", "sector_hot_batch", AccessPolicy.OWNER_ONLY, InputContext.NONE, OutputPolicy.BLOCK, MissingContextPolicy.OWNER_ONLY, rationale="Protected by require_api_key; scanner batch uses owner engine."),
    _rp("POST", "/api/trades/log", "log_trade_endpoint", AccessPolicy.OWNER_ONLY, InputContext.NONE, OutputPolicy.BLOCK, MissingContextPolicy.OWNER_ONLY, rationale="Protected by require_api_key; trade logging mutates owner state."),
    _rp("POST", "/api/trades/{trade_id}/close", "close_trade_endpoint", AccessPolicy.OWNER_ONLY, InputContext.NONE, OutputPolicy.BLOCK, MissingContextPolicy.OWNER_ONLY, rationale="Protected by require_api_key; trade mutation."),
    _rp("POST", "/api/paper/run", "paper_run_endpoint", AccessPolicy.OWNER_ONLY, InputContext.NONE, OutputPolicy.BLOCK, MissingContextPolicy.OWNER_ONLY, rationale="Protected by require_api_key; paper execution mutates owner state."),
    _rp("POST", "/api/paper/check-closes", "paper_check_closes", AccessPolicy.OWNER_ONLY, InputContext.NONE, OutputPolicy.BLOCK, MissingContextPolicy.OWNER_ONLY, rationale="Protected by require_api_key; paper execution mutates owner state."),
    _rp("POST", "/api/trading/dry-run", "trading_dry_run", AccessPolicy.OWNER_ONLY, InputContext.NONE, OutputPolicy.BLOCK, MissingContextPolicy.OWNER_ONLY, rationale="Protected by require_api_key; trading execution surface."),
    _rp("POST", "/api/trading/paper-order", "trading_paper_order", AccessPolicy.OWNER_ONLY, InputContext.NONE, OutputPolicy.BLOCK, MissingContextPolicy.OWNER_ONLY, rationale="Protected by require_api_key; trading execution surface."),
    _rp("POST", "/api/trading/consensus-order", "trading_consensus_order", AccessPolicy.OWNER_ONLY, InputContext.NONE, OutputPolicy.BLOCK, MissingContextPolicy.OWNER_ONLY, rationale="Protected by require_api_key; trading execution surface."),
    _rp("POST", "/api/trading/simulate-session", "trading_simulate_session", AccessPolicy.OWNER_ONLY, InputContext.NONE, OutputPolicy.BLOCK, MissingContextPolicy.OWNER_ONLY, rationale="Protected by require_api_key; trading execution surface."),
    _rp("POST", "/api/trading/deep-debate/{symbol}", "trading_deep_debate", AccessPolicy.OWNER_ONLY, InputContext.NONE, OutputPolicy.BLOCK, MissingContextPolicy.OWNER_ONLY, rationale="Protected by require_api_key; trading execution surface."),
    _rp("GET", "/api/trading/gate-status", "trading_gate_status", AccessPolicy.OWNER_ONLY, InputContext.NONE, OutputPolicy.BLOCK, MissingContextPolicy.OWNER_ONLY, rationale="Protected by require_api_key; trading gate introspection."),
    _rp("POST", "/api/ollama/analyze", "ollama_removed_endpoint", AccessPolicy.OWNER_ONLY, InputContext.NONE, OutputPolicy.BLOCK, MissingContextPolicy.OWNER_ONLY, rationale="Protected by require_api_key; legacy owner-only LLM surface."),
    _rp("POST", "/api/ollama/second-opinion/{symbol}", "ollama_removed_endpoint", AccessPolicy.OWNER_ONLY, InputContext.NONE, OutputPolicy.BLOCK, MissingContextPolicy.OWNER_ONLY, rationale="Protected by require_api_key; legacy owner-only LLM surface."),
    _rp("POST", "/api/gemini/analyze", "gemini_analyze_endpoint", AccessPolicy.OWNER_ONLY, InputContext.NONE, OutputPolicy.BLOCK, MissingContextPolicy.OWNER_ONLY, rationale="Protected by require_api_key; owner-only Gemini surface."),
    _rp("POST", "/api/gemini/second-opinion/{symbol}", "gemini_second_opinion_endpoint", AccessPolicy.OWNER_ONLY, InputContext.NONE, OutputPolicy.BLOCK, MissingContextPolicy.OWNER_ONLY, rationale="Protected by require_api_key; owner-only Gemini surface."),
    _rp("POST", "/api/gemini/market-commentary", "gemini_market_commentary_endpoint", AccessPolicy.OWNER_ONLY, InputContext.NONE, OutputPolicy.BLOCK, MissingContextPolicy.OWNER_ONLY, rationale="Protected by require_api_key; owner-only Gemini surface."),
    # usage/summary is explicitly OWNER_ONLY (aggregate all-customer usage)
    _rp("GET", "/api/usage/summary", "usage_summary_endpoint", AccessPolicy.OWNER_ONLY, InputContext.NONE, OutputPolicy.BLOCK, MissingContextPolicy.OWNER_ONLY, rationale="Returns aggregate all-customer usage; owner-only introspection endpoint."),

    # =====================================================================
    # PUBLIC routes (exactly the three specified)
    # =====================================================================
    _rp("POST", "/api/billing/webhook", "billing_webhook", AccessPolicy.PUBLIC, InputContext.NONE, OutputPolicy.RAW_SAFE, MissingContextPolicy.NOT_APPLICABLE, rationale="Public Lemon Squeezy webhook (HMAC-gated ingress)."),
    _rp("GET", "/api/congress", "api_congress", AccessPolicy.PUBLIC, InputContext.NONE, OutputPolicy.RAW_SAFE, MissingContextPolicy.NOT_APPLICABLE, rationale="Public congress trading data source."),
    _rp("GET", "/api/congress/{symbol}", "api_congress_symbol", AccessPolicy.PUBLIC, InputContext.NONE, OutputPolicy.RAW_SAFE, MissingContextPolicy.NOT_APPLICABLE, rationale="Public congress trading data source for single symbol."),

    # =====================================================================
    # CUSTOMER_OWNED routes — RAW_SAFE + NOT_APPLICABLE (mybook: CUSTOMER_BOOK input; others: NONE)
    # =====================================================================
    _rp("GET", "/api/mybook", "mybook_get", AccessPolicy.CUSTOMER, InputContext.CUSTOMER_BOOK, OutputPolicy.RAW_SAFE, MissingContextPolicy.NOT_APPLICABLE, rationale="Customer-owned book; raw customer dollars and quantities."),
    _rp("POST", "/api/mybook", "mybook_set", AccessPolicy.CUSTOMER, InputContext.CUSTOMER_BOOK, OutputPolicy.RAW_SAFE, MissingContextPolicy.NOT_APPLICABLE, rationale="Customer-owned book; raw customer dollars and quantities."),
    _rp("DELETE", "/api/mybook/{symbol}", "mybook_remove", AccessPolicy.CUSTOMER, InputContext.CUSTOMER_BOOK, OutputPolicy.RAW_SAFE, MissingContextPolicy.NOT_APPLICABLE, rationale="Customer-owned book; raw customer dollars and quantities."),
    _rp("POST", "/api/mybook/clear", "mybook_clear", AccessPolicy.CUSTOMER, InputContext.CUSTOMER_BOOK, OutputPolicy.RAW_SAFE, MissingContextPolicy.NOT_APPLICABLE, rationale="Customer-owned book; raw customer dollars and quantities."),
    _rp("POST", "/api/mybook/analyze", "mybook_analyze", AccessPolicy.CUSTOMER, InputContext.CUSTOMER_BOOK, OutputPolicy.RAW_SAFE, MissingContextPolicy.NOT_APPLICABLE, rationale="Customer-owned book analysis; raw customer dollars and quantities."),
    _rp("POST", "/api/mybook/import-watchlist", "mybook_import_watchlist", AccessPolicy.CUSTOMER, InputContext.CUSTOMER_BOOK, OutputPolicy.RAW_SAFE, MissingContextPolicy.NOT_APPLICABLE, rationale="Customer-owned book; raw customer dollars and quantities."),
    _rp("POST", "/api/mybook/bulk", "mybook_bulk", AccessPolicy.CUSTOMER, InputContext.CUSTOMER_BOOK, OutputPolicy.RAW_SAFE, MissingContextPolicy.NOT_APPLICABLE, rationale="Customer-owned book; raw customer dollars and quantities."),
    _rp("POST", "/api/mybook/deploy-cash", "mybook_deploy_cash", AccessPolicy.CUSTOMER, InputContext.CUSTOMER_BOOK, OutputPolicy.RAW_SAFE, MissingContextPolicy.NOT_APPLICABLE, rationale="Customer-owned book; raw customer dollars and quantities."),
    _rp("GET", "/api/mybook/summary", "mybook_summary", AccessPolicy.CUSTOMER, InputContext.CUSTOMER_BOOK, OutputPolicy.RAW_SAFE, MissingContextPolicy.NOT_APPLICABLE, rationale="Customer-owned book; raw customer dollars and quantities."),
    _rp("GET", "/api/account/export", "account_export", AccessPolicy.CUSTOMER, InputContext.NONE, OutputPolicy.RAW_SAFE, MissingContextPolicy.NOT_APPLICABLE, rationale="Customer account export; raw customer data."),
    _rp("POST", "/api/account/delete", "account_delete", AccessPolicy.CUSTOMER, InputContext.NONE, OutputPolicy.RAW_SAFE, MissingContextPolicy.NOT_APPLICABLE, rationale="Customer account deletion; raw customer data."),
    _rp("GET", "/api/me", "whoami", AccessPolicy.CUSTOMER, InputContext.NONE, OutputPolicy.RAW_SAFE, MissingContextPolicy.NOT_APPLICABLE, rationale="Customer identity; raw customer data."),
    _rp("GET", "/api/watchlist", "watchlist_get", AccessPolicy.CUSTOMER, InputContext.NONE, OutputPolicy.RAW_SAFE, MissingContextPolicy.NOT_APPLICABLE, rationale="Customer watchlist; raw customer data."),
    _rp("POST", "/api/watchlist", "watchlist_add", AccessPolicy.CUSTOMER, InputContext.NONE, OutputPolicy.RAW_SAFE, MissingContextPolicy.NOT_APPLICABLE, rationale="Customer watchlist; raw customer data."),
    _rp("DELETE", "/api/watchlist/{symbol}", "watchlist_remove", AccessPolicy.CUSTOMER, InputContext.NONE, OutputPolicy.RAW_SAFE, MissingContextPolicy.NOT_APPLICABLE, rationale="Customer watchlist; raw customer data."),
    _rp("GET", "/api/preferences", "preferences_get", AccessPolicy.CUSTOMER, InputContext.NONE, OutputPolicy.RAW_SAFE, MissingContextPolicy.NOT_APPLICABLE, rationale="Customer preferences; raw customer data."),
    _rp("POST", "/api/preferences", "preferences_set", AccessPolicy.CUSTOMER, InputContext.NONE, OutputPolicy.RAW_SAFE, MissingContextPolicy.NOT_APPLICABLE, rationale="Customer preferences; raw customer data."),

    # =====================================================================
    # GET /api/portfolio — PUBLISHED_PORTFOLIO anchored by its own calculated_total_equity
    # =====================================================================
    _rp(
        "GET",
        "/api/portfolio",
        "get_portfolio",
        AccessPolicy.CUSTOMER,
        InputContext.CURRENT_MODEL,
        OutputPolicy.PUBLISHED_PORTFOLIO,
        MissingContextPolicy.TYPED_503,
        scaling_basis_contract="eventual factor equals configured model-book notional divided by data.calculated_total_equity from that same response; a missing or nonpositive basis maps to TYPED_503; an unrelated snapshot must never be substituted.",
        rationale="Live portfolio view published for the authenticated customer.",
    ),

    # =====================================================================
    # SANITIZE_NO_SCALE current-model symbol-selection routes (7 explicit)
    # =====================================================================
    _rp(
        "GET",
        "/api/market-data",
        "market_data_endpoint",
        AccessPolicy.CUSTOMER,
        InputContext.CURRENT_MODEL,
        OutputPolicy.SANITIZE_NO_SCALE,
        MissingContextPolicy.TYPED_503,
        forbidden_private_fields=_MARKET_DATA_FORBIDDEN,
        schema_proof=(
            "Returns only public per-symbol market data with response "
            "data.analyst_targets, data.performance, data.insider_trades, "
            "data.ownership, data.as_of. Insider shares/value are public "
            "transactions. CURRENT_MODEL contributes symbol selection only "
            "and excludes owner cash, equity, quantities, cost basis, weights, "
            "buying power, margin, and owner-sizing prose. No owner cash/equity/"
            "quantity/cost basis/weights/buying power/margin or owner-sizing "
            "prose is present in the response schema."
        ),
        rationale="Current-model symbol selection returning public analyst/institutional data only.",
    ),
    _rp(
        "GET",
        "/api/regime",
        "regime_endpoint",
        AccessPolicy.CUSTOMER,
        InputContext.CURRENT_MODEL,
        OutputPolicy.SANITIZE_NO_SCALE,
        MissingContextPolicy.TYPED_503,
        forbidden_private_fields=_COMMON_FORBIDDEN,
        schema_proof=(
            "Returns only public per-symbol regime data with data.regimes row "
            "fields symbol, regime, adx, bb_width, rsi, pct_b, icon, description, "
            "prompt_context plus regime_summary, macro, cached and ran_at metadata. "
            "CURRENT_MODEL contributes symbol selection only and excludes owner "
            "cash, equity, quantities, cost basis, weights, buying power, margin, "
            "and owner-sizing prose. No owner cash/equity/quantity/cost basis/"
            "weights/buying power/margin or owner-sizing prose is present in the "
            "response schema."
        ),
        rationale="Current-model regime for customer holdings; sanitized public indicators only.",
    ),
    _rp(
        "GET",
        "/api/agents/sec/history",
        "agent_sec_history",
        AccessPolicy.CUSTOMER,
        InputContext.CURRENT_MODEL,
        OutputPolicy.SANITIZE_NO_SCALE,
        MissingContextPolicy.TYPED_503,
        forbidden_private_fields=_COMMON_FORBIDDEN,
        schema_proof=(
            "Returns only public per-symbol SEC filing history with public filing "
            "symbol, form_type, filing_date, filing_url, action_signal, urgency, "
            "analysis metadata. CURRENT_MODEL contributes symbol selection only "
            "and excludes owner cash, equity, quantities, cost basis, weights, "
            "buying power, margin, and owner-sizing prose. No owner cash/equity/"
            "quantity/cost basis/weights/buying power/margin or owner-sizing "
            "prose is present in the response schema."
        ),
        rationale="Current-model cached SEC history; sanitized public filings only.",
    ),
    _rp(
        "GET",
        "/api/agents/news/history",
        "agent_news_history",
        AccessPolicy.CUSTOMER,
        InputContext.CURRENT_MODEL,
        OutputPolicy.SANITIZE_NO_SCALE,
        MissingContextPolicy.TYPED_503,
        forbidden_private_fields=_COMMON_FORBIDDEN,
        schema_proof=(
            "Returns only public per-symbol news history with data.results row "
            "symbol, overall_sentiment, sentiment_score, impact_level, analysis, "
            "analyzed_at plus avg_score, market_mood, cached. CURRENT_MODEL "
            "contributes symbol selection only and excludes owner cash, equity, "
            "quantities, cost basis, weights, buying power, margin, and "
            "owner-sizing prose. No owner cash/equity/quantity/cost basis/"
            "weights/buying power/margin or owner-sizing prose is present in the "
            "response schema."
        ),
        rationale="Current-model cached news history; sanitized public sentiment only.",
    ),
    _rp(
        "GET",
        "/api/fft-cycles",
        "fft_cycles_endpoint",
        AccessPolicy.CUSTOMER,
        InputContext.CURRENT_MODEL,
        OutputPolicy.SANITIZE_NO_SCALE,
        MissingContextPolicy.TYPED_503,
        forbidden_private_fields=_COMMON_FORBIDDEN,
        schema_proof=(
            "Returns only public per-symbol FFT cycle data with data.cycles "
            "keyed by symbol with dominant_cycles, primary_cycle, "
            "cycle_position, is_rising, signal, phase_label, detail, "
            "n_candles, as_of plus data.symbols and data.as_of. "
            "CURRENT_MODEL contributes symbol selection only and excludes "
            "owner cash, equity, quantities, cost basis, weights, buying "
            "power, margin, and owner-sizing prose. No owner cash/equity/"
            "quantity/cost basis/weights/buying power/margin or owner-sizing "
            "prose is present in the response schema."
        ),
        rationale="Current-model FFT cycles for holdings; sanitized public cycle data only.",
    ),
    _rp(
        "GET",
        "/api/benchmarks/{period}",
        "api_benchmarks",
        AccessPolicy.CUSTOMER,
        InputContext.CURRENT_MODEL,
        OutputPolicy.SANITIZE_NO_SCALE,
        MissingContextPolicy.TYPED_503,
        forbidden_private_fields=_COMMON_FORBIDDEN,
        schema_proof=(
            "Returns only public benchmark comparison with period and "
            "alpha_table rows ticker, sector_etf, period, stock_return_pct, "
            "spy_return_pct, sector_return_pct, vs_market_pct, vs_sector_pct, "
            "alpha_grade, summary or error. CURRENT_MODEL contributes symbol "
            "selection only and excludes owner cash, equity, quantities, cost "
            "basis, weights, buying power, margin, and owner-sizing prose. "
            "No owner cash/equity/quantity/cost basis/weights/buying power/"
            "margin or owner-sizing prose is present in the response schema."
        ),
        rationale="Current-model benchmark comparison; sanitized public benchmark data only.",
    ),
    _rp(
        "GET",
        "/api/crypto-correlation",
        "api_crypto_correlation",
        AccessPolicy.CUSTOMER,
        InputContext.CURRENT_MODEL,
        OutputPolicy.SANITIZE_NO_SCALE,
        MissingContextPolicy.TYPED_503,
        forbidden_private_fields=_COMMON_FORBIDDEN,
        schema_proof=(
            "Returns only public crypto correlation data with avg_btc_corr, "
            "max_btc_corr, highest_corr_symbol, risk_label, correlations per "
            "symbol with btc and eth, summary_text, detail_lines. "
            "CURRENT_MODEL contributes symbol selection only and excludes "
            "owner cash, equity, quantities, cost basis, weights, buying "
            "power, margin, and owner-sizing prose. No owner cash/equity/"
            "quantity/cost basis/weights/buying power/margin or owner-sizing "
            "prose is present in the response schema."
        ),
        rationale="Current-model crypto correlation; sanitized public correlation data only.",
    ),

    # =====================================================================
    # REQUEST_SCALED — ECG and yield/landscape (exact fields per spec)
    # =====================================================================
    _rp(
        "GET",
        "/api/ecg",
        "equity_curve_gate",
        AccessPolicy.CUSTOMER,
        InputContext.CURRENT_MODEL,
        OutputPolicy.REQUEST_SCALED,
        MissingContextPolicy.TYPED_503,
        financial_fields=(
            FinancialField("data.states[*].equity_history[*]", FieldKind.MONEY_ARRAY, "owner-derived equity history array that must scale"),
            FinancialField("data.states[*].drawdown_pct", FieldKind.PERCENTAGE, "percentage that must not scale"),
            FinancialField("data.states[*].message", FieldKind.PROSE_REDACTION, "prose review field that may not retain owner dollar/share sizing"),
            FinancialField("data.portfolio_message", FieldKind.PROSE_REDACTION, "prose review field that may not retain owner dollar/share sizing"),
        ),
        scaling_basis_contract="the request-scoped factor is configured model-book notional divided by the canonical sum of holding market values plus cash from the same CURRENT_MODEL request snapshot; no unrelated later snapshot may be used; missing or nonpositive basis maps to TYPED_503. Only MONEY_ARRAY fields scale, percentages stay raw, prose fields are redaction-reviewed, and current schema has no public per-share prices or portfolio quantities.",
        rationale="ECG drawdown circuit breaker; request-scaled money arrays with prose redaction.",
    ),
    _rp(
        "GET",
        "/api/yield/landscape",
        "api_yield_landscape",
        AccessPolicy.CUSTOMER,
        InputContext.CURRENT_MODEL,
        OutputPolicy.REQUEST_SCALED,
        MissingContextPolicy.TYPED_503,
        financial_fields=(
            FinancialField("cash_usd", FieldKind.PORTFOLIO_DOLLARS, "portfolio dollars to scale"),
            FinancialField("recommendation.monthly_income_usd", FieldKind.PORTFOLIO_DOLLARS, "portfolio dollars to scale"),
            FinancialField("recommendation.annual_income_usd", FieldKind.PORTFOLIO_DOLLARS, "portfolio dollars to scale"),
            FinancialField("monthly_projection[*].income_usd", FieldKind.MONEY_ARRAY, "money array dollars to scale"),
            FinancialField("monthly_projection[*].cumulative", FieldKind.MONEY_ARRAY, "money array dollars to scale"),
            FinancialField("tbill_rate_pct", FieldKind.PERCENTAGE, "percentage not to scale"),
            FinancialField("fed_rate_pct", FieldKind.PERCENTAGE, "percentage not to scale"),
            FinancialField("tiers.*[*].apy", FieldKind.PERCENTAGE, "percentage not to scale"),
            FinancialField("recommendation.apy", FieldKind.PERCENTAGE, "percentage not to scale"),
            FinancialField("tiers.tier3[*].tvl_usd", FieldKind.PUBLIC_EXTERNAL_USD, "public external USD fact not to scale"),
            FinancialField("recommendation.reason", FieldKind.PROSE_REDACTION, "prose redaction review (rate/instrument only)"),
        ),
        scaling_basis_contract="the request-scoped factor is configured model-book notional divided by the canonical sum of holding market values plus cash from the same CURRENT_MODEL request snapshot; no unrelated later snapshot may be used; missing or nonpositive basis maps to TYPED_503. Only PORTFOLIO_DOLLARS and MONEY_ARRAY fields scale; percentages and PUBLIC_EXTERNAL_USD stay raw; prose is redaction-reviewed.",
        rationale="DeFi/cash yield landscape; request-scaled portfolio dollars and money arrays.",
    ),

    # =====================================================================
    # BLOCK current-model with no publishable contract
    # =====================================================================
    _rp("GET", "/api/tax/harvest", "api_tax_harvest", AccessPolicy.CUSTOMER, InputContext.CURRENT_MODEL, OutputPolicy.BLOCK, MissingContextPolicy.TYPED_503, scaling_basis_contract="Current main has no publishable cost-basis/lot-provenance contract; current price alone cannot establish harvest dollars; Stage B must define that contract before exposure.", rationale="Current main has no publishable model cost-basis contract."),

    # =====================================================================
    # HISTORICAL_DATASET + BLOCK (exactly the 12 listed; no invented basis)
    # =====================================================================
    _rp("GET", "/api/history", "analysis_history", AccessPolicy.CUSTOMER, InputContext.HISTORICAL_DATASET, OutputPolicy.BLOCK, MissingContextPolicy.TYPED_503, scaling_basis_contract=_UNPROVEN_HISTORICAL_BASIS, rationale="No immutable composition/total basis, timestamp, stable dataset ID, and dataset-level normalization anchor are proven. Do not invent a basis."),
    _rp("GET", "/api/history/{run_id}", "analysis_detail", AccessPolicy.CUSTOMER, InputContext.HISTORICAL_DATASET, OutputPolicy.BLOCK, MissingContextPolicy.TYPED_503, scaling_basis_contract=_UNPROVEN_HISTORICAL_BASIS, rationale="No immutable composition/total basis, timestamp, stable dataset ID, and dataset-level normalization anchor are proven. Do not invent a basis."),
    _rp("GET", "/api/portfolio/history", "portfolio_history", AccessPolicy.CUSTOMER, InputContext.HISTORICAL_DATASET, OutputPolicy.BLOCK, MissingContextPolicy.TYPED_503, scaling_basis_contract=_UNPROVEN_HISTORICAL_BASIS, rationale="No immutable composition/total basis, timestamp, stable dataset ID, and dataset-level normalization anchor are proven. Do not invent a basis."),
    _rp("GET", "/api/deep-analysis/cached", "deep_analysis_cached", AccessPolicy.CUSTOMER, InputContext.HISTORICAL_DATASET, OutputPolicy.BLOCK, MissingContextPolicy.TYPED_503, scaling_basis_contract=_UNPROVEN_HISTORICAL_BASIS, rationale="No immutable composition/total basis, timestamp, stable dataset ID, and dataset-level normalization anchor are proven. Do not invent a basis."),
    _rp("GET", "/api/consensus/cached", "consensus_cached", AccessPolicy.CUSTOMER, InputContext.HISTORICAL_DATASET, OutputPolicy.BLOCK, MissingContextPolicy.TYPED_503, scaling_basis_contract=_UNPROVEN_HISTORICAL_BASIS, rationale="No immutable composition/total basis, timestamp, stable dataset ID, and dataset-level normalization anchor are proven. Do not invent a basis."),
    _rp("GET", "/api/kelly/cached", "kelly_cached", AccessPolicy.CUSTOMER, InputContext.HISTORICAL_DATASET, OutputPolicy.BLOCK, MissingContextPolicy.TYPED_503, scaling_basis_contract=_UNPROVEN_HISTORICAL_BASIS, rationale="No immutable composition/total basis, timestamp, stable dataset ID, and dataset-level normalization anchor are proven. Do not invent a basis."),
    _rp("GET", "/api/monte-carlo/cached", "monte_carlo_cached", AccessPolicy.CUSTOMER, InputContext.HISTORICAL_DATASET, OutputPolicy.BLOCK, MissingContextPolicy.TYPED_503, scaling_basis_contract=_UNPROVEN_HISTORICAL_BASIS, rationale="No immutable composition/total basis, timestamp, stable dataset ID, and dataset-level normalization anchor are proven. Do not invent a basis."),
    _rp("GET", "/api/trades", "get_trades", AccessPolicy.CUSTOMER, InputContext.HISTORICAL_DATASET, OutputPolicy.BLOCK, MissingContextPolicy.TYPED_503, scaling_basis_contract=_UNPROVEN_HISTORICAL_BASIS, rationale="No immutable composition/total basis, timestamp, stable dataset ID, and dataset-level normalization anchor are proven. Do not invent a basis."),
    _rp("GET", "/api/trades/stats", "trade_stats", AccessPolicy.CUSTOMER, InputContext.HISTORICAL_DATASET, OutputPolicy.BLOCK, MissingContextPolicy.TYPED_503, scaling_basis_contract=_UNPROVEN_HISTORICAL_BASIS, rationale="No immutable composition/total basis, timestamp, stable dataset ID, and dataset-level normalization anchor are proven. Do not invent a basis."),
    _rp("GET", "/api/paper/summary", "paper_summary_endpoint", AccessPolicy.CUSTOMER, InputContext.HISTORICAL_DATASET, OutputPolicy.BLOCK, MissingContextPolicy.TYPED_503, scaling_basis_contract=_UNPROVEN_HISTORICAL_BASIS, rationale="No immutable composition/total basis, timestamp, stable dataset ID, and dataset-level normalization anchor are proven. Do not invent a basis."),
    _rp("GET", "/api/consensus/{symbol}", "consensus_symbol", AccessPolicy.CUSTOMER, InputContext.HISTORICAL_DATASET, OutputPolicy.BLOCK, MissingContextPolicy.TYPED_503, scaling_basis_contract=_UNPROVEN_HISTORICAL_BASIS, rationale="run_consensus_holding always invokes node_ecg and exposes owner-derived drawdown/streak from equity_snapshots despite zeroed position parameters."),
    _rp("GET", "/api/explainability/{symbol}", "api_explainability", AccessPolicy.CUSTOMER, InputContext.HISTORICAL_DATASET, OutputPolicy.BLOCK, MissingContextPolicy.TYPED_503, scaling_basis_contract=_UNPROVEN_HISTORICAL_BASIS, rationale="node bullets/full narrative embed the same owner ECG drawdown/streak."),

    # =====================================================================
    # Remaining non-owner, non-public, non-special routes — conservatively NONE + RAW_SAFE + NOT_APPLICABLE
    # (single-symbol stateless facts, statuses, cached public data, analyze-ticker, etc.)
    # Per-share prices, targets, EPS, volume, RSI, percentages, correlations treated as unscaled public facts.
    # =====================================================================
    _rp("GET", "/api/chart/{symbol}", "price_chart", AccessPolicy.CUSTOMER, InputContext.NONE, OutputPolicy.RAW_SAFE, MissingContextPolicy.NOT_APPLICABLE, rationale="Single-symbol public price chart; per-share prices are unscaled public facts."),
    _rp("GET", "/api/analyst/{symbol}", "analyst_symbol", AccessPolicy.CUSTOMER, InputContext.NONE, OutputPolicy.RAW_SAFE, MissingContextPolicy.NOT_APPLICABLE, rationale="Single-symbol public analyst targets; unscaled public facts."),
    _rp("GET", "/api/fundamentals/{symbol}", "fundamentals_symbol", AccessPolicy.CUSTOMER, InputContext.NONE, OutputPolicy.RAW_SAFE, MissingContextPolicy.NOT_APPLICABLE, rationale="Single-symbol public fundamentals; unscaled public facts."),
    _rp("GET", "/api/news/{symbol}", "news_symbol", AccessPolicy.CUSTOMER, InputContext.NONE, OutputPolicy.RAW_SAFE, MissingContextPolicy.NOT_APPLICABLE, rationale="Single-symbol public news; unscaled public facts."),
    _rp("GET", "/api/momentum/{symbol}", "momentum_symbol", AccessPolicy.CUSTOMER, InputContext.NONE, OutputPolicy.RAW_SAFE, MissingContextPolicy.NOT_APPLICABLE, rationale="Single-symbol public momentum; unscaled public facts."),
    _rp("GET", "/api/levels/{symbol}", "levels_symbol", AccessPolicy.CUSTOMER, InputContext.NONE, OutputPolicy.RAW_SAFE, MissingContextPolicy.NOT_APPLICABLE, rationale="Single-symbol public levels; unscaled public facts."),
    _rp("GET", "/api/earnings/{symbol}", "earnings_symbol", AccessPolicy.CUSTOMER, InputContext.NONE, OutputPolicy.RAW_SAFE, MissingContextPolicy.NOT_APPLICABLE, rationale="Single-symbol public earnings; unscaled public facts."),
    _rp("GET", "/api/regime/{symbol}", "regime_symbol", AccessPolicy.CUSTOMER, InputContext.NONE, OutputPolicy.RAW_SAFE, MissingContextPolicy.NOT_APPLICABLE, rationale="Single-symbol public regime; unscaled public facts."),
    _rp("GET", "/api/fft-cycles/{symbol}", "fft_cycles_single", AccessPolicy.CUSTOMER, InputContext.NONE, OutputPolicy.RAW_SAFE, MissingContextPolicy.NOT_APPLICABLE, rationale="Single-symbol public FFT; unscaled public facts."),
    _rp("GET", "/api/symbol-search", "symbol_search", AccessPolicy.CUSTOMER, InputContext.NONE, OutputPolicy.RAW_SAFE, MissingContextPolicy.NOT_APPLICABLE, rationale="Public symbol search; unscaled public facts."),
    _rp("GET", "/api/signals/history/{symbol}", "signals_history", AccessPolicy.CUSTOMER, InputContext.NONE, OutputPolicy.RAW_SAFE, MissingContextPolicy.NOT_APPLICABLE, rationale="Single-symbol signals history; unscaled public facts (require_customer_or_owner)."),
    _rp("GET", "/api/volatility/compare/{symbol}", "api_vol_compare", AccessPolicy.CUSTOMER, InputContext.NONE, OutputPolicy.RAW_SAFE, MissingContextPolicy.NOT_APPLICABLE, rationale="Single-symbol volatility compare; unscaled public facts."),
    _rp("POST", "/api/analyze-ticker", "analyze_ticker_endpoint", AccessPolicy.CUSTOMER, InputContext.NONE, OutputPolicy.RAW_SAFE, MissingContextPolicy.NOT_APPLICABLE, rationale="Single-symbol AI research card; unscaled public facts (require_customer_or_owner)."),
    _rp("GET", "/api/scanner/consolidation/{symbol}", "consolidation_zone_single", AccessPolicy.CUSTOMER, InputContext.NONE, OutputPolicy.RAW_SAFE, MissingContextPolicy.NOT_APPLICABLE, rationale="Single-symbol consolidation scan; unscaled public facts."),
    _rp("GET", "/api/scanner/sector/{symbol}", "sector_hot_single", AccessPolicy.CUSTOMER, InputContext.NONE, OutputPolicy.RAW_SAFE, MissingContextPolicy.NOT_APPLICABLE, rationale="Single-symbol sector scan; unscaled public facts."),
    _rp("GET", "/api/long-term-macro", "long_term_macro", AccessPolicy.CUSTOMER, InputContext.NONE, OutputPolicy.RAW_SAFE, MissingContextPolicy.NOT_APPLICABLE, rationale="Public long-term macro; unscaled public facts."),
    _rp("GET", "/api/long-term-macro/cached", "long_term_macro_cached", AccessPolicy.CUSTOMER, InputContext.NONE, OutputPolicy.RAW_SAFE, MissingContextPolicy.NOT_APPLICABLE, rationale="Public long-term macro cached; unscaled public facts."),
    _rp("GET", "/api/agents/fundamentals/latest", "agent_fundamentals_latest", AccessPolicy.CUSTOMER, InputContext.NONE, OutputPolicy.RAW_SAFE, MissingContextPolicy.NOT_APPLICABLE, rationale="Public fundamentals latest cached; unscaled public facts."),
    _rp("GET", "/api/screener/cached", "screener_cached", AccessPolicy.CUSTOMER, InputContext.NONE, OutputPolicy.RAW_SAFE, MissingContextPolicy.NOT_APPLICABLE, rationale="Public screener cached; unscaled public facts."),
    _rp("GET", "/api/sections/status", "sections_status", AccessPolicy.CUSTOMER, InputContext.NONE, OutputPolicy.RAW_SAFE, MissingContextPolicy.NOT_APPLICABLE, rationale="Public sections status; unscaled public facts."),
    _rp("GET", "/api/security/health", "api_security_health", AccessPolicy.CUSTOMER, InputContext.NONE, OutputPolicy.RAW_SAFE, MissingContextPolicy.NOT_APPLICABLE, rationale="Public security health; unscaled public facts."),
    _rp("GET", "/api/ollama/status", "ollama_removed_endpoint", AccessPolicy.CUSTOMER, InputContext.NONE, OutputPolicy.RAW_SAFE, MissingContextPolicy.NOT_APPLICABLE, rationale="Public Ollama status; unscaled public facts."),
    _rp("GET", "/api/gemini/status", "gemini_status_endpoint", AccessPolicy.CUSTOMER, InputContext.NONE, OutputPolicy.RAW_SAFE, MissingContextPolicy.NOT_APPLICABLE, rationale="Public Gemini status; unscaled public facts."),
)

# =====================================================================
# Import-time validation
# =====================================================================
_keys: list[tuple[str, str]] = [(p.method, p.path) for p in _POLICIES]
if len(_keys) != len(set(_keys)):
    from collections import Counter
    counts = Counter(_keys)
    dups = [k for k, c in counts.items() if c > 1]
    raise RuntimeError(f"Duplicate route policies detected at import: {dups}")
if len(_POLICIES) != 95:
    raise RuntimeError(f"Expected exactly 95 policies for all runtime /api routes, got {len(_POLICIES)}")


def get_all_route_policies() -> Tuple[RoutePolicy, ...]:
    """Return the immutable tuple of all 95 route policies."""
    return _POLICIES


def get_route_policy(method: str, path: str) -> RoutePolicy:
    """Exact lookup by (method, path). Raises KeyError for unknown keys.
    No normalization, no prefix matching, no fallback.
    """
    for policy in _POLICIES:
        if policy.method == method and policy.path == path:
            return policy
    raise KeyError(f"No RoutePolicy for method={method} path={path}")
