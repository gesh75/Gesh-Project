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

import llm_contracts
import llm_gateway

load_dotenv()
logger = logging.getLogger(__name__)

_HAIKU = llm_gateway.resolve_model("haiku", default="haiku")
_SONNET = llm_gateway.resolve_model(os.getenv("CLAUDE_MODEL"), default="sonnet")


# ── Internal helpers ───────────────────────────────────────────────────────────


def _tool_call(
    *,
    operation: str,
    model: str,
    system: str,
    user: str,
    tool_name: str,
    schema: dict[str, Any],
) -> dict[str, Any]:
    """Single Claude tool-use call via Gateway; returns the structured JSON dict."""
    tool = llm_contracts.strict_tool(tool_name, system[:80], schema)
    resp = llm_gateway.get_gateway().create(
        operation=operation,
        model=model,
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": user}],
        tools=[tool],
        tool_choice={"type": "tool", "name": tool_name},
    )
    return llm_contracts.extract_tool_input(resp, expected_tool=tool_name, operation=operation)


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
            "bull": {...},          # DEBATE_CASE_SCHEMA output
            "bear": {...},          # DEBATE_CASE_SCHEMA output
            "decision": {...},      # DEBATE_DECISION_SCHEMA output
            "debate_summary": str,  # human-readable one-liner
        }
    """
    ctx = _context_prompt(symbol, consensus, fundamentals)

    # ── Call 1: Bull case (haiku — cheap) ────────────────────────────────────
    bull = llm_contracts.validate_debate_case(
        _tool_call(
            operation="debate_bull",
            model=_HAIKU,
            system="You are an aggressive buy-side analyst. Build the strongest possible BULL case.",
            user=(
                f"Build the strongest bull case for {symbol}.\n\n"
                f"Market context:\n{ctx}\n"
                f"Focus on catalysts, growth drivers, and why the bears are wrong."
            ),
            tool_name="bull_case",
            schema=llm_contracts.DEBATE_CASE_SCHEMA,
        )
    )
    logger.info(
        "Debate [%s] bull: %s (conf=%s%%)",
        symbol,
        bull.get("thesis", "")[:60],
        bull.get("confidence"),
    )

    # ── Call 2: Bear case (haiku — cheap) ────────────────────────────────────
    bear = llm_contracts.validate_debate_case(
        _tool_call(
            operation="debate_bear",
            model=_HAIKU,
            system="You are a short-seller. Build the strongest possible BEAR case.",
            user=(
                f"Build the strongest bear case for {symbol}.\n\n"
                f"Market context:\n{ctx}\n"
                f"The bull analyst argues: {bull.get('thesis', '')}\n"
                f"Dismantle that thesis. Focus on risks, overvaluation, and headwinds."
            ),
            tool_name="bear_case",
            schema=llm_contracts.DEBATE_CASE_SCHEMA,
        )
    )
    logger.info(
        "Debate [%s] bear: %s (conf=%s%%)",
        symbol,
        bear.get("thesis", "")[:60],
        bear.get("confidence"),
    )

    # ── Call 3: PM synthesis (sonnet — full reasoning) ────────────────────────
    decision = llm_contracts.validate_debate_decision(
        _tool_call(
            operation="debate_pm",
            model=_SONNET,
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
            schema=llm_contracts.DEBATE_DECISION_SCHEMA,
        )
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
