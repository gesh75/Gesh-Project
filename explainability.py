"""
explainability.py — Plain-English Transparency Node

Translates the black-box consensus output into a human-readable reasoning
narrative.  Designed to be injected into Claude prompts AND rendered in the
dashboard "Why?" panel so the user understands exactly what drove a verdict.

Key functions
─────────────
  explain_consensus(symbol, consensus_result)
      → {headline, node_bullets, conflict_notes, plain_english, confidence_bar}

  explain_monte_carlo(mc_result)
      → {plain_english, risk_level, key_numbers}

  build_transparency_report(symbol, consensus, mc_result, regime, kelly)
      → full transparency dict for dashboard injection

  transparency_for_claude(symbol, consensus, mc_result)
      → compact string for Claude prompt injection
"""

from __future__ import annotations

from typing import Any


# ─── Verdict → human labels ───────────────────────────────────────────────────

VERDICT_LABEL: dict[str, str] = {
    "STRONG BUY":  "Strong Buy 🚀",
    "BUY":         "Buy 🟢",
    "HOLD":        "Hold 🟡",
    "SELL":        "Sell 🔴",
    "STRONG SELL": "Strong Sell 💀",
    "INSUFFICIENT DATA": "Not Enough Data ⚠️",
}

REGIME_PLAIN: dict[str, str] = {
    "TRENDING":       "the stock is in a strong directional trend",
    "RANGING":        "the stock is moving sideways in a low-volatility range",
    "BREAKOUT-READY": "price is coiling in a tight range and a breakout looks imminent",
    "MOMENTUM-HIGH":  "the stock is overbought (RSI elevated) — momentum is stretched",
    "MOMENTUM-LOW":   "the stock is oversold (RSI depressed) — possible reversal setup",
    "NEUTRAL":        "no dominant trend signal",
}

NODE_PLAIN: dict[str, str] = {
    "Momentum":      "Short-term price momentum (RSI + moving average crossover)",
    "Trend":         "Trend strength measured by ADX and Bollinger Band width",
    "Rel.Strength":  "1-month return relative to the S&P 500",
    "Fundamentals":  "Revenue growth, profit margins, and PE ratio",
    "Analyst":       "Wall Street analyst consensus and price target upside",
    "News":          "Live Reddit sentiment (r/stocks, r/investing, r/wsb)",
    "Kelly":         "Kelly criterion position sizing signal",
    "ECG":           "Equity Curve Gate — portfolio health circuit breaker",
    "Volume":        "Volume trend vs 30-day average",
    "FFT_Cycle":     "Dominant price cycle phase (spectral analysis)",
    "Macro":         "Fed Funds Rate, yield curve, VIX, and credit spreads",
}

VOTE_LABEL: dict[int | None, str] = {
    1:    "Bullish ↑",
    0:    "Neutral →",
    -1:   "Bearish ↓",
    None: "Abstained —",
}

RISK_LEVEL: dict[str, str] = {
    "LOW":      "Low Risk 🟢",
    "MODERATE": "Moderate Risk 🟡",
    "HIGH":     "High Risk 🔴",
    "EXTREME":  "Extreme Risk 💀",
}


# ─── Core explainer ──────────────────────────────────────────────────────────

def explain_consensus(symbol: str, consensus: dict[str, Any]) -> dict[str, Any]:
    """
    Translate a consensus result dict into plain English.

    Parameters
    ----------
    symbol    : ticker symbol
    consensus : dict returned by run_consensus_holding() in consensus.py

    Returns
    -------
    {
        headline        : "NVDA — Strong Buy (score=+0.67, 9/11 nodes)"
        node_bullets    : list of "✅ Momentum: Bullish — RSI=72..."
        conflict_notes  : list of conflicting signal warnings
        plain_english   : full paragraph narrative
        confidence_bar  : "████████░░ 82%"
    }
    """
    verdict    = consensus.get("verdict", "INSUFFICIENT DATA")
    raw_score  = consensus.get("raw_score", consensus.get("score", 0.0))
    active     = consensus.get("active_nodes", consensus.get("nodes_voted", 0))
    confidence = consensus.get("confidence", 0.0)
    votes      = consensus.get("votes", {})
    details    = consensus.get("details", consensus.get("node_results", {}))
    regime     = consensus.get("regime", "NEUTRAL")

    label = VERDICT_LABEL.get(verdict, verdict)
    headline = f"{symbol} — {label} (score={raw_score:+.2f}, {active} nodes active)"

    # Node-by-node bullets
    node_bullets: list[str] = []
    for node, vote_val in votes.items():
        icon   = "✅" if vote_val == 1 else "⚪" if vote_val == 0 else "❌" if vote_val == -1 else "⬜"
        plain  = NODE_PLAIN.get(node, node)
        detail = details.get(node, "")
        vote_str = VOTE_LABEL.get(vote_val, "Unknown")
        node_bullets.append(f"{icon} {node}: {vote_str} — {plain}. {detail}")

    # Detect conflicts
    conflict_notes: list[str] = []
    bull_nodes = [n for n, v in votes.items() if v == 1]
    bear_nodes = [n for n, v in votes.items() if v == -1]
    if bull_nodes and bear_nodes:
        conflict_notes.append(
            f"Mixed signals: {', '.join(bull_nodes[:3])} say Bullish but "
            f"{', '.join(bear_nodes[:2])} say Bearish. "
            f"Net score {raw_score:+.2f} favours {'bulls' if raw_score > 0 else 'bears'}."
        )
    if votes.get("Kelly") == -1 and raw_score > 0.25:
        conflict_notes.append(
            "Kelly says TRIM despite a bullish consensus — position may already be oversized relative to volatility."
        )
    if votes.get("Macro") == -1 and raw_score > 0:
        conflict_notes.append(
            "Macro headwinds (Fed rate, yield curve, or credit spreads) are elevated — "
            "consider holding back 20% of intended allocation."
        )

    # Confidence bar (10 chars)
    filled = round(confidence / 10)
    conf_bar = "█" * filled + "░" * (10 - filled) + f"  {confidence:.0f}%"

    # Plain-English paragraph
    regime_desc = REGIME_PLAIN.get(regime, "the regime is unclear")
    para_parts = [
        f"For {symbol}, the 11-node consensus engine returns a {verdict} verdict "
        f"with a net score of {raw_score:+.2f} and {confidence:.0f}% confidence "
        f"({active} of 11 nodes voted).",
        f"The current market regime indicates {regime_desc}.",
    ]
    if bull_nodes:
        para_parts.append(f"Bullish signals came from: {', '.join(bull_nodes)}.")
    if bear_nodes:
        para_parts.append(f"Bearish signals came from: {', '.join(bear_nodes)}.")
    if conflict_notes:
        para_parts.append("⚠ " + " ".join(conflict_notes))
    plain_english = " ".join(para_parts)

    return {
        "headline":      headline,
        "node_bullets":  node_bullets,
        "conflict_notes":conflict_notes,
        "plain_english": plain_english,
        "confidence_bar":conf_bar,
        "verdict":       verdict,
        "raw_score":     raw_score,
        "active_nodes":  active,
        "confidence":    confidence,
    }


def explain_monte_carlo(mc: dict[str, Any]) -> dict[str, Any]:
    """Translate Monte Carlo output into plain English."""
    if "error" in mc:
        return {"plain_english": f"Monte Carlo unavailable: {mc['error']}", "risk_level": "UNKNOWN"}

    equity   = mc.get("current_equity", 0)
    var95    = mc.get("var_95", 0)
    var_pct  = mc.get("var_95_pct", 0)
    median   = mc.get("median", equity)
    p5       = mc.get("p5", 0)
    p95      = mc.get("p95", 0)
    horizon  = mc.get("horizon_days", 21)
    vol_meth = mc.get("vol_method", "EWMA")
    regime   = mc.get("regime", "NEUTRAL")
    mult     = mc.get("regime_multiplier", 1.0)

    # Risk classification
    if var_pct > 25:    risk = "EXTREME"
    elif var_pct > 15:  risk = "HIGH"
    elif var_pct > 8:   risk = "MODERATE"
    else:               risk = "LOW"

    plain = (
        f"Over the next {horizon} trading days, the portfolio's "
        f"median outcome is ${median:,.0f} "
        f"(from current ${equity:,.0f}). "
        f"There is a 5% chance of losing more than ${var95:,.0f} "
        f"({var_pct:.1f}% of portfolio). "
        f"The range from worst-case (5th pct) to best-case (95th pct) is "
        f"${p5:,.0f}–${p95:,.0f}. "
        f"Volatility estimated via {vol_meth}. "
        f"Regime '{regime}' applied a {mult:.2f}× drift multiplier."
    )

    return {
        "plain_english": plain,
        "risk_level":    risk,
        "risk_label":    RISK_LEVEL.get(risk, risk),
        "key_numbers": {
            "var_95_usd":   var95,
            "var_95_pct":   var_pct,
            "median_usd":   median,
            "p5_usd":       p5,
            "p95_usd":      p95,
        },
    }


def build_transparency_report(
    symbol: str,
    consensus: dict[str, Any],
    mc_result:  dict[str, Any] | None = None,
    regime:     str = "NEUTRAL",
    kelly:      dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Full transparency report combining all engines."""
    exp_con = explain_consensus(symbol, {**consensus, "regime": regime})
    exp_mc  = explain_monte_carlo(mc_result) if mc_result else {}

    kelly_plain = ""
    if kelly:
        action = kelly.get("action", "HOLD")
        pct    = kelly.get("kelly_pct", 0)
        kelly_plain = (
            f"Kelly sizing recommends {action} — "
            f"optimal allocation is {pct:.1f}% of portfolio."
        )

    return {
        "symbol":          symbol,
        "consensus":       exp_con,
        "monte_carlo":     exp_mc,
        "kelly_plain":     kelly_plain,
        "full_narrative":  (
            exp_con["plain_english"] + " " +
            exp_mc.get("plain_english", "") + " " +
            kelly_plain
        ).strip(),
    }


def transparency_for_claude(
    symbol: str,
    consensus: dict[str, Any],
    mc_result: dict[str, Any] | None = None,
) -> str:
    """Compact string for injection into Claude deep-analysis prompts."""
    report = build_transparency_report(symbol, consensus, mc_result)
    lines = [
        f"[TRANSPARENCY — {symbol}]",
        report["consensus"]["headline"],
        report["consensus"]["plain_english"],
    ]
    if mc_result:
        lines.append(report["monte_carlo"].get("plain_english", ""))
    if report["consensus"]["conflict_notes"]:
        lines.extend(["CONFLICTS:"] + report["consensus"]["conflict_notes"])
    return "\n".join(lines)
