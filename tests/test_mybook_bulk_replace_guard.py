"""Replace-import wipe guard — no gesh-core / server import required.

#308 moved parse before clear so a 413 line-cap cannot empty the book, but a
header-only or junk paste still called ``clear_positions``. Loaded from source
so this file stays runnable when the private core pin cannot install.
"""

from __future__ import annotations

import ast
import math
import re
from pathlib import Path

_SRC = (Path(__file__).resolve().parents[1] / "server.py").read_text()
_WANTED = {
    "_parse_pos_float",
    "_parse_nonneg_float",
    "_parse_bulk_book",
}


def _load():
    tree = ast.parse(_SRC)
    chunks: list[str] = []
    constants: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in _WANTED:
            chunk = ast.get_source_segment(_SRC, node)
            assert chunk is not None
            chunks.append(chunk)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in {
                    "_BULK_HEADER_WORDS",
                    "_BULK_MAX_LINES",
                    "_BULK_MAX_INVALID",
                    "_SYMBOL_RE",
                }:
                    seg = ast.get_source_segment(_SRC, node)
                    assert seg is not None
                    constants.append(seg)
    ns: dict = {
        "re": re,
        "math": math,
        "HTTPException": type("HTTPException", (Exception,), {"status_code": 400}),
    }
    exec("\n\n".join(constants + chunks), ns)  # noqa: S102 — extracted helpers only
    return ns


_NS = _load()
_parse_bulk_book = _NS["_parse_bulk_book"]


def _mybook_bulk_source() -> str:
    tree = ast.parse(_SRC)
    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "mybook_bulk":
            chunk = ast.get_source_segment(_SRC, node)
            assert chunk is not None
            return chunk
    raise AssertionError("mybook_bulk not found")


def test_header_and_junk_parse_to_zero_items():
    items, invalid = _parse_bulk_book("Symbol,Shares,Cost\n???\n@@@")
    assert items == []
    assert invalid


def test_valid_sized_line_still_parses():
    items, invalid = _parse_bulk_book("TSLA 5 200")
    assert invalid == []
    assert items == [{"symbol": "TSLA", "shares": 5.0, "avg_cost": 200.0}]


def test_non_finite_tokens_do_not_become_share_or_cost():
    """The source-extract loader must see ``math``. ``inf`` used to NameError
    inside ``_parse_pos_float`` and, before the finite guard, become a share count.
    A bad token drops that field; the symbol row itself stays."""
    items, invalid = _parse_bulk_book("TSLA inf 200\nNVDA 5 nan")
    assert invalid == []
    by_symbol = {item["symbol"]: item for item in items}
    assert by_symbol["TSLA"] == {"symbol": "TSLA", "shares": None, "avg_cost": 200.0}
    assert by_symbol["NVDA"] == {"symbol": "NVDA", "shares": 5.0, "avg_cost": None}


def test_mybook_bulk_clear_requires_parseable_items():
    """The 413 path is not enough: empty parse + replace used to wipe the book."""
    src = _mybook_bulk_source()
    assert "if replace and items:" in src
    assert "clear_positions" in src
    assert not re.search(r"if replace:\s*\n\s+auth_users\.clear_positions", src)


def test_extracted_helpers_are_present():
    assert set(_NS) >= _WANTED
