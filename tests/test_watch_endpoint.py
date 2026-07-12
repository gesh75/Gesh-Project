"""Integration tests for the /watch + /watch/data routes (TestClient over server.app).

Points watch_view at an empty throwaway DB so the routes never read production data
or hit yfinance (0 open positions → no price fetch). Also guards the auth wall and
proves the existing /track-record + /progress views still work.
"""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

import server
import watch_view

client = TestClient(server.app)


@pytest.fixture(autouse=True)
def _empty_tracker_db(tmp_path, monkeypatch):
    """Route handlers call load_watch_data() with no db_path → DEFAULT_TRACKER_DB.
    Redirect that to an empty DB (no tables → honest empty contract, no yfinance).

    Also force the login wall OFF: `auth.py` (imported transitively via `server`) calls
    load_dotenv() at import time with no path, so a real .env with GESH_AUTH_ENABLED=1
    (set for the paid product's login wall) leaks into this process and gates every
    route below with a 302 instead of the expected 200. test_billing.py guards the same
    leak the same way — this test predates that .env setting, which is exactly the kind
    of drift an orphaned (uncollected) test file can hide indefinitely."""
    p = tmp_path / "endpoint.db"
    sqlite3.connect(p).close()
    monkeypatch.setattr(watch_view, "DEFAULT_TRACKER_DB", str(p))
    monkeypatch.delenv("GESH_AUTH_ENABLED", raising=False)


def test_watch_page_200_html():
    r = client.get("/watch")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    for needle in ("Open Positions", "Recent Closes", "Activity", "PAPER"):
        assert needle in r.text


def test_watch_data_contract():
    r = client.get("/watch/data")
    assert r.status_code == 200
    assert "application/json" in r.headers["content-type"]
    d = r.json()
    for key in ("schema_version", "as_of", "mode", "price_source", "live_prices_available",
                "summary", "open_positions", "recent_closes", "activity_feed", "autonomy"):
        assert key in d, key
    assert d["schema_version"] == 1
    assert d["mode"] == "paper"
    assert isinstance(d["live_prices_available"], bool)
    # The retired 30-trade gate is gone: required now comes from the engine's
    # versioned gate DTO (100 engine-only closes; GATE_REQUIRED_FALLBACK when the
    # engine import fails — both are 100 today).
    assert d["autonomy"]["required"] == 100


def test_watch_data_lookback_param_honored():
    assert client.get("/watch/data?lookback_days=1").status_code == 200
    assert client.get("/watch/data?lookback_days=30&activity_limit=10").status_code == 200


def test_auth_gate_blocks_when_enabled(monkeypatch):
    """With the login wall on and no session cookie, BOTH /watch and /watch/data are
    gated (302 → /login; they are not under /api/ so the gate redirects rather than
    401s). Proves the cockpit is internal-only without editing auth_users.py."""
    monkeypatch.setenv("GESH_AUTH_ENABLED", "1")
    assert client.get("/watch", follow_redirects=False).status_code == 302
    assert client.get("/watch/data", follow_redirects=False).status_code == 302


def test_existing_views_unbroken():
    # auth disabled (default) — the additive routes must not break the siblings.
    assert client.get("/track-record").status_code == 200
    assert client.get("/progress").status_code == 200
