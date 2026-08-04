from __future__ import annotations

import importlib.util
import os
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "set_env", Path(__file__).parents[1] / "scripts" / "set_env.py"
)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def test_new_env_file_is_private(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    previous_umask = os.umask(0o022)
    try:
        assert _MODULE.main(["BILLING_WEBHOOK_SECRET=whsec_test"]) == 0
    finally:
        os.umask(previous_umask)

    env = tmp_path / ".env"
    assert env.stat().st_mode & 0o777 == 0o600
    assert env.read_text() == "BILLING_WEBHOOK_SECRET=whsec_test\n"


def test_existing_env_permissions_are_preserved(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    env = tmp_path / ".env"
    env.write_text("KEEP=unchanged\nRESEND_API_KEY=old\n")
    env.chmod(0o640)

    assert _MODULE.main(["RESEND_API_KEY=new"]) == 0

    assert env.stat().st_mode & 0o777 == 0o640
    assert env.read_text() == "KEEP=unchanged\nRESEND_API_KEY=new\n"
