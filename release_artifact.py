"""Build a deterministic GESH runtime archive from an exact clean Git commit."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import platform
import re
import subprocess
import tarfile
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree

from build_identity import BUILD_INFO_FILENAME, SERVICE_NAME, validate_build_info

ARTIFACT_FILENAME = "gesh-financial-runtime.tar"
MANIFEST_FILENAME = "GESH_BUILD_MANIFEST.json"
_FULL_SHA_RE = re.compile(r"[0-9a-f]{40}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_MAX_JUNIT_BYTES = 50 * 1024 * 1024
_REQUIRED_MEMBERS = frozenset(
    (
        ".python-version",
        "requirements.lock",
        "server.py",
        "templates/dashboard.html",
        "templates/owner_console.html",
        "trading/__init__.py",
        "trading/contract.py",
        "trading/execution.py",
        "trading/risk_gate.py",
        "trading/state.py",
    )
)
_ROOT_METADATA = frozenset(
    (
        ".python-version",
        "pyproject.toml",
        "requirements.lock",
        "requirements.txt",
        "requirements-openbb.txt",
        "TERMS_OF_SERVICE.md",
    )
)
_EXACT_RUNTIME_RESOURCES = frozenset(("docs/GESH_PRIVACY_POLICY_DRAFT.md",))
_RUNTIME_PACKAGE_FILES = frozenset(
    (
        "security/__init__.py",
        "security/api_verifier.py",
        "trading/__init__.py",
        "trading/alerts.py",
        "trading/autopilot.py",
        "trading/config.py",
        "trading/contract.py",
        "trading/execution.py",
        "trading/risk_gate.py",
        "trading/robinhood_quotes.py",
        "trading/state.py",
    )
)
_RUNTIME_TEMPLATE_FILES = frozenset(
    (
        "templates/dashboard.html",
        "templates/owner_console.html",
    )
)
_RUNTIME_ROOT_FILES = frozenset(
    (
        "agent_db.py",
        "ai_analyzer.py",
        "allocation_config.py",
        "auth.py",
        "auth_users.py",
        "benchmarks.py",
        "billing.py",
        "build_identity.py",
        "bull_bear_debate.py",
        "congress_agent.py",
        "consensus.py",
        "consolidation_zone.py",
        "crypto_correlation.py",
        "data_fetcher.py",
        "db.py",
        "decision_quality.py",
        "defi_yield.py",
        "earnings_viz.py",
        "ecg.py",
        "email_sender.py",
        "explainability.py",
        "fair_value.py",
        "fetch_live_data.py",
        "fft_cycles.py",
        "fundamental_agent.py",
        "fundamentals_openbb.py",
        "gemini_agent.py",
        "invest_radar.py",
        "invest_view.py",
        "job_health.py",
        "kelly.py",
        "legal_pages.py",
        "long_term_macro.py",
        "macro_connector.py",
        "market_data.py",
        "model_portfolio.py",
        "momentum_signal.py",
        "monte_carlo.py",
        "news_agent.py",
        "paper_trading.py",
        "portfolio_valuation.py",
        "price_cache.py",
        "price_sanity.py",
        "price_source.py",
        "progress_view.py",
        "proof_page.py",
        "regime.py",
        "sec_agent.py",
        "sector_filter.py",
        "sentiment_connector.py",
        "server.py",
        "signal_history.py",
        "start_gesh_main.sh",
        "symbol_search.py",
        "tax_harvester.py",
        "trade_levels.py",
        "trade_log.py",
        "verdict_reconciliation.py",
        "volatility_models.py",
        "watch_view.py",
    )
)


class ArtifactBuildError(RuntimeError):
    """Base class for a release-integrity gate failure."""


class DirtyTreeError(ArtifactBuildError):
    """The checkout contains staged, unstaged, or untracked non-ignored files."""


class CommitMismatchError(ArtifactBuildError):
    """The requested build commit is not the checked-out HEAD."""


def _git(repo: Path, *args: str, check: bool = True) -> bytes:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=False,
        capture_output=True,
    )
    if check and result.returncode:
        raise ArtifactBuildError("Git provenance check failed")
    return result.stdout


def _validate_sha(value: str, label: str) -> str:
    if not isinstance(value, str) or not _FULL_SHA_RE.fullmatch(value):
        raise ArtifactBuildError(f"{label} must be a full lowercase Git SHA")
    return value


def _assert_clean_exact_commit(repo: Path, commit_sha: str) -> None:
    head = _git(repo, "rev-parse", "HEAD").decode("ascii").strip()
    if head != commit_sha:
        raise CommitMismatchError("requested commit is not checked-out HEAD")
    if _git(repo, "status", "--porcelain=v1", "--untracked-files=all"):
        raise DirtyTreeError("release builds require a completely clean tree")


def _is_runtime_member(path: str) -> bool:
    """Explicit code/resource allowlist; mutable/live/customer data is excluded."""
    if path in _ROOT_METADATA or path in _EXACT_RUNTIME_RESOURCES:
        return True
    if path in _RUNTIME_ROOT_FILES:
        return True
    if path in _RUNTIME_TEMPLATE_FILES:
        return True
    if path in _RUNTIME_PACKAGE_FILES:
        return True
    return False


def _tree_blobs(repo: Path, commit_sha: str) -> list[tuple[str, str, str]]:
    entries: list[tuple[str, str, str]] = []
    raw = _git(repo, "ls-tree", "-r", "-z", "--full-tree", commit_sha)
    for encoded in raw.split(b"\0"):
        if not encoded:
            continue
        try:
            header, encoded_path = encoded.split(b"\t", 1)
            mode, object_type, blob_sha = header.decode("ascii").split()
            path = encoded_path.decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise ArtifactBuildError("Git tree contains an unsupported entry") from exc
        if object_type == "blob" and _is_runtime_member(path):
            if mode not in {"100644", "100755"}:
                raise ArtifactBuildError("runtime allowlist contains a non-regular file")
            entries.append((path, mode, blob_sha))
    entries.sort(key=lambda item: item[0])
    missing = _REQUIRED_MEMBERS.difference(path for path, _mode, _sha in entries)
    if missing:
        raise ArtifactBuildError("required runtime members are missing")
    return entries


def _blob(repo: Path, blob_sha: str) -> bytes:
    return _git(repo, "cat-file", "blob", blob_sha)


def _write_deterministic_tar(
    repo: Path,
    destination: Path,
    entries: Iterable[tuple[str, str, str]],
) -> list[str]:
    names: list[str] = []
    with tarfile.open(destination, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for path, git_mode, blob_sha in entries:
            content = _blob(repo, blob_sha)
            info = tarfile.TarInfo(path)
            info.size = len(content)
            info.mode = 0o755 if git_mode == "100755" else 0o644
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mtime = 0
            archive.addfile(info, io.BytesIO(content))
            names.append(path)
    return names


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, allow_nan=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _junit_suites(root: ElementTree.Element) -> list[ElementTree.Element]:
    if root.tag == "testsuite":
        return [root]
    if root.tag == "testsuites":
        return list(root.findall("./testsuite"))
    raise ArtifactBuildError("test evidence is not JUnit XML")


def _validate_test_evidence(value: object, commit_sha: str) -> list[dict]:
    if not isinstance(value, list) or not value:
        raise ArtifactBuildError("exact test evidence is required")
    out: list[dict] = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {"command", "junit_xml"}:
            raise ArtifactBuildError("test evidence entry is incomplete")
        if not isinstance(item["command"], str) or not item["command"].strip():
            raise ArtifactBuildError("test command is invalid")
        try:
            evidence_path = Path(item["junit_xml"]).resolve(strict=True)
            if evidence_path.stat().st_size > _MAX_JUNIT_BYTES:
                raise ArtifactBuildError("JUnit evidence file is too large")
            evidence_bytes = evidence_path.read_bytes()
            root = ElementTree.fromstring(evidence_bytes)
        except ArtifactBuildError:
            raise
        except (OSError, TypeError, ElementTree.ParseError) as exc:
            raise ArtifactBuildError("JUnit evidence cannot be read") from exc
        suites = _junit_suites(root)
        if not suites:
            raise ArtifactBuildError("JUnit evidence has no test suite")

        totals = {"tests": 0, "failed": 0, "errors": 0, "skipped": 0}
        duration = 0.0
        properties: dict[str, set[str]] = {}
        try:
            for suite in suites:
                counts = {
                    "tests": int(suite.attrib.get("tests", "0")),
                    "failed": int(suite.attrib.get("failures", "0")),
                    "errors": int(suite.attrib.get("errors", "0")),
                    "skipped": int(suite.attrib.get("skipped", "0")),
                }
                if (
                    any(count < 0 for count in counts.values())
                    or counts["failed"] + counts["errors"] + counts["skipped"]
                    > counts["tests"]
                ):
                    raise ArtifactBuildError("JUnit evidence counts are invalid")
                suite_duration = float(suite.attrib.get("time", "0"))
                if not math.isfinite(suite_duration) or suite_duration < 0:
                    raise ArtifactBuildError("JUnit evidence duration is invalid")
                for count_name, count in counts.items():
                    totals[count_name] += count
                duration += suite_duration
                for prop in suite.findall("./properties/property"):
                    prop_name = prop.attrib.get("name")
                    prop_value = prop.attrib.get("value")
                    if prop_name and prop_value is not None:
                        properties.setdefault(prop_name, set()).add(prop_value)
            if not math.isfinite(duration):
                raise ArtifactBuildError("JUnit evidence duration is invalid")
        except ArtifactBuildError:
            raise
        except (TypeError, ValueError) as exc:
            raise ArtifactBuildError("JUnit evidence counts are invalid") from exc

        if properties.get("git_commit") != {commit_sha}:
            raise ArtifactBuildError("JUnit evidence is not bound to final SHA")
        if properties.get("git_dirty") != {"false"}:
            raise ArtifactBuildError("JUnit evidence came from a dirty tree")
        passed = (
            totals["tests"]
            - totals["failed"]
            - totals["errors"]
            - totals["skipped"]
        )
        if passed <= 0 or totals["failed"] or totals["errors"]:
            raise ArtifactBuildError("release test evidence is not green")
        evidence_sha = hashlib.sha256(evidence_bytes).hexdigest()
        if not _SHA256_RE.fullmatch(evidence_sha):  # defensive invariant
            raise ArtifactBuildError("JUnit evidence digest is invalid")
        out.append(
            {
                "command": item["command"],
                "commit_sha": commit_sha,
                "passed": passed,
                "failed": totals["failed"],
                "errors": totals["errors"],
                "skipped": totals["skipped"],
                "duration_seconds": round(duration, 3),
                "evidence": {
                    "type": "junit_xml",
                    "sha256": evidence_sha,
                },
            }
        )
    return out


def build_release_artifact(
    *,
    repo: Path,
    output_dir: Path,
    base_sha: str,
    commit_sha: str,
    built_at: str,
    test_evidence: object,
) -> dict:
    """Seal runtime code and adjacent provenance sidecars from one clean commit."""
    repo = repo.resolve(strict=True)
    if not (repo / ".git").exists():
        # A linked worktree has a .git FILE; a normal checkout has a directory.
        raise ArtifactBuildError("repository root is invalid")
    base_sha = _validate_sha(base_sha, "base SHA")
    commit_sha = _validate_sha(commit_sha, "commit SHA")
    build_info = validate_build_info(
        {
            "service": SERVICE_NAME,
            "commit_sha": commit_sha,
            "built_at": built_at,
            "dirty": False,
        }
    )
    tests = _validate_test_evidence(test_evidence, commit_sha)
    _assert_clean_exact_commit(repo, commit_sha)
    if subprocess.run(
        ["git", "merge-base", "--is-ancestor", base_sha, commit_sha],
        cwd=repo,
        check=False,
        capture_output=True,
    ).returncode:
        raise ArtifactBuildError("base SHA is not an ancestor of final SHA")

    resolved_output = output_dir.resolve(strict=False)
    try:
        resolved_output.relative_to(repo)
    except ValueError:
        pass
    else:
        raise ArtifactBuildError("artifact output must be outside the repository")
    if resolved_output.exists() and any(resolved_output.iterdir()):
        raise ArtifactBuildError("artifact output directory must be empty")

    entries = _tree_blobs(repo, commit_sha)
    output_created = not resolved_output.exists()
    resolved_output.mkdir(parents=True, exist_ok=True)
    archive_tmp = resolved_output / f".{ARTIFACT_FILENAME}.{os.getpid()}.tmp"
    archive_path = resolved_output / ARTIFACT_FILENAME
    try:
        members = _write_deterministic_tar(repo, archive_tmp, entries)
        # Catch a concurrent worktree mutation before publishing the result.
        _assert_clean_exact_commit(repo, commit_sha)
        artifact_sha = _sha256(archive_tmp)
        archive_size = archive_tmp.stat().st_size
        os.replace(archive_tmp, archive_path)
        archive_path.chmod(0o644)

        lock_sha = next(
            blob_sha for path, _mode, blob_sha in entries if path == "requirements.lock"
        )
        lock_bytes = _blob(repo, lock_sha)
        build_info_path = resolved_output / BUILD_INFO_FILENAME
        build_info_path.write_bytes(_json_bytes(build_info))
        build_info_path.chmod(0o644)
        manifest = {
            "schema_version": 1,
            "service": SERVICE_NAME,
            "base_sha": base_sha,
            "final_sha": commit_sha,
            "built_at": built_at,
            "clean_tree": True,
            "artifact": {
                "name": ARTIFACT_FILENAME,
                "format": "ustar",
                "sha256": artifact_sha,
                "size_bytes": archive_size,
                "member_count": len(members),
            },
            "build_info": {
                "name": BUILD_INFO_FILENAME,
                "sha256": _sha256(build_info_path),
            },
            "requirements_lock_sha256": hashlib.sha256(lock_bytes).hexdigest(),
            "runtime": {
                "python": platform.python_version(),
                "python_implementation": platform.python_implementation(),
            },
            "runtime_members": members,
            "tests": tests,
            "production_deployment": False,
        }
        manifest_path = resolved_output / MANIFEST_FILENAME
        manifest_path.write_bytes(_json_bytes(manifest))
        manifest_path.chmod(0o644)
        return manifest
    except Exception:
        archive_tmp.unlink(missing_ok=True)
        if output_created and resolved_output.exists() and not any(resolved_output.iterdir()):
            resolved_output.rmdir()
        raise


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a deterministic runtime tar and immutable provenance sidecars."
    )
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--built-at", required=True, help="UTC timestamp: YYYY-MM-DDTHH:MM:SSZ")
    parser.add_argument(
        "--test-evidence",
        type=Path,
        required=True,
        help="JSON array containing exact commands and clean-commit JUnit XML paths.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        test_evidence = json.loads(args.test_evidence.read_text(encoding="utf-8"))
        manifest = build_release_artifact(
            repo=args.repo,
            output_dir=args.output_dir,
            base_sha=args.base_sha,
            commit_sha=args.commit_sha,
            built_at=args.built_at,
            test_evidence=test_evidence,
        )
    except (OSError, json.JSONDecodeError, ArtifactBuildError) as exc:
        raise SystemExit(f"release build refused: {exc}") from None
    print(json.dumps(manifest, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
