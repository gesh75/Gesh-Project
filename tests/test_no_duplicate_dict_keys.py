"""M-10: a duplicate literal key silently overwrites the first value.

benchmarks.SECTOR_BENCHMARKS mapped AMZN to XLK and later to XLY; Python kept the
last one with no error, so the first entry was dead and the sector alpha was
benchmarked against whichever line happened to come last. Scan the data-mapping
modules for repeated constant keys so it cannot recur unnoticed.
"""

from __future__ import annotations

import ast
from pathlib import Path

import benchmarks

ROOT = Path(__file__).resolve().parent.parent
# Modules whose module-level dicts are lookup tables that drive money/benchmark logic.
SCANNED = ("benchmarks.py", "sector_map.py", "regime.py")


def _duplicate_keys(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    out: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        seen: dict[object, int] = {}
        for key in node.keys:
            if isinstance(key, ast.Constant):
                if key.value in seen:
                    out.append(f"{path.name}:{key.lineno}: duplicate key {key.value!r} (first at line {seen[key.value]})")
                else:
                    seen[key.value] = key.lineno
    return out


def test_scanner_detects_a_duplicate(tmp_path):
    f = tmp_path / "x.py"
    f.write_text('D = {"A": 1, "B": 2, "A": 3}\n')
    assert _duplicate_keys(f) and "'A'" in _duplicate_keys(f)[0]


def test_lookup_tables_have_no_duplicate_keys():
    offenders: list[str] = []
    for name in SCANNED:
        path = ROOT / name
        if path.exists():
            offenders += _duplicate_keys(path)
    assert not offenders, "\n".join(offenders)


def test_amzn_is_benchmarked_to_consumer_discretionary():
    # Amazon is GICS Consumer Discretionary; XLY is the sector ETF, XLK is not.
    assert benchmarks.get_sector_benchmark("AMZN") == "XLY"
    assert benchmarks.SECTOR_BENCHMARKS["AMZN"] == "XLY"
