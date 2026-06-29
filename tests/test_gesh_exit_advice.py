"""Tests for gesh_exit_advice.py — advisory exit-band table from the engine."""
from gesh_exit_advice import exit_advice

KELLY = {
    "NVDA": {"volatility_tier": "MEDIUM"},
    "MU": {"volatility_tier": "HIGH"},
    "KO": {"volatility_tier": "LOW"},
}


def test_per_holding_bands_match_tier():
    table = {r["symbol"]: r for r in exit_advice(["NVDA", "MU", "KO"], KELLY)}
    assert table["NVDA"]["take_profit_pct"] == 8.0
    assert table["MU"]["take_profit_pct"] == 12.0
    assert table["KO"]["take_profit_pct"] == 4.8
    assert table["MU"]["trailing_pct"] == 6.0   # 4 * 1.5
    assert table["KO"]["trailing_pct"] == 2.4   # 4 * 0.6


def test_untracked_holding_falls_back_to_medium():
    table = {r["symbol"]: r for r in exit_advice(["WFC"], KELLY)}
    assert table["WFC"]["tracked_by_kelly"] is False
    assert table["WFC"]["volatility_tier"] == "MEDIUM"
    assert table["WFC"]["take_profit_pct"] == 8.0


def test_every_holding_covered():
    holdings = ["NVDA", "MU", "KO", "WFC", "GOOGL"]
    table = exit_advice(holdings, KELLY)
    assert len(table) == len(holdings)
    assert {r["symbol"] for r in table} == set(holdings)


def test_lowercase_kelly_key_still_tracked_with_real_bands():
    # A lower-case engine key must not show tracked=True while falling back to MEDIUM.
    kelly = {"mu": {"volatility_tier": "HIGH"}}
    row = exit_advice(["MU"], kelly)[0]
    assert row["tracked_by_kelly"] is True
    assert row["volatility_tier"] == "HIGH"
    assert row["take_profit_pct"] == 12.0


def test_symbols_uppercased():
    table = exit_advice(["nvda"], KELLY)
    assert table[0]["symbol"] == "NVDA"
    assert table[0]["tracked_by_kelly"] is True
