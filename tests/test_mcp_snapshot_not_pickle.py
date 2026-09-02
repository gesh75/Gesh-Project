"""Owner live data is Robinhood MCP, not the robin_stocks pickle.

reauth.py + server startup used to POST ROBINHOOD_USER/PASS. That path now
returns 'Unable to log in with provided credentials' / KeyError token_type on
a 0-byte pickle, and it is not how the daily chain or dashboard snapshot job
get data. Those use fetch_snapshot_direct.py (OAuth MCP).
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def test_startup_login_does_not_post_pickle_credentials():
    src = (REPO / "server.py").read_text()
    chunk = src.split("def _startup_auth")[1].split("yield")[0]
    assert "login(allow_credentials=False)" in chunk
    assert "login()" not in chunk.replace("login(allow_credentials=False)", "")


def test_reauth_delegates_to_mcp_snapshot_and_does_not_call_robin_stocks_login():
    src = (REPO / "reauth.py").read_text()
    assert "fetch_snapshot_direct.py" in src
    assert "rh.login" not in src
    assert "ROBINHOOD_PASS" not in src


def test_auth_reuse_only_never_calls_rh_login(monkeypatch):
    import auth as auth_mod

    monkeypatch.setattr(auth_mod, "_restore_pickle_session", lambda: False)
    called = {"n": 0}

    def _boom(*_a, **_k):
        called["n"] += 1
        raise AssertionError("reuse-only must not POST credentials")

    monkeypatch.setattr(auth_mod.rh, "login", _boom)
    with pytest.raises(RuntimeError, match="reauth"):
        auth_mod.login(allow_credentials=False)
    assert called["n"] == 0
