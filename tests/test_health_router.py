"""PR1 extract: /healthz, /api/me, /favicon.ico, /api/security/health live on routers.health.

Existing TestClient tests keep importing server.app. This file locks the extract:
the four routes are served from routers.health, not from leftover @app.get handlers.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

import server

HEALTH_PATHS = ("/healthz", "/api/me", "/favicon.ico", "/api/security/health")


def _get_endpoints(path: str):
    return [
        route.endpoint
        for route in server.app.routes
        if getattr(route, "path", None) == path
        and "GET" in (getattr(route, "methods", None) or set())
    ]


def test_health_router_module_exists():
    import routers.health as health

    assert hasattr(health, "router")


def test_health_routes_are_served_from_routers_health():
    for path in HEALTH_PATHS:
        matches = _get_endpoints(path)
        assert matches, f"missing GET {path}"
        for endpoint in matches:
            assert endpoint.__module__ == "routers.health", (
                f"{path} still on {endpoint.__module__}"
            )


def test_server_py_no_longer_declares_moved_health_handlers():
    src = Path(server.__file__).read_text(encoding="utf-8")
    for needle in (
        '@app.get("/healthz")',
        '@app.get("/api/me")',
        '@app.get("/favicon.ico"',
        '@app.get("/api/security/health")',
    ):
        assert needle not in src, f"handler still in server.py: {needle}"


def test_healthz_includes_uptime_and_no_secrets():
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.delenv("GESH_AUTH_ENABLED", raising=False)
        client = TestClient(server.app, raise_server_exceptions=False)
        r = client.get("/healthz")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert "auth" in body
        assert isinstance(body["uptime_seconds"], int)
        assert body["uptime_seconds"] >= 0
        blob = str(body).lower()
        for forbidden in ("/users/", "portfolio", "equity", "password", "secret", "@"):
            assert forbidden not in blob
    finally:
        monkeypatch.undo()


def test_favicon_is_inline_svg():
    client = TestClient(server.app, raise_server_exceptions=False)
    r = client.get("/favicon.ico")
    assert r.status_code == 200
    assert "image/svg+xml" in r.headers.get("content-type", "")
    assert b"<svg" in r.content


def test_security_health_uses_server_sanitize(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "security.api_verifier.all_api_health",
        lambda: {"probe": float("nan")},
    )
    client = TestClient(server.app, raise_server_exceptions=False)
    r = client.get("/api/security/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["sources"]["probe"] is None
