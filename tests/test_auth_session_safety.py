"""Refuse to restore a world-writable Robinhood session pickle.

``auth._session_file_is_safe`` is the startup-path twin of
``fetch_live_portfolio._session_file_is_safe`` (already tested). Deserializing
an attacker-writable pickle is an RCE vector; an unsafe file must fail closed
before ``pickle.load``.

Loaded from source so this file does not import robin_stocks / dotenv.
"""

from __future__ import annotations

import ast
import os
import pickle
from pathlib import Path
from typing import Any

_SRC = (Path(__file__).resolve().parents[1] / "auth.py").read_text()
_WANTED = {"_session_file_is_safe", "_restore_pickle_session"}


def _load():
    tree = ast.parse(_SRC)
    chunks: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in _WANTED:
            chunk = ast.get_source_segment(_SRC, node)
            assert chunk is not None
            chunks.append(chunk)
    ns: dict[str, Any] = {
        "os": os,
        "pickle": pickle,
        "_PICKLE_PATH": "/tmp/gesh-auth-session-does-not-exist.pickle",
    }
    exec("\n\n".join(chunks), ns)  # noqa: S102 — extracted production helpers only
    return ns


_NS = _load()
_session_file_is_safe = _NS["_session_file_is_safe"]
_restore_pickle_session = _NS["_restore_pickle_session"]


def test_extracted_helpers_are_present():
    assert set(_NS) >= _WANTED


def test_session_file_world_writable_is_rejected(tmp_path):
    path = tmp_path / "robinhood_session.pickle"
    path.touch(mode=0o600)
    path.chmod(0o666)
    assert _session_file_is_safe(str(path)) is False


def test_session_file_group_writable_is_rejected(tmp_path):
    path = tmp_path / "robinhood_session.pickle"
    path.touch(mode=0o600)
    path.chmod(0o620)
    assert _session_file_is_safe(str(path)) is False


def test_session_file_different_owner_is_rejected(tmp_path, monkeypatch):
    path = tmp_path / "robinhood_session.pickle"
    path.touch(mode=0o600)
    monkeypatch.setattr(os, "getuid", lambda: path.stat().st_uid + 1)
    assert _session_file_is_safe(str(path)) is False


def test_session_file_0600_owned_is_accepted(tmp_path):
    path = tmp_path / "robinhood_session.pickle"
    path.touch(mode=0o600)
    assert _session_file_is_safe(str(path)) is True


def test_session_file_missing_is_rejected(tmp_path):
    assert _session_file_is_safe(str(tmp_path / "nope.pickle")) is False


def test_restore_missing_file_is_false_without_unpickle(tmp_path, monkeypatch):
    monkeypatch.setitem(_NS, "_PICKLE_PATH", str(tmp_path / "missing.pickle"))
    loaded = {"n": 0}

    def _load(_fh):
        loaded["n"] += 1
        raise AssertionError("pickle.load must not run")

    monkeypatch.setattr(pickle, "load", _load)
    assert _restore_pickle_session() is False
    assert loaded["n"] == 0


def test_restore_unsafe_file_never_unpickles(tmp_path, monkeypatch):
    path = tmp_path / "robinhood_session.pickle"
    path.write_bytes(b"not-a-real-session")
    path.chmod(0o666)
    monkeypatch.setitem(_NS, "_PICKLE_PATH", str(path))

    loaded = {"n": 0}

    def _load(_fh):
        loaded["n"] += 1
        raise AssertionError("unsafe pickle must not be deserialized")

    monkeypatch.setattr(pickle, "load", _load)
    assert _restore_pickle_session() is False
    assert loaded["n"] == 0
