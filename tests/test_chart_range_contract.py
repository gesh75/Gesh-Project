"""/api/chart must honour the requested window.

Audit F-02 (2026-07-22): `/api/chart/{sym}?range=1D|5D|1M|3M|6M|1Y|max` all
returned the identical payload — `period:"1mo"`, 21 daily points,
2026-06-23 → 2026-07-22.

Two traced causes, both silent:
  1. The handler had no `range` parameter at all. FastAPI drops unknown query
     params, so `range=1D` was discarded and the `period="1mo"` default served.
  2. `period_map.get(period, "1mo")` fell back to a month for ANY unrecognized
     value — including the correct-looking uppercase `1D`/`1Y` the UI labels use.

A chart that answers a different question than the one asked is a correctness
bug, so unsupported windows are now a typed 400 rather than a quiet month-long
substitution.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from starlette.testclient import TestClient

import price_source
import server


def _frame(n: int, freq: str = "B") -> pd.DataFrame:
    idx = pd.date_range(end="2026-07-22", periods=n, freq=freq)
    c = np.linspace(100.0, 120.0, n)
    return pd.DataFrame({"Open": c, "High": c + 1, "Low": c - 1,
                         "Close": c, "Volume": np.full(n, 1e6)}, index=idx)


# Bars a provider would return for each window — distinct on purpose, so an
# ignored parameter cannot accidentally look correct.
_BARS = {"1d": 78, "5d": 390, "1mo": 21, "3mo": 63, "6mo": 126,
         "1y": 252, "ytd": 140, "5y": 260, "max": 520}


@pytest.fixture
def chart(monkeypatch):
    seen: list[dict] = []

    def fake_download(sym, **kw):
        seen.append({"sym": sym, **kw})
        return _frame(_BARS.get(kw.get("period"), 21))

    def fake_intraday(sym, span):
        seen.append({"sym": sym, "period": span, "interval": "5m"})
        return _frame(_BARS.get(span, 78), freq="5min")

    monkeypatch.setattr(price_source, "yf_download", fake_download)
    monkeypatch.setattr(price_source, "intraday_5m", fake_intraday)
    monkeypatch.setattr(price_source, "frame_source", lambda f: "yahoo", raising=False)
    server._chart_cache.clear()
    return seen


@pytest.fixture
def client(chart, monkeypatch):
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: False)
    return TestClient(server.app, raise_server_exceptions=False)


_DISTINCT = ["1d", "5d", "1mo", "3mo", "6mo", "1y", "max"]


def test_each_range_returns_a_different_window(client):
    seen: dict[str, tuple] = {}
    for rng in _DISTINCT:
        server._chart_cache.clear()
        r = client.get(f"/api/chart/NVDA?range={rng}")
        assert r.status_code == 200, f"{rng}: {r.status_code} {r.text[:200]}"
        b = r.json()
        seen[rng] = (b["point_count"], b["first_timestamp"], b["resolved_interval"])
    # The defect was that every entry here was identical.
    assert len(set(seen.values())) == len(_DISTINCT), f"windows collapsed: {seen}"


@pytest.mark.parametrize("label,expect_period", [
    ("1D", "1d"), ("5D", "5d"), ("1M", "1mo"), ("3M", "3mo"),
    ("6M", "6mo"), ("1Y", "1y"), ("MAX", "max"), ("1mo", "1mo"),
])
def test_ui_labels_resolve_case_insensitively(client, label, expect_period):
    """The audit used the UI's own button labels; they must not silently
    degrade to a month."""
    server._chart_cache.clear()
    b = client.get(f"/api/chart/NVDA?range={label}").json()
    assert b["resolved_period"] == expect_period
    assert b["requested_range"] == label


def test_intraday_ranges_use_intraday_bars(client):
    for rng in ("1d", "5d"):
        server._chart_cache.clear()
        b = client.get(f"/api/chart/NVDA?range={rng}").json()
        assert b["resolved_interval"] == "5m", f"{rng} served daily bars"


def test_long_ranges_use_daily_or_weekly_bars(client):
    server._chart_cache.clear()
    assert client.get("/api/chart/NVDA?range=1y").json()["resolved_interval"] == "1d"
    server._chart_cache.clear()
    assert client.get("/api/chart/NVDA?range=max").json()["resolved_interval"] == "1wk"


def test_unsupported_range_is_a_typed_400_not_a_silent_month(client):
    r = client.get("/api/chart/NVDA?range=42y")
    assert r.status_code == 400
    assert "Unsupported range" in r.text
    assert "Supported:" in r.text


def test_period_parameter_still_works_for_existing_callers(client):
    """The dashboard sends ?period= — that contract must not break."""
    server._chart_cache.clear()
    b = client.get("/api/chart/NVDA?period=3mo").json()
    assert b["resolved_period"] == "3mo" and b["period"] == "3mo"


def test_range_wins_over_period_when_both_sent(client):
    server._chart_cache.clear()
    b = client.get("/api/chart/NVDA?period=1mo&range=1y").json()
    assert b["resolved_period"] == "1y"


def test_response_carries_verifiable_metadata(client):
    server._chart_cache.clear()
    b = client.get("/api/chart/NVDA?range=6mo").json()
    for key in ("requested_range", "resolved_period", "resolved_interval",
                "point_count", "first_timestamp", "last_timestamp",
                "source", "data_as_of"):
        assert key in b, f"missing contract field: {key}"
    assert b["point_count"] == len(b["prices"]) == len(b["dates"])


def test_cache_does_not_serve_one_range_for_another(client):
    """The cache key must include the window — a hot 1mo entry answering a 1y
    request would reproduce the original defect from memory."""
    server._chart_cache.clear()
    a = client.get("/api/chart/NVDA?range=1mo").json()
    b = client.get("/api/chart/NVDA?range=1y").json()
    assert a["point_count"] != b["point_count"]
    assert a["resolved_period"] == "1mo" and b["resolved_period"] == "1y"
