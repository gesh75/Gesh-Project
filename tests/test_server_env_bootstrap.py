"""Server bootstrap must load this checkout's dotenv before config imports."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROBE = "GESH_SERVER_ENV_BOOTSTRAP_PROBE"


@pytest.mark.parametrize(
    ("external_value", "expected"),
    [
        (None, "from-repo-dotenv"),
        ("from-process-env", "from-process-env"),
    ],
)
def test_server_loads_repo_dotenv_before_env_sensitive_imports(
    tmp_path: Path,
    external_value: str | None,
    expected: str,
) -> None:
    """The first env-sensitive import observes repo dotenv with env precedence."""
    checkout = tmp_path / "checkout"
    elsewhere = tmp_path / "elsewhere"
    checkout.mkdir()
    elsewhere.mkdir()
    shutil.copy(ROOT / "server.py", checkout / "server.py")
    (checkout / ".env").write_text(f"{PROBE}=from-repo-dotenv\n", encoding="utf-8")
    (checkout / "llm_gateway.py").write_text(
        f"import os\nprint(os.environ.get({PROBE!r}, 'missing'))\nraise SystemExit(0)\n",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.pop(PROBE, None)
    if external_value is not None:
        env[PROBE] = external_value

    result = subprocess.run(
        [sys.executable, str(checkout / "server.py")],
        cwd=elsewhere,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected
