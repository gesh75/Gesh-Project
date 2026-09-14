"""Astra R7: import and pytest collection must not mutate operational data.

resolve_db_path is path-only in the pinned gesh-core. Product collection must
set temporary DB/auth paths and deny network before any application import,
and every state writer — not only db/auth/signal_history — must resolve inside
the disposable test root.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _clean_env(**extra: str) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("GESH_DB_PATH", None)
    env.pop("GESH_AUTH_DB", None)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(REPO_ROOT), existing) if p)
    env.update(extra)
    return env


def test_pytest_configure_sets_temp_paths_and_blocks_network(monkeypatch):
    c = sys.modules["conftest"]

    assert hasattr(c, "pytest_configure"), "collection hook missing"
    config = types.SimpleNamespace()
    monkeypatch.delenv("GESH_DB_PATH", raising=False)
    monkeypatch.delenv("GESH_AUTH_DB", raising=False)
    c.pytest_configure(config)
    try:
        db_path = Path(os.environ["GESH_DB_PATH"])
        auth_path = Path(os.environ["GESH_AUTH_DB"])
        assert db_path.name == "gesh_financial.db"
        assert auth_path.name == "gesh_auth.db"
        assert db_path.parent == auth_path.parent
        assert db_path.parent.exists()
        with pytest.raises(c._NetworkBlocked):
            socket.socket().connect(("1.1.1.1", 443))
    finally:
        unconfigure = getattr(c, "pytest_unconfigure", None)
        if unconfigure is not None:
            unconfigure(config)


def test_trade_log_db_path_is_isolated(tmp_path):
    import trade_log

    path = Path(trade_log.DB_PATH).resolve()
    assert tmp_path.resolve() in path.parents or path.parent == tmp_path.resolve(), (
        f"trade_log.DB_PATH={path} escaped the disposable test root {tmp_path}"
    )


def test_trading_state_db_path_is_isolated(tmp_path):
    import trading.state as state

    path = Path(state.DB_PATH).resolve()
    assert tmp_path.resolve() in path.parents or path.parent == tmp_path.resolve(), (
        f"trading.state.DB_PATH={path} escaped the disposable test root {tmp_path}"
    )


def test_import_product_modules_against_readonly_sentinel(tmp_path):
    sentinel = tmp_path / "operational.db"
    payload = b"operational-sentinel-v1"
    sentinel.write_bytes(payload)
    sentinel.chmod(0o444)
    before = sentinel.stat()
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import trade_log; import db; import trading.state",
        ],
        cwd=tmp_path,
        env=_clean_env(GESH_DB_PATH=str(sentinel)),
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    after = sentinel.stat()
    assert sentinel.read_bytes() == payload
    assert after.st_size == before.st_size
    assert after.st_mtime_ns == before.st_mtime_ns
