"""Regression checks for required CI gate trigger coverage."""

import re
import subprocess
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


NOTION_CONTROL = Path(__file__).parents[1] / ".github" / "workflows" / "notion-control-update.yml"


def test_notion_control_update_truncates_summary_without_pipefail_sigpipe():
    """Long squash-merge messages SIGPIPE `printf | head` under `set -o pipefail`.

    PR #246 / run 33666867623 failed at `printf: write error: Broken pipe`
    because head closed the pipe after 1800 bytes.
    """
    workflow = NOTION_CONTROL.read_text(encoding="utf-8")
    assert "${SUMMARY:0:1800}" in workflow
    assert "printf '%s' \"$SUMMARY\" | head" not in workflow

    # Same shape as PR #246's squash-merge body: many newlines, >8KiB.
    new = subprocess.run(
        [
            "bash",
            "-c",
            "set -euo pipefail\n"
            "SUMMARY=$(python3 -c 'print(\"feat: long\\n\"*4000)')\n"
            "SUMMARY=${SUMMARY:0:1800}\n"
            'echo "len=${#SUMMARY}"',
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert new.stdout.strip() == "len=1800"
