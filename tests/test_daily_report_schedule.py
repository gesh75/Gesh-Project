"""Contract tests for the 10:45 ET daily gesh_report.html job.

Claude's Cowork "Gesh daily portfolio analysis" card used to pick up the
HTML report around 10:45 ET. launchd replaces that card: the 10:30 chain
writes the file; com.gesh.daily-report at 10:45 publishes or retries it.
"""

from __future__ import annotations

import os
import plistlib
import re
import stat
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "daily_report_et.sh"
NARRATE = ROOT / "scripts" / "narrate_daily_et.sh"
ET_GATE = ROOT / "scripts" / "et_gate.sh"
PLIST = ROOT / "launchd" / "com.gesh.daily-report.plist"
CHAIN_WRAPPER = ROOT / "scripts" / "daily_analysis_et.sh"


def _strip_shell_comments(text: str) -> str:
    lines = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        lines.append(re.sub(r"\s+#.*$", "", line))
    return "\n".join(lines)


def test_daily_report_plist_is_et_gated_cadence_not_wall_clock() -> None:
    data = plistlib.loads(PLIST.read_bytes())
    assert data["Label"] == "com.gesh.daily-report"
    assert data["StartInterval"] == 600
    assert "StartCalendarInterval" not in data
    assert data["KeepAlive"] is False
    args = data["ProgramArguments"]
    assert args[0] == "/bin/bash"
    assert args[1].endswith("scripts/daily_report_et.sh")
    path = data["EnvironmentVariables"]["PATH"]
    assert "/.local/bin" in path
    assert data["StandardOutPath"] == "/tmp/gesh_daily_report.log"


def test_daily_report_wrapper_is_weekday_1045_et_and_executable() -> None:
    text = SCRIPT.read_text()
    body = _strip_shell_comments(text)
    assert os.access(SCRIPT, os.X_OK)
    assert 'et_gate_in_window "$TARGET_ET" "$TOL" mon-fri' in body
    assert "${DAILY_REPORT_ET:-10:45}" in text
    assert "${DAILY_REPORT_TOL:-90}" in text
    assert "${DAILY_REPORT_WAIT_S:-2400}" in text
    assert "mon-fri" in text
    assert "run_daily_analysis.sh" in text
    assert "gesh_report.html" in text
    assert "$HOME/.local/bin" in text
    assert "gesh_daily_chain.lock" in text
    ready = body.find("et_gate_file_is_today_et")
    retry = body.find("run_daily_analysis.sh")
    require = body.find("et_gate_require")
    in_window = body.find("et_gate_in_window")
    assert 0 <= in_window < require
    assert 0 <= ready < retry
    assert 'source "$REPO/scripts/et_gate.sh"' in body
    # Recovery path must inherit the scheduled LLM order and must NOT stamp
    # before exec — 2026-08-26 burned the day on a failed retry.
    no_fresh = body.find("no fresh")
    assert no_fresh > 0
    assert "et_gate_export_chain_llm" in body[no_fresh:]
    assert "et_gate_require" not in body[no_fresh:]
    assert "GESH_ET_GATE_STAMP" in body[no_fresh:]


def test_ten_thirty_chain_wrapper_stays_primary() -> None:
    text = CHAIN_WRAPPER.read_text()
    body = _strip_shell_comments(text)
    assert "et_gate_in_window" in body
    assert "et_gate_already_stamped" in body
    assert "GESH_ET_GATE_STAMP" in body
    assert "et_gate_require" not in body
    assert "et_gate_export_chain_llm" in body
    gate = ET_GATE.read_text()
    assert 'GESH_CHAIN_LLM_FAILOVER:-xai,gemini,grok' in gate
    assert 'GESH_CHAIN_GROK_TIMEOUT_S:-180' in gate
    assert 'GESH_CHAIN_XAI_TIMEOUT_S:-480' in gate
    assert 'GESH_CHAIN_GEMINI_TIMEOUT_S:-180' in gate


def test_chain_script_does_not_abort_on_ai_failure() -> None:
    text = (ROOT / "run_daily_analysis.sh").read_text()
    body = _strip_shell_comments(text)
    assert "FATAL: run_ai2 failed" not in body
    assert "AI_FAILED=1" in body
    assert "run_ai2.py" in body
    assert "gen_report2.py" in body
    ai = body.find("run_ai2.py")
    gen = body.find("gen_report2.py")
    assert 0 <= ai < gen
    assert "GESH_ET_GATE_STAMP" in body


def test_narrate_does_not_consume_stamp_before_todays_report() -> None:
    """A 10:50±20 window includes 10:30. Stamping then is a silent no-briefing day."""
    body = _strip_shell_comments(NARRATE.read_text())
    assert 'source "$REPO/scripts/et_gate.sh"' in body
    assert "$TRADING/scripts/et_gate.sh" not in body
    in_window = body.find("et_gate_in_window")
    ready = body.find("if ! et_gate_file_is_today_et")
    require = body.find("et_gate_require")
    assert 0 <= in_window < ready < require


def test_et_gate_file_is_today_et_size_and_date(tmp_path: Path) -> None:
    fresh = tmp_path / "gesh_report.html"
    fresh.write_bytes(b"x" * 60000)
    stale = tmp_path / "old.html"
    stale.write_bytes(b"x" * 60000)
    old = time.time() - 3 * 24 * 3600
    os.utime(stale, (old, old))
    tiny = tmp_path / "tiny.html"
    tiny.write_bytes(b"nope")

    def _check(path: Path, min_bytes: int = 60000) -> int:
        return subprocess.run(
            [
                "/bin/bash",
                "-c",
                f'source "{ET_GATE}" && et_gate_file_is_today_et "$1" "$2"',
                "_",
                str(path),
                str(min_bytes),
            ],
            check=False,
        ).returncode

    assert _check(fresh) == 0
    assert _check(stale) == 1
    assert _check(tiny) == 1
    assert _check(tmp_path / "missing.html") == 1


def test_et_gate_in_window_does_not_write_a_stamp(tmp_path: Path) -> None:
    env = {**os.environ, "ET_GATE_STAMP_DIR": str(tmp_path)}
    proc = subprocess.run(
        [
            "/bin/bash",
            "-c",
            f'source "{ET_GATE}" && ET_GATE_LABEL=daily-report-test '
            f'et_gate_in_window "10:45" 12 mon-fri; echo rc:$?',
        ],
        check=False,
        env=env,
        capture_output=True,
        text=True,
    )
    assert "rc:1" in proc.stdout or "rc:0" in proc.stdout
    assert list(tmp_path.iterdir()) == []


def test_et_gate_already_stamped_then_commit(tmp_path: Path) -> None:
    env = {**os.environ, "ET_GATE_STAMP_DIR": str(tmp_path), "ET_GATE_LABEL": "daily-analysis-test"}
    script = f"""
source "{ET_GATE}"
if et_gate_already_stamped; then echo stamped-before; else echo clean; fi
path=$(et_gate_stamp_path)
: > "$path"
if et_gate_already_stamped; then echo stamped-after; fi
"""
    proc = subprocess.run(
        ["/bin/bash", "-c", script],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )
    assert "clean" in proc.stdout
    assert "stamped-after" in proc.stdout
    assert "stamped-before" not in proc.stdout


def test_daily_report_script_mode_bits_are_owner_executable() -> None:
    mode = SCRIPT.stat().st_mode
    assert mode & stat.S_IXUSR
