"""First-execution tests for regime classification and FRED macro honesty.

``_classify_regime`` labels every daily-chain symbol. Defaulting the Bollinger
average to the current width silently disables BREAKOUT-READY on short history
(the bug the helper's comment documents). ``get_macro_context`` is injected
into every deep-analysis prompt; an invented INVERTED curve or a fabricated
CPI print would resize risk and steer Claude.

Loaded from source so this file does not import yfinance / price_source.
No network: ``urlopen`` and ``_fetch_fred_series`` are stubbed.
"""

from __future__ import annotations

import ast
import math
import urllib
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pytest

_SRC = (Path(__file__).resolve().parents[1] / "regime.py").read_text()
_WANTED = {
    "_classify_regime",
    "_rate_direction",
    "_fetch_fred_series",
    "get_macro_context",
}
_CONSTS = {"FRED_BASE", "FRED_HEADERS"}


def _load():
    tree = ast.parse(_SRC)
    chunks: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in _WANTED:
            chunk = ast.get_source_segment(_SRC, node)
            assert chunk is not None
            chunks.append(chunk)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in _CONSTS:
                    chunk = ast.get_source_segment(_SRC, node)
                    assert chunk is not None
                    chunks.append(chunk)
    ns: dict[str, Any] = {
        "np": np,
        "urllib": urllib,
        "datetime": datetime,
        "timezone": timezone,
        "Any": Any,
    }
    exec("\n\n".join(chunks), ns)  # noqa: S102 — extracted production helpers only
    return ns


_NS = _load()
_classify_regime = _NS["_classify_regime"]
_rate_direction = _NS["_rate_direction"]
_fetch_fred_series = _NS["_fetch_fred_series"]
get_macro_context = _NS["get_macro_context"]


def test_extracted_helpers_are_present():
    assert set(_NS) >= _WANTED | _CONSTS


# ── _classify_regime ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "adx,bb_width,rsi,history,expected",
    [
        (35, 5.0, 72, [5.0] * 20, "MOMENTUM-HIGH"),
        (30, 5.0, 70, [5.0] * 20, "MOMENTUM-HIGH"),  # inclusive ADX + RSI
        (35, 5.0, 28, [5.0] * 20, "MOMENTUM-LOW"),
        (30, 5.0, 30, [5.0] * 20, "MOMENTUM-LOW"),
        (35, 5.0, 50, [5.0] * 20, "TRENDING"),
        (29, 5.0, 50, [5.0] * 20, "RANGING"),  # ADX just under 30, not compressed
        (20, 2.0, 50, [4.0] * 20, "BREAKOUT-READY"),  # width < avg*0.75 and < 4
        (18.1, 2.0, 50, [4.0] * 20, "BREAKOUT-READY"),  # adx > 18 inclusive-open
        (18, 2.0, 50, [4.0] * 20, "RANGING"),  # adx == 18 is not > 18
        (20, 3.0, 50, [4.0] * 20, "RANGING"),  # 3.0 is not < 3.0 (avg*0.75)
        (20, 4.0, 50, [8.0] * 20, "RANGING"),  # width == 4.0 is not < 4.0
    ],
)
def test_classify_regime_labels(adx, bb_width, rsi, history, expected):
    assert _classify_regime(adx, bb_width, rsi, history) == expected


def test_classify_empty_history_cannot_invent_breakout():
    """Empty history still defaults avg=current, so BREAKOUT stays off.

    That is the documented short-history trap: ``bb_width < avg*0.75`` is
    always False when avg is the current width.
    """
    assert _classify_regime(20, 2.0, 50, []) == "RANGING"


def test_classify_short_nonempty_history_can_still_flag_breakout():
    """A single prior wider bar must be enough — do not require 20 prints."""
    assert _classify_regime(20, 2.0, 50, [4.0]) == "BREAKOUT-READY"


# ── _rate_direction ───────────────────────────────────────────────────────────


def _vals(*numbers: float) -> list[tuple[str, float]]:
    return [(f"2026-01-{i:02d}", v) for i, v in enumerate(numbers, 1)]


def test_rate_direction_short_series_is_flat():
    assert _rate_direction(_vals(4.3, 4.4), window=5) == "FLAT"


def test_rate_direction_rising_falling_flat_edges():
    # window=5 compares last vs 6th-from-end (index -(window+1)).
    # Avoid constructing a literal 0.1 delta — binary float makes 1.1-1.0 > 0.1.
    assert _rate_direction(_vals(1, 1, 1, 1, 1, 1.11), window=5) == "RISING"
    assert _rate_direction(_vals(1, 1, 1, 1, 1, 0.89), window=5) == "FALLING"
    assert _rate_direction(_vals(1, 1, 1, 1, 1, 1.05), window=5) == "FLAT"
    assert _rate_direction(_vals(1, 1, 1, 1, 1, 0.95), window=5) == "FLAT"
    assert _rate_direction(_vals(1, 1, 1, 1, 1, 1.0), window=5) == "FLAT"


def test_rate_direction_drops_nan_before_comparing():
    series = _vals(1.0, 1.0, 1.0, 1.0, 1.0, 1.2)
    series[1] = ("2026-01-02", float("nan"))
    assert math.isnan(series[1][1])
    # After dropping the NaN there are only 5 finite points → FLAT
    assert _rate_direction(series, window=5) == "FLAT"


# ── _fetch_fred_series ────────────────────────────────────────────────────────


class _FakeResp:
    def __init__(self, body: str):
        self._body = body.encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_fetch_fred_skips_dot_missing_and_truncates(monkeypatch):
    csv = "DATE,DFF\n2026-01-01,.\n2026-01-02,4.33\n2026-01-03,4.40\n2026-01-04,not-a-float\n"
    monkeypatch.setattr(
        _NS["urllib"].request,
        "urlopen",
        lambda *a, **k: _FakeResp(csv),
    )
    rows = _fetch_fred_series("DFF", limit_rows=2)
    assert rows == [("2026-01-02", 4.33), ("2026-01-03", 4.40)]


def test_fetch_fred_exception_is_empty_not_zero(monkeypatch):
    def _boom(*_a, **_k):
        raise urllib.error.URLError("blocked")

    monkeypatch.setattr(_NS["urllib"].request, "urlopen", _boom)
    assert _fetch_fred_series("DFF") == []


def test_fetch_fred_malformed_csv_is_empty(monkeypatch):
    monkeypatch.setattr(
        _NS["urllib"].request,
        "urlopen",
        lambda *a, **k: _FakeResp("not,csv,enough\n"),
    )
    assert _fetch_fred_series("T10Y2Y") == []


# ── get_macro_context ─────────────────────────────────────────────────────────


def _cpi_months(*, year_ago: float, latest: float, n: int = 14) -> list[tuple[str, float]]:
    vals = [200.0] * n
    vals[-13] = year_ago
    vals[-1] = latest
    # last 3 vs prior 3 stay equal unless the caller overwrites
    return [(f"2025-{(i % 12) + 1:02d}-01", v) for i, v in enumerate(vals)]


@pytest.mark.parametrize(
    "spread,state",
    [
        (-0.26, "INVERTED"),
        (-0.25, "FLAT"),  # exclusive: must be < -0.25
        (0.09, "FLAT"),
        (0.10, "NORMAL"),
        (1.0, "NORMAL"),
        (1.01, "STEEP"),
    ],
)
def test_macro_yield_curve_state_bands(monkeypatch, spread, state):
    def _fetch(series_id, limit_rows=60):
        if series_id == "T10Y2Y":
            return [("2026-09-01", spread)]
        return []

    monkeypatch.setitem(_NS, "_fetch_fred_series", _fetch)
    ctx = get_macro_context()
    assert ctx["yield_curve_state"] == state
    assert ctx["yield_curve_spread"] == spread
    assert ctx["available"] is True
    assert ctx["fed_funds_rate"] is None
    assert ctx["cpi_yoy_pct"] is None


def test_macro_all_fetches_fail_stays_unavailable_no_invented_numbers(monkeypatch):
    monkeypatch.setitem(_NS, "_fetch_fred_series", lambda *a, **k: [])
    ctx = get_macro_context()
    assert ctx["available"] is False
    assert ctx["fed_funds_rate"] is None
    assert ctx["yield_curve_spread"] is None
    assert ctx["cpi_yoy_pct"] is None
    assert ctx["fed_funds_direction"] == "UNKNOWN"
    assert ctx["yield_curve_state"] == "UNKNOWN"
    assert ctx["inflation_trend"] == "UNKNOWN"
    assert "unavailable" in ctx["macro_summary"].lower()


def test_macro_partial_dff_only_is_available_without_inventing_curve(monkeypatch):
    def _fetch(series_id, limit_rows=60):
        if series_id == "DFF":
            return [("2026-09-01", 4.33)] * 15
        return []

    monkeypatch.setitem(_NS, "_fetch_fred_series", _fetch)
    ctx = get_macro_context()
    assert ctx["available"] is True
    assert ctx["fed_funds_rate"] == pytest.approx(4.33)
    assert ctx["yield_curve_state"] == "UNKNOWN"
    assert ctx["yield_curve_spread"] is None
    assert ctx["cpi_yoy_pct"] is None


def test_macro_cpi_yoy_and_short_history_do_not_invent(monkeypatch):
    def _fetch_ok(series_id, limit_rows=60):
        if series_id == "CPIAUCNS":
            return _cpi_months(year_ago=200.0, latest=206.0)
        return []

    monkeypatch.setitem(_NS, "_fetch_fred_series", _fetch_ok)
    ctx = get_macro_context()
    assert ctx["cpi_yoy_pct"] == pytest.approx(3.0)
    assert ctx["cpi_series"] == "CPIAUCNS"
    assert ctx["available"] is True

    def _fetch_short(series_id, limit_rows=60):
        if series_id == "CPIAUCNS":
            return [("2025-01-01", 200.0)] * 12
        return []

    monkeypatch.setitem(_NS, "_fetch_fred_series", _fetch_short)
    short = get_macro_context()
    assert short["cpi_yoy_pct"] is None
    assert short["inflation_trend"] == "UNKNOWN"
    assert short["available"] is False
