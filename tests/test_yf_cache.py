"""Phase 2.5: price_source.yf_download in-process TTL cache (Flash #5).

Cuts redundant Yahoo hits when multiple engines request the same symbol+window
in a single run. Period-based frames cache; ranged (start/end) queries bypass.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import price_source as ps


@pytest.fixture(autouse=True)
def _clear_cache():
    ps._YF_CACHE.clear()
    yield
    ps._YF_CACHE.clear()


def test_period_download_is_cached(monkeypatch):
    calls = {"n": 0}

    def fake(symbols, **k):
        calls["n"] += 1
        return pd.DataFrame({"Close": np.arange(10.0)})

    monkeypatch.setattr(ps, "_yf_download_uncached", fake)
    ps.yf_download("XYZ", period="90d")
    ps.yf_download("XYZ", period="90d")
    assert calls["n"] == 1   # second served from cache


def test_ranged_query_bypasses_cache(monkeypatch):
    calls = {"n": 0}

    def fake(symbols, **k):
        calls["n"] += 1
        return pd.DataFrame({"Close": np.arange(10.0)})

    monkeypatch.setattr(ps, "_yf_download_uncached", fake)
    ps.yf_download("XYZ", start="2026-01-01", end="2026-02-01")
    ps.yf_download("XYZ", start="2026-01-01", end="2026-02-01")
    assert calls["n"] == 2   # ranged queries are never cached


def test_cached_frame_is_isolated_from_mutation(monkeypatch):
    monkeypatch.setattr(ps, "_yf_download_uncached",
                        lambda symbols, **k: pd.DataFrame({"Close": np.arange(10.0)}))
    a = ps.yf_download("XYZ", period="90d")
    a.loc[0, "Close"] = 999.0                 # caller mutates its copy
    b = ps.yf_download("XYZ", period="90d")
    assert b.loc[0, "Close"] == 0.0           # cache untouched


def test_ttl_expiry_refetches(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(ps, "_yf_download_uncached",
                        lambda symbols, **k: (calls.__setitem__("n", calls["n"] + 1)
                                              or pd.DataFrame({"Close": np.arange(10.0)})))
    monkeypatch.setattr(ps, "_YF_CACHE_TTL_S", 0.0)   # everything immediately stale
    ps.yf_download("XYZ", period="90d")
    ps.yf_download("XYZ", period="90d")
    assert calls["n"] == 2
