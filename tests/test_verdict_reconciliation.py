"""Deterministic stance reconciliation.

Audit BUG-03: "STRONG BUY" (11-node consensus) rendered beside "HOLD — Elevated
Price, overvalued −18.1%" (AI card) for AAPL, in one viewport, unreconciled.
"""

from __future__ import annotations

import pytest

from verdict_reconciliation import Inputs, Stance, reconcile

# ─── the exact audited case ──────────────────────────────────────────────────

def test_the_aapl_case_becomes_one_stance_with_the_conflict_stated():
    r = reconcile(Inputs(
        mechanical_verdict="STRONG BUY", mechanical_confidence=0.60, node_agreement=0.57,
        valuation_status="OVERVALUED", valuation_pct_diff=-18.1,
        ai_verdict="HOLD", regime="RANGING"))
    assert r.stance == Stance.WATCH
    assert r.agrees is False
    assert "above our fair-value" in r.reason
    # both readings survive, labelled — nothing is hidden to force agreement
    layers = {c["layer"]: c["reading"] for c in r.components}
    assert layers["Mechanical signal"] == "STRONG BUY"
    assert layers["Valuation view"] == "OVERVALUED"
    assert layers["AI thesis"] == "HOLD"


def test_conflict_halves_confidence_and_says_why():
    agree = reconcile(Inputs(mechanical_verdict="BUY", node_agreement=0.8,
                             valuation_status="UNDERVALUED"))
    clash = reconcile(Inputs(mechanical_verdict="BUY", node_agreement=0.8,
                             valuation_status="OVERVALUED"))
    assert agree.confidence == pytest.approx(0.8)
    assert clash.confidence == pytest.approx(0.4)
    assert "conflict" in clash.confidence_basis
    assert agree.confidence_basis and "node" in agree.confidence_basis


# ─── the required branch matrix ──────────────────────────────────────────────

def test_agreement_bullish():
    r = reconcile(Inputs(mechanical_verdict="BUY", ai_verdict="BUY",
                         valuation_status="UNDERVALUED", node_agreement=0.9))
    assert r.stance == Stance.ACCUMULATE and r.agrees is True


def test_agreement_bearish():
    r = reconcile(Inputs(mechanical_verdict="SELL", ai_verdict="TRIM",
                         valuation_status="OVERVALUED", node_agreement=0.9))
    assert r.stance == Stance.REDUCE and r.agrees is True


def test_technical_bullish_fundamental_bearish():
    r = reconcile(Inputs(mechanical_verdict="BUY", valuation_status="OVERVALUED",
                         node_agreement=0.9))
    assert r.stance == Stance.WATCH and r.agrees is False


def test_technical_bearish_fundamental_bullish():
    r = reconcile(Inputs(mechanical_verdict="SELL", valuation_status="UNDERVALUED",
                         node_agreement=0.9))
    assert r.stance == Stance.WATCH
    assert "falling price and a low price" in r.reason


def test_insufficient_data_is_no_stance_not_hold():
    r = reconcile(Inputs())
    assert r.stance == Stance.NO_STANCE
    assert r.confidence is None
    assert "not have enough" in r.reason


def test_risk_gate_override_only_tightens():
    open_gate = reconcile(Inputs(mechanical_verdict="BUY", valuation_status="UNDERVALUED",
                                 node_agreement=0.9, risk_gate="APPROVED"))
    shut_gate = reconcile(Inputs(mechanical_verdict="BUY", valuation_status="UNDERVALUED",
                                 node_agreement=0.9, risk_gate="REJECTED"))
    assert open_gate.stance == Stance.ACCUMULATE
    assert shut_gate.stance == Stance.WATCH
    assert "do not currently permit" in shut_gate.reason


def test_risk_gate_never_loosens_a_negative_stance():
    r = reconcile(Inputs(mechanical_verdict="SELL", valuation_status="OVERVALUED",
                         node_agreement=0.9, risk_gate="APPROVED"))
    assert r.stance == Stance.REDUCE


def test_low_agreement_split_vote_cannot_present_as_conviction():
    r = reconcile(Inputs(mechanical_verdict="STRONG BUY", valuation_status="UNDERVALUED",
                         node_agreement=0.34))
    assert r.stance == Stance.WATCH
    assert "evenly split" in r.reason


def test_neutral_everything_is_hold():
    r = reconcile(Inputs(mechanical_verdict="HOLD", valuation_status="FAIR",
                         node_agreement=0.7))
    assert r.stance == Stance.HOLD and r.agrees is True


# ─── input handling ──────────────────────────────────────────────────────────

def test_numeric_fair_value_gap_substitutes_for_a_missing_label():
    assert reconcile(Inputs(mechanical_verdict="BUY", valuation_pct_diff=-18.1,
                            node_agreement=0.8)).stance == Stance.WATCH
    assert reconcile(Inputs(mechanical_verdict="BUY", valuation_pct_diff=+22.0,
                            node_agreement=0.8)).stance == Stance.ACCUMULATE
    assert reconcile(Inputs(mechanical_verdict="BUY", valuation_pct_diff=+1.0,
                            node_agreement=0.8)).stance == Stance.ACCUMULATE


def test_unknown_verdict_string_is_unknown_not_neutral():
    """An unrecognized label must not be silently mapped to HOLD."""
    r = reconcile(Inputs(mechanical_verdict="???", node_agreement=0.8))
    assert r.stance == Stance.NO_STANCE


def test_verdicts_are_case_and_underscore_insensitive():
    for label in ("strong buy", "STRONG_BUY", " Strong Buy "):
        assert reconcile(Inputs(mechanical_verdict=label, valuation_status="OVERVALUED",
                                node_agreement=0.8)).stance == Stance.WATCH


def test_output_is_json_safe():
    d = reconcile(Inputs(mechanical_verdict="BUY", valuation_status="OVERVALUED",
                         node_agreement=0.8)).as_dict()
    import json
    json.loads(json.dumps(d))
    assert set(d) >= {"stance", "headline", "reason", "components_agree",
                      "components", "confidence", "confidence_basis"}


def test_reconcile_is_deterministic():
    inp = Inputs(mechanical_verdict="STRONG BUY", valuation_status="OVERVALUED",
                 valuation_pct_diff=-18.1, ai_verdict="HOLD", node_agreement=0.57)
    assert reconcile(inp).as_dict() == reconcile(inp).as_dict()
