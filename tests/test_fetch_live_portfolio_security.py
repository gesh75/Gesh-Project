"""Security regression tests for the sandbox portfolio session loader."""

import fetch_live_portfolio as loader


def test_session_file_world_writable_is_rejected(tmp_path):
    session_pickle = tmp_path / "robinhoodrobinhood_session.pickle"
    session_pickle.touch(mode=0o600)
    session_pickle.chmod(0o666)

    assert loader._session_file_is_safe(session_pickle) is False


def test_session_file_different_owner_is_rejected(tmp_path, monkeypatch):
    session_pickle = tmp_path / "robinhoodrobinhood_session.pickle"
    session_pickle.touch(mode=0o600)
    monkeypatch.setattr(loader.os, "getuid", lambda: session_pickle.stat().st_uid + 1)

    assert loader._session_file_is_safe(session_pickle) is False


def test_robinhood_loader_unsafe_pickle_fails_before_import(tmp_path, monkeypatch):
    session_pickle = tmp_path / "robinhoodrobinhood_session.pickle"
    session_pickle.touch(mode=0o600)
    session_pickle.chmod(0o666)
    monkeypatch.setattr(loader, "SESSION_PICKLE", session_pickle)

    assert loader._load_from_robinhood() is None
