"""Regression checks for required CI gate trigger coverage."""

import re
from pathlib import Path

import pytest

WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "tests.yml"
CONFTEST = Path(__file__).resolve().parents[0] / "conftest.py"


@pytest.mark.parametrize("job_name", ["pr-lint", "test", "security", "customer-isolation"])
def test_required_ci_job_manual_dispatch_runs_gate(job_name):
    workflow = WORKFLOW.read_text(encoding="utf-8")
    job_match = re.search(
        rf"^  {re.escape(job_name)}:\n(?P<body>.*?)(?=^  [a-z][a-z-]*:\n|\Z)",
        workflow,
        flags=re.MULTILINE | re.DOTALL,
    )

    assert job_match, f"required CI job {job_name!r} is missing"
    condition_match = re.search(
        r"^    if:\s*(?P<condition>.*?)(?=^    [a-z][a-z-]*:)",
        job_match.group("body"),
        flags=re.MULTILINE | re.DOTALL,
    )

    assert condition_match, f"required CI job {job_name!r} has no trigger guard"
    assert "github.event_name == 'workflow_dispatch'" in condition_match.group("condition")


def test_conftest_isolates_host_dotenv_leaks():
    """Source lock: host .env must not leak APP_BASE_URL / GESH_AUTH_ENABLED.

    File-only so it can run with --noconftest when gesh_core is unavailable.
    """
    text = CONFTEST.read_text(encoding="utf-8")
    assert 'delenv("APP_BASE_URL"' in text
    assert 'delenv("GESH_AUTH_ENABLED"' in text
