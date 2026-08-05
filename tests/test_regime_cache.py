"""Regression tests for malformed cached output consumed by ``/api/regime``."""

from __future__ import annotations

import pytest

import server


@pytest.mark.parametrize("malformed_cache", ["not-a-regime", ["not", "a", "mapping"]])
def test_regime_endpoint_malformed_cache_falls_through_to_live(monkeypatch, malformed_cache):
    monkeypatch.setattr(
        server,
        "_load_engine_results",
        lambda: {
            "regime": malformed_cache,
            "regime_ran_at": "2099-01-01T00:00:00+00:00",
        },
    )
    monkeypatch.setattr(server, "_context_payload", lambda _request: {"holdings": []})
    monkeypatch.setattr(
        server,
        "detect_regimes",
        lambda _symbols: {"regimes": [], "regime_summary": "live"},
    )

    response = server.regime_endpoint()

    assert response == {
        "status": "ok",
        "data": {"regimes": [], "regime_summary": "live"},
    }


def test_regime_endpoint_malformed_cache_offline_returns_empty_degraded_response(
    monkeypatch,
):
    monkeypatch.setattr(
        server,
        "_load_engine_results",
        lambda: {"regime": "not-a-regime", "regime_ran_at": "stale"},
    )
    monkeypatch.setattr(
        server,
        "_context_payload",
        lambda _request: (_ for _ in ()).throw(server.RobinhoodOfflineError("offline")),
    )

    response = server.regime_endpoint(force_live=True)

    assert response == {
        "status": "degraded",
        "data": {"regimes": [], "regime_summary": "Live regime unavailable."},
    }
