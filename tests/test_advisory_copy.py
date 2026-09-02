"""Plain-language Consensus vs Kelly lines for the customer advisory Home.

These strings are the product. They must never name Kelly / ECG / Monte Carlo,
never invent a number, and must prefer a conflict (like-the-name / don't-add)
over a raw BUY chip.
"""

from __future__ import annotations

from advisory_copy import book_actions, plain_line


def test_plain_line_conflict_buy_and_trim():
    line = plain_line(consensus="BUY", kelly_action="TRIM", delta_pp=-7.6)
    assert "Kelly" not in line and "consensus" not in line.lower()
    assert "don't add" in line.lower() or "do not add" in line.lower()
    assert "large" in line.lower() or "overweight" in line.lower()


def test_plain_line_conflict_strong_buy_and_avoid():
    line = plain_line(consensus="STRONG BUY", kelly_action="AVOID")
    assert "Kelly" not in line
    assert "skip" in line.lower() or "don't add" in line.lower() or "do not add" in line.lower()


def test_plain_line_both_want_less():
    line = plain_line(consensus="STRONG SELL", kelly_action="AVOID")
    assert "less" in line.lower()


def test_plain_line_add_when_signal_sells():
    line = plain_line(consensus="SELL", kelly_action="ADD")
    assert "wait" in line.lower()


def test_plain_line_hold_is_size_ok():
    line = plain_line(consensus="HOLD", kelly_action="HOLD")
    assert "about right" in line.lower() or "size" in line.lower()


def test_plain_line_empty_is_honest():
    assert plain_line(consensus=None, kelly_action=None) == "No read yet."


def test_plain_line_does_not_invent_percentages():
    line = plain_line(consensus="BUY", kelly_action="TRIM", delta_pp=-7.6)
    assert "7.6" not in line
    assert "%" not in line


def test_book_actions_ranks_conflict_first():
    rows = [
        {"symbol": "MSFT", "consensus": "BUY", "kelly_action": "ADD", "delta_pp": 7.9},
        {"symbol": "LLY", "consensus": "BUY", "kelly_action": "TRIM", "delta_pp": -7.6},
        {"symbol": "UNH", "consensus": "HOLD", "kelly_action": "HOLD", "delta_pp": -1.9},
    ]
    acts = book_actions(rows, limit=3)
    assert acts[0]["symbol"] == "LLY"
    assert acts[0]["kind"] == "conflict"
    assert "LLY" in acts[0]["headline"]
    assert "Kelly" not in acts[0]["headline"]


def test_book_actions_caps_at_limit_and_skips_empty_rows():
    rows = [
        {"symbol": "A", "consensus": None, "kelly_action": None},
        {"symbol": "B", "consensus": "HOLD", "kelly_action": "HOLD", "delta_pp": 0},
        {"symbol": "C", "consensus": "SELL", "kelly_action": "TRIM", "delta_pp": -4},
        {"symbol": "D", "consensus": "BUY", "kelly_action": "ADD", "delta_pp": 5},
    ]
    acts = book_actions(rows, limit=2)
    assert len(acts) == 2
    assert {a["symbol"] for a in acts} == {"C", "D"}


def test_book_actions_includes_confirmed_flip_when_room():
    rows = [{"symbol": "DE", "consensus": "BUY", "kelly_action": "HOLD", "delta_pp": -1.6}]
    flips = [
        {
            "symbol": "DE",
            "prior_verdict": "HOLD",
            "new_verdict": "BUY",
            "date": "2026-09-01",
        }
    ]
    acts = book_actions(rows, top_changes=flips, limit=3)
    kinds = [a["kind"] for a in acts]
    assert "flip" in kinds
    flip = next(a for a in acts if a["kind"] == "flip")
    assert "HOLD" in flip["headline"] and "BUY" in flip["headline"]
