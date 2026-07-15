"""Unified degraded-decision block (Codex #6).

decision_quality.build() emits ONE structured shape for a degraded per-ticker
decision, so the dashboard, logs, and audit history stop reading three different
key names for the same condition. These lock the shape, the attach/no-op behavior,
the structured log, and the /api/levels wiring (clean + suspect paths).
"""
from __future__ import annotations

import logging

import pytest
from starlette.testclient import TestClient

import decision_quality as dq

# ── build() shape ─────────────────────────────────────────────────────────────

def test_clean_decision_is_ok_and_not_degraded():
    b = dq.build(data_sources=["price_source"])
    assert b["price_quality"] == "ok"
    assert b["decision_degraded"] is False
    assert b["reasons"] == []
    assert b["data_sources"] == ["price_source"]


def test_suspect_price_marks_degraded_with_reason():
    b = dq.build(price_suspect=True, reason="52-wk range cross-check failed",
                 data_sources=["price_source", "rh_fundamentals_52w"])
    assert b["price_quality"] == "suspect"
    assert b["decision_degraded"] is True
    assert b["reasons"] == ["52-wk range cross-check failed"]


def test_degraded_flag_without_suspect_still_degrades():
    # e.g. no grounding quote at all — degraded even though the price isn't "suspect".
    b = dq.build(degraded=True)
    assert b["price_quality"] == "ok"        # price wasn't judged suspect …
    assert b["decision_degraded"] is True    # … but the decision is still degraded


def test_data_sources_defaults_to_empty_list_not_none():
    assert dq.build()["data_sources"] == []


# ── attach() + logging ────────────────────────────────────────────────────────

def test_attach_sets_block_and_returns_target():
    target = {"symbol": "X"}
    out = dq.attach(target, dq.build(), context="t")
    assert out is target
    assert target["decision_quality"]["price_quality"] == "ok"


def test_attach_is_noop_on_non_dict():
    assert dq.attach(None, dq.build(), context="t") is None       # must not raise


def test_degraded_decision_emits_one_structured_log(caplog):
    with caplog.at_level(logging.WARNING, logger="gesh.decision_quality"):
        dq.attach({}, dq.build(price_suspect=True, reason="bad tick",
                               data_sources=["price_source"]), context="levels:ABC")
    lines = [r for r in caplog.records if "degraded_decision" in r.getMessage()]
    assert len(lines) == 1
    msg = lines[0].getMessage()
    assert "context=levels:ABC" in msg and "price_quality=suspect" in msg


def test_clean_decision_emits_no_log(caplog):
    with caplog.at_level(logging.WARNING, logger="gesh.decision_quality"):
        dq.attach({}, dq.build(data_sources=["price_source"]), context="levels:OK")
    assert not [r for r in caplog.records if "degraded_decision" in r.getMessage()]


# ── /api/levels wiring ────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch):
    import server
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: False)
    return server, TestClient(server.app, raise_server_exceptions=False)


def test_levels_endpoint_attaches_decision_quality_clean(client, monkeypatch):
    server, c = client
    monkeypatch.setattr(server, "_latest_close", lambda sym: 100.0)
    monkeypatch.setattr(server, "_price_quality", lambda sym, px: (False, ""))
    monkeypatch.setattr("trade_levels.compute",
                        lambda sym, current_price=None: {"available": True, "symbol": sym,
                                                         "setup": {"entry": 100.0}}, raising=False)
    r = c.get("/api/levels/NVDA")
    assert r.status_code == 200
    block = r.json()["data"]["decision_quality"]
    assert block["price_quality"] == "ok" and block["decision_degraded"] is False


def test_levels_endpoint_attaches_decision_quality_suspect(client, monkeypatch):
    server, c = client
    monkeypatch.setattr(server, "_latest_close", lambda sym: 17.16)
    monkeypatch.setattr(server, "_price_quality", lambda sym, px: (True, "17.16 below 52-wk low"))
    monkeypatch.setattr(server.price_sanity, "strict_reject_enabled", lambda: False)
    monkeypatch.setattr("trade_levels.compute",
                        lambda sym, current_price=None: {"available": True, "symbol": sym,
                                                         "setup": {"entry": 17.16}}, raising=False)
    r = c.get("/api/levels/NVDA")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["decision_quality"]["price_quality"] == "suspect"
    assert data["decision_quality"]["decision_degraded"] is True
    assert data["setup"]["actionable"] is False          # existing GESH-2 flag still set
