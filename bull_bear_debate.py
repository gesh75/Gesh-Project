"""
Bull / Bear Debate Engine — GESH Trading

Runs a structured 3-call Claude debate for any symbol:
  1. Bull analyst builds the strongest bull case
  2. Bear analyst builds the strongest bear case
  3. Portfolio manager synthesises both into a final decision

Uses Claude tool-use for structured JSON output (same pattern as ai_analyzer.py).
Cheap path: Bull + Bear calls use claude-haiku, PM synthesis uses claude-sonnet.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from dotenv import load_dotenv

import llm_gateway

load_dotenv()
logger = logging.getLogger(__name__)

_HAIKU = llm_gateway.resolve_model("haiku", default="haiku")
_SONNET = llm_gateway.resolve_model(os.getenv("CLAUDE_MODEL"), default="sonnet")

# ── Schemas ────────────────────────────────────────────────────────────────────

_CASE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "thesis": {"type": "string", "description": "One-sentence core thesis"},
        "arguments": {
            "type": "array",
            "description": "3-5 supporting arguments, most important first",
            "items": {
                "type": "object",
                "properties": {
                    "point": {"type": "string"},
                    "evidence": {"type": "string"},
                },
                "required": ["point", "evidence"],
            },
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

_DECISION_SCHEMA: dict[str, Any] = {
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


# ── Internal helpers ───────────────────────────────────────────────────────────


def _tool_call(
    client: Any,
    model: str,
    system: str,
    user: str,
    tool_name: str,
    schema: dict[str, Any],
) -> dict[str, Any]:
    """Single Claude tool-use call; returns the structured JSON dict."""
    resp = client.messages.create(
        model=model,
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": user}],
        tools=[{"name": tool_name, "description": system[:80], "input_schema": schema}],
        tool_choice={"type": "tool", "name": tool_name},
    )
    for block in resp.content:
        if block.type == "tool_use" and block.name == tool_name:
            return block.input  # type: ignore[return-value]
    raise ValueError(f"No tool_use block returned for {tool_name}")


def _context_prompt(symbol: str, consensus: dict[str, Any], fundamentals: dict[str, Any]) -> str:
    """Build the shared market context injected into every debate call."""
    return (
        f"Symbol: {symbol}\n"
        f"Consensus score: {consensus.get('raw_score', 0):.2f} "
        f"({consensus.get('verdict', 'N/A')})\n"
        f"Active nodes: {consensus.get('active_nodes', 0)}/11\n"
        f"Node results: {json.dumps(consensus.get('details', {}), indent=2)}\n\n"
        f"Fundamentals (OpenBB/yfinance):\n"
        f"  Revenue growth:  {fundamentals.get('rev_growth')}\n"
        f"  Forward PE:      {fundamentals.get('pe')}\n"
        f"  Profit margin:   {fundamentals.get('profit_margin')}\n"
        f"  EPS growth:      {fundamentals.get('eps_growth')}\n"
        f"  Analyst rec:     {fundamentals.get('rec_key')}\n"
        f"  Price target:    {fundamentals.get('target_mean_price')}\n"
        f"  Current price:   {fundamentals.get('current_price')}\n"
    )


# ── Public API ─────────────────────────────────────────────────────────────────


def run_debate(
    symbol: str,
    consensus: dict[str, Any],
    fundamentals: dict[str, Any],
) -> dict[str, Any]:
    """
    Run the 3-call bull/bear debate for `symbol`.

    Args:
        symbol:       Ticker, e.g. "NVDA"
        consensus:    Output of run_consensus_holding() — must contain raw_score, details, verdict
        fundamentals: Output of get_fundamentals_openbb() — may be empty dict

    Returns:
        {
            "symbol": str,
            "bull": {...},          # _CASE_SCHEMA output
            "bear": {...},          # _CASE_SCHEMA output
            "decision": {...},      # _DECISION_SCHEMA output
            "debate_summary": str,  # human-readable one-liner
        }
    """
    client = llm_gateway.create_client()

    ctx = _context_prompt(symbol, consensus, fundamentals)

    # ── Call 1: Bull case (haiku — cheap) ────────────────────────────────────
    bull = _tool_call(
        client,
        _HAIKU,
        system="You are an aggressive buy-side analyst. Build the strongest possible BULL case.",
        user=(
            f"Build the strongest bull case for {symbol}.\n\n"
            f"Market context:\n{ctx}\n"
            f"Focus on catalysts, growth drivers, and why the bears are wrong."
        ),
        tool_name="bull_case",
        schema=_CASE_SCHEMA,
    )
    logger.info(
        "Debate [%s] bull: %s (conf=%s%%)",
        symbol,
        bull.get("thesis", "")[:60],
        bull.get("confidence"),
    )

    # ── Call 2: Bear case (haiku — cheap) ────────────────────────────────────
    bear = _tool_call(
        client,
        _HAIKU,
        system="You are a short-seller. Build the strongest possible BEAR case.",
        user=(
            f"Build the strongest bear case for {symbol}.\n\n"
            f"Market context:\n{ctx}\n"
            f"The bull analyst argues: {bull.get('thesis', '')}\n"
            f"Dismantle that thesis. Focus on risks, overvaluation, and headwinds."
        ),
        tool_name="bear_case",
        schema=_CASE_SCHEMA,
    )
    logger.info(
        "Debate [%s] bear: %s (conf=%s%%)",
        symbol,
        bear.get("thesis", "")[:60],
        bear.get("confidence"),
    )

    # ── Call 3: PM synthesis (sonnet — full reasoning) ────────────────────────
    decision = _tool_call(
        client,
        _SONNET,
        system=(
            "You are a disciplined portfolio manager. You have heard both sides of the debate. "
            "Your job is to weigh the evidence and make a clear, executable decision. "
            "Be specific about price targets, conviction, and risk management."
        ),
        user=(
            f"You must decide on {symbol}. Here is the full debate:\n\n"
            f"Market context:\n{ctx}\n"
            f"BULL CASE (conf={bull.get('confidence')}%):\n"
            f"  Thesis: {bull.get('thesis')}\n"
            f"  Arguments: {json.dumps(bull.get('arguments', []))}\n"
            f"  Bull target: ${bull.get('price_target')}\n"
            f"  Bull key risk: {bull.get('key_risk')}\n\n"
            f"BEAR CASE (conf={bear.get('confidence')}%):\n"
            f"  Thesis: {bear.get('thesis')}\n"
            f"  Arguments: {json.dumps(bear.get('arguments', []))}\n"
            f"  Bear target: ${bear.get('price_target')}\n"
            f"  Bear key risk: {bear.get('key_risk')}\n\n"
            f"Current price: ${fundamentals.get('current_price', 'unknown')}\n"
            f"Make your final portfolio decision."
        ),
        tool_name="pm_decision",
        schema=_DECISION_SCHEMA,
    )
    logger.info(
        "Debate [%s] PM decision: %s conv=%s%%",
        symbol,
        decision.get("action"),
        decision.get("conviction"),
    )

    summary = (
        f"{symbol}: {decision.get('action')} | "
        f"Bull {bull.get('confidence')}% vs Bear {bear.get('confidence')}% | "
        f"PM target ${decision.get('price_target')} "
        f"({decision.get('upside_pct', 0):+.1f}%) | "
        f"Conv={decision.get('conviction')}%"
    )

    return {
        "symbol": symbol,
        "bull": bull,
        "bear": bear,
        "decision": decision,
        "debate_summary": summary,
    }
