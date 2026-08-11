"""Regression checks for required CI gate trigger coverage."""

import re
from pathlib import Path

import pytest

WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "tests.yml"


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
