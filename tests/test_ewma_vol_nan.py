"""NaN-safety for volatility_models.ewma_vol.

``nan or 0.018`` returns nan (NaN is truthy). That leak reaches Monte Carlo
and any GARCH fallback that delegates to EWMA. The production helper must
return a finite positive floor, never NaN.

Loaded from source so this file does not import price_source / gesh-core.
"""

from __future__ import annotations

import ast
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

_SRC = (Path(__file__).resolve().parents[1] / "volatility_models.py").read_text()


def _load():
    tree = ast.parse(_SRC)
    chunk = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "ewma_vol":
            chunk = ast.get_source_segment(_SRC, node)
            break
    assert chunk is not None
    ns: dict[str, Any] = {"np": np, "pd": pd}
    exec(chunk, ns)  # noqa: S102 — extracted production helper only
    return ns["ewma_vol"]


ewma_vol = _load()


def test_ewma_vol_short_sample_is_floor():
    assert ewma_vol(pd.Series([0.01, 0.02])) == pytest.approx(0.018)
    assert ewma_vol(pd.Series(dtype=float)) == pytest.approx(0.018)


def test_ewma_vol_flat_series_is_finite_floor_not_nan():
    v = ewma_vol(pd.Series([0.0] * 60))
    assert math.isfinite(v) and v > 0
    assert v == pytest.approx(0.018)


def test_ewma_vol_all_nan_is_floor_not_nan():
    v = ewma_vol(pd.Series([float("nan")] * 20))
    assert v == pytest.approx(0.018)
    assert not math.isnan(v)


def test_ewma_vol_normal_series_is_positive_finite():
    rng = np.random.default_rng(0)
    v = ewma_vol(pd.Series(rng.normal(0.0, 0.02, 120)))
    assert math.isfinite(v) and v > 0
    assert v != pytest.approx(0.018)


def test_ewma_vol_never_returns_nan():
    cases = [
        pd.Series([0.01, 0.02]),
        pd.Series([0.0] * 60),
        pd.Series([float("nan")] * 20),
        pd.Series([0.01, float("nan"), -0.02, 0.0, 0.03, 0.01] * 10),
    ]
    for series in cases:
        v = ewma_vol(series)
        assert isinstance(v, float)
        assert not math.isnan(v)
