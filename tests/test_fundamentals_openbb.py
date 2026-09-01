"""OpenBB fundamentals wrapper: typed blanks, not invented numbers.

Consensus Nodes 4/5 call `get_fundamentals_openbb` and fall back to
yfinance `.info` on None. `_safe_float` must reject NaN (NaN is truthy
enough to survive a `or` chain if we ever stored it) and unparseable
strings. Analyst consensus is optional — a consensus failure must still
return the metrics already fetched.
"""

from __future__ import annotations

from types import SimpleNamespace

import fundamentals_openbb as fo


def test_safe_float_none_and_unparseable_are_none():
    assert fo._safe_float(None) is None
    assert fo._safe_float("n/a") is None
    assert fo._safe_float("") is None
    assert fo._safe_float(object()) is None


def test_safe_float_parses_numeric_strings():
    assert fo._safe_float("1.5") == 1.5
    assert fo._safe_float(0) == 0.0
    assert fo._safe_float(-3) == -3.0


def test_safe_float_nan_is_none():
    assert fo._safe_float(float("nan")) is None


def test_safe_int_truncates_and_rejects_nan():
    assert fo._safe_int(3.9) == 3
    assert fo._safe_int("4") == 4
    assert fo._safe_int(None) is None
    assert fo._safe_int(float("nan")) is None


def test_unavailable_openbb_returns_none(monkeypatch):
    monkeypatch.setattr(fo, "_OPENBB_AVAILABLE", False)
    monkeypatch.setattr(fo, "obb", None)
    assert fo.get_fundamentals_openbb("AAPL") is None


class _Row(dict):
    pass


class _DF:
    def __init__(self, row=None, empty=False):
        self.empty = empty
        self._row = _Row(row or {})

    @property
    def iloc(self):
        return [_Row(), self._row] if False else _ILoc(self._row)


class _ILoc:
    def __init__(self, row):
        self._row = row

    def __getitem__(self, idx):
        if idx != 0:
            raise IndexError(idx)
        return self._row


def _metrics_ok(row: dict):
    return SimpleNamespace(to_dataframe=lambda: _DF(row, empty=False))


def test_empty_metrics_frame_returns_none(monkeypatch):
    monkeypatch.setattr(fo, "_OPENBB_AVAILABLE", True)
    monkeypatch.setattr(
        fo,
        "obb",
        SimpleNamespace(
            equity=SimpleNamespace(
                fundamental=SimpleNamespace(
                    metrics=lambda *_a, **_k: SimpleNamespace(to_dataframe=lambda: _DF(empty=True))
                ),
                estimates=SimpleNamespace(
                    consensus=lambda *_a, **_k: (_ for _ in ()).throw(
                        AssertionError("consensus must not run after empty metrics")
                    )
                ),
            )
        ),
    )
    assert fo.get_fundamentals_openbb("ZZZ") is None


def test_metrics_ok_consensus_fail_still_returns_fundamentals(monkeypatch):
    monkeypatch.setattr(fo, "_OPENBB_AVAILABLE", True)
    monkeypatch.setattr(fo, "time", SimpleNamespace(sleep=lambda *_a, **_k: None))
    monkeypatch.setattr(
        fo,
        "obb",
        SimpleNamespace(
            equity=SimpleNamespace(
                fundamental=SimpleNamespace(
                    metrics=lambda *_a, **_k: _metrics_ok(
                        {
                            "revenue_growth": "0.12",
                            "forward_pe": "18.5",
                            "profit_margin": "0.22",
                            "earnings_growth": "0.08",
                            "current_price": "150.0",
                        }
                    )
                ),
                estimates=SimpleNamespace(
                    consensus=lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("crumb"))
                ),
            )
        ),
    )
    data = fo.get_fundamentals_openbb("MSFT")
    assert data is not None
    assert data["source"] == "openbb"
    assert data["rev_growth"] == 0.12
    assert data["pe"] == 18.5
    assert data["profit_margin"] == 0.22
    assert data["eps_growth"] == 0.08
    assert data["current_price"] == 150.0
    assert "rec_key" not in data
    assert "num_analysts" not in data


def test_forward_pe_preferred_then_pe_ratio(monkeypatch):
    monkeypatch.setattr(fo, "_OPENBB_AVAILABLE", True)
    monkeypatch.setattr(fo, "time", SimpleNamespace(sleep=lambda *_a, **_k: None))

    def _run(row):
        monkeypatch.setattr(
            fo,
            "obb",
            SimpleNamespace(
                equity=SimpleNamespace(
                    fundamental=SimpleNamespace(metrics=lambda *_a, **_k: _metrics_ok(row)),
                    estimates=SimpleNamespace(
                        consensus=lambda *_a, **_k: SimpleNamespace(
                            to_dataframe=lambda: _DF(empty=True)
                        )
                    ),
                )
            ),
        )
        return fo.get_fundamentals_openbb("X")

    both = _run({"forward_pe": "20", "pe_ratio": "40"})
    assert both["pe"] == 20.0
    fallback = _run({"forward_pe": "nan", "pe_ratio": "14.2"})
    assert fallback["pe"] == 14.2


def test_metrics_exception_returns_none_not_partial(monkeypatch):
    monkeypatch.setattr(fo, "_OPENBB_AVAILABLE", True)
    monkeypatch.setattr(
        fo,
        "obb",
        SimpleNamespace(
            equity=SimpleNamespace(
                fundamental=SimpleNamespace(
                    metrics=lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("openbb down"))
                ),
            )
        ),
    )
    assert fo.get_fundamentals_openbb("NVDA") is None
