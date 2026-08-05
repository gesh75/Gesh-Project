"""Regression tests for Docker context and private dependency secret handling."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _dockerignore_patterns() -> set[str]:
    return {
        line.strip()
        for line in (ROOT / ".dockerignore").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def test_dockerignore_excludes_gitignored_runtime_secrets_and_live_data():
    patterns = _dockerignore_patterns()

    required = {
        ".rh_session/",
        "*.session",
        "gesh_live_cache.json",
        "portfolio_live.json",
        "live_portfolio_snapshot.json",
        "gesh_daily_log.json",
        "gesh_analysis_data.json",
        "latest_analysis.json",
        "last_analysis_snapshot.json",
        "last_action_plan.json",
        "gesh_auth.secret",
        ".logs/",
    }

    assert required <= patterns


def test_dockerfile_uses_buildkit_secret_not_pat_build_arg():
    dockerfile = (ROOT / "Dockerfile").read_text()

    assert "ARG GESH_CORE_PAT" not in dockerfile
    assert "x-access-token:${GESH_CORE_PAT}@" not in dockerfile
    assert "--mount=type=secret,id=gesh_core_pat,required=true" in dockerfile
    assert "docker build --secret id=gesh_core_pat" in dockerfile
    assert "http.https://github.com/gesh75/gesh-core.git.extraheader" in dockerfile
    assert "rm -f /root/.gitconfig" in dockerfile
