"""Strict Anthropic tool construction and semantic response validators.

Schemas here are the contract surface for Claude tool-use calls. Agents are
migrated onto these helpers in a later task; this module only defines the
hardened tool builder, extraction, and field-level validators.
"""

from __future__ import annotations

import copy
import math
from typing import Any

# ── Errors ────────────────────────────────────────────────────────────────────


class LLMContractError(ValueError):
    """Raised when a Claude tool payload or response violates its contract."""


# ── Schema hardening ──────────────────────────────────────────────────────────


def _is_object_schema(node: dict[str, Any]) -> bool:
    t = node.get("type")
    if t == "object":
        return True
    if isinstance(t, list) and "object" in t:
        return True
    return "properties" in node


def _harden_schema(node: Any) -> Any:
    """Deep-copy walk: set additionalProperties=false on every object schema."""
    if isinstance(node, list):
        return [_harden_schema(item) for item in node]
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {k: _harden_schema(v) for k, v in node.items()}
    if _is_object_schema(out):
        out["additionalProperties"] = False
    return out


def strict_tool(name: str, description: str, schema: dict[str, Any]) -> dict[str, Any]:
    """Build an Anthropic tool dict with strict=True and a hardened schema.

    Recursively sets ``additionalProperties: false`` on every object node so
    the payload is compatible with Anthropic structured/strict tool mode.
    The input schema is deep-copied; the caller's dict is never mutated.
    """
    if not isinstance(schema, dict):
        raise LLMContractError("tool schema must be a dict")
    return {
        "name": name,
        "description": description,
        "strict": True,
        "input_schema": _harden_schema(copy.deepcopy(schema)),
    }


# ── Tool-use extraction ───────────────────────────────────────────────────────


def extract_tool_input(message: Any, *, expected_tool: str, operation: str) -> dict[str, Any]:
    """Return the input dict of the matching tool_use block.

    Skips leading text (or other non-tool) blocks. Raises LLMContractError when
    the expected tool is missing or a different tool was called instead.
    """
    content = getattr(message, "content", None) or []
    seen_tools: list[str] = []
    for block in content:
        if getattr(block, "type", None) != "tool_use":
            continue
        name = getattr(block, "name", None)
        if name == expected_tool:
            payload = getattr(block, "input", None)
            if not isinstance(payload, dict):
                raise LLMContractError(
                    f"{operation}: tool {expected_tool!r} returned non-object input"
                )
            return payload
        if isinstance(name, str):
            seen_tools.append(name)

    if seen_tools:
        raise LLMContractError(
            f"{operation}: expected tool {expected_tool!r}, got {seen_tools[0]!r}"
        )
    stop = getattr(message, "stop_reason", None)
    raise LLMContractError(
        f"{operation}: missing tool_use for {expected_tool!r} (stop_reason={stop!r})"
    )


# ── Numeric / range helpers ─────────────────────────────────────────────────


def _require_dict(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LLMContractError(f"{label} must be an object")
    return value


def _finite_number(value: Any, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LLMContractError(f"{label} must be a finite number")
    num = float(value)
    if not math.isfinite(num):
        raise LLMContractError(f"{label} must be a finite number")
    return num


def _in_range(value: Any, *, label: str, lo: float, hi: float) -> float:
    num = _finite_number(value, label=label)
    if num < lo or num > hi:
        raise LLMContractError(f"{label} must be between {lo} and {hi}")
    return num


def _require_enum(value: Any, *, label: str, allowed: frozenset[str]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise LLMContractError(f"{label} must be one of {sorted(allowed)}; got {value!r}")
    return value


# ── Semantic validators ───────────────────────────────────────────────────────

_SCORE_KEYS = ("valuation", "growth", "health", "momentum", "dividend")

_SEC_SENTIMENT = frozenset({"POSITIVE", "NEUTRAL", "NEGATIVE", "MIXED"})
_SEC_ACTION = frozenset({"BULLISH", "NEUTRAL", "BEARISH"})
_SEC_URGENCY = frozenset({"HIGH", "MEDIUM", "LOW"})


def validate_ticker_card(
    card: Any, *, requested_symbol: str, current_price: float | None
) -> dict[str, Any]:
    """Validate a single-ticker research card against symbol + score + price contracts."""
    data = _require_dict(card, label="ticker card")
    symbol = data.get("symbol")
    if not isinstance(symbol, str) or symbol.upper() != requested_symbol.upper():
        raise LLMContractError(
            f"ticker card symbol {symbol!r} does not match requested {requested_symbol!r}"
        )

    scores = _require_dict(data.get("scores"), label="scores")
    for key in _SCORE_KEYS:
        _in_range(scores.get(key), label=f"scores.{key}", lo=0, hi=100)

    if current_price is not None:
        fair = _require_dict(data.get("fair_value"), label="fair_value")
        grounded = _finite_number(fair.get("current"), label="fair_value.current")
        if abs(grounded - float(current_price)) > 0.005:
            raise LLMContractError(
                f"fair_value.current {grounded} does not match "
                f"grounded price {current_price} (tol 0.005)"
            )
    return data


def validate_sentiment(result: Any, *, symbol: str, article_count: int) -> dict[str, Any]:
    """Validate a news-sentiment payload against symbol, count, and score range."""
    data = _require_dict(result, label="sentiment result")
    got_symbol = data.get("symbol")
    if not isinstance(got_symbol, str) or got_symbol.upper() != symbol.upper():
        raise LLMContractError(
            f"sentiment symbol {got_symbol!r} does not match requested {symbol!r}"
        )
    got_count = data.get("article_count")
    if got_count != article_count:
        raise LLMContractError(
            f"sentiment article_count {got_count!r} does not match {article_count}"
        )
    _in_range(
        data.get("sentiment_score"),
        label="sentiment_score",
        lo=-100,
        hi=100,
    )
    return data


def validate_debate_case(case: dict[str, Any]) -> dict[str, Any]:
    """Validate a bull/bear debate case (confidence 0..100, finite price_target)."""
    data = _require_dict(case, label="debate case")
    _in_range(data.get("confidence"), label="confidence", lo=0, hi=100)
    _finite_number(data.get("price_target"), label="price_target")
    return data


def validate_debate_decision(decision: dict[str, Any]) -> dict[str, Any]:
    """Validate a PM debate decision (conviction/weights + finite prices/pcts)."""
    data = _require_dict(decision, label="debate decision")
    _in_range(data.get("conviction"), label="conviction", lo=0, hi=100)
    _in_range(data.get("bull_weight"), label="bull_weight", lo=0, hi=100)
    _finite_number(data.get("price_target"), label="price_target")
    _finite_number(data.get("upside_pct"), label="upside_pct")
    _finite_number(data.get("stop_loss_pct"), label="stop_loss_pct")
    return data


def validate_fundamental_opportunity(opp: dict[str, Any]) -> dict[str, Any]:
    """Validate one fundamental scan opportunity; score 0..100 when present."""
    data = _require_dict(opp, label="fundamental opportunity")
    if "fundamental_score" in data and data["fundamental_score"] is not None:
        _in_range(
            data["fundamental_score"],
            label="fundamental_score",
            lo=0,
            hi=100,
        )
    return data


def validate_sec_analysis(result: dict[str, Any]) -> dict[str, Any]:
    """Validate SEC filing analysis enums (sentiment / action_signal / urgency)."""
    data = _require_dict(result, label="sec analysis")
    _require_enum(data.get("sentiment"), label="sentiment", allowed=_SEC_SENTIMENT)
    _require_enum(data.get("action_signal"), label="action_signal", allowed=_SEC_ACTION)
    _require_enum(data.get("urgency"), label="urgency", allowed=_SEC_URGENCY)
    return data


# ── Schema constants (strict-mode compatible) ─────────────────────────────────
# Every object property is listed in `required`. Optional/nullable values use
# type unions like ["number", "null"] rather than omitting the property.

NEWS_SENTIMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "symbol": {"type": "string"},
        "overall_sentiment": {
            "type": "string",
            "enum": [
                "VERY_POSITIVE",
                "POSITIVE",
                "NEUTRAL",
                "NEGATIVE",
                "VERY_NEGATIVE",
            ],
        },
        "sentiment_score": {"type": "integer"},
        "impact_level": {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW"]},
        "article_count": {"type": "integer"},
        "key_themes": {"type": "array", "items": {"type": "string"}},
        "positive_signals": {"type": "array", "items": {"type": "string"}},
        "negative_signals": {"type": "array", "items": {"type": "string"}},
        "notable_events": {"type": "array", "items": {"type": "string"}},
        "momentum_narrative": {"type": "string"},
        "investor_action": {
            "type": "string",
            "enum": ["WATCH_CLOSELY", "RESEARCH_MORE", "MONITOR", "NO_ACTION"],
        },
        "confidence": {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW"]},
    },
    "required": [
        "symbol",
        "overall_sentiment",
        "sentiment_score",
        "impact_level",
        "article_count",
        "key_themes",
        "positive_signals",
        "negative_signals",
        "notable_events",
        "momentum_narrative",
        "investor_action",
        "confidence",
    ],
}

SEC_FILING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "filing_type": {"type": "string", "enum": ["10-K", "10-Q", "8-K"]},
        "company": {"type": "string"},
        "period": {"type": "string"},
        "sentiment": {
            "type": "string",
            "enum": ["POSITIVE", "NEUTRAL", "NEGATIVE", "MIXED"],
        },
        "revenue_trend": {
            "type": "string",
            "enum": ["ACCELERATING", "STABLE", "DECELERATING", "DECLINING", "N/A"],
        },
        "key_findings": {"type": "array", "items": {"type": "string"}},
        "red_flags": {"type": "array", "items": {"type": "string"}},
        "positive_signals": {"type": "array", "items": {"type": "string"}},
        "guidance_tone": {
            "type": "string",
            "enum": ["RAISED", "MAINTAINED", "LOWERED", "WITHDRAWN", "N/A"],
        },
        "guidance_detail": {"type": "string"},
        "action_signal": {
            "type": "string",
            "enum": ["BULLISH", "NEUTRAL", "BEARISH"],
        },
        "summary": {"type": "string"},
        "urgency": {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW"]},
    },
    "required": [
        "filing_type",
        "company",
        "period",
        "sentiment",
        "revenue_trend",
        "key_findings",
        "red_flags",
        "positive_signals",
        "guidance_tone",
        "guidance_detail",
        "action_signal",
        "summary",
        "urgency",
    ],
}

_FUNDAMENTAL_OPPORTUNITY_ITEM: dict[str, Any] = {
    "type": "object",
    "properties": {
        "symbol": {"type": "string"},
        "company_name": {"type": "string"},
        "sector": {"type": "string"},
        "fundamental_score": {"type": "integer"},
        "momentum_gap": {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW"]},
        "revenue_trend": {
            "type": "string",
            "enum": ["ACCELERATING", "STABLE", "DECELERATING"],
        },
        "margin_trend": {
            "type": "string",
            "enum": ["EXPANDING", "STABLE", "CONTRACTING"],
        },
        "balance_sheet": {
            "type": "string",
            "enum": ["STRENGTHENING", "STABLE", "WEAKENING"],
        },
        "valuation_vs_growth": {
            "type": "string",
            "enum": ["CHEAP", "FAIR", "EXPENSIVE"],
        },
        "insider_activity": {
            "type": "string",
            "enum": ["BUYING", "NEUTRAL", "SELLING"],
        },
        "key_catalyst": {"type": "string"},
        "investment_horizon": {
            "type": "string",
            "enum": ["1-3 months", "3-6 months", "6-12 months"],
        },
        "thesis": {"type": "string"},
        "risk": {"type": "string"},
        "pe_ratio": {"type": ["number", "null"]},
        "revenue_growth": {"type": ["number", "null"]},
        "earnings_growth": {"type": ["number", "null"]},
        "analyst_upside": {"type": ["number", "null"]},
    },
    "required": [
        "symbol",
        "company_name",
        "sector",
        "fundamental_score",
        "momentum_gap",
        "revenue_trend",
        "margin_trend",
        "balance_sheet",
        "valuation_vs_growth",
        "insider_activity",
        "key_catalyst",
        "investment_horizon",
        "thesis",
        "risk",
        "pe_ratio",
        "revenue_growth",
        "earnings_growth",
        "analyst_upside",
    ],
}

_SECTOR_ROTATION_ITEM: dict[str, Any] = {
    "type": "object",
    "properties": {
        "sector": {"type": "string"},
        "signal": {"type": "string", "enum": ["INFLOW", "OUTFLOW", "NEUTRAL"]},
        "reason": {"type": "string"},
    },
    "required": ["sector", "signal", "reason"],
}

FUNDAMENTAL_SCAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "scan_date": {"type": "string"},
        "market_summary": {"type": "string"},
        "top_opportunities": {
            "type": "array",
            "items": _FUNDAMENTAL_OPPORTUNITY_ITEM,
        },
        "sector_rotation_signals": {
            "type": "array",
            "items": _SECTOR_ROTATION_ITEM,
        },
        "macro_headwinds": {"type": "array", "items": {"type": "string"}},
        "macro_tailwinds": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "scan_date",
        "market_summary",
        "top_opportunities",
        "sector_rotation_signals",
        "macro_headwinds",
        "macro_tailwinds",
    ],
}

_DEBATE_ARGUMENT_ITEM: dict[str, Any] = {
    "type": "object",
    "properties": {
        "point": {"type": "string"},
        "evidence": {"type": "string"},
    },
    "required": ["point", "evidence"],
}

DEBATE_CASE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "thesis": {"type": "string", "description": "One-sentence core thesis"},
        "arguments": {
            "type": "array",
            "description": "3-5 supporting arguments, most important first",
            "items": _DEBATE_ARGUMENT_ITEM,
        },
        "price_target": {
            "type": "number",
            "description": "12-month price target implied by this case",
        },
        "key_risk": {"type": "string", "description": "Biggest risk to this case"},
        "confidence": {"type": "integer", "description": "Confidence 0-100"},
    },
    "required": ["thesis", "arguments", "price_target", "key_risk", "confidence"],
}

DEBATE_DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["STRONG BUY", "BUY", "HOLD", "SELL", "STRONG SELL"],
        },
        "conviction": {"type": "integer", "description": "Conviction 0-100"},
        "price_target": {
            "type": "number",
            "description": "Blended 12-month price target",
        },
        "upside_pct": {"type": "number"},
        "bull_weight": {
            "type": "integer",
            "description": "Weight given to bull case 0-100 (100 = fully bullish)",
        },
        "rationale": {"type": "string", "description": "2-3 sentence synthesis"},
        "catalysts": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Top 2-3 catalysts to watch",
        },
        "stop_loss_pct": {
            "type": "number",
            "description": "Suggested stop-loss % below current price (positive number)",
        },
    },
    "required": [
        "action",
        "conviction",
        "price_target",
        "upside_pct",
        "bull_weight",
        "rationale",
        "catalysts",
        "stop_loss_pct",
    ],
}
