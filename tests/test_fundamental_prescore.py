"""Deterministic fundamental pre-score — the Claude-unavailable fallback rank.

`_pre_score` / `_is_valid_opportunity` / `_build_fallback_opportunities` are
what populate the Intelligence panel when the LLM scan fails. A junk symbol
(NO_DATA) or a flipped insider/momentum penalty would rank the wrong names
into the customer UI. Loaded from source so this file does not import the
agent's LLM/yfinance stack.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_SRC = (Path(__file__).resolve().parents[1] / "fundamental_agent.py").read_text()
_WANTED = {
    "_pre_score",
    "_is_valid_opportunity",
    "_build_fallback_opportunities",
}


def _load():
    tree = ast.parse(_SRC)
    chunks: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in _WANTED:
            chunk = ast.get_source_segment(_SRC, node)
            assert chunk is not None
            chunks.append(chunk)
    ns: dict = {}
    exec("\n\n".join(chunks), ns)  # noqa: S102 — extracted production helpers only
    return ns


_NS = _load()
_pre_score = _NS["_pre_score"]
_is_valid_opportunity = _NS["_is_valid_opportunity"]
_build_fallback_opportunities = _NS["_build_fallback_opportunities"]


def test_extracted_helpers_are_present():
    assert set(_NS) >= _WANTED


def test_pre_score_insider_and_momentum_penalty():
    base = {
        "revenue_growth": 20,
        "earnings_growth": 10,
        "analyst_upside": 10,
        "forward_pe": 18,
        "insider_signal": "NEUTRAL",
        "ret_1m": 5,
    }
    # 20*0.4 + 10*0.3 + 10*0.5 + 15 PE = 8+3+5+15 = 31
    assert _pre_score(base) == pytest.approx(31.0)

    buying = {**base, "insider_signal": "BUYING"}
    selling = {**base, "insider_signal": "SELLING"}
    assert _pre_score(buying) == pytest.approx(51.0)
    assert _pre_score(selling) == pytest.approx(21.0)

    already_ran = {**base, "ret_1m": 30.01}
    assert _pre_score(already_ran) == pytest.approx(21.0)
    just_under = {**base, "ret_1m": 30.0}
    assert _pre_score(just_under) == pytest.approx(31.0)


def test_pre_score_ignores_non_positive_and_clamps_caps():
    empty = _pre_score({})
    assert empty == 0.0

    # 0 / negative / None contributions stay out; PE outside (0, 30) earns nothing
    junk = {
        "revenue_growth": 0,
        "earnings_growth": -5,
        "analyst_upside": None,
        "forward_pe": 30,
        "insider_signal": "HOLD",
        "ret_1m": None,
    }
    assert _pre_score(junk) == 0.0

    capped = {
        "revenue_growth": 100,  # min 60 * 0.4 = 24
        "earnings_growth": 80,  # min 40 * 0.3 = 12
        "analyst_upside": 50,  # min 30 * 0.5 = 15
        "forward_pe": 12,  # +15
    }
    assert _pre_score(capped) == pytest.approx(66.0)


@pytest.mark.parametrize(
    "row,ok",
    [
        ({"symbol": "NVDA"}, True),
        ({"symbol": " nvda "}, True),
        ({"symbol": "NO_DATA"}, False),
        ({"symbol": "N/A"}, False),
        ({"symbol": "none"}, False),
        ({"symbol": "UNKNOWN"}, False),
        ({"symbol": ""}, False),
        ({"symbol": None}, False),
        ({}, False),
    ],
)
def test_is_valid_opportunity_rejects_placeholder_tickers(row, ok):
    assert _is_valid_opportunity(row) is ok


def test_fallback_opportunities_rank_and_clamp_score():
    valid = [
        {"symbol": "SLOW", "name": "Slow Co", "revenue_growth": 1, "sector": "Tech"},
        {"symbol": "FAST", "name": "Fast Co", "revenue_growth": 40, "insider_signal": "BUYING"},
        {"symbol": "MEH", "revenue_growth": 10},
    ]
    opps = _build_fallback_opportunities(valid, n=2)
    assert [o["symbol"] for o in opps] == ["FAST", "MEH"]
    assert opps[0]["source"] == "pre_score_fallback"
    # 40*0.4 + BUYING 20 = 36 → MEDIUM (>=25, <50)
    assert opps[0]["momentum_gap"] == "MEDIUM"
    assert 0 <= opps[0]["fundamental_score"] <= 100
    assert "Claude deep scan unavailable" in opps[0]["risk"]
    assert opps[1]["company_name"] == "MEH"  # missing name falls back to symbol
