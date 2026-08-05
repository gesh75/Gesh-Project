"""/api/chart must route through price_source (VPN-block regression, 2026-07-03).

The handler called raw yf.download — under the VPN finance-block Yahoo returns
nothing and the dashboard price chart rendered blank. price_source's unauth
Robinhood fallback keeps daily ranges alive with no session. Offline: the
fetch is monkeypatched; the handler function is called directly (no auth wall).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import price_source
import server


def _frame(n: int = 21) -> pd.DataFrame:
    idx = pd.date_range(end="2026-07-02", periods=n, freq="B")
    c = np.linspace(214.75, 194.83, n)
    return pd.DataFrame(
        {"Open": c, "High": c + 2, "Low": c - 2, "Close": c, "Volume": np.full(n, 1e6)}, index=idx
    )


def test_chart_served_via_price_source(monkeypatch):
    calls: dict = {}

    def fake_download(sym, **kw):
        calls["sym"], calls["kw"] = sym, kw
        return _frame()

    monkeypatch.setattr(price_source, "yf_download", fake_download)
    server._chart_cache.clear()

    out = server.price_chart("NVDA", period="1mo")
    assert calls["sym"] == "NVDA", "must fetch through price_source.yf_download"
    assert calls["kw"]["use_cache"] is False, "the endpoint owns its 30s freshness cache"
    assert out["symbol"] == "NVDA" and len(out["prices"]) == 21
    assert out["pct_change"] == pytest.approx(-9.27, abs=0.05)


def test_chart_404_when_both_sources_fail(monkeypatch):
    monkeypatch.setattr(price_source, "yf_download", lambda *a, **k: None)
    server._chart_cache.clear()
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as ei:
        server.price_chart("NVDA", period="3mo")
    assert ei.value.status_code == 404
