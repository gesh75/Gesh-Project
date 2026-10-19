"""Tests for gesh_exit_advice.py — advisory exit-band table from the engine."""

from gesh_exit_advice import _holdings_from_engine, advice_for_engine, exit_advice

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
    assert table["MU"]["trailing_pct"] == 6.0  # 4 * 1.5
    assert table["KO"]["trailing_pct"] == 2.4  # 4 * 0.6


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


def test_holdings_from_engine_skips_blank_and_non_rows():
    """CLI/engine reader must not invent a symbol from a dict-shaped holdings blob."""
    assert _holdings_from_engine({"holdings": []}) == []
    assert _holdings_from_engine({}) == []
    assert _holdings_from_engine({"holdings": None}) == []
    engine = {
        "holdings": [
            {"symbol": "nvda"},
            {"ticker": "mu"},
            {"symbol": ""},
            {"symbol": None},
            "NVDA",
        ]
    }
    assert _holdings_from_engine(engine) == ["NVDA", "MU"]
    # A dict-shaped holdings blob is not a book — iterating it must not invent tickers.
    assert _holdings_from_engine({"holdings": {"NVDA": {"equity": 1}}}) == []


def test_advice_for_engine_keeps_explicit_zero_and_marks_only_real_tiers():
    payload = advice_for_engine(
        {
            "holdings": [
                {
                    "symbol": "nvda",
                    "quantity": 10,
                    "average_buy_price": 40,
                    "price": 100,
                    "shares_held_for_sells": 0,
                }
            ],
            "kelly": {
                "NVDA": {"optimal_pct": 5},
                "MU": {"volatility_tier": "HIGH"},
                "bad": ["not-a-row"],
            },
        }
    )
    notes = {note["lesson_id"]: note for note in payload["lesson_notes"]}
    assert "round_trip" in notes
    assert notes["round_trip"]["severity"] == "watch"
    assert "does not size a sale" in notes["round_trip"]["text"]
    assert payload["measured_tiers"] == ["MU"]
    band = {row["symbol"]: row for row in payload["bands"]}["NVDA"]
    assert band["tracked_by_kelly"] is True
    assert band["volatility_tier"] == "MEDIUM"
    assert set(band) == {
        "symbol",
        "volatility_tier",
        "take_profit_pct",
        "stop_loss_pct",
        "trailing_pct",
        "tracked_by_kelly",
    }
