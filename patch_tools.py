import re

with open("ai_analyzer.py", "r") as f:
    code = f.read()

# Add schemas right before deep_analyze_portfolio
schemas = '''
DEEP_ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "portfolio_score": {"type": "integer"},
        "portfolio_verdict": {"type": "string"},
        "portfolio_type": {"type": "string"},
        "risk_score": {"type": "integer"},
        "market_context": {"type": "string"},
        "diversification_score": {"type": "integer"},
        "holdings_analysis": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "company_type": {"type": "string"},
                    "scores": {
                        "type": "object",
                        "properties": {
                            "valuation": {"type": "integer"},
                            "growth": {"type": "integer"},
                            "health": {"type": "integer"},
                            "momentum": {"type": "integer"},
                            "dividend": {"type": "integer"}
                        },
                        "required": ["valuation", "growth", "health", "momentum", "dividend"]
                    },
                    "snowflake_summary": {"type": "string"},
                    "fair_value": {
                        "type": "object",
                        "properties": {
                            "estimate": {"type": "number"},
                            "current": {"type": "number"},
                            "status": {"type": "string"},
                            "pct_diff": {"type": "number"}
                        },
                        "required": ["estimate", "current", "status", "pct_diff"]
                    },
                    "risks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"text": {"type": "string"}, "severity": {"type": "string"}},
                            "required": ["text", "severity"]
                        }
                    },
                    "rewards": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"text": {"type": "string"}},
                            "required": ["text"]
                        }
                    },
                    "prediction": {
                        "type": "object",
                        "properties": {"3m": {"type": "string"}, "12m": {"type": "string"}, "rationale": {"type": "string"}},
                        "required": ["3m", "12m", "rationale"]
                    },
                    "verdict": {"type": "string"},
                    "analyst_note": {"type": "string"}
                },
                "required": ["symbol", "company_type", "scores", "snowflake_summary", "fair_value", "risks", "rewards", "prediction", "verdict", "analyst_note"]
            }
        },
        "portfolio_risks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"text": {"type": "string"}, "severity": {"type": "string"}},
                "required": ["text", "severity"]
            }
        },
        "portfolio_recommendations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"action": {"type": "string"}, "priority": {"type": "string"}, "rationale": {"type": "string"}},
                "required": ["action", "priority", "rationale"]
            }
        },
        "sector_breakdown": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"sector": {"type": "string"}, "allocation_pct": {"type": "number"}, "assessment": {"type": "string"}},
                "required": ["sector", "allocation_pct", "assessment"]
            }
        }
    },
    "required": ["portfolio_score", "portfolio_verdict", "portfolio_type", "risk_score", "market_context", "diversification_score", "holdings_analysis", "portfolio_risks", "portfolio_recommendations", "sector_breakdown"]
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
                    "recommendation": {"type": "string"}
                },
                "required": ["symbol", "company_name", "sector", "composite_score", "momentum_score", "value_score", "growth_score", "analyst_score", "verdict", "key_thesis", "main_risk", "price", "ret_1w", "ret_1m", "analyst_target", "analyst_upside", "num_analysts", "recommendation"]
            }
        }
    },
    "required": ["screen_summary", "market_bias", "top_sectors", "picks"]
}

def deep_analyze_portfolio(
    payload: dict[str, Any],
    regime_context: str | None = None,
) -> dict[str, Any]:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY must be set in your .env file.")
    client = anthropic.Anthropic(api_key=api_key)

    today = __import__('datetime').date.today().isoformat()
    regime_block = f"\\n\\n{regime_context}" if regime_context else ""

    message = client.messages.create(
        model=MODEL,
        max_tokens=8000,
        system=DEEP_SYSTEM_PROMPT,
        tools=[{"name": "return_portfolio_analysis", "input_schema": DEEP_ANALYSIS_SCHEMA}],
        tool_choice={"type": "tool", "name": "return_portfolio_analysis"},
        messages=[{
            "role": "user",
            "content": (
                f"Perform a deep financial analysis of this portfolio. Today is {today}.{regime_block}\\n\\n"
                f"```json\\n{json.dumps(payload, indent=2)}\\n```"
            )
        }]
    )
    return message.content[0].input

'''

# Find def deep_analyze_portfolio and replace up to end of that function or screener prompt
old_func_start = r'def deep_analyze_portfolio\('
old_func_end = r'return _extract_and_parse\(repair_msg\.content\[0\]\.text\)'

code = re.sub(old_func_start + r'.*?' + old_func_end, schemas.strip() + "\n\n", code, flags=re.DOTALL)

# Same for analyze_screener
screener_func_start = r'def analyze_screener\('
screener_func_end = r'return _extract_and_parse\(repair\.content\[0\]\.text\)'

new_screener = '''def analyze_screener(candidates: list[dict]) -> dict[str, Any]:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY must be set in your .env file.")
    client = anthropic.Anthropic(api_key=api_key)

    today = __import__('datetime').date.today().isoformat()

    message = client.messages.create(
        model=MODEL,
        max_tokens=6000,
        system=SCREENER_SYSTEM_PROMPT,
        tools=[{"name": "return_screener_picks", "input_schema": SCREENER_SCHEMA}],
        tool_choice={"type": "tool", "name": "return_screener_picks"},
        messages=[{
            "role": "user",
            "content": (
                f"Today is {today}. Analyze these {len(candidates)} stock candidates "
                f"and select the best 20 investment opportunities this week.\\n\\n"
                f"```json\\n{json.dumps(candidates, indent=2)}\\n```"
            )
        }]
    )
    return message.content[0].input'''

code = re.sub(screener_func_start + r'.*?' + screener_func_end, new_screener.strip(), code, flags=re.DOTALL)

with open("ai_analyzer.py", "w") as f:
    f.write(code)

print("Tool Use API implemented.")
