"""Bulk-import share/cost tokens must not become inf or NaN.

``_parse_pos_float`` / ``_parse_nonneg_float`` were added with the #307/#308
mybook cost-preservation work. ``float("inf")`` and ``float("1e999")`` succeed
and compare as positive, so an unguarded parser would hand ``set_position`` an
infinite share count or cost basis. Loaded from source so this file does not
import the server stack.
"""

from __future__ import annotations

import ast
import math
from pathlib import Path

import pytest

_SRC = (Path(__file__).resolve().parents[1] / "server.py").read_text()
_WANTED = {"_parse_pos_float", "_parse_nonneg_float"}


def _load():
    tree = ast.parse(_SRC)
    chunks: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in _WANTED:
            chunk = ast.get_source_segment(_SRC, node)
            assert chunk is not None
            chunks.append(chunk)
    ns: dict = {"math": math}
    exec("\n\n".join(chunks), ns)  # noqa: S102 — extracted production helpers only
    return ns


_NS = _load()
_parse_pos_float = _NS["_parse_pos_float"]
_parse_nonneg_float = _NS["_parse_nonneg_float"]


def test_extracted_helpers_are_present():
    assert set(_NS) >= _WANTED


@pytest.mark.parametrize(
    "tok",
    ["inf", "+inf", "-inf", "nan", "NaN", "1e999", "Infinity"],
)
def test_parse_pos_float_rejects_non_finite(tok):
    assert _parse_pos_float(tok) is None


@pytest.mark.parametrize(
    "tok",
    ["inf", "+inf", "-inf", "nan", "NaN", "1e999", "Infinity"],
)
def test_parse_nonneg_float_rejects_non_finite(tok):
    assert _parse_nonneg_float(tok) is None


def test_parse_pos_float_keeps_real_sizes():
    assert _parse_pos_float("10") == 10.0
    assert _parse_pos_float("$1,250.50") == 1250.50
    assert _parse_pos_float("0") is None
    assert _parse_pos_float("-3") is None
    assert _parse_pos_float("x") is None
    assert _parse_pos_float(None) is None


def test_parse_nonneg_float_keeps_zero_cost():
    """Explicit 0.0 still resets basis; only non-finite / negative are refused."""
    assert _parse_nonneg_float("0") == 0.0
    assert _parse_nonneg_float("0.0") == 0.0
    assert _parse_nonneg_float("$800.00") == 800.0
    assert _parse_nonneg_float("-1") is None
    assert _parse_nonneg_float("") is None
