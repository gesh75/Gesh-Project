"""Plain-language Consensus vs Kelly lines for the customer advisory Home.

These strings are the product. They must never name Kelly / ECG / Monte Carlo,
never invent a number, and must prefer a conflict (like-the-name / don't-add)
over a raw BUY chip.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from advisory_copy import book_actions, plain_line

TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "dashboard.html"


def _branch(line: str) -> str:
    """Map product copy to a stable branch id. Wording may differ JS vs Python."""
    low = line.lower()
    if "no read" in low:
        return "empty"
    if "don't add" in low or "do not add" in low:
        return "conflict_trim"
    if "skip" in low:
        return "conflict_avoid"
    if "wait" in low:
        return "conflict_add_vs_sell"
    if "both lean add" in low:
        return "agree_add"
    if "both want less" in low:
        return "agree_trim"
    if "cautious" in low:
        return "hold_sell"
    if "keep" in low:
        return "hold_buy"
    if "about right" in low:
        return "hold"
    if "small" in low:
        return "add_only"
    if "large" in low:
        return "trim_only"
    if "would not" in low or "not hold" in low:
        return "avoid_only"
    if "leans buy" in low:
        return "buy_only"
    if "leans sell" in low:
        return "sell_only"
    if "no clear" in low:
        return "unclear"
    raise AssertionError(f"unclassified advisory line: {line!r}")


_MATRIX = [
    (None, None),
    ("BUY", "TRIM"),
    ("STRONG BUY", "AVOID"),
    ("SELL", "ADD"),
    ("BUY", "ADD"),
    ("STRONG SELL", "AVOID"),
    ("BUY", "HOLD"),
    ("SELL", "HOLD"),
    ("HOLD", "HOLD"),
    (None, "ADD"),
    (None, "TRIM"),
    (None, "AVOID"),
    ("BUY", None),
    ("SELL", None),
    ("HOLD", None),
]


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


def test_book_actions_ranks_conflict_then_trim_then_add_then_hold():
    """Ticket order is the product: a conflict must beat a trim, a trim an add."""
    rows = [
        {"symbol": "HOLD1", "consensus": "HOLD", "kelly_action": "HOLD", "delta_pp": 9.0},
        {"symbol": "ADD1", "consensus": "BUY", "kelly_action": "ADD", "delta_pp": 8.0},
        {"symbol": "TRIM1", "consensus": "HOLD", "kelly_action": "TRIM", "delta_pp": 1.0},
        {"symbol": "CONF1", "consensus": "BUY", "kelly_action": "AVOID", "delta_pp": 0.2},
    ]
    acts = book_actions(rows, limit=4)
    assert [a["symbol"] for a in acts] == ["CONF1", "TRIM1", "ADD1", "HOLD1"]
    assert [a["kind"] for a in acts] == ["conflict", "trim", "add", "hold"]


def test_book_actions_tiebreaks_same_kind_by_abs_delta():
    """Larger |delta_pp| surfaces first inside a kind — the more-off-size name."""
    rows = [
        {"symbol": "SMALL", "consensus": "BUY", "kelly_action": "ADD", "delta_pp": 1.2},
        {"symbol": "BIG", "consensus": "BUY", "kelly_action": "ADD", "delta_pp": -6.4},
    ]
    acts = book_actions(rows, limit=2)
    assert [a["symbol"] for a in acts] == ["BIG", "SMALL"]


def test_book_actions_skips_blank_symbol_and_survives_bad_delta():
    rows = [
        {"symbol": "   ", "consensus": "BUY", "kelly_action": "TRIM", "delta_pp": -9},
        {"symbol": None, "consensus": "BUY", "kelly_action": "TRIM", "delta_pp": -9},
        {"symbol": "OK", "consensus": "BUY", "kelly_action": "TRIM", "delta_pp": "n/a"},
        {"symbol": "ALSO", "consensus": "SELL", "kelly_action": "ADD", "delta_pp": {"x": 1}},
    ]
    acts = book_actions(rows, limit=3)
    assert {a["symbol"] for a in acts} == {"OK", "ALSO"}
    assert {a["kind"] for a in acts} == {"conflict"}


def test_book_actions_normalizes_case_and_whitespace():
    rows = [{"symbol": "lly", "consensus": " buy ", "kelly_action": " trim ", "delta_pp": -2}]
    acts = book_actions(rows, limit=1)
    assert acts[0]["symbol"] == "LLY"
    assert acts[0]["kind"] == "conflict"
    assert "don't add" in acts[0]["headline"].lower() or "do not add" in acts[0]["headline"].lower()


def test_book_actions_does_not_overflow_limit_with_flips():
    rows = [
        {"symbol": "A", "consensus": "HOLD", "kelly_action": "HOLD", "delta_pp": 0},
        {"symbol": "B", "consensus": "HOLD", "kelly_action": "HOLD", "delta_pp": 0},
        {"symbol": "C", "consensus": "HOLD", "kelly_action": "HOLD", "delta_pp": 0},
    ]
    flips = [{"symbol": "DE", "prior_verdict": "HOLD", "new_verdict": "BUY"}]
    acts = book_actions(rows, top_changes=flips, limit=3)
    assert len(acts) == 3
    assert all(a["kind"] != "flip" for a in acts)


def test_book_actions_skips_incomplete_and_duplicate_flips():
    rows = [{"symbol": "MSFT", "consensus": "HOLD", "kelly_action": "HOLD", "delta_pp": 0}]
    flips = [
        {"symbol": "", "prior_verdict": "HOLD", "new_verdict": "BUY"},
        {"symbol": "DE", "prior_verdict": "", "new_verdict": "BUY"},
        {"symbol": "DE", "prior_verdict": "HOLD", "new_verdict": ""},
        {"symbol": "de", "prior_verdict": "HOLD", "new_verdict": "BUY"},
        {"symbol": "DE", "prior_verdict": "HOLD", "new_verdict": "SELL"},
    ]
    acts = book_actions(rows, top_changes=flips, limit=3)
    flips_out = [a for a in acts if a["kind"] == "flip"]
    assert len(flips_out) == 1
    assert flips_out[0]["symbol"] == "DE"
    assert "HOLD" in flips_out[0]["headline"] and "BUY" in flips_out[0]["headline"]
    assert "SELL" not in flips_out[0]["headline"]


def test_plain_line_remaining_decision_tree():
    """Uncovered branches still must stay engine-name-free and not invent %."""
    cases = [
        ("BUY", "ADD", "add"),
        ("BUY", "HOLD", "right"),
        ("SELL", "HOLD", "cautious"),
        (None, "ADD", "small"),
        (None, "TRIM", "large"),
        (None, "AVOID", "size"),
        ("BUY", None, "buy"),
        ("SELL", None, "sell"),
        ("HOLD", None, "action"),
    ]
    for cons, kelly, token in cases:
        line = plain_line(consensus=cons, kelly_action=kelly, delta_pp=12.5)
        low = line.lower()
        assert token in low, (cons, kelly, line)
        assert "kelly" not in low
        assert "consensus" not in low
        assert "12.5" not in line
        assert "%" not in line


def test_plain_action_line_js_mirrors_python_branches():
    """Home blotter (Python) and book-table Action column (JS) must fire the
    same decision tree. Copy may be shorter in the table; the branch may not."""
    html = TEMPLATE.read_text()
    m = re.search(r"function plainActionLine\(consensus, kelly\)\{", html)
    assert m, "plainActionLine missing from dashboard.html"
    start = m.start()
    depth = 0
    end = None
    for i, ch in enumerate(html[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    assert end, "plainActionLine body unclosed"
    js_fn = html[start:end]
    assert "Kelly" not in js_fn
    node = shutil.which("node")
    if not node:
        # Structural fallback: condition order must stay TRIM+buy → AVOID+buy
        # → ADD+sell before the agree/hold catch-alls.
        conds = re.findall(r"if\(([^)]+)\)", js_fn)
        assert any("TRIM" in c and "buy" in c for c in conds[:3])
        assert any("AVOID" in c and "buy" in c for c in conds[:3])
        assert any("ADD" in c and "sell" in c for c in conds[:4])
        return
    script = (
        js_fn
        + "\nconst pairs="
        + json.dumps([[c, k] for c, k in _MATRIX])
        + ";\nprocess.stdout.write(JSON.stringify(pairs.map(([c,k])=>plainActionLine(c,k))));\n"
    )
    proc = subprocess.run(
        [node, "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    js_lines = json.loads(proc.stdout)
    assert len(js_lines) == len(_MATRIX)
    for (cons, kelly), js_line in zip(_MATRIX, js_lines, strict=True):
        py_line = plain_line(consensus=cons, kelly_action=kelly, delta_pp=-7.6)
        assert _branch(py_line) == _branch(js_line), (cons, kelly, py_line, js_line)
        assert "kelly" not in js_line.lower()
        assert "7.6" not in js_line and "%" not in js_line
