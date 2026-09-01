"""GET /api/consensus/cached must not ship the owner's ECG curve to customers.

The daily chain (run_engines2) persists consensus rows computed with
allow_ecg=True against equity_snapshots — a table keyed by symbol with no
user column. GET /api/consensus/cached is gated by require_customer_or_owner,
so a paying subscriber can read that blob. The customer dollar-masker only
redacts strings containing '$' or 'share'; 'ECG=SUSPENDED DD=-71.9% Streak=1'
has neither, and would otherwise leave the handler unchanged.

The per-symbol live route (test_consensus_symbol_ecg_leak.py) already forces
allow_ecg=False. This file covers the CACHED path, which never re-runs the
engine and so cannot be saved by that argument.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from starlette.testclient import TestClient

import consensus
import server

OWNER_HELD_SYMBOL = "NVDA"
OWNER_ECG_DETAIL = "ECG=SUSPENDED DD=-71.9% Streak=1"


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _leaky_row() -> dict:
    return {
        "symbol": OWNER_HELD_SYMBOL,
        "verdict": "SELL",
        "raw_score": -0.4,
        "votes": {"MOMENTUM": 1, "ECG": -1, "NEWS": 0},
        "details": {
            "MOMENTUM": "RSI 55",
            "ECG": OWNER_ECG_DETAIL,
            "NEWS": "neutral",
        },
        "active_nodes": 3,
    }


@pytest.fixture()
def engine_file(tmp_path, monkeypatch):
    eng = tmp_path / "engine_results.json"
    monkeypatch.setattr(server, "ENGINE_RESULTS", eng)

    def _load():
        return json.loads(eng.read_text()) if eng.exists() else {}

    monkeypatch.setattr(server, "_load_engine_results", _load)
    return eng


@pytest.fixture()
def cached_owner_consensus(engine_file):
    engine_file.write_text(
        json.dumps(
            {
                "consensus": {
                    "status": "ok",
                    "data": {"holdings": [_leaky_row()]},
                },
                "consensus_ran_at": _iso_now(),
            }
        )
    )
    return engine_file


def _client(monkeypatch, *, email: str, owner: bool) -> TestClient:
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: True)
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: email)
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda p, c: "allow", raising=False)
    monkeypatch.setattr(server.auth_users, "is_owner", lambda e: owner, raising=False)
    monkeypatch.setattr(server, "_customer_scale_factor", lambda: 0.5)
    c = TestClient(server.app, raise_server_exceptions=False)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    return c


@pytest.fixture
def customer_client(monkeypatch):
    return _client(monkeypatch, email="cust@x.com", owner=False)


@pytest.fixture
def owner_client(monkeypatch):
    return _client(monkeypatch, email="owner@x.com", owner=True)


def test_redact_owner_ecg_forces_abstain_and_drops_drawdown():
    """Pure helper: the leak string and the BEARISH vote must both go."""
    out = consensus.redact_owner_ecg({"data": {"holdings": [_leaky_row()]}})
    row = out["data"]["holdings"][0]
    assert row["votes"]["ECG"] is None
    assert row["details"]["ECG"] == consensus._ECG_DISABLED_DETAIL
    assert "DD=" not in row["details"]["ECG"]
    assert row["ecg_enabled"] is False
    assert row["votes"]["MOMENTUM"] == 1
    assert row["verdict"] == "SELL"


def test_redact_owner_ecg_does_not_mutate_input():
    src = {"details": {"ECG": OWNER_ECG_DETAIL}, "votes": {"ECG": -1}}
    consensus.redact_owner_ecg(src)
    assert src["details"]["ECG"] == OWNER_ECG_DETAIL
    assert src["votes"]["ECG"] == -1


def test_customer_cached_consensus_strips_owner_drawdown(
    cached_owner_consensus, customer_client
):
    r = customer_client.get("/api/consensus/cached")
    assert r.status_code == 200, r.text
    body = r.text
    assert "ECG=SUSPENDED" not in body
    assert "DD=-" not in body
    assert "Streak=" not in body

    payload = r.json()
    holdings = ((payload.get("data") or {}).get("holdings")) or []
    assert holdings, payload
    row = holdings[0]
    assert row["votes"]["ECG"] is None
    assert row["details"]["ECG"] == consensus._ECG_DISABLED_DETAIL
    assert row.get("ecg_enabled") is False


def test_owner_cached_consensus_keeps_ecg_node(cached_owner_consensus, owner_client):
    r = owner_client.get("/api/consensus/cached")
    assert r.status_code == 200, r.text
    assert OWNER_ECG_DETAIL in r.text
    row = r.json()["data"]["holdings"][0]
    assert row["votes"]["ECG"] == -1
    assert row["details"]["ECG"] == OWNER_ECG_DETAIL


def test_chain_per_symbol_shape_is_also_stripped(engine_file, customer_client):
    """run_engines2 stores consensus as {SYM: row}, not {holdings: [...] }."""
    engine_file.write_text(
        json.dumps(
            {
                "consensus": {OWNER_HELD_SYMBOL: _leaky_row()},
                "consensus_ran_at": _iso_now(),
            }
        )
    )
    r = customer_client.get("/api/consensus/cached")
    assert r.status_code == 200, r.text
    assert "DD=-" not in r.text
    holdings = r.json()["data"]["holdings"]
    assert holdings[0]["votes"]["ECG"] is None
