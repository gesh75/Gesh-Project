"""First execution tests for the dashboard / Claude transparency node.

`explainability.py` turns consensus + Monte Carlo into the customer-facing
"Why?" panel and prompt injection. VaR risk bands and Kelly/Macro conflict
notes are the only place a HIGH/EXTREME label or a TRIM warning is produced
for that surface — a band-off-by-one or a dropped conflict reads as a clean
book.
"""

from __future__ import annotations

import pytest

import explainability as ex


def _consensus(**overrides):
    base = {
        "verdict": "BUY",
        "raw_score": 0.40,
        "active_nodes": 8,
        "confidence": 72.0,
        "votes": {"Momentum": 1, "Trend": 1, "Kelly": 0, "Macro": 0},
        "details": {"Momentum": "RSI=61"},
        "regime": "TRENDING",
    }
    base.update(overrides)
    return base


def test_explain_consensus_defaults_and_score_alias():
    out = ex.explain_consensus("nvda", {"score": 0.5, "nodes_voted": 3})
    assert out["verdict"] == "INSUFFICIENT DATA"
    assert out["raw_score"] == pytest.approx(0.5)
    assert out["active_nodes"] == 3
    assert "nvda —" in out["headline"]
    assert "Not Enough Data" in out["headline"]


def test_explain_consensus_mixed_signals_and_kelly_trim():
    out = ex.explain_consensus(
        "HOOD",
        _consensus(
            raw_score=0.30,
            votes={"Momentum": 1, "News": 1, "Kelly": -1, "ECG": -1},
        ),
    )
    notes = " ".join(out["conflict_notes"])
    assert "Mixed signals" in notes
    assert "Momentum" in notes and "Kelly" in notes
    assert "favours bulls" in notes
    assert "Kelly says TRIM" in notes
    assert out["plain_english"].startswith("For HOOD")


def test_explain_consensus_macro_headwind_only_when_net_bullish():
    bull = ex.explain_consensus(
        "NVDA",
        _consensus(raw_score=0.10, votes={"Momentum": 1, "Macro": -1}),
    )
    assert any("Macro headwinds" in n for n in bull["conflict_notes"])
    assert any("20%" in n for n in bull["conflict_notes"])

    flat = ex.explain_consensus(
        "NVDA",
        _consensus(raw_score=0.0, votes={"Momentum": 0, "Macro": -1}),
    )
    assert not any("Macro headwinds" in n for n in flat["conflict_notes"])


def test_explain_consensus_kelly_trim_requires_bullish_score():
    weak = ex.explain_consensus(
        "IONQ",
        _consensus(raw_score=0.25, votes={"Kelly": -1, "Momentum": 1}),
    )
    assert not any("Kelly says TRIM" in n for n in weak["conflict_notes"])


def test_explain_consensus_confidence_bar_clamps_to_ten():
    out = ex.explain_consensus("AAPL", _consensus(confidence=82.0))
    bar, _, pct = out["confidence_bar"].partition("  ")
    assert bar.count("█") == 8
    assert bar.count("░") == 2
    assert pct == "82%"


@pytest.mark.parametrize(
    "var_pct,risk",
    [
        (25.01, "EXTREME"),
        (25.0, "HIGH"),
        (15.01, "HIGH"),
        (15.0, "MODERATE"),
        (8.01, "MODERATE"),
        (8.0, "LOW"),
        (0.0, "LOW"),
    ],
)
def test_explain_monte_carlo_var_risk_bands(var_pct, risk):
    out = ex.explain_monte_carlo(
        {
            "current_equity": 100_000,
            "var_95": 1_000 * var_pct,
            "var_95_pct": var_pct,
            "median": 101_000,
            "p5": 80_000,
            "p95": 120_000,
            "horizon_days": 21,
        }
    )
    assert out["risk_level"] == risk
    assert out["risk_label"] == ex.RISK_LEVEL[risk]
    assert out["key_numbers"]["var_95_pct"] == var_pct
    assert "5% chance of losing more than" in out["plain_english"]


def test_explain_monte_carlo_error_is_unknown_not_low():
    out = ex.explain_monte_carlo({"error": "garch unavailable"})
    assert out["risk_level"] == "UNKNOWN"
    assert "garch unavailable" in out["plain_english"]
    assert "key_numbers" not in out


def test_build_transparency_report_and_claude_conflicts():
    consensus = _consensus(
        raw_score=0.40,
        votes={"Momentum": 1, "Kelly": -1},
    )
    mc = {"error": "not run"}
    report = ex.build_transparency_report(
        "NVDA",
        consensus,
        mc_result=mc,
        kelly={"action": "TRIM", "kelly_pct": 4.5},
    )
    assert report["kelly_plain"].startswith("Kelly sizing recommends TRIM")
    assert "4.5%" in report["kelly_plain"]
    assert "Monte Carlo unavailable" in report["full_narrative"]

    blob = ex.transparency_for_claude("NVDA", consensus, mc_result=mc)
    assert blob.startswith("[TRANSPARENCY — NVDA]")
    assert "CONFLICTS:" in blob
    assert "Kelly says TRIM" in blob
