"""Unit tests for llm_contracts — strict tools + semantic validators."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from llm_contracts import (
    DEBATE_CASE_SCHEMA,
    DEBATE_DECISION_SCHEMA,
    FUNDAMENTAL_SCAN_SCHEMA,
    NEWS_SENTIMENT_SCHEMA,
    SEC_FILING_SCHEMA,
    LLMContractError,
    extract_tool_input,
    strict_tool,
    validate_debate_case,
    validate_debate_decision,
    validate_fundamental_opportunity,
    validate_sec_analysis,
    validate_sentiment,
    validate_ticker_card,
)


def test_strict_tool_recursively_forbids_unknown_object_properties():
    tool = strict_tool(
        "return_card",
        "Return exactly one structured card.",
        {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
    )
    assert tool["strict"] is True
    assert tool["name"] == "return_card"
    assert tool["input_schema"]["additionalProperties"] is False


def test_strict_tool_sets_additional_properties_false_on_nested_objects_and_arrays():
    schema = {
        "type": "object",
        "properties": {
            "scores": {
                "type": "object",
                "properties": {"valuation": {"type": "integer"}},
                "required": ["valuation"],
            },
            "risks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            },
        },
        "required": ["scores", "risks"],
    }
    original = {
        "type": "object",
        "properties": {
            "scores": {
                "type": "object",
                "properties": {"valuation": {"type": "integer"}},
                "required": ["valuation"],
            },
            "risks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            },
        },
        "required": ["scores", "risks"],
    }
    tool = strict_tool("nested", "Nested objects.", schema)
    hardened = tool["input_schema"]
    assert hardened["additionalProperties"] is False
    assert hardened["properties"]["scores"]["additionalProperties"] is False
    assert hardened["properties"]["risks"]["items"]["additionalProperties"] is False
    # Caller schema is not mutated (deepcopy).
    assert "additionalProperties" not in schema
    assert schema == original


def test_extract_tool_input_skips_leading_text_and_returns_matching_tool():
    message = SimpleNamespace(
        stop_reason="tool_use",
        content=[
            SimpleNamespace(type="text", text="thinking aloud…"),
            SimpleNamespace(
                type="tool_use",
                name="return_card",
                input={"symbol": "AAPL", "verdict": "BUY"},
            ),
        ],
    )
    payload = extract_tool_input(
        message, expected_tool="return_card", operation="deep_analyze_ticker"
    )
    assert payload == {"symbol": "AAPL", "verdict": "BUY"}


def test_extract_tool_input_rejects_wrong_tool_name():
    message = SimpleNamespace(
        stop_reason="tool_use",
        content=[
            SimpleNamespace(
                type="tool_use",
                name="wrong_tool",
                input={"symbol": "AAPL"},
            ),
        ],
    )
    with pytest.raises(LLMContractError, match="expected tool"):
        extract_tool_input(message, expected_tool="return_card", operation="deep_analyze_ticker")


def test_extract_tool_input_rejects_missing_tool():
    message = SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text="I refuse.")],
    )
    with pytest.raises(LLMContractError, match="missing tool_use"):
        extract_tool_input(message, expected_tool="return_card", operation="deep_analyze_ticker")


def _valid_ticker_card(*, symbol: str = "AAPL", price: float = 100.0) -> dict:
    return {
        "symbol": symbol,
        "scores": {
            "valuation": 60,
            "growth": 70,
            "health": 80,
            "momentum": 55,
            "dividend": 10,
        },
        "fair_value": {
            "estimate": 110.0,
            "current": price,
            "status": "undervalued",
            "pct_diff": 10.0,
        },
    }


def test_validate_ticker_card_rejects_wrong_symbol_and_out_of_range_score():
    card = {"symbol": "MSFT", "scores": {"valuation": 101}}
    with pytest.raises(LLMContractError, match="symbol"):
        validate_ticker_card(card, requested_symbol="AAPL", current_price=100.0)


def test_validate_ticker_card_rejects_score_out_of_range():
    card = _valid_ticker_card()
    card["scores"]["momentum"] = 101
    with pytest.raises(LLMContractError, match="scores.momentum"):
        validate_ticker_card(card, requested_symbol="AAPL", current_price=100.0)


def test_validate_ticker_card_rejects_non_finite_score():
    card = _valid_ticker_card()
    card["scores"]["growth"] = float("nan")
    with pytest.raises(LLMContractError, match="scores.growth"):
        validate_ticker_card(card, requested_symbol="AAPL", current_price=100.0)


def test_validate_ticker_card_rejects_bool_score():
    """True must not validate as valuation=1.0."""
    card = _valid_ticker_card()
    card["scores"]["valuation"] = True
    with pytest.raises(LLMContractError, match="scores.valuation"):
        validate_ticker_card(card, requested_symbol="AAPL", current_price=100.0)


def test_validate_debate_case_rejects_bool_and_inf_price_target():
    base = {
        "thesis": "AI demand",
        "arguments": [],
        "price_target": 250.0,
        "key_risk": "regulation",
        "confidence": 70,
    }
    with pytest.raises(LLMContractError, match="confidence"):
        validate_debate_case({**base, "confidence": True})
    with pytest.raises(LLMContractError, match="price_target"):
        validate_debate_case({**base, "price_target": float("inf")})


def test_validate_ticker_card_rejects_wrong_grounded_price():
    card = _valid_ticker_card(price=99.0)
    with pytest.raises(LLMContractError, match="fair_value.current"):
        validate_ticker_card(card, requested_symbol="AAPL", current_price=100.0)


def test_validate_ticker_card_accepts_price_within_tolerance():
    card = _valid_ticker_card(price=100.004)
    out = validate_ticker_card(card, requested_symbol="aapl", current_price=100.0)
    assert out["symbol"] == "AAPL"


def test_validate_sentiment_rejects_range_and_mismatches():
    with pytest.raises(LLMContractError, match="symbol"):
        validate_sentiment(
            {"symbol": "MSFT", "article_count": 3, "sentiment_score": 10},
            symbol="AAPL",
            article_count=3,
        )
    with pytest.raises(LLMContractError, match="article_count"):
        validate_sentiment(
            {"symbol": "AAPL", "article_count": 1, "sentiment_score": 10},
            symbol="AAPL",
            article_count=3,
        )
    with pytest.raises(LLMContractError, match="sentiment_score"):
        validate_sentiment(
            {"symbol": "AAPL", "article_count": 3, "sentiment_score": 150},
            symbol="AAPL",
            article_count=3,
        )
    ok = validate_sentiment(
        {"symbol": "aapl", "article_count": 3, "sentiment_score": -40},
        symbol="AAPL",
        article_count=3,
    )
    assert ok["sentiment_score"] == -40


def test_validate_sec_analysis_rejects_invalid_enum():
    good = {
        "sentiment": "POSITIVE",
        "action_signal": "BULLISH",
        "urgency": "HIGH",
    }
    assert validate_sec_analysis(good)["urgency"] == "HIGH"
    bad = {**good, "action_signal": "BUY"}
    with pytest.raises(LLMContractError, match="action_signal"):
        validate_sec_analysis(bad)


def test_validate_debate_confidence_out_of_range():
    case = {
        "thesis": "AI demand",
        "arguments": [],
        "price_target": 250.0,
        "key_risk": "regulation",
        "confidence": 120,
    }
    with pytest.raises(LLMContractError, match="confidence"):
        validate_debate_case(case)

    decision = {
        "action": "BUY",
        "conviction": 70,
        "price_target": 200.0,
        "upside_pct": 15.0,
        "bull_weight": 101,
        "rationale": "balanced",
        "catalysts": ["earnings"],
        "stop_loss_pct": 8.0,
    }
    with pytest.raises(LLMContractError, match="bull_weight"):
        validate_debate_decision(decision)


def test_validate_fundamental_opportunity_score_range():
    opp = {"symbol": "CAT", "fundamental_score": 88}
    assert validate_fundamental_opportunity(opp)["fundamental_score"] == 88
    with pytest.raises(LLMContractError, match="fundamental_score"):
        validate_fundamental_opportunity({"symbol": "CAT", "fundamental_score": -1})


def test_schema_constants_list_every_property_as_required():
    for schema in (
        NEWS_SENTIMENT_SCHEMA,
        SEC_FILING_SCHEMA,
        FUNDAMENTAL_SCAN_SCHEMA,
        DEBATE_CASE_SCHEMA,
        DEBATE_DECISION_SCHEMA,
    ):
        props = set(schema["properties"])
        required = set(schema["required"])
        assert props == required, f"missing required: {props - required}"

    # Nullable fundamentals fields use type unions, not omitted keys.
    opp = FUNDAMENTAL_SCAN_SCHEMA["properties"]["top_opportunities"]["items"]
    for key in ("pe_ratio", "revenue_growth", "earnings_growth", "analyst_upside"):
        assert opp["properties"][key]["type"] == ["number", "null"]
        assert key in opp["required"]
