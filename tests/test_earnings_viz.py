"""Earnings history (earnings_viz.get_earnings) — Finnhub primary, yfinance fallback.

Both sources are mocked; no network. Locks the beat/miss + surprise math and the
honest 'unavailable' degrade.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

import earnings_viz as ev


class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._p = payload
    def json(self):
        return self._p


_FINNHUB = [   # Finnhub returns newest-first
    {"period": "2026-03-31", "actual": 1.20, "estimate": 1.00},
    {"period": "2025-12-31", "actual": 0.90, "estimate": 1.00},
    {"period": "2025-09-30", "actual": 1.10, "estimate": 1.05},
]


def test_finnhub_beats_and_surprise(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "k")
    monkeypatch.setattr(ev.requests, "get", lambda *a, **k: _Resp(200, _FINNHUB))
    out = ev.get_earnings("AAPL")
    assert out["available"] is True
    # oldest→newest for the chart
    assert out["quarters"][0]["period"] == "2025-09-30"
    assert out["quarters"][-1]["period"] == "2026-03-31"
    # 1.20 vs 1.00 = +20% beat; 0.90 vs 1.00 = -10% miss
    assert out["quarters"][-1]["surprise_pct"] == 20.0
    assert out["beat_count"] == 2 and out["scored_count"] == 3


def test_no_key_no_yf_is_unavailable(monkeypatch):
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    monkeypatch.setattr(ev, "_from_yfinance", lambda s: None)
    out = ev.get_earnings("AAPL")
    assert out["available"] is False
    # Customer-facing copy must never name an internal environment variable
    # (audit BUG-05: the UI told customers to "set FINNHUB_API_KEY").
    assert "FINNHUB_API_KEY" not in out["reason"]
    assert "API_KEY" not in out["reason"] and "$" not in out["reason"]
    # …the operational detail is still available, out of band.
    assert "FINNHUB_API_KEY" in out["diagnostic"]


def test_finnhub_http_error_falls_back_to_yf(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "k")
    monkeypatch.setattr(ev.requests, "get", lambda *a, **k: _Resp(429, {}))
    monkeypatch.setattr(ev, "_from_yfinance",
                        lambda s: [{"period": "2026-03-31", "eps_actual": 2.0,
                                    "eps_estimate": 1.8, "surprise_pct": 11.1}])
    out = ev.get_earnings("AAPL")
    assert out["available"] is True and out["quarters"][0]["eps_actual"] == 2.0


def test_empty_symbol():
    assert ev.get_earnings("")["available"] is False


# ── endpoint ──────────────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch):
    import server
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: False)
    return server, TestClient(server.app, raise_server_exceptions=False)


def test_endpoint(client, monkeypatch):
    server, c = client
    monkeypatch.setattr("earnings_viz.get_earnings",
                        lambda sym: {"available": True, "symbol": sym,
                                     "quarters": [{"period": "2026-03-31", "eps_actual": 1.2,
                                                   "eps_estimate": 1.0, "surprise_pct": 20.0}],
                                     "beat_count": 1, "scored_count": 1, "avg_surprise_pct": 20.0,
                                     "note": "x"}, raising=False)
    r = c.get("/api/earnings/NVDA")
    assert r.status_code == 200
    assert r.json()["data"]["beat_count"] == 1
