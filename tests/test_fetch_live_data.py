import json
import runpy
import stat
import sys

import pytest

import fetch_live_data


def test_write_cache_typical_umask_creates_private_file(tmp_path, monkeypatch):
    cache_file = tmp_path / "gesh_live_cache.json"
    monkeypatch.setattr(fetch_live_data, "CACHE_FILE", cache_file)

    previous_umask = fetch_live_data.os.umask(0o022)
    try:
        fetch_live_data.write_cache({"holdings": [{"symbol": "FAKE"}]})
    finally:
        fetch_live_data.os.umask(previous_umask)

    assert stat.S_IMODE(cache_file.stat().st_mode) == 0o600
    assert json.loads(cache_file.read_text())["holdings"] == [{"symbol": "FAKE"}]


def test_snapshot_cli_without_runtime_input_fails_closed(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", [fetch_live_data.__file__, "--snapshot"])

    with pytest.raises(SystemExit, match="2"):
        runpy.run_path(fetch_live_data.__file__, run_name="__main__")

    assert "has no embedded portfolio data" in capsys.readouterr().err
