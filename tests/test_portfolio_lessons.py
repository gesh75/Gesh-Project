"""Advisory lesson notes. Synthetic books only. These tests do not touch a gate."""

from gesh_exit_advice import exit_advice, exit_advice_with_lessons
from trading.portfolio_lessons import lesson_notes

KELLY = {"AAA": {"volatility_tier": "MEDIUM"}}


def test_two_x_and_trail_are_notes_not_orders():
    notes = lesson_notes(
        positions=[
            {
                "ticker": "AAA",
                "qty": 10,
                "avg_cost": 100.0,
                "current_price": 200.0,
                "high_water_price": 250.0,
            }
        ],
        as_of="2026-10-18",
    )
    ids = {n.lesson_id for n in notes}
    assert ids == {"round_trip", "trail"}
    assert all(n.severity == "watch" for n in notes)
    assert all("does not" in n.text for n in notes)


def test_missing_high_water_and_missing_stop_field_stay_quiet():
    notes = lesson_notes(
        positions=[
            {
                "ticker": "AAA",
                "qty": 10,
                "avg_cost": 100.0,
                "current_price": 110.0,
            }
        ],
        as_of="2026-10-18",
    )
    assert notes == []


def test_explicit_zero_reserved_shares_is_a_protective_note():
    notes = lesson_notes(
        positions=[
            {
                "ticker": "BBB",
                "qty": 2,
                "avg_cost": 50.0,
                "current_price": 51.0,
                "shares_held_for_sells": 0,
                "entry_date": "2026-01-01",
            }
        ],
        as_of="2026-10-18",
        total_nav=10_000,
    )
    assert any(n.lesson_id == "protective_order" and n.symbol == "BBB" for n in notes)


def test_reentry_after_a_loss_above_the_exit():
    notes = lesson_notes(
        order={"ticker": "CCC", "order_usd": 500.0, "current_price": 12.0},
        closed_lots=[
            {
                "ticker": "CCC",
                "exit_date": "2026-04-01",
                "exit_price": 10.0,
                "realized_pnl": -25.0,
            }
        ],
        as_of="2026-04-20",
        settled_cash=1_000.0,
    )
    assert any(n.lesson_id == "reentry" and n.symbol == "CCC" for n in notes)


def test_winning_exit_and_old_loss_do_not_flag_reentry():
    lots = [
        {"ticker": "CCC", "exit_date": "2026-04-01", "exit_price": 10.0, "realized_pnl": 5.0},
        {"ticker": "CCC", "exit_date": "2026-01-01", "exit_price": 8.0, "realized_pnl": -5.0},
    ]
    notes = lesson_notes(
        order={"ticker": "CCC", "order_usd": 500.0, "current_price": 12.0},
        closed_lots=lots,
        as_of="2026-04-20",
        settled_cash=1_000.0,
    )
    assert not any(n.lesson_id == "reentry" for n in notes)


def test_settled_cash_short_and_optional_minimum():
    short = lesson_notes(
        order={"ticker": "DDD", "order_usd": 80.0, "current_price": 10.0},
        settled_cash=50.0,
        as_of="2026-10-18",
    )
    assert any(n.lesson_id == "settled_cash" for n in short)
    assert not any(n.lesson_id == "min_notional" for n in short)

    floored = lesson_notes(
        order={"ticker": "DDD", "order_usd": 80.0, "current_price": 10.0},
        settled_cash=500.0,
        min_notional=100.0,
        as_of="2026-10-18",
    )
    assert any(n.lesson_id == "min_notional" for n in floored)
    assert not any(n.lesson_id == "settled_cash" for n in floored)


def test_cluster_map_and_combined_cap_use_the_caller_map():
    notes = lesson_notes(
        positions=[
            {"ticker": "AAA", "market_value": 400.0},
            {"ticker": "BBB", "market_value": 400.0},
        ],
        total_nav=1_000.0,
        cluster_map={"LEFT": ["AAA"], "RIGHT": ["BBB"]},
        cluster_cap_pct={"LEFT": 25.0, "RIGHT": 50.0},
        combined_caps=[{"clusters": ["LEFT", "RIGHT"], "cap_pct": 50.0}],
        order={"ticker": "AAA", "order_usd": 50.0, "current_price": 10.0},
        settled_cash=1_000.0,
    )
    cluster = [n for n in notes if n.lesson_id == "cluster"]
    assert any(n.symbol == "AAA" and "LEFT" in n.text for n in cluster)
    assert any("LEFT + RIGHT" in n.text for n in cluster)
    assert not any("RIGHT is" in n.text and n.symbol == "BBB" for n in cluster)


def test_stub_short_hold_and_cash_band():
    notes = lesson_notes(
        positions=[
            {
                "ticker": "EEE",
                "qty": 1,
                "avg_cost": 10.0,
                "current_price": 10.0,
                "entry_date": "2026-10-16",
            }
        ],
        as_of="2026-10-18",
        total_nav=10_000.0,
        cash_usd=100.0,
        cash_band_pct=(5.0, 10.0),
    )
    ids = {n.lesson_id for n in notes}
    assert "stub" in ids
    assert "short_hold" in ids
    assert "cash_band" in ids
    assert all("93%" not in n.text for n in notes)


def test_exit_advice_bands_stay_the_same_when_notes_are_attached():
    bands = exit_advice(["AAA"], KELLY)
    wrapped = exit_advice_with_lessons(
        ["AAA"],
        KELLY,
        positions=[
            {
                "ticker": "AAA",
                "qty": 1,
                "avg_cost": 10.0,
                "current_price": 25.0,
                "high_water_price": 25.0,
            }
        ],
    )
    assert wrapped["bands"] == bands
    notes = wrapped["lesson_notes"]
    assert isinstance(notes, list)
    assert notes[0]["lesson_id"] == "round_trip"
    assert "lesson_notes" not in bands[0]
