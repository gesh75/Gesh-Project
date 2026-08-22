"""Chain scripts must actually emit logs, and the :9000 launcher must not
`source venv/bin/activate` (rule #57)."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_chain_scripts_configure_logging():
    for name in ("run_engines2.py", "run_ai2.py", "run_paper_gate2.py"):
        src = (REPO / name).read_text()
        assert "logging.basicConfig" in src, (
            f"{name} never configures logging — every logger.info in imported "
            "engines is dropped (price_source tags included)"
        )


def test_start_gesh_main_invokes_venv_python_absolutely():
    src = (REPO / "start_gesh_main.sh").read_text()
    commands = "\n".join(
        line for line in src.splitlines() if line.strip() and not line.lstrip().startswith("#")
    )
    assert "source venv/bin/activate" not in commands
    assert "venv/bin/python3" in commands
    assert "sys.prefix" in src
