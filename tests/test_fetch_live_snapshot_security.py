"""Pickle restore for the tier-3 snapshot path must refuse unsafe sessions.

``_restore_session`` is a second copy of the owner-session loader (the other
is ``auth._restore_pickle_session`` / ``fetch_live_portfolio``). World-writable
or foreign-uid files must never be unpickled and must never import robin_stocks.
"""

from __future__ import annotations

import builtins
import pickle

import fetch_live_snapshot as fls


def _write_pickle(path, payload=None) -> None:
    data = payload if payload is not None else {
        "token_type": "Bearer",
        "access_token": "fake-test-token-not-a-secret",
    }
    path.write_bytes(pickle.dumps(data))
    path.chmod(0o600)


def test_missing_pickle_returns_false_without_import(tmp_path, monkeypatch):
    monkeypatch.setattr(fls, "PICKLE_PATH", str(tmp_path / "missing.pickle"))
    real_import = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name.startswith("robin_stocks"):
            raise AssertionError("robin_stocks imported when pickle is missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    assert fls._restore_session() is False


def test_empty_pickle_is_removed_and_refused(tmp_path, monkeypatch):
    dead = tmp_path / "empty.pickle"
    dead.write_bytes(b"")
    monkeypatch.setattr(fls, "PICKLE_PATH", str(dead))
    assert fls._restore_session() is False
    assert not dead.exists()


def test_world_writable_pickle_is_refused_before_unpickle(tmp_path, monkeypatch):
    sess = tmp_path / "sess.pickle"
    _write_pickle(sess)
    sess.chmod(0o666)
    monkeypatch.setattr(fls, "PICKLE_PATH", str(sess))
    real_import = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name.startswith("robin_stocks"):
            raise AssertionError("robin_stocks imported for a world-writable pickle")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    assert fls._restore_session() is False
    assert sess.exists()  # unsafe file is not deleted; just refused


def test_group_writable_pickle_is_refused(tmp_path, monkeypatch):
    sess = tmp_path / "sess.pickle"
    _write_pickle(sess)
    sess.chmod(0o660)
    monkeypatch.setattr(fls, "PICKLE_PATH", str(sess))
    assert fls._restore_session() is False


def test_foreign_uid_pickle_is_refused(tmp_path, monkeypatch):
    sess = tmp_path / "sess.pickle"
    _write_pickle(sess)
    monkeypatch.setattr(fls, "PICKLE_PATH", str(sess))
    monkeypatch.setattr(fls.os, "getuid", lambda: sess.stat().st_uid + 1)
    assert fls._restore_session() is False


def test_corrupt_pickle_is_refused_without_robinhood_import(tmp_path, monkeypatch):
    sess = tmp_path / "sess.pickle"
    sess.write_bytes(b"not-a-pickle")
    sess.chmod(0o600)
    monkeypatch.setattr(fls, "PICKLE_PATH", str(sess))
    real_import = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name.startswith("robin_stocks"):
            raise AssertionError("robin_stocks imported for a corrupt pickle")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    assert fls._restore_session() is False
