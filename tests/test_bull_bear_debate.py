"""First-execution tests for the bull/bear debate engine.

The debate injects consensus math into three LLM calls, then validates the
structured cases before a BUY/SELL decision can be stored. These pin:

  * prompt honesty — missing/bool/NaN consensus score is n/a, not 0.00 / 1.00
  * None raw_score no longer TypeErrors on ``:.2f`` and aborts the debate
  * run_debate wires validators; a bool confidence cannot invent a BUY
  * empty fundamentals still produce a context block (never crash)

LLM transport is mocked. No network, no gateway spend.
"""

from __future__ import annotations

import sys
from types import ModuleType

import pytest

from llm_contracts import LLMContractError

# bull_bear_debate imports dotenv + llm_gateway at module load. Stub only when
# those extras are missing so CI (full tree) still uses the real modules.
try:
    import dotenv as _dotenv_mod  # noqa: F401
except ImportError:
    _dotenv = ModuleType("dotenv")
    _dotenv.load_dotenv = lambda *a, **k: None
    sys.modules["dotenv"] = _dotenv
try:
    import llm_gateway as _gw_mod  # noqa: F401
except ImportError:
    _gw = ModuleType("llm_gateway")
    _gw.resolve_model = lambda *a, **k: "claude-haiku-4-5"
    _gw.get_gateway = lambda: None
    sys.modules["llm_gateway"] = _gw

import bull_bear_debate as bbd


def _valid_case(*, confidence: int = 70, price_target: float = 250.0) -> dict:
    return {
        "thesis": "AI demand",
        "arguments": [{"point": "backlog", "evidence": "guide"}],
        "price_target": price_target,
        "key_risk": "regulation",
        "confidence": confidence,
    }


def _valid_decision() -> dict:
    return {
        "action": "BUY",
        "conviction": 65,
        "price_target": 220.0,
        "upside_pct": 12.0,
        "bull_weight": 55,
        "rationale": "balanced",
        "catalysts": ["earnings"],
        "stop_loss_pct": 8.0,
    }


# ── prompt honesty ────────────────────────────────────────────────────────────


def test_fmt_score_known_number_is_two_decimals():
    assert bbd._fmt_score(0.55) == "0.55"
    assert bbd._fmt_score(0) == "0.00"
    assert bbd._fmt_score(-0.49) == "-0.49"


@pytest.mark.parametrize("bad", [None, "", "x", True, False, float("nan"), float("inf"), []])
def test_fmt_score_unknown_is_na_not_invented(bad):
    assert bbd._fmt_score(bad) == "n/a"


@pytest.mark.parametrize("bad", [None, True, False, float("nan"), float("inf"), "8"])
def test_fmt_nodes_unknown_is_na_not_invented(bad):
    assert bbd._fmt_nodes(bad) == "n/a"


def test_fmt_nodes_known_int_is_plain():
    assert bbd._fmt_nodes(8) == "8"
    assert bbd._fmt_nodes(8.0) == "8"


def test_context_prompt_includes_symbol_and_known_score():
    prompt = bbd._context_prompt(
        "NVDA",
        {"raw_score": 0.55, "verdict": "BUY", "active_nodes": 8, "details": {"MOMENTUM": 1}},
        {"current_price": 100.0, "pe": 28.0},
    )
    assert "Symbol: NVDA" in prompt
    assert "Consensus score: 0.55 (BUY)" in prompt
    assert "Active nodes: 8/11" in prompt
    assert "Current price:   100.0" in prompt
    assert "MOMENTUM" in prompt


def test_context_prompt_missing_score_is_na_not_zero():
    prompt = bbd._context_prompt("AAPL", {}, {})
    assert "Consensus score: n/a (N/A)" in prompt
    assert "Active nodes: n/a/11" in prompt
    assert "0.00" not in prompt


def test_context_prompt_none_raw_score_does_not_raise():
    # Pre-fix: consensus.get('raw_score', 0) returned None and ``:.2f`` TypeError'd.
    prompt = bbd._context_prompt("MSFT", {"raw_score": None, "active_nodes": None}, {})
    assert "Consensus score: n/a" in prompt
    assert "Active nodes: n/a/11" in prompt


def test_context_prompt_bool_score_is_not_one():
    prompt = bbd._context_prompt(
        "TSLA", {"raw_score": True, "verdict": "BUY", "active_nodes": True}, {}
    )
    assert "1.00" not in prompt
    assert "Consensus score: n/a (BUY)" in prompt
    assert "Active nodes: n/a/11" in prompt


def test_context_prompt_nan_score_is_na():
    prompt = bbd._context_prompt("KO", {"raw_score": float("nan"), "active_nodes": 3}, {})
    assert "Consensus score: n/a" in prompt
    assert "Active nodes: 3/11" in prompt


# ── run_debate wiring ─────────────────────────────────────────────────────────


def test_run_debate_returns_validated_cases(monkeypatch):
    calls: list[str] = []

    def fake_tool(**kwargs):
        calls.append(kwargs["tool_name"])
        if kwargs["tool_name"] == "pm_decision":
            return _valid_decision()
        return _valid_case()

    monkeypatch.setattr(bbd, "_tool_call", fake_tool)
    out = bbd.run_debate(
        "NVDA",
        {"raw_score": 0.55, "verdict": "BUY", "active_nodes": 8, "details": {}},
        {"current_price": 180.0},
    )
    assert calls == ["bull_case", "bear_case", "pm_decision"]
    assert out["symbol"] == "NVDA"
    assert out["bull"]["confidence"] == 70
    assert out["bear"]["confidence"] == 70
    assert out["decision"]["action"] == "BUY"
    assert out["decision"]["price_target"] == 220.0
    assert "NVDA: BUY" in out["debate_summary"]
    assert "PM target $220.0" in out["debate_summary"]


def test_run_debate_empty_fundamentals_do_not_crash(monkeypatch):
    monkeypatch.setattr(
        bbd,
        "_tool_call",
        lambda **k: _valid_decision() if k["tool_name"] == "pm_decision" else _valid_case(),
    )
    out = bbd.run_debate("AAPL", {}, {})
    assert out["symbol"] == "AAPL"
    assert out["decision"]["action"] == "BUY"


def test_run_debate_bool_confidence_cannot_invent_a_buy(monkeypatch):
    monkeypatch.setattr(
        bbd,
        "_tool_call",
        lambda **k: {
            "thesis": "x",
            "arguments": [],
            "price_target": 10.0,
            "key_risk": "y",
            "confidence": True,
        },
    )
    with pytest.raises(LLMContractError, match="confidence"):
        bbd.run_debate("NVDA", {"raw_score": 0.9, "verdict": "STRONG BUY"}, {})


def test_run_debate_inf_price_target_is_rejected(monkeypatch):
    monkeypatch.setattr(
        bbd,
        "_tool_call",
        lambda **k: _valid_case(price_target=float("inf")),
    )
    with pytest.raises(LLMContractError, match="price_target"):
        bbd.run_debate("NVDA", {}, {})
