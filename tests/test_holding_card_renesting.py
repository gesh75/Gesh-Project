"""The wire->stored contract for holding cards (2026-08-07 schema slimming).

The tool schema went FLAT to fit under Anthropic's compiled-grammar ceiling, but
seven reader surfaces — gen_report2, dashboard.html, owner_console.html,
server.py, verdict_reconciliation, bull_bear_debate and the persisted analysis
history — all read the NESTED shape. `renest_holding_card` is the seam that keeps
that contract, so these tests pin it rather than the readers.

The other half of the change: the model no longer supplies `fair_value.current`,
`pct_diff` or `status`. `current` echoed a price we already hold, `pct_diff` was
already 100% recomputed by fair_value.canonicalize_analysis, and `status` was
model prose that could contradict its own numbers. All three are now derived.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def aa(monkeypatch_session=None):
    """Import ai_analyzer with a dummy key (module-level load_dotenv/client setup)."""
    import os

    os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")
    import ai_analyzer

    return ai_analyzer


def _flat_card(**over: Any) -> dict[str, Any]:
    card = {
        "symbol": "NVDA",
        "company_type": "Mega-cap semis",
        "scores": {"valuation": 60, "growth": 90, "health": 85, "momentum": 70, "dividend": 5},
        "snowflake_summary": "Strong growth, rich valuation.",
        "fair_value_estimate": 265.0,
        "risks": [{"text": "Customer concentration", "severity": "MEDIUM"}],
        "rewards": ["Data-centre demand", "Margin expansion"],
        "prediction_3m": "Range-bound",
        "prediction_12m": "Higher on AI capex",
        "prediction_rationale": "Backlog visibility",
        "verdict": "BUY",
        "analyst_note": "Watch RSI above 72.",
        "bear_thesis": "Capex digestion could stall revenue.",
        "bear_invalidators": ["Gross margin below 70%", "Hyperscaler capex guide cut"],
    }
    card.update(over)
    return card


# ── Shape restoration ─────────────────────────────────────────────────────────


def test_flat_card_is_restored_to_the_nested_reader_shape(aa) -> None:
    out = aa.renest_holding_card(_flat_card(), current_price=222.09)

    # prediction: readers do h.prediction['3m'] / ['12m']
    assert out["prediction"] == {
        "3m": "Range-bound",
        "12m": "Higher on AI capex",
        "rationale": "Backlog visibility",
    }
    # bear_case: readers do h.bear_case.thesis / .invalidators
    assert out["bear_case"]["thesis"] == "Capex digestion could stall revenue."
    assert len(out["bear_case"]["invalidators"]) == 2
    # rewards: renderers do x.text
    assert out["rewards"] == [{"text": "Data-centre demand"}, {"text": "Margin expansion"}]
    # risks keep severity (drives the UI dot colour) — must NOT be flattened
    assert out["risks"][0]["severity"] == "MEDIUM"

    # The flat wire keys must not leak into stored output.
    for leaked in (
        "fair_value_estimate",
        "prediction_3m",
        "prediction_12m",
        "prediction_rationale",
        "bear_thesis",
        "bear_invalidators",
    ):
        assert leaked not in out, f"wire key {leaked} leaked into the stored card"


def test_fair_value_is_derived_from_the_live_price_not_the_model(aa) -> None:
    out = aa.renest_holding_card(_flat_card(), current_price=222.09)
    fv = out["fair_value"]
    assert fv["estimate"] == 265.0
    assert fv["current"] == 222.09  # live price, never model-supplied
    assert fv["pct_diff"] == pytest.approx(19.32, abs=0.01)
    assert "Under" in fv["status"]  # gen_report2 colours on this substring


def test_fair_value_status_cannot_contradict_its_own_pct_diff(aa) -> None:
    """The old failure mode: model prose saying 'Undervalued' on a negative diff."""
    over = aa.renest_holding_card(_flat_card(fair_value_estimate=180.0), current_price=222.09)
    assert over["fair_value"]["pct_diff"] < 0
    assert "Over" in over["fair_value"]["status"]

    fair = aa.renest_holding_card(_flat_card(fair_value_estimate=223.0), current_price=222.09)
    assert "Over" not in fair["fair_value"]["status"]
    assert "Under" not in fair["fair_value"]["status"]


# ── Robustness: this sits on the analysis write path ──────────────────────────


def test_renesting_is_idempotent(aa) -> None:
    """Replayed history and cached results are already nested — leave them alone."""
    once = aa.renest_holding_card(_flat_card(), current_price=222.09)
    twice = aa.renest_holding_card(once, current_price=222.09)
    assert twice == once


def test_missing_price_degrades_without_raising(aa) -> None:
    out = aa.renest_holding_card(_flat_card(), current_price=None)
    assert out["fair_value"]["current"] == 0.0
    # canonical_pct_diff returns a neutral 0.0 when current == 0 rather than inf.
    assert out["fair_value"]["pct_diff"] == 0.0


@pytest.mark.parametrize(
    "bad",
    [
        {"fair_value_estimate": "not-a-number"},
        {"rewards": "not-a-list"},
        {"bear_invalidators": "not-a-list"},
        {"prediction_3m": None},
    ],
)
def test_malformed_payloads_never_raise(aa, bad: dict[str, Any]) -> None:
    out = aa.renest_holding_card(_flat_card(**bad), current_price=100.0)
    assert isinstance(out, dict)
    assert isinstance(out["bear_case"]["invalidators"], list)


def test_non_dict_input_passes_through(aa) -> None:
    assert aa.renest_holding_card("nonsense") == "nonsense"
    assert aa.renest_analysis(None) is None
    assert aa.renest_analysis({"holdings_analysis": "bad"}) == {"holdings_analysis": "bad"}


def test_renest_analysis_matches_prices_by_symbol_case_insensitively(aa) -> None:
    result = {
        "holdings_analysis": [
            _flat_card(symbol="nvda", fair_value_estimate=265.0),
            _flat_card(symbol="MU", fair_value_estimate=1100.0),
        ]
    }
    out = aa.renest_analysis(result, {"NVDA": 222.09, "mu": 872.27})
    assert out["holdings_analysis"][0]["fair_value"]["current"] == 222.09
    assert out["holdings_analysis"][1]["fair_value"]["current"] == 872.27
    # input not mutated
    assert "fair_value" not in result["holdings_analysis"][0]


# ── The schema and the re-nester must not drift apart ─────────────────────────


def test_every_flat_wire_field_is_consumed_by_the_renester(aa) -> None:
    """A field added to the wire schema but not re-nested would silently vanish
    from the report — the exact class of failure this seam exists to prevent."""
    ns: dict[str, Any] = {}
    for node in ast.parse((_REPO_ROOT / "ai_analyzer.py").read_text()).body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            try:
                exec(compile(ast.Module(body=[node], type_ignores=[]), "<s>", "exec"), ns)
            except Exception:  # noqa: BLE001
                pass
    wire_fields = set(ns["_HOLDING_ANALYSIS_ITEM"]["properties"])

    card = {k: _flat_card().get(k, "x") for k in wire_fields}
    card["scores"] = _flat_card()["scores"]
    card["risks"] = _flat_card()["risks"]
    card["rewards"] = _flat_card()["rewards"]
    card["bear_invalidators"] = ["a"]
    card["fair_value_estimate"] = 100.0
    out = aa.renest_holding_card(card, current_price=90.0)

    # Nothing prefixed for flattening may survive; everything else must.
    flattened = {
        "fair_value_estimate",
        "prediction_3m",
        "prediction_12m",
        "prediction_rationale",
        "bear_thesis",
        "bear_invalidators",
    }
    for field in wire_fields - flattened:
        assert field in out, f"wire field {field} was dropped by renest_holding_card"
    for target in ("fair_value", "prediction", "bear_case"):
        assert target in out
