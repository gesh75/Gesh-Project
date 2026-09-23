"""Radar factor observations must not treat bool/inf as real numbers.

``_is_num`` used ``isinstance(v, (int, float))``, so ``True`` scored as 1.0 and
``inf`` entered the cross-sectional z-score (and can blow the SD to inf).
Extracted from source so this file does not import the yfinance stack.
"""

from __future__ import annotations

import ast
import math
import statistics
from pathlib import Path
from typing import Any

_SRC = (Path(__file__).resolve().parents[1] / "invest_radar.py").read_text()
_WANTED = {"_is_num", "winsorized_z"}


def _load():
    tree = ast.parse(_SRC)
    chunks: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in _WANTED:
            chunk = ast.get_source_segment(_SRC, node)
            assert chunk is not None
            chunks.append(chunk)
    ns: dict = {
        "math": math,
        "statistics": statistics,
        "Any": Any,
        "MIN_COVERAGE": 3,
    }
    exec("\n\n".join(chunks), ns)  # noqa: S102 — extracted production helpers only
    return ns


_NS = _load()
_is_num = _NS["_is_num"]
winsorized_z = _NS["winsorized_z"]


def test_extracted_helpers_are_present():
    assert set(_NS) >= _WANTED


def test_is_num_rejects_bool_nan_and_inf():
    assert _is_num(True) is False
    assert _is_num(False) is False
    assert _is_num(float("nan")) is False
    assert _is_num(float("inf")) is False
    assert _is_num(float("-inf")) is False
    assert _is_num(None) is False
    assert _is_num("1.0") is False


def test_is_num_keeps_real_zero_and_ints():
    assert _is_num(0) is True
    assert _is_num(0.0) is True
    assert _is_num(1) is True
    assert _is_num(-2.5) is True


def test_winsorized_z_bool_does_not_count_as_coverage():
    """Three True values used to look like three 1.0s and invent a flat factor."""
    assert winsorized_z([True, True, True]) == [0.0, 0.0, 0.0]


def test_winsorized_z_inf_is_neutral_not_unbounded():
    z = winsorized_z([1.0, 2.0, 3.0, float("inf")])
    assert z[3] == 0.0
    assert all(math.isfinite(v) for v in z)
