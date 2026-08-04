from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anthropic
from dotenv import load_dotenv

import llm_contracts
import llm_gateway

load_dotenv()

# Canonical routing lives in llm_gateway — keep these public names for callers
# and existing tests that assert request shape against MODEL / SCREENER_MODEL.
MODEL = llm_gateway.resolve_model(os.getenv("CLAUDE_MODEL"), default="sonnet")
# Screener = structured scoring over pre-computed candidates → Haiku by default
# (cost-routing convention: cheap batch tasks → Haiku). Override: CLAUDE_SCREENER_MODEL.
SCREENER_MODEL = llm_gateway.resolve_model(os.getenv("CLAUDE_SCREENER_MODEL"), default="haiku")
# One count, used by BOTH screener prompts — they used to contradict each other
# ("exactly 20" in the system prompt vs "best 6" in the user message).
SCREENER_PICK_COUNT = int(os.getenv("SCREENER_PICK_COUNT", "6"))

# Bounded client everywhere: SDK defaults (600s timeout × retries) allow a
# ~30-min silent stall in the daily chain. Retries are owned by llm_gateway
# (max_retries=0 on the SDK client); timeout from GESH_LLM_TIMEOUT_SECONDS.
_CLIENT_TIMEOUT_S = llm_gateway.default_timeout_seconds()


def _client() -> anthropic.Anthropic:
    return llm_gateway.create_client(timeout_seconds=_CLIENT_TIMEOUT_S)


def _create_streaming(client: anthropic.Anthropic, **kwargs: Any) -> Any:
    """messages.create() over SSE, returning the same final Message object.

    WHY (2026-07-27 outage): the daily chain died at step 3/5 with
    `anthropic.APITimeoutError` three days running. Nothing was wrong with the
    request — `_CLIENT_TIMEOUT_S` is a WALL-CLOCK budget on a non-streaming
    POST, and deep_analyze_portfolio generates ~11-12K output tokens, which at
    Sonnet's throughput lands at ~200-240s. It sat right on the 240s cliff, so a
    normal throughput dip tipped it over; `max_retries=2` then burned 3 x 240s
    (~12 min) before failing. Streaming makes the timeout PER-CHUNK instead of
    per-request, so a long generation can no longer trip the wall clock — this
    is also what the SDK requires for requests that may exceed 10 minutes.

    Callers keep using the returned Message exactly as before (`.usage`,
    `.stop_reason`, `.content`), so `_record_usage` / `llm_contracts.extract_tool_input` /
    the max_tokens truncation guard are all unchanged.
    """
    with client.messages.stream(**kwargs) as stream:
        return stream.get_final_message()


# ─── Usage accounting + optional daily budget ────────────────────────────────
# Every call appends one JSONL row (tokens + est. cost). LLM_DAILY_BUDGET_USD
# (unset/0 = no cap) hard-stops new calls once today's estimated spend crosses
# the cap — a runaway retry loop can no longer multiply cost silently.
_USAGE_LOG = Path(os.getenv("LLM_USAGE_LOG") or (Path(__file__).parent / "llm_usage.jsonl"))
_DAILY_BUDGET_USD = float(os.getenv("LLM_DAILY_BUDGET_USD", "0") or 0)
_PRICE_PER_MTOK = {  # (input $, output $) per million tokens
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


def _est_cost_usd(model: str, in_tok: int, out_tok: int) -> float:
    p_in, p_out = _PRICE_PER_MTOK.get(model, (3.0, 15.0))
    return (in_tok * p_in + out_tok * p_out) / 1_000_000


def _record_usage(fn: str, model: str, message: Any) -> None:
    """Append one usage row. Accounting must never break analysis — fail soft."""
    try:
        usage = getattr(message, "usage", None)
        if usage is None:
            return
        in_tok = int(getattr(usage, "input_tokens", 0) or 0)
        out_tok = int(getattr(usage, "output_tokens", 0) or 0)
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "fn": fn,
            "model": model,
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "est_cost_usd": round(_est_cost_usd(model, in_tok, out_tok), 6),
        }
        with open(_USAGE_LOG, "a") as f:
            f.write(json.dumps(row) + "\n")
    except Exception:
        pass


def _spent_today_usd() -> float:
    try:
        today = datetime.now(timezone.utc).date().isoformat()
        total = 0.0
        # Read only the TAIL: rows are append-only so today's are always at the end.
        # Bounds the per-call budget check no matter how large the log grows (1 MiB ≫
        # any single day). A partial first line after the seek fails json.loads → skipped.
        with open(_USAGE_LOG, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 1_048_576))
            chunk = f.read().decode("utf-8", "replace")
        for line in chunk.splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if str(row.get("ts", "")).startswith(today):
                total += float(row.get("est_cost_usd", 0) or 0)
        return total
    except FileNotFoundError:
        return 0.0
    except Exception:
        return 0.0


def _budget_guard(fn: str) -> None:
    """Raise BEFORE spending when the optional daily budget is exhausted.

    Budget is read at CALL time (not import) so an env change or a test override via
    os.environ is honored — the import-time constant only documents the default."""
    budget = float(os.getenv("LLM_DAILY_BUDGET_USD", "0") or 0)
    if budget <= 0:
        return
    spent = _spent_today_usd()
    if spent >= budget:
        raise RuntimeError(
            f"{fn}: LLM daily budget exhausted (${spent:.2f} of "
            f"${budget:.2f}) — raise LLM_DAILY_BUDGET_USD or wait for UTC rollover"
        )


def _extract_tool_input(
    message: Any, fn_name: str, *, expected_tool: str | None = None
) -> dict[str, Any]:
    """Thin wrapper around llm_contracts.extract_tool_input.

    Prefer calling ``llm_contracts.extract_tool_input`` directly with an
    explicit ``expected_tool``. When ``expected_tool`` is omitted, ``fn_name``
    is used as both the operation label and the tool name (legacy callers).
    """
    tool = expected_tool if expected_tool is not None else fn_name
    return llm_contracts.extract_tool_input(message, expected_tool=tool, operation=fn_name)


# ─── Quick Risk Analysis (plain text) ────────────────────────────────────────
SYSTEM_PROMPT = """You are an elite quantitative risk manager. Analyze the portfolio and produce exactly these sections:
1. PORTFOLIO OVERVIEW - equity, cash, buying power, position count
2. SECTOR & CONCENTRATION RISK - overexposure flags, top 3 by weight, diversification score
3. MARKET CONTEXT - SPY/QQQ signals, beta divergence
4. KEY RISK FACTORS - top 3-5 risks with severity LOW/MEDIUM/HIGH/CRITICAL
5. ACTIONABLE INSIGHTS - 3-5 specific prioritized recommendations
6. RISK SCORE - 1-10 with one-sentence justification
Be direct, precise, use dollar figures and percentages."""


def analyze_portfolio(payload: dict[str, Any]) -> str:
    _budget_guard("analyze_portfolio")
    client = _client()
    message = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        messages=[
            {
                "role": "user",
                "content": f"Analyze this portfolio:\n\n```json\n{json.dumps(payload, separators=(',', ':'))}\n```",
            }
        ],
    )
    _record_usage("analyze_portfolio", MODEL, message)
    if getattr(message, "stop_reason", None) == "max_tokens":
        raise RuntimeError(
            "analyze_portfolio: response truncated at max_tokens (2048) — output incomplete"
        )
    if not message.content:
        raise ValueError("analyze_portfolio: empty response from Claude")
    # content[0] is not guaranteed to be a text block (refusal / leading non-text
    # block); select the first block exposing .text rather than blindly indexing
    # [0]. Keying on the .text attribute works for both the SDK's TextBlock and a
    # tool_use/refusal block (which lacks it).
    text = next((b.text for b in message.content if getattr(b, "text", None) is not None), None)
    if text is None:
        raise ValueError("analyze_portfolio: no text block in response")
    return text


# ─── Deep Structured Analysis (JSON) ─────────────────────────────────────────
DEEP_SYSTEM_PROMPT = """You are an elite quantitative financial analyst combining the depth of Simply Wall St, Bloomberg, and Goldman Sachs research.

SCORING GUIDE (be realistic — avoid clustering all at 50):
- valuation: 0=massively overvalued, 50=fair, 100=deeply undervalued
- growth: 0=declining revenues, 50=5-10% growth, 100=>50% revenue growth
- health: 0=<3 months cash runway, 50=average balance sheet, 100=net cash fortress
- momentum: 0=down >50% with deteriorating fundamentals, 50=sideways, 100=strong uptrend
- dividend: 0=no dividend, 60=moderate 2-4% yield, 100=high yield >5% safe payout

Provide 2-4 risks per stock and 1-3 rewards. Be specific — reference actual business metrics.

CONSISTENCY (mandatory): keep regime language internally consistent. If ANY named holding is
trending, do NOT write that the "entire portfolio" is ranging — say "most names are ranging"
or "the portfolio is broadly ranging, except <tickers>". Never make a whole-portfolio absolute
claim you then contradict with per-name exceptions. Use the same regime label for a given
ticker everywhere it appears in your response."""


# The per-holding research card — extracted so BOTH the portfolio deep-analysis
# and the single-ticker customer card (TICKER_ANALYSIS_SCHEMA) share ONE definition
# and can never drift apart (the frontend daRender markup renders both).
# NOTE: any field added here is ALSO prompted on every holding in
# deep_analyze_portfolio (incl. the token-budget-constrained customer book
# analysis) — cap it in the `concise` brevity instruction below.
_HOLDING_ANALYSIS_ITEM = {
    "type": "object",
    "properties": {
        "symbol": {"type": "string"},
        # A plain-language DESCRIPTOR of the business, not a taxonomy value. The
        # Fundamentals card's `sector` is a vendor CLASSIFICATION (Robinhood's, non-GICS);
        # this is model prose with no taxonomy behind it. Rendering the two side by side
        # unlabeled made them read as competing sector values (GESH-6). No enum here on
        # purpose — the model was never given a taxonomy to conform to.
        "company_type": {
            "type": "string",
            "description": "Short plain-language descriptor of what the business does "
            "(e.g. 'Mega-cap consumer tech'). A descriptor, NOT a GICS or "
            "vendor sector classification.",
        },
        "scores": {
            "type": "object",
            "properties": {
                "valuation": {"type": "integer"},
                "growth": {"type": "integer"},
                "health": {"type": "integer"},
                "momentum": {"type": "integer"},
                "dividend": {"type": "integer"},
            },
            "required": ["valuation", "growth", "health", "momentum", "dividend"],
        },
        "snowflake_summary": {"type": "string"},
        "fair_value": {
            "type": "object",
            "properties": {
                "estimate": {"type": "number"},
                "current": {"type": "number"},
                "status": {"type": "string"},
                "pct_diff": {"type": "number"},
            },
            "required": ["estimate", "current", "status", "pct_diff"],
        },
        "risks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"text": {"type": "string"}, "severity": {"type": "string"}},
                "required": ["text", "severity"],
            },
        },
        "rewards": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
        "prediction": {
            "type": "object",
            "properties": {
                "3m": {"type": "string"},
                "12m": {"type": "string"},
                "rationale": {"type": "string"},
            },
            "required": ["3m", "12m", "rationale"],
        },
        "verdict": {"type": "string"},
        "analyst_note": {"type": "string"},
        # Retention feature: the strongest argument AGAINST the verdict, plus
        # concrete conditions that would flip it — steelmanned, never a strawman,
        # and tied to observable numbers/events so it can't degrade into vibes.
        "bear_case": {
            "type": "object",
            "properties": {
                "thesis": {
                    "type": "string",
                    "description": "The single strongest argument AGAINST your verdict, "
                    "2-3 sentences, grounded in the same data you used — "
                    "steelman it, do not strawman.",
                },
                "invalidators": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "2-4 concrete OBSERVABLE conditions that would flip this "
                    "verdict, each tied to a number or event, e.g. 'gross "
                    "margin prints below 70% next quarter' — never vague "
                    "('sentiment worsens').",
                },
            },
            "required": ["thesis", "invalidators"],
        },
    },
    "required": [
        "symbol",
        "company_type",
        "scores",
        "snowflake_summary",
        "fair_value",
        "risks",
        "rewards",
        "prediction",
        "verdict",
        "analyst_note",
        "bear_case",
    ],
}

DEEP_ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "portfolio_score": {"type": "integer"},
        "portfolio_verdict": {"type": "string"},
        "portfolio_type": {"type": "string"},
        "risk_score": {"type": "integer"},
        "market_context": {"type": "string"},
        "diversification_score": {"type": "integer"},
        "holdings_analysis": {"type": "array", "items": _HOLDING_ANALYSIS_ITEM},
        "portfolio_risks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"text": {"type": "string"}, "severity": {"type": "string"}},
                "required": ["text", "severity"],
            },
        },
        "portfolio_recommendations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "priority": {"type": "string"},
                    "rationale": {"type": "string"},
                    # Nullable (strict-mode): set when the recommendation is an
                    # advisory NEW-name diversification idea from the radar. category
                    # "DIVERSIFICATION" lets the UI group it; symbol names the new ticker.
                    "category": {"type": ["string", "null"]},
                    "symbol": {"type": ["string", "null"]},
                },
                "required": ["action", "priority", "rationale", "category", "symbol"],
            },
        },
        "sector_breakdown": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "sector": {"type": "string"},
                    "allocation_pct": {"type": "number"},
                    "assessment": {"type": "string"},
                },
                "required": ["sector", "allocation_pct", "assessment"],
            },
        },
    },
    "required": [
        "portfolio_score",
        "portfolio_verdict",
        "portfolio_type",
        "risk_score",
        "market_context",
        "diversification_score",
        "holdings_analysis",
        "portfolio_risks",
        "portfolio_recommendations",
        "sector_breakdown",
    ],
}

SCREENER_SCHEMA = {
    "type": "object",
    "properties": {
        "screen_summary": {"type": "string"},
        "market_bias": {"type": "string"},
        "top_sectors": {"type": "array", "items": {"type": "string"}},
        "picks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "company_name": {"type": "string"},
                    "sector": {"type": "string"},
                    "composite_score": {"type": "integer"},
                    "momentum_score": {"type": "integer"},
                    "value_score": {"type": "integer"},
                    "growth_score": {"type": "integer"},
                    "analyst_score": {"type": "integer"},
                    "verdict": {"type": "string"},
                    "key_thesis": {"type": "string"},
                    "main_risk": {"type": "string"},
                    "price": {"type": "number"},
                    "ret_1w": {"type": "number"},
                    "ret_1m": {"type": "number"},
                    "analyst_target": {"type": ["number", "null"]},
                    "analyst_upside": {"type": ["number", "null"]},
                    "num_analysts": {"type": "integer"},
                    "recommendation": {"type": "string"},
                },
                "required": [
                    "symbol",
                    "company_name",
                    "sector",
                    "composite_score",
                    "momentum_score",
                    "value_score",
                    "growth_score",
                    "analyst_score",
                    "verdict",
                    "key_thesis",
                    "main_risk",
                    "price",
                    "ret_1w",
                    "ret_1m",
                    "analyst_target",
                    "analyst_upside",
                    "num_analysts",
                    "recommendation",
                ],
            },
        },
    },
    "required": ["screen_summary", "market_bias", "top_sectors", "picks"],
}


def deep_analyze_portfolio(
    payload: dict[str, Any],
    regime_context: str | None = None,
    concise: bool = False,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """`concise` trades card verbosity for speed — the LLM's runtime scales with
    output tokens, so terse cards keep a live interactive analysis (customer book)
    from running minutes-long. `max_tokens` overrides the output ceiling."""
    _budget_guard("deep_analyze_portfolio")
    # Bounded client: SDK defaults (600s timeout x retries) allow a ~30-min
    # silent stall. 240s covers a slow ~7-10K-token generation.
    client = _client()

    today = __import__("datetime").date.today().isoformat()
    regime_block = f"\n\n{regime_context}" if regime_context else ""
    brevity = (
        (
            "\n\nBE CONCISE — this is a fast interactive report on a larger book. "
            "For each holding give at most 2 short risks, 2 short rewards, and "
            "one-line 3M/12M outlooks; a one-sentence snowflake_summary; "
            "bear_case: a one-sentence thesis and at most 2 invalidators. "
            "No preamble or repetition."
        )
        if concise
        else ""
    )
    _max_tokens = max_tokens if max_tokens is not None else 24000

    import copy

    sanitized = copy.deepcopy(payload)
    for h in sanitized.get("holdings", []):
        h.pop("equity_history", None)
    sanitized.pop("watchlist", None)

    def prune_dict(d):
        for k, v in list(d.items()):
            if isinstance(v, float):
                d[k] = round(v, 2)
            elif isinstance(v, dict):
                prune_dict(v)
            elif isinstance(v, list):
                if k == "key_findings":
                    d[k] = v[:3]
                for item in d[k]:
                    if isinstance(item, dict):
                        prune_dict(item)

    prune_dict(sanitized)

    # STREAMED (2026-07-27): this is the longest generation in the codebase
    # (~11-12K output tokens) and was timing out against the 240s wall clock on
    # the non-streaming path. See _create_streaming for the full writeup.
    tool = llm_contracts.strict_tool(
        "return_portfolio_analysis",
        "Return the complete deep portfolio analysis.",
        DEEP_ANALYSIS_SCHEMA,
    )
    tool["cache_control"] = {"type": "ephemeral"}
    message = _create_streaming(
        client,
        model=MODEL,
        max_tokens=_max_tokens,
        system=[
            {"type": "text", "text": DEEP_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
        ],
        tools=[tool],
        tool_choice={"type": "tool", "name": "return_portfolio_analysis"},
        messages=[
            {
                "role": "user",
                "content": (
                    f"Perform a deep financial analysis of this portfolio. Today is {today}.{regime_block}{brevity}\n\n"
                    f"```json\n{json.dumps(sanitized, separators=(',', ':'))}\n```"
                ),
            }
        ],
    )
    _record_usage("deep_analyze_portfolio", MODEL, message)
    if getattr(message, "stop_reason", None) == "max_tokens":
        raise RuntimeError(
            "deep_analyze_portfolio: tool output truncated at max_tokens — "
            "the JSON is unusable; tighten DEEP_ANALYSIS_SCHEMA or raise max_tokens."
        )
    result = llm_contracts.extract_tool_input(
        message,
        expected_tool="return_portfolio_analysis",
        operation="deep_analyze_portfolio",
    )
    # Same defensive default as deep_analyze_ticker: bear_case is in the shared
    # per-holding schema, but a model may omit it — every holding card must reach
    # the renderer with the scaffold present, never raise on a missing key.
    for holding in result.get("holdings_analysis") or []:
        if isinstance(holding, dict):
            holding.setdefault("bear_case", {"thesis": "", "invalidators": []})
    return result


# ─── Single-Ticker Deep Analysis (customer 'Analyze any ticker' card) ────────
# The money-free, per-ticker subset of deep_analyze_portfolio. Same per-holding
# schema (so the shared daRender markup renders it), but for ONE public ticker
# with no portfolio, no positions, and no owner dollars — safe for customers.
TICKER_ANALYSIS_SCHEMA = _HOLDING_ANALYSIS_ITEM

TICKER_SYSTEM_PROMPT = """You are an elite quantitative equity analyst combining the depth of Simply Wall St, Bloomberg, and Goldman Sachs research. Analyze a SINGLE stock and return one structured research card.

SCORING GUIDE (be realistic — avoid clustering all at 50):
- valuation: 0=massively overvalued, 50=fair, 100=deeply undervalued
- growth: 0=declining revenues, 50=5-10% growth, 100=>50% revenue growth
- health: 0=<3 months cash runway, 50=average balance sheet, 100=net cash fortress
- momentum: 0=down >50% with deteriorating fundamentals, 50=sideways, 100=strong uptrend
- dividend: 0=no dividend, 60=moderate 2-4% yield, 100=high yield >5% safe payout

Provide 2-4 risks and 1-3 rewards — be specific, reference actual business metrics.
fair_value.current is the latest share price; fair_value.estimate is your intrinsic
per-share value. This is single-name research: no portfolio, no position sizing, no
holder context — refer ONLY to per-share prices and public company figures, never to
share counts or a holder's dollar amounts."""


def deep_analyze_ticker(
    symbol: str,
    current_price: float | None = None,
    regime_context: str | None = None,
) -> dict[str, Any]:
    """Single-ticker deep AI research card — the money-free per-ticker subset of
    deep_analyze_portfolio (VAL/GRO/HLT/MOM/DIV scores, fair value, 3M/12M
    prediction, risks/rewards, verdict). Takes only a ticker (no portfolio, no
    positions, no dollars), so it is safe on the customer 'Analyze any ticker'
    surface. Returns one holdings_analysis-shaped dict.

    current_price is the REAL latest quote the caller fetched — it grounds
    fair_value.current so the model can't fabricate a stale price (the schema
    forces a number). Optional regime_context is injected verbatim when the caller
    has it."""
    _budget_guard("deep_analyze_ticker")
    client = _client()
    today = __import__("datetime").date.today().isoformat()
    regime_block = f"\n\n{regime_context}" if regime_context else ""
    # Ground the valuation on the caller's real quote — never let the model invent
    # a current price (it would derive status/pct_diff/verdict off a stale base).
    price_block = (
        f" The latest share price is ${current_price:,.2f} — use EXACTLY this value "
        f"as fair_value.current and derive status/pct_diff from it; do not invent a price."
        if current_price is not None
        else ""
    )
    tool = llm_contracts.strict_tool(
        "return_ticker_analysis",
        "Return the complete single-ticker research card.",
        TICKER_ANALYSIS_SCHEMA,
    )
    tool["cache_control"] = {"type": "ephemeral"}
    message = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        system=[
            {"type": "text", "text": TICKER_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
        ],
        tools=[tool],
        tool_choice={"type": "tool", "name": "return_ticker_analysis"},
        messages=[
            {
                "role": "user",
                "content": (
                    f"Provide a deep single-stock research analysis of {symbol}. "
                    f"Today is {today}.{price_block}{regime_block}"
                ),
            }
        ],
    )
    _record_usage("deep_analyze_ticker", MODEL, message)
    if getattr(message, "stop_reason", None) == "max_tokens":
        raise RuntimeError(
            "deep_analyze_ticker: tool output truncated at max_tokens — "
            "raise max_tokens or tighten TICKER_ANALYSIS_SCHEMA."
        )
    card = llm_contracts.extract_tool_input(
        message,
        expected_tool="return_ticker_analysis",
        operation="deep_analyze_ticker",
    )
    card = llm_contracts.validate_ticker_card(
        card, requested_symbol=symbol, current_price=current_price
    )
    # Defensive default: the schema asks for bear_case but a model can still omit
    # it — never let a missing key surface as a KeyError on the customer surface.
    card.setdefault("bear_case", {"thesis": "", "invalidators": []})
    return card


# ─── Investment Screener AI Analysis ─────────────────────────────────────────

SCREENER_SYSTEM_PROMPT = f"""You are an elite quantitative stock screener combining Goldman Sachs research depth with Simply Wall St clarity.

Given a list of stock candidates with 1-week data and fundamentals, select exactly {SCREENER_PICK_COUNT} stocks that represent the BEST investment opportunities for this week.

Selection criteria — use a BALANCED MIXTURE (not pure momentum):
- MOMENTUM (25%): Strong 1W and 1M price action, volume confirmation
- VALUE (20%): P/E vs growth rate (PEG concept), P/B not excessive, analyst upside
- GROWTH (25%): Revenue + earnings growth trajectory, forward guidance
- ANALYST CONSENSUS (20%): Strong buy/buy ratings, meaningful price target upside
- QUALITY (10%): Business model durability, balance sheet, sector tailwinds

Rules:
- Include a mix across large-cap stability, mid-cap growth, value, and contrarian/turnaround picks (proportional to the pick count)
- Avoid ETFs (SPY, QQQ, IWM) as final picks
- 1-2 picks can be WATCH (not quite BUY but worth monitoring)
- Be realistic about scores — spread them meaningfully (not all 75-85)

Order picks by composite_score descending."""


def analyze_screener(candidates: list[dict]) -> dict[str, Any]:
    _budget_guard("analyze_screener")
    client = _client()

    today = __import__("datetime").date.today().isoformat()

    tool = llm_contracts.strict_tool(
        "return_screener_picks",
        "Return the complete screener picks contract.",
        SCREENER_SCHEMA,
    )
    tool["cache_control"] = {"type": "ephemeral"}
    message = client.messages.create(
        model=SCREENER_MODEL,
        max_tokens=6000,
        system=[
            {"type": "text", "text": SCREENER_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
        ],
        tools=[tool],
        tool_choice={"type": "tool", "name": "return_screener_picks"},
        messages=[
            {
                "role": "user",
                "content": (
                    f"Today is {today}. Analyze these {len(candidates)} stock candidates "
                    f"and select the best {SCREENER_PICK_COUNT} investment opportunities this week.\n\n"
                    f"```json\n{json.dumps(candidates, separators=(',', ':'))}\n```"
                ),
            }
        ],
    )
    _record_usage("analyze_screener", SCREENER_MODEL, message)
    if getattr(message, "stop_reason", None) == "max_tokens":
        raise RuntimeError(
            "analyze_screener: tool output truncated at max_tokens — "
            "the JSON is unusable; tighten SCREENER_SCHEMA or raise max_tokens."
        )
    return llm_contracts.extract_tool_input(
        message,
        expected_tool="return_screener_picks",
        operation="analyze_screener",
    )
