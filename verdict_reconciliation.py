"""Reconcile the engines instead of printing their disagreement as two answers.

Audit BUG-03 (2026-07-22): the Analyze page showed, for AAPL, in the same
viewport and off the same snapshot —

    11-node consensus …… STRONG BUY   (confidence 60%)
    AI research card ……… HOLD — Elevated Price, fair value $267,
                          "overvalued −18.1%", "avoid chasing above $330"

Neither was wrong. They answer different questions: the consensus is a
MECHANICAL/technical signal, the card is a FUNDAMENTAL/valuation opinion. Shown
side by side with equal weight and no relationship, they read as the product
contradicting itself — and a reader who acts on whichever they saw first is
acting on half the evidence.

This module makes the relationship explicit and computes ONE combined stance.
It is deliberately pure and rule-based: no LLM, no network, no I/O, so the same
inputs always produce the same stance and every branch is directly testable.

Stances, ordered from most to least permissive:

    ACCUMULATE  — signal and valuation agree constructively
    WATCH       — evidence conflicts, or valuation argues against a rising price
    HOLD        — no actionable edge either way
    REDUCE      — signal and valuation agree negatively
    NO_STANCE   — not enough evidence to say anything (never a silent "HOLD")

A restrictive risk gate can only ever make the stance MORE cautious.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Stance", "Inputs", "Reconciliation", "reconcile"]


class Stance:
    ACCUMULATE = "ACCUMULATE"
    WATCH = "WATCH"
    HOLD = "HOLD"
    REDUCE = "REDUCE"
    NO_STANCE = "NO_STANCE"


# Mechanical verdict → direction. Anything unrecognized is treated as unknown
# rather than mapped to neutral: "we don't know" and "we know it's neutral" are
# different claims and must not collapse.
_BULLISH = {"STRONG BUY", "STRONG_BUY", "BUY", "BULLISH", "ADD", "ACCUMULATE"}
_BEARISH = {"STRONG SELL", "STRONG_SELL", "SELL", "BEARISH", "TRIM", "REDUCE"}
_NEUTRAL = {"HOLD", "NEUTRAL", "WATCH"}

_STRONG = {"STRONG BUY", "STRONG_BUY", "STRONG SELL", "STRONG_SELL"}

# Fair-value status → valuation direction.
_UNDERVALUED = {"UNDERVALUED", "UNDER", "CHEAP"}
_OVERVALUED = {"OVERVALUED", "OVER", "EXPENSIVE", "ELEVATED"}
_FAIR = {"FAIR", "FAIRLY VALUED", "FAIR_VALUE", "IN LINE"}


def _norm(v: object) -> str:
    return str(v or "").strip().upper()


def _direction(verdict: object) -> str | None:
    v = _norm(verdict)
    if v in _BULLISH:
        return "bullish"
    if v in _BEARISH:
        return "bearish"
    if v in _NEUTRAL:
        return "neutral"
    return None


def _valuation_direction(status: object, pct_diff: object) -> str | None:
    s = _norm(status)
    if s in _UNDERVALUED:
        return "cheap"
    if s in _OVERVALUED:
        return "expensive"
    if s in _FAIR:
        return "fair"
    # Fall back to the numeric gap when the label is missing/unknown: pct_diff is
    # the canonical (estimate - current)/|current| * 100 (see fair_value.py), so
    # a NEGATIVE value means the estimate sits below the price = expensive.
    try:
        d = float(pct_diff)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if d <= -10:
        return "expensive"
    if d >= 10:
        return "cheap"
    return "fair"


@dataclass(frozen=True)
class Inputs:
    """Everything the stance is derived from. Any field may be absent."""
    mechanical_verdict: str | None = None      # consensus.py
    mechanical_confidence: float | None = None  # 0..1
    node_agreement: float | None = None        # 0..1, share of non-abstaining nodes agreeing
    valuation_status: str | None = None        # fair_value status
    valuation_pct_diff: float | None = None    # canonical (estimate-current)/|current|*100
    ai_verdict: str | None = None              # deep_analyze_ticker verdict
    risk_gate: str | None = None               # APPROVED | REJECTED | RESTRICTED | None
    regime: str | None = None                  # TRENDING | RANGING | VOLATILE | None


@dataclass(frozen=True)
class Reconciliation:
    stance: str
    headline: str
    reason: str
    agrees: bool
    components: tuple[dict, ...]
    confidence: float | None
    confidence_basis: str

    def as_dict(self) -> dict:
        return {
            "stance": self.stance,
            "headline": self.headline,
            "reason": self.reason,
            "components_agree": self.agrees,
            "components": list(self.components),
            "confidence": self.confidence,
            "confidence_basis": self.confidence_basis,
        }


_HEADLINES = {
    Stance.ACCUMULATE: "Constructive",
    Stance.WATCH: "Watch — do not chase",
    Stance.HOLD: "No actionable edge",
    Stance.REDUCE: "Reduce on strength",
    Stance.NO_STANCE: "Not enough evidence",
}


def _confidence(inp: Inputs, agrees: bool) -> tuple[float | None, str]:
    """Confidence is derived and its derivation is stated.

    The audit found a 60%-confidence STRONG BUY sitting beside an 80%-confidence
    HOLD — the number tracked neither the vote spread nor the stance, so it
    carried no information. Here it starts from node agreement (or the engine's
    own confidence) and is HALVED when the components disagree, because a stance
    built on conflicting evidence is by definition less certain.
    """
    base = inp.node_agreement
    basis = "share of non-abstaining consensus nodes agreeing"
    if base is None:
        base = inp.mechanical_confidence
        basis = "consensus engine confidence"
    if base is None:
        return None, "unavailable — no consensus inputs"
    base = max(0.0, min(1.0, float(base)))
    if not agrees:
        return round(base * 0.5, 3), basis + ", halved for signal/valuation conflict"
    return round(base, 3), basis


def reconcile(inp: Inputs) -> Reconciliation:
    """Combine the engines into one stance plus the evidence behind it."""
    mech = _direction(inp.mechanical_verdict)
    ai = _direction(inp.ai_verdict)
    val = _valuation_direction(inp.valuation_status, inp.valuation_pct_diff)
    gate = _norm(inp.risk_gate)

    components: list[dict] = []
    if inp.mechanical_verdict:
        components.append({
            "layer": "Mechanical signal",
            "reading": str(inp.mechanical_verdict),
            "direction": mech or "unknown",
            "means": "Momentum, trend and volume across the consensus nodes.",
        })
    if val:
        components.append({
            "layer": "Valuation view",
            "reading": str(inp.valuation_status or f"{inp.valuation_pct_diff:+.1f}% vs estimate"),
            "direction": val,
            "means": "Where the price sits against the modelled fair value.",
        })
    if inp.ai_verdict:
        components.append({
            "layer": "AI thesis",
            "reading": str(inp.ai_verdict),
            "direction": ai or "unknown",
            "means": "The research card's own conclusion.",
        })
    if gate:
        components.append({
            "layer": "Risk gate",
            "reading": gate,
            "direction": "restrictive" if gate in ("REJECTED", "RESTRICTED") else "permissive",
            "means": "Whether current conditions permit a new position at all.",
        })
    if inp.regime:
        components.append({
            "layer": "Market regime",
            "reading": str(inp.regime),
            "direction": "context",
            "means": "The behaviour the market is currently showing.",
        })

    # ── no evidence at all ───────────────────────────────────────────────────
    if mech is None and val is None and ai is None:
        return Reconciliation(
            Stance.NO_STANCE, _HEADLINES[Stance.NO_STANCE],
            "We do not have enough signal or valuation data for this symbol to take "
            "a view. Nothing here should be read as a recommendation either way.",
            True, tuple(components), None, "unavailable — no inputs")

    # Do the layers point the same way? The AI thesis is treated as part of the
    # valuation side: it is the qualitative expression of the same question.
    positives = [d for d in (mech, ai) if d == "bullish"]
    negatives = [d for d in (mech, ai) if d == "bearish"]
    val_positive = val == "cheap"
    val_negative = val == "expensive"

    conflict = bool(
        (positives and (val_negative or "bearish" in (mech, ai)))
        or (negatives and (val_positive or "bullish" in (mech, ai)))
    )
    agrees = not conflict

    if conflict:
        stance = Stance.WATCH
        if positives and val_negative:
            reason = ("Momentum is positive, but the price already sits above our fair-value "
                      "estimate — the case for buying strength here is weak.")
        elif negatives and val_positive:
            reason = ("The technical signal is negative while the valuation looks cheap — "
                      "a falling price and a low price are not the same thing.")
        else:
            reason = ("The mechanical signal and the research thesis point in opposite "
                      "directions, so neither carries enough weight to act on alone.")
    elif (mech == "bullish" or ai == "bullish") and not val_negative:
        stance = Stance.ACCUMULATE
        reason = ("Signal and valuation point the same way: the trend is constructive and "
                  "the price is not stretched against our estimate.")
    elif (mech == "bearish" or ai == "bearish") and not val_positive:
        stance = Stance.REDUCE
        reason = ("Signal and valuation agree negatively: the trend is weak and the price "
                  "is not cheap enough to compensate.")
    else:
        stance = Stance.HOLD
        reason = ("Nothing in the signal or the valuation argues strongly in either "
                  "direction right now.")

    # ── the risk gate can only tighten ───────────────────────────────────────
    if gate in ("REJECTED", "RESTRICTED"):
        if stance == Stance.ACCUMULATE:
            stance = Stance.WATCH
            reason += (" Risk conditions do not currently permit adding, so this is a "
                       "watch rather than an entry.")
        elif stance == Stance.HOLD:
            reason += " Risk conditions do not currently permit a new position."

    # A single-node or very low-agreement reading should not present as conviction.
    if inp.node_agreement is not None and inp.node_agreement < 0.5 and stance in (
            Stance.ACCUMULATE, Stance.REDUCE):
        stance = Stance.WATCH
        reason = ("The consensus nodes are close to evenly split, so the signal is not "
                  "strong enough to act on. " + reason)

    conf, basis = _confidence(inp, agrees)
    return Reconciliation(stance, _HEADLINES[stance], reason, agrees,
                          tuple(components), conf, basis)
