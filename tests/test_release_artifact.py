"""Deterministic, clean-commit release artifact and provenance manifest."""

from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from release_artifact import (
    ArtifactBuildError,
    CommitMismatchError,
    DirtyTreeError,
    _is_runtime_member,
    _json_bytes,
    build_release_artifact,
)

BUILT_AT = "2026-07-25T16:00:00Z"
TEST_COMMAND = "pytest tests/ -q --junitxml=full.xml"


def test_manifest_json_refuses_non_finite_numbers():
    with pytest.raises(ValueError):
        _json_bytes({"duration_seconds": float("nan")})


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _git_bytes(repo: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    ).stdout


def _write(repo: Path, relative: str, content: str = "fixture\n") -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_junit(
    path: Path,
    commit_sha: str,
    *,
    tests: int = 123,
    failures: int = 0,
    errors: int = 0,
    skipped: int = 0,
    dirty: str = "false",
    duration: str | float = "1.25",
) -> None:
    path.write_text(
        (
            '<testsuites><testsuite name="pytest" '
            f'tests="{tests}" failures="{failures}" errors="{errors}" '
            f'skipped="{skipped}" time="{duration}">'
            "<properties>"
            f'<property name="git_commit" value="{commit_sha}"/>'
            f'<property name="git_dirty" value="{dirty}"/>'
            "</properties></testsuite></testsuites>"
        ),
        encoding="utf-8",
    )


@pytest.fixture
def release_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Artifact Test")
    _git(repo, "config", "user.email", "artifact@example.invalid")

    included = {
        "server.py": (
            "from trading.contract import manual\n"
            "from trading.execution import place\n"
            "from trading.risk_gate import evaluate\n"
            "from trading.state import init_trading_tables\n"
        ),
        "helper.py": "VALUE = 1\n",
        "start_gesh_main.sh": "#!/bin/sh\nexec python server.py\n",
        "templates/dashboard.html": "<main>customer</main>\n",
        "templates/owner_console.html": "<main>owner</main>\n",
        "security/__init__.py": "",
        "security/api_verifier.py": "def health(): return {}\n",
        "trading/__init__.py": "",
        "trading/contract.py": "def manual(): return None\n",
        "trading/execution.py": "def place(): return None\n",
        "trading/risk_gate.py": "def evaluate(): return None\n",
        "trading/state.py": "def init_trading_tables(): return None\n",
        "requirements.lock": "fastapi==1.0\n",
        "requirements.txt": "fastapi\n",
        "requirements-openbb.txt": "openbb\n",
        ".python-version": "3.12\n",
        "pyproject.toml": "[tool.pytest.ini_options]\n",
        "TERMS_OF_SERVICE.md": "# Terms\n",
        "docs/GESH_PRIVACY_POLICY_DRAFT.md": "# Privacy\n",
    }
    excluded = {
        "docs/GESH_CONTROL_PACK_2026-07-25/GESH_00_EXECUTIVE_STATE.md": "audit\n",
        "tests/test_secret.py": "OWNER_CASH = 999\n",
        "android/GeshFinancial/app/Owner.kt": "owner\n",
        "gesh_config.json": '{"total_buying_power": 999999}\n',
        "gesh_financial.db": "not-a-real-db\n",
        "live_portfolio_snapshot.json": '{"source":"robinhood_mcp"}\n',
        ".github/workflows/tests.yml": "name: tests\n",
    }
    for relative, content in {**included, **excluded}.items():
        _write(repo, relative, content)
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "fixture runtime")
    return repo, _git(repo, "rev-parse", "HEAD")


def _build(repo: Path, output: Path, base_sha: str, commit_sha: str):
    junit = output.parent / f"{output.name}-junit.xml"
    _write_junit(junit, commit_sha)
    return build_release_artifact(
        repo=repo,
        output_dir=output,
        base_sha=base_sha,
        commit_sha=commit_sha,
        built_at=BUILT_AT,
        test_evidence=[{"command": TEST_COMMAND, "junit_xml": str(junit)}],
    )


def test_archive_is_deterministic_allowlisted_and_machine_verifiable(release_repo, tmp_path):
    repo, sha = release_repo
    first = _build(repo, tmp_path / "out-1", sha, sha)
    second = _build(repo, tmp_path / "out-2", sha, sha)

    assert first["artifact"]["sha256"] == second["artifact"]["sha256"]
    archive = tmp_path / "out-1" / first["artifact"]["name"]
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == first["artifact"]["sha256"]

    with tarfile.open(archive, "r:") as tar:
        members = tar.getmembers()
        names = [member.name for member in members]
        assert names == sorted(names)
        assert all(member.uid == member.gid == member.mtime == 0 for member in members)
        assert all(not member.uname and not member.gname for member in members)

    assert "server.py" in names
    assert "start_gesh_main.sh" in names
    assert "templates/dashboard.html" in names
    assert "templates/owner_console.html" in names
    assert "templates/future_secret.html" not in names
    assert "docs/GESH_PRIVACY_POLICY_DRAFT.md" in names
    assert "trading/contract.py" in names
    assert "trading/execution.py" in names
    assert "trading/risk_gate.py" in names
    assert "trading/state.py" in names
    assert not any(name.startswith("tests/") for name in names)
    assert not any("GESH_CONTROL_PACK" in name for name in names)
    assert not any(name.startswith("android/") for name in names)
    assert "gesh_config.json" not in names
    assert "gesh_financial.db" not in names
    assert "live_portfolio_snapshot.json" not in names
    assert "helper.py" not in names

    extracted = tmp_path / "extracted"
    with tarfile.open(archive, "r:") as tar:
        tar.extractall(extracted, filter="data")
    import_check = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            (
                f"import sys; sys.path.insert(0, {str(extracted)!r}); "
                "import server, trading.contract, trading.execution, "
                "trading.risk_gate, trading.state"
            ),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert import_check.returncode == 0, import_check.stderr

    manifest_path = tmp_path / "out-1" / "GESH_BUILD_MANIFEST.json"
    assert json.loads(manifest_path.read_text(encoding="utf-8")) == first
    assert first["base_sha"] == sha
    assert first["final_sha"] == sha
    assert first["clean_tree"] is True
    assert first["tests"] == [
        {
            "command": TEST_COMMAND,
            "commit_sha": sha,
            "passed": 123,
            "failed": 0,
            "errors": 0,
            "skipped": 0,
            "duration_seconds": 1.25,
            "evidence": {
                "type": "junit_xml",
                "sha256": hashlib.sha256(
                    (tmp_path / "out-1-junit.xml").read_bytes()
                ).hexdigest(),
            },
        }
    ]
    assert first["production_deployment"] is False
    assert first["requirements_lock_sha256"] == hashlib.sha256(
        _git_bytes(repo, "show", f"{sha}:requirements.lock")
    ).hexdigest()

    build_info = json.loads(
        (tmp_path / "out-1" / "GESH_BUILD_INFO.json").read_text(encoding="utf-8")
    )
    assert build_info == {
        "service": "gesh-financial",
        "commit_sha": sha,
        "built_at": BUILT_AT,
        "dirty": False,
    }


def test_runtime_allowlist_covers_the_real_local_import_closure():
    root_modules = {path.stem: path for path in Path(".").glob("*.py")}
    queued = ["server"]
    visited: set[str] = set()

    while queued:
        module = queued.pop()
        if module in visited or module not in root_modules:
            continue
        visited.add(module)
        tree = ast.parse(root_modules[module].read_text(encoding="utf-8"))
        dependencies: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                dependencies.update(alias.name.partition(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                dependencies.add(node.module.partition(".")[0])
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "__import__"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                dependencies.add(node.args[0].value.partition(".")[0])
        queued.extend(dependencies.intersection(root_modules))

    missing = sorted(
        f"{module}.py"
        for module in visited
        if not _is_runtime_member(f"{module}.py")
    )
    assert not missing, f"runtime import closure is absent from artifact: {missing}"
    assert not _is_runtime_member("future_debug_dump.py")
    assert not _is_runtime_member("operator_migration.py")
    assert not _is_runtime_member("patch_ai.py")
    assert not _is_runtime_member("patch_tools.py")
    assert not _is_runtime_member("unblock.py")
    assert not _is_runtime_member("export_portfolio.py")
    assert not _is_runtime_member("reauth.py")
    assert not _is_runtime_member("ops_backup.py")
    assert not _is_runtime_member("trading/future_secret_dump.py")
    assert not _is_runtime_member("security/debug_console.py")


def test_docs_only_final_commit_keeps_runtime_digest_but_updates_identity(
    release_repo, tmp_path
):
    repo, base_sha = release_repo
    candidate = _build(repo, tmp_path / "candidate", base_sha, base_sha)
    _write(
        repo,
        "docs/GESH_CONTROL_PACK_2026-07-25/GESH_09_P0_CONTAINMENT_RESULT.md",
        "candidate runtime digest recorded\n",
    )
    _git(repo, "add", "docs/GESH_CONTROL_PACK_2026-07-25")
    _git(repo, "commit", "-qm", "document release evidence")
    final_sha = _git(repo, "rev-parse", "HEAD")

    final = _build(repo, tmp_path / "final", base_sha, final_sha)

    assert final["artifact"]["sha256"] == candidate["artifact"]["sha256"]
    assert final["final_sha"] == final_sha
    build_info = json.loads(
        (tmp_path / "final" / "GESH_BUILD_INFO.json").read_text(encoding="utf-8")
    )
    assert build_info["commit_sha"] == final_sha


@pytest.mark.parametrize("dirty_kind", ["tracked", "untracked"])
def test_build_refuses_any_dirty_tree(release_repo, tmp_path, dirty_kind):
    repo, sha = release_repo
    if dirty_kind == "tracked":
        _write(repo, "server.py", "changed\n")
    else:
        _write(repo, "untracked.txt", "untracked\n")

    with pytest.raises(DirtyTreeError):
        _build(repo, tmp_path / "out", sha, sha)

    assert not (tmp_path / "out").exists()


def test_build_refuses_commit_other_than_checked_out_head(release_repo, tmp_path):
    repo, first_sha = release_repo
    _write(repo, "helper.py", "VALUE = 2\n")
    _git(repo, "add", "helper.py")
    _git(repo, "commit", "-qm", "second runtime")

    with pytest.raises(CommitMismatchError):
        _build(repo, tmp_path / "out", first_sha, first_sha)

    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize(
    ("changes", "message"),
    (
        ({"failures": 1}, "not green"),
        ({"errors": 1}, "not green"),
        ({"tests": 0}, "not green"),
        ({"tests": 0, "skipped": -1}, "counts are invalid"),
        ({"tests": -1, "skipped": -2}, "counts are invalid"),
        ({"tests": 1, "skipped": 2}, "counts are invalid"),
        ({"duration": "nan"}, "duration is invalid"),
        ({"duration": "inf"}, "duration is invalid"),
        ({"duration": -0.01}, "duration is invalid"),
        ({"dirty": "true"}, "dirty tree"),
        ({"commit_sha": "b" * 40}, "final SHA"),
    ),
)
def test_build_refuses_unbound_or_non_green_junit(
    release_repo, tmp_path, changes, message
):
    repo, sha = release_repo
    junit = tmp_path / "invalid.xml"
    changes = dict(changes)
    junit_sha = changes.pop("commit_sha", sha)
    _write_junit(junit, junit_sha, **changes)

    with pytest.raises(ArtifactBuildError, match=message):
        build_release_artifact(
            repo=repo,
            output_dir=tmp_path / "out",
            base_sha=sha,
            commit_sha=sha,
            built_at=BUILT_AT,
            test_evidence=[{"command": TEST_COMMAND, "junit_xml": str(junit)}],
        )
