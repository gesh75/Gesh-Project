import os
import sqlite3
from pathlib import Path
from unittest.mock import patch

import ops_backup


def test_main_restricts_backup_permissions(tmp_path: Path) -> None:
    source = tmp_path / "gesh_auth.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE users (email TEXT)")
        connection.execute("INSERT INTO users VALUES ('private@example.com')")

    backup_dir = tmp_path / "backups" / "nightly"
    backup_dir.mkdir(parents=True, mode=0o755)

    old_umask = os.umask(0o022)
    try:
        with patch.object(ops_backup, "TARGETS", [source]):
            assert ops_backup.main() == 0
    finally:
        os.umask(old_umask)

    backup = next(backup_dir.glob("*.db"))
    assert backup_dir.stat().st_mode & 0o777 == 0o700
    assert backup.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(backup) as connection:
        assert connection.execute("SELECT email FROM users").fetchone() == (
            "private@example.com",
        )
