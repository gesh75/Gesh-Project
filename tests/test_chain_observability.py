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


def test_direct_mcp_failure_alerts_once_before_claude_fallback():
    """Tier 1 dying must not stay a log line. A later tier can still succeed,
    so the all-retries-failed alert never fires and the Claude courier keeps
    spending. Alert once, before fetch_snapshot_mcp.sh, and not on every retry."""
    src = (REPO / "run_daily_analysis.sh").read_text()
    body = src.split("fetch_snapshot_once()", 1)[1].split("SNAP_OK=0", 1)[0]
    fail = body.index('echo "direct MCP fetch failed — falling back to claude -p MCP path"')
    alert = body.index("tg_alert", fail)
    fallback = body.index("fetch_snapshot_mcp.sh", fail)
    assert fail < alert < fallback
    assert "DIRECT_MCP_ALERT_SENT" in body
    assert '[ "${DIRECT_MCP_ALERT_SENT:-0}" -ne 1 ]' in body
    assert "step 1/5: direct MCP fetch failed" in body
    assert "snapshot fetch failed after all retries" in src


def test_start_gesh_main_invokes_venv_python_absolutely():
    src = (REPO / "start_gesh_main.sh").read_text()
    commands = "\n".join(
        line for line in src.splitlines() if line.strip() and not line.lstrip().startswith("#")
    )
    assert "source venv/bin/activate" not in commands
    assert "venv/bin/python3" in commands
    assert "sys.prefix" in src
    assert ".local/bin" in commands, (
        "launchd PATH omits ~/.local/bin; without it grok_available() is False "
        "and dashboard LLM failover cannot reach the Grok CLI"
    )
