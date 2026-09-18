"""``_consensus_args`` money inputs — first-execution edges.

``run_consensus_holding`` sizes against equity / total_portfolio / avg_cost /
price. A missing or $0 book must not divide-by-zero (falls through to 1.0);
a held name must not inherit the watchlist quote; a blank symbol is ignored.

Extracted from ``trading/autopilot.py`` so this file stays importable without
gesh-core (the sandbox venv cannot clone the private pin).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SRC = (REPO / "trading" / "autopilot.py").read_text()


def _load_consensus_args():
    tree = ast.parse(SRC)
    fn = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_consensus_args"
    )
    ns: dict = {"Any": object}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "autopilot.py", "exec"), ns)
    return ns["_consensus_args"]


_consensus_args = _load_consensus_args()


def test_held_symbol_uses_holding_not_watchlist_quote():
    snap = {
        "equity_summary": {"portfolio_equity": 80_000.0},
        "holdings": [
            {
                "symbol": "HOOD",
                "equity": 4_000.0,
                "average_buy_price": 20.0,
                "current_price": 25.0,
            }
        ],
        "watchlist": [{"symbol": "HOOD", "last_trade_price": 99.0}],
    }
    assert _consensus_args("HOOD", snap) == {
        "equity": 4_000.0,
        "total_portfolio": 80_000.0,
        "avg_cost": 20.0,
        "current_price": 25.0,
    }


def test_unheld_symbol_uses_watchlist_price_and_zero_cost():
    snap = {
        "equity_summary": {"portfolio_equity": 80_000.0},
        "holdings": [],
        "watchlist": [{"symbol": "SPY", "last_trade_price": 500.0}],
    }
    assert _consensus_args("SPY", snap) == {
        "equity": 0.0,
        "total_portfolio": 80_000.0,
        "avg_cost": 0.0,
        "current_price": 500.0,
    }


@pytest.mark.parametrize(
    "equity_summary",
    [
        {},
        {"portfolio_equity": 0},
        {"portfolio_equity": 0.0},
        {"portfolio_equity": None},
        {"portfolio_equity": ""},
    ],
)
def test_zero_or_missing_book_falls_through_to_one(equity_summary):
    """``or 1.0`` is the divide-by-zero floor — never invent a $100k book."""
    snap = {"equity_summary": equity_summary, "holdings": [], "watchlist": []}
    assert _consensus_args("HOOD", snap)["total_portfolio"] == 1.0


def test_missing_holding_fields_are_zero_not_none():
    snap = {
        "equity_summary": {"portfolio_equity": 10_000.0},
        "holdings": [{"symbol": "MU"}],
        "watchlist": [],
    }
    args = _consensus_args("MU", snap)
    assert args["equity"] == 0.0
    assert args["avg_cost"] == 0.0
    assert args["current_price"] == 0.0
    assert args["total_portfolio"] == 10_000.0


def test_unknown_symbol_is_unheld_zeros():
    snap = {
        "equity_summary": {"portfolio_equity": 10_000.0},
        "holdings": [{"symbol": "HOOD", "equity": 1, "average_buy_price": 1, "current_price": 1}],
        "watchlist": [],
    }
    assert _consensus_args("NVDA", snap) == {
        "equity": 0.0,
        "total_portfolio": 10_000.0,
        "avg_cost": 0.0,
        "current_price": 0.0,
    }
