"""Nightly SQLite backup: integrity, 0600 dest, prune, fail-closed main.

`ops_backup.py` copies gesh_auth.db and the financial DBs. A skipped integrity
check or a world-readable dest is a P0. Tests pin TARGETS to tmp so they never
touch live databases.
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

import ops_backup as ob


def _make_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    conn.close()


def test_backup_one_copies_and_restricts_mode(tmp_path: Path):
    src = tmp_path / "gesh_auth.db"
    _make_db(src)
    dest_dir = tmp_path / "nightly"
    dest_dir.mkdir()

    assert ob.backup_one(src, dest_dir, "20260902_1000") is True
    dest = dest_dir / "gesh_auth_20260902_1000.db"
    assert dest.is_file()
    assert dest.stat().st_mode & 0o777 == 0o600
    conn = sqlite3.connect(dest)
    assert conn.execute("SELECT x FROM t").fetchone() == (1,)
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    conn.close()


def test_integrity_result_requires_literal_ok():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (x INTEGER)")
    assert ob._integrity_result(conn) == "ok"
    conn.close()

    class _Bad:
        def execute(self, _sql):
            class _Row:
                def fetchone(self):
                    return ("2nd reference to page 2",)

            return _Row()

    assert ob._integrity_result(_Bad()) == "2nd reference to page 2"  # type: ignore[arg-type]


def test_backup_one_integrity_failure_returns_false(tmp_path: Path, monkeypatch, capsys):
    src = tmp_path / "src.db"
    _make_db(src)
    dest_dir = tmp_path / "nightly"
    dest_dir.mkdir()

    monkeypatch.setattr(ob, "_integrity_result", lambda _conn: "2nd reference to page 2")
    assert ob.backup_one(src, dest_dir, "stamp") is False
    logged = capsys.readouterr().out
    assert "FAIL integrity" in logged
    assert "2nd reference to page 2" in logged


def test_backup_one_missing_source_fail_soft(tmp_path: Path, capsys):
    dest_dir = tmp_path / "nightly"
    dest_dir.mkdir()
    assert ob.backup_one(tmp_path / "missing.db", dest_dir, "stamp") is False
    assert "FAIL" in capsys.readouterr().out
    assert list(dest_dir.glob("*.db")) == []


def test_prune_keeps_fresh_and_deletes_older_than_retention(tmp_path: Path):
    dest_dir = tmp_path / "nightly"
    dest_dir.mkdir()
    old = dest_dir / "old.db"
    fresh = dest_dir / "fresh.db"
    keep_other = dest_dir / "notes.txt"
    old.write_bytes(b"x")
    fresh.write_bytes(b"y")
    keep_other.write_text("not a db")
    old_age = time.time() - (ob.RETENTION_DAYS + 1) * 86400
    os.utime(old, (old_age, old_age))
    ob.prune(dest_dir)
    assert not old.exists()
    assert fresh.exists()
    assert keep_other.exists()


def test_main_skips_missing_and_fails_on_integrity(tmp_path: Path, monkeypatch):
    present = tmp_path / "ok.db"
    _make_db(present)
    missing = tmp_path / "gone.db"
    monkeypatch.setattr(ob, "TARGETS", [missing, present])

    dest_dir = present.parent / "backups" / "nightly"
    assert ob.main() == 0
    copies = list(dest_dir.glob("ok_*.db"))
    assert len(copies) == 1
    assert copies[0].stat().st_mode & 0o777 == 0o600
    assert dest_dir.stat().st_mode & 0o777 == 0o700

    def _fail(*_a, **_k):
        return False

    monkeypatch.setattr(ob, "backup_one", _fail)
    assert ob.main() == 1
