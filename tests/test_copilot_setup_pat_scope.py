"""Copilot setup must not leave GESH_CORE_PAT live for branch-controlled installs.

``tests.yml`` already scopes the PAT to a throwaway GIT_CONFIG_GLOBAL and
installs only the source-pinned gesh-core line while it is set. The Copilot
setup workflow used to write the token into the runner's default ~/.gitconfig
and then ``pip install -r requirements.lock`` — so a workflow_dispatch (or
Copilot checkout) on a branch that adds an install-hook dependency could steal
the PAT. These assertions lock the two-step install.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "copilot-setup-steps.yml"


def _workflow_text() -> str:
    return WORKFLOW.read_text()


def test_copilot_setup_disables_checkout_credentials():
    assert "persist-credentials: false" in _workflow_text()


def test_copilot_setup_uses_ephemeral_git_config_for_pat():
    text = _workflow_text()
    assert "GIT_CONFIG_GLOBAL" in text
    assert "trap 'rm -f \"$GIT_CONFIG_GLOBAL\"' EXIT" in text


def test_copilot_setup_pins_gesh_core_source_before_pat_install():
    text = _workflow_text()
    assert "gesh-core @ git+https://github.com/gesh75/gesh-core.git@" in text
    assert "CORE_SPEC=\"$(grep '^gesh-core @' requirements.lock)\"" in text


def _run_script_lines(text: str) -> list[str]:
    """Executable workflow lines only — comments may mention the old hole."""
    return [
        line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")
    ]


def test_copilot_setup_remaining_deps_install_excludes_gesh_core():
    text = _workflow_text()
    script = "\n".join(_run_script_lines(text))
    assert "grep -v '^gesh-core @' requirements.lock" in script
    # A lock-wide install while the PAT is in ~/.gitconfig is the hole.
    assert "pip install -r requirements.lock" not in script


def test_copilot_setup_remaining_deps_step_has_no_pat_env():
    """The no-credential step must not receive GESH_CORE_PAT at all."""
    text = _workflow_text()
    remaining = text.split("Install remaining runtime + test deps", 1)[1]
    assert "GESH_CORE_PAT" not in remaining
