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

import importlib.util
import json
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest

OWNER_HELD_SYMBOL = "NVDA"
OWNER_ECG_DETAIL = "ECG=SUSPENDED DD=-71.9% Streak=1"


def _consensus():
    """Load redact helpers without the gesh-core / yfinance boot path.

    `consensus.py` imports yfinance, price_source, and agent_db→db at module
    level. Helper assertions only need redact_owner_ecg; HTTP tests lazy-import
    the real server when the full tree is present.
    """
    try:
        import consensus as real

        return real
    except ModuleNotFoundError:
        pass

    name = "gesh_consensus_ecg_redact"
    cached = sys.modules.get(name)
    if cached is not None:
        return cached

    sys.modules.setdefault(
        "yfinance", types.SimpleNamespace(Ticker=lambda *a, **k: types.SimpleNamespace(info={}))
    )
    sys.modules.setdefault(
        "numpy", types.SimpleNamespace(ndarray=object, array=lambda *a, **k: None)
    )
    sys.modules.setdefault("price_source", types.ModuleType("price_source"))
    sys.modules.setdefault("db", types.SimpleNamespace(DB_PATH="/tmp/gesh_ecg_redact.db"))
    sys.modules.setdefault(
        "agent_db", types.SimpleNamespace(agent_db_path=lambda: Path("/tmp/gesh_ecg_redact.db"))
    )

    src = Path(__file__).resolve().parents[1] / "consensus.py"
    spec = importlib.util.spec_from_file_location(name, src)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


consensus = _consensus()


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


def _server():
    pytest.importorskip("starlette")
    try:
        import server
    except ModuleNotFoundError as exc:
        pytest.skip(f"server import requires full deps: {exc}")
    return server


@pytest.fixture()
def engine_file(tmp_path, monkeypatch):
    server = _server()
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


def _client(monkeypatch, *, email: str, owner: bool):
    from starlette.testclient import TestClient

    server = _server()
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


def test_customer_cached_consensus_strips_owner_drawdown(cached_owner_consensus, customer_client):
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


def test_looks_like_owner_ecg_detail_requires_drawdown_tokens():
    """Do not treat every string that mentions ECG as owner-curve leakage."""
    assert consensus._looks_like_owner_ecg_detail(OWNER_ECG_DETAIL) is True
    assert consensus._looks_like_owner_ecg_detail("ECG=HEALTHY DD=-2.1%") is True
    assert consensus._looks_like_owner_ecg_detail("ECG=SUSPENDED Streak=4") is True
    assert consensus._looks_like_owner_ecg_detail("ECG node abstained — no history") is False
    assert consensus._looks_like_owner_ecg_detail("coverage 80%") is False


def test_redact_owner_ecg_strips_leftover_drawdown_in_narrative_fields():
    """The daily chain sometimes copies node text into a summary/note field."""
    src = {
        "data": {
            "holdings": [_leaky_row()],
            "summary": f"NVDA {OWNER_ECG_DETAIL} — exit the name",
        }
    }
    out = consensus.redact_owner_ecg(src)
    assert OWNER_ECG_DETAIL not in json.dumps(out)
    assert "DD=" not in out["data"]["summary"]
    assert out["data"]["summary"] == consensus._ECG_DISABLED_DETAIL
    assert src["data"]["summary"].startswith("NVDA ECG=")


def test_redact_owner_ecg_cached_miss_is_a_noop():
    """_cached_response returns {cached: False} when the chain has not run."""
    miss = {"cached": False}
    out = consensus.redact_owner_ecg(miss)
    assert out == {"cached": False}
    assert out is not miss


def test_customer_empty_cache_does_not_500(engine_file, customer_client):
    """No consensus key → {cached: False}, not a redact crash."""
    engine_file.write_text(json.dumps({"kelly": {"status": "ok"}}))
    r = customer_client.get("/api/consensus/cached")
    assert r.status_code == 200, r.text
    assert r.json().get("cached") is False
    assert "DD=" not in r.text
