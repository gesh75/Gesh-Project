"""Guard: the 30-min dashboard snapshot must not depend on claude -p as primary."""
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_dashboard_snapshot_prefers_llm_free_direct_mcp() -> None:
    src = (REPO / "refresh_dashboard_snapshot.sh").read_text()
    assert "fetch_snapshot_direct.py" in src
    direct = src.find("fetch_snapshot_direct.py")
    courier = src.find("fetch_snapshot_mcp.sh")
    assert 0 <= direct < courier, "claude -p courier must be fallback, not primary"
    assert 'PY="${GESH_PY:-$DIR/venv/bin/python}"' in src


def test_dashboard_snapshot_plist_has_no_wall_clock() -> None:
    import plistlib

    data = plistlib.loads((REPO / "com.gesh.dashboard-snapshot.plist").read_bytes())
    assert "StartCalendarInterval" not in data
    assert data.get("StartInterval") == 1800
