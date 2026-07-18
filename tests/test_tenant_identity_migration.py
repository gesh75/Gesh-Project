"""Schema-v2 migration from mutable email ownership to immutable user IDs.

All tests use temporary SQLite databases, including backup and failure cases.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import stat
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Iterator

import pytest

import auth_users as au


@pytest.fixture
def auth_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[Path, None, None]:
    path = tmp_path / "auth.db"
    monkeypatch.setenv("GESH_AUTH_DB", str(path))
    monkeypatch.setenv("GESH_AUTH_SECRET", "test-secret-deterministic")
    monkeypatch.delenv("GESH_AUTH_ENABLED", raising=False)
    monkeypatch.setattr(au, "AUTH_DB_PATH", path)
    yield path


def _create_legacy_db(path: Path, *, include_orphans: bool = False) -> None:
    with sqlite3.connect(path) as con:
        con.executescript(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT '',
                last_login_at TEXT
            );
            CREATE TABLE used_magic_tokens (
                token_hash TEXT PRIMARY KEY,
                used_at TEXT NOT NULL
            );
            CREATE TABLE watchlist (
                email TEXT NOT NULL,
                symbol TEXT NOT NULL,
                added_at TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (email, symbol)
            );
            CREATE TABLE customer_positions (
                email TEXT NOT NULL,
                symbol TEXT NOT NULL,
                shares REAL NOT NULL,
                avg_cost REAL,
                updated_at TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (email, symbol)
            );
            CREATE TABLE customer_analyses (
                email TEXT NOT NULL,
                result_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX idx_customer_analyses_email
                ON customer_analyses (email, created_at);
            CREATE TABLE usage_events (
                email TEXT NOT NULL,
                event TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT '',
                at TEXT NOT NULL
            );
            CREATE INDEX idx_usage_events_at ON usage_events (at);
            CREATE TABLE customer_book_prefs (
                email TEXT PRIMARY KEY,
                equal_weight INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT ''
            );

            INSERT INTO users
                (email, password_hash, is_active, created_at)
                VALUES ('owner@example.com', 'legacy-hash', 1, '2026-01-01');
            INSERT INTO watchlist VALUES ('owner@example.com', 'NVDA', '2026-01-01');
            INSERT INTO customer_positions
                VALUES ('owner@example.com', 'AAPL', 3, 100, '2026-01-01');
            INSERT INTO customer_analyses
                VALUES ('owner@example.com', '{"score": 42}', '2026-01-01');
            INSERT INTO usage_events
                VALUES ('owner@example.com', 'ticker_card', 'NVDA', '2026-01-01');
            INSERT INTO customer_book_prefs
                VALUES ('owner@example.com', 1, '2026-01-01');
            """
        )
        if include_orphans:
            con.executescript(
                """
                INSERT INTO watchlist VALUES ('orphan@example.com', 'TSLA', '2026-01-01');
                INSERT INTO customer_positions
                    VALUES ('orphan@example.com', 'META', 4, 200, '2026-01-01');
                INSERT INTO customer_analyses
                    VALUES ('orphan@example.com', '{"score": 99}', '2026-01-01');
                INSERT INTO usage_events
                    VALUES ('orphan@example.com', 'ticker_card', 'TSLA', '2026-01-01');
                INSERT INTO customer_book_prefs
                    VALUES ('orphan@example.com', 1, '2026-01-01');
                """
            )


def _backup_path(path: Path) -> Path:
    return path.with_suffix(".pre-v2.bak")


def _manifest_path(path: Path) -> Path:
    backup = _backup_path(path)
    return backup.with_name(f"{backup.name}.manifest.json")


def _create_unrelated_legacy_db(path: Path) -> None:
    _create_legacy_db(path)
    with sqlite3.connect(path) as con:
        for table in _child_tables():
            con.execute(
                f"UPDATE {table} SET email='unrelated@example.com' "
                "WHERE email='owner@example.com'"
            )
        con.execute(
            "UPDATE users SET email='unrelated@example.com' "
            "WHERE email='owner@example.com'"
        )


def _is_canonical_uuid(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value
    except (ValueError, AttributeError):
        return False


def _child_tables() -> tuple[str, ...]:
    return (
        "watchlist",
        "customer_positions",
        "customer_analyses",
        "usage_events",
        "customer_book_prefs",
    )


def test_old_email_reuse_cannot_inherit_customer_rows(auth_db: Path) -> None:
    au.init_auth_db()
    au.create_user("old@example.com", "pw")
    original_id = au.resolve_user_id("old@example.com")
    assert original_id is not None
    assert au.add_watch("old@example.com", "NVDA")
    assert au.set_position("old@example.com", "AAPL", 3)

    assert au.rename_user_email("old@example.com", "new@example.com")
    au.create_user("old@example.com", "other")

    assert au.resolve_user_id("new@example.com") == original_id
    assert au.resolve_user_id("old@example.com") != original_id
    assert au.get_watch("new@example.com") == ["NVDA"]
    assert au.get_watch("old@example.com") == []
    assert au.get_positions("old@example.com") == []


def test_child_tables_reference_user_id_with_foreign_keys(auth_db: Path) -> None:
    au.init_auth_db()
    with au._conn() as con:
        for table in _child_tables():
            columns = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
            foreign = con.execute(f"PRAGMA foreign_key_list({table})").fetchall()
            assert "user_id" in columns
            assert "email" not in columns
            assert any(
                row[2] == "users"
                and row[3] == "user_id"
                and row[4] == "user_id"
                and row[6].upper() == "CASCADE"
                for row in foreign
            )


def test_orphaned_legacy_rows_fail_closed_and_backup_stays_intact(auth_db: Path) -> None:
    _create_legacy_db(auth_db, include_orphans=True)

    with pytest.raises(sqlite3.IntegrityError, match="orphan"):
        au.init_auth_db()

    backup = _backup_path(auth_db)
    assert backup.exists()
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    with sqlite3.connect(auth_db) as source:
        assert source.execute("PRAGMA user_version").fetchone()[0] == 0
        assert "user_id" not in {
            row[1] for row in source.execute("PRAGMA table_info(users)")
        }
        assert source.execute(
            "SELECT COUNT(*) FROM watchlist WHERE email='orphan@example.com'"
        ).fetchone()[0] == 1
    with sqlite3.connect(backup) as saved:
        assert saved.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert saved.execute(
            "SELECT COUNT(*) FROM watchlist WHERE email='orphan@example.com'"
        ).fetchone()[0] == 1


def test_clean_legacy_rows_migrate_once_with_one_pre_v2_backup(auth_db: Path) -> None:
    _create_legacy_db(auth_db)

    au.init_auth_db()
    backup = _backup_path(auth_db)
    manifest_path = _manifest_path(auth_db)
    assert backup.exists()
    assert manifest_path.exists()
    assert stat.S_IMODE(manifest_path.stat().st_mode) == 0o600
    original_backup = backup.read_bytes()
    original_manifest = manifest_path.read_bytes()
    manifest = json.loads(original_manifest)
    assert manifest["manifest_version"] == 1
    assert manifest["source_user_version"] < 2
    assert len(manifest["source_identity_sha256"]) == 64
    assert len(manifest["source_schema_sha256"]) == 64
    assert len(manifest["snapshot_sha256"]) == 64
    original_id = au.resolve_user_id("owner@example.com")
    assert original_id is not None
    assert au.get_user_by_id(original_id)["email"] == "owner@example.com"
    assert au.get_watch("owner@example.com") == ["NVDA"]
    assert au.get_positions("owner@example.com") == [
        {"symbol": "AAPL", "shares": 3.0, "avg_cost": 100.0}
    ]
    assert au.get_latest_book_analysis("owner@example.com")["score"] == 42
    assert au.get_equal_weight("owner@example.com") is True

    with au._conn() as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 2
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
        for table in _child_tables():
            assert con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1

    au.init_auth_db()
    assert au.resolve_user_id("owner@example.com") == original_id
    assert au.get_watch("owner@example.com") == ["NVDA"]
    assert backup.read_bytes() == original_backup
    assert manifest_path.read_bytes() == original_manifest

    with sqlite3.connect(backup) as saved:
        assert saved.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert saved.execute("PRAGMA user_version").fetchone()[0] == 0
        assert "email" in {
            row[1] for row in saved.execute("PRAGMA table_info(watchlist)")
        }


def test_brand_new_database_does_not_create_pre_v2_backup(auth_db: Path) -> None:
    au.init_auth_db()

    assert not _backup_path(auth_db).exists()
    assert not _manifest_path(auth_db).exists()


def test_empty_existing_backup_fails_closed(auth_db: Path) -> None:
    _create_legacy_db(auth_db)
    _backup_path(auth_db).touch()

    with pytest.raises(sqlite3.DatabaseError, match="schema"):
        au.init_auth_db()

    with sqlite3.connect(auth_db) as source:
        assert source.execute("PRAGMA user_version").fetchone()[0] == 0
        assert "email" in {
            row[1] for row in source.execute("PRAGMA table_info(watchlist)")
        }


def test_same_schema_different_data_backup_without_manifest_fails_closed(
    auth_db: Path,
) -> None:
    _create_legacy_db(auth_db)
    unrelated = auth_db.parent / "unrelated.db"
    _create_unrelated_legacy_db(unrelated)
    shutil.copyfile(unrelated, _backup_path(auth_db))

    with pytest.raises(sqlite3.DatabaseError, match="logical snapshot|source"):
        au.init_auth_db()

    with sqlite3.connect(auth_db) as source:
        assert source.execute("PRAGMA user_version").fetchone()[0] == 0
        assert source.execute("SELECT email FROM users").fetchone()[0] == "owner@example.com"


def test_exact_unmanifested_backup_is_adopted_once(auth_db: Path) -> None:
    _create_legacy_db(auth_db)
    backup = _backup_path(auth_db)
    with sqlite3.connect(auth_db) as source, sqlite3.connect(backup) as saved:
        source.backup(saved)
    backup.chmod(0o600)
    original_backup = backup.read_bytes()

    au.init_auth_db()

    manifest_path = _manifest_path(auth_db)
    assert manifest_path.exists()
    assert stat.S_IMODE(manifest_path.stat().st_mode) == 0o600
    assert backup.read_bytes() == original_backup
    assert json.loads(manifest_path.read_text())["backup_file_sha256"] == au._file_sha256(
        backup
    )
    with sqlite3.connect(auth_db) as source:
        assert source.execute("PRAGMA user_version").fetchone()[0] == 2


def test_backup_promotion_crash_recovers_by_exact_adoption(
    auth_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create_legacy_db(auth_db)
    real_replace = au.os.replace
    failed_once = False

    def _crash_before_manifest(source: Path, destination: Path) -> None:
        nonlocal failed_once
        if str(destination).endswith(".manifest.json") and not failed_once:
            failed_once = True
            raise OSError("simulated crash before manifest promotion")
        real_replace(source, destination)

    monkeypatch.setattr(au.os, "replace", _crash_before_manifest)
    with pytest.raises(OSError, match="simulated crash"):
        au.init_auth_db()

    assert _backup_path(auth_db).exists()
    assert not _manifest_path(auth_db).exists()
    with sqlite3.connect(auth_db) as source:
        assert source.execute("PRAGMA user_version").fetchone()[0] == 0

    monkeypatch.setattr(au.os, "replace", real_replace)
    au.init_auth_db()

    assert _manifest_path(auth_db).exists()
    with sqlite3.connect(auth_db) as source:
        assert source.execute("PRAGMA user_version").fetchone()[0] == 2


def test_backup_and_manifest_promotions_are_fsynced(
    auth_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create_legacy_db(auth_db)
    synced_files: list[Path] = []
    synced_directories: list[Path] = []

    monkeypatch.setattr(
        au,
        "_fsync_file",
        lambda path: synced_files.append(Path(path)),
    )
    monkeypatch.setattr(
        au,
        "_fsync_directory",
        lambda path: synced_directories.append(Path(path)),
    )

    au.init_auth_db()

    assert any(path.name.endswith(".pre-v2.bak") for path in synced_files)
    assert any(path.name.endswith(".manifest.json") for path in synced_files)
    assert synced_directories.count(auth_db.parent) >= 2


def test_symlink_backup_is_rejected_before_adoption(auth_db: Path) -> None:
    _create_legacy_db(auth_db)
    real_backup = auth_db.parent / "real-backup.db"
    with sqlite3.connect(auth_db) as source, sqlite3.connect(real_backup) as saved:
        source.backup(saved)
    real_backup.chmod(0o600)
    os.symlink(real_backup, _backup_path(auth_db))

    with pytest.raises(
        sqlite3.DatabaseError, match="must be a regular file|symbolic link"
    ):
        au.init_auth_db()


def test_symlink_manifest_is_rejected(auth_db: Path) -> None:
    _create_legacy_db(auth_db, include_orphans=True)
    with pytest.raises(sqlite3.IntegrityError, match="orphan"):
        au.init_auth_db()

    manifest = _manifest_path(auth_db)
    real_manifest = auth_db.parent / "real-manifest.json"
    manifest.replace(real_manifest)
    os.symlink(real_manifest, manifest)

    with pytest.raises(
        sqlite3.DatabaseError, match="must be a regular file|symbolic link"
    ):
        au.init_auth_db()


def test_non_regular_backup_is_rejected(auth_db: Path) -> None:
    _create_legacy_db(auth_db)
    _backup_path(auth_db).mkdir()

    with pytest.raises(sqlite3.DatabaseError, match="regular file"):
        au.init_auth_db()


def test_backup_manifest_is_bound_to_its_source_path(
    auth_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_a = auth_db.parent / "source-a.db"
    _create_legacy_db(source_a, include_orphans=True)
    monkeypatch.setattr(au, "AUTH_DB_PATH", source_a)
    with pytest.raises(sqlite3.IntegrityError, match="orphan"):
        au.init_auth_db()

    source_a_manifest = _manifest_path(source_a)
    if not source_a_manifest.exists():
        pytest.fail("source-linked backup manifest was not created")

    _create_unrelated_legacy_db(auth_db)
    shutil.copyfile(_backup_path(source_a), _backup_path(auth_db))
    shutil.copyfile(source_a_manifest, _manifest_path(auth_db))
    monkeypatch.setattr(au, "AUTH_DB_PATH", auth_db)

    with pytest.raises(sqlite3.DatabaseError, match="source identity"):
        au.init_auth_db()


def test_original_backup_remains_valid_after_operator_cleans_orphans(
    auth_db: Path,
) -> None:
    _create_legacy_db(auth_db, include_orphans=True)
    with pytest.raises(sqlite3.IntegrityError, match="orphan"):
        au.init_auth_db()

    backup = _backup_path(auth_db)
    manifest = _manifest_path(auth_db)
    original_backup = backup.read_bytes()
    original_manifest = manifest.read_bytes()
    with sqlite3.connect(auth_db) as source:
        for table in _child_tables():
            source.execute(
                f"DELETE FROM {table} WHERE email='orphan@example.com'"
            )

    au.init_auth_db()

    assert backup.read_bytes() == original_backup
    assert manifest.read_bytes() == original_manifest
    with sqlite3.connect(backup) as saved:
        assert saved.execute(
            "SELECT COUNT(*) FROM watchlist WHERE email='orphan@example.com'"
        ).fetchone()[0] == 1


def test_new_backup_is_private_when_chmod_is_unavailable(
    auth_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create_legacy_db(auth_db)

    def _deny_chmod(path: Path, mode: int) -> None:
        raise PermissionError("chmod unavailable")

    monkeypatch.setattr(au.os, "chmod", _deny_chmod)
    au.init_auth_db()

    assert stat.S_IMODE(_backup_path(auth_db).stat().st_mode) == 0o600


def test_insecure_existing_backup_fails_closed_when_chmod_is_unavailable(
    auth_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create_legacy_db(auth_db)
    backup = _backup_path(auth_db)
    with sqlite3.connect(auth_db) as source, sqlite3.connect(backup) as saved:
        source.backup(saved)
    backup.chmod(0o644)

    def _deny_chmod(path: Path, mode: int) -> None:
        raise PermissionError("chmod unavailable")

    monkeypatch.setattr(au.os, "chmod", _deny_chmod)

    with pytest.raises(PermissionError, match="permissions"):
        au.init_auth_db()

    with sqlite3.connect(auth_db) as source:
        assert source.execute("PRAGMA user_version").fetchone()[0] == 0


def test_schema_v2_reads_do_not_compete_for_writer_lock(
    auth_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    au.init_auth_db()
    monkeypatch.setattr(au, "_BUSY_TIMEOUT_MS", 25)
    blocker = sqlite3.connect(auth_db)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        with au._conn() as con:
            assert con.execute("PRAGMA user_version").fetchone()[0] == 2
    finally:
        blocker.rollback()
        blocker.close()


def test_schema_v2_fast_path_rejects_foreign_key_violations(auth_db: Path) -> None:
    au.init_auth_db()
    with sqlite3.connect(auth_db) as corrupt:
        corrupt.execute("PRAGMA foreign_keys = OFF")
        corrupt.execute(
            "INSERT INTO watchlist (user_id, symbol, added_at) VALUES (?,?,?)",
            (str(uuid.uuid4()), "ORPHAN", "2026-01-01"),
        )

    with pytest.raises(sqlite3.IntegrityError, match="foreign key"):
        with au._conn():
            pass

    with sqlite3.connect(auth_db) as source:
        assert source.execute(
            "SELECT COUNT(*) FROM watchlist WHERE symbol='ORPHAN'"
        ).fetchone()[0] == 1


def test_migration_replaces_invalid_and_duplicate_legacy_user_ids(
    auth_db: Path,
) -> None:
    duplicate = str(uuid.uuid4())
    _create_legacy_db(auth_db)
    with sqlite3.connect(auth_db) as legacy:
        legacy.execute("ALTER TABLE users ADD COLUMN user_id TEXT")
        legacy.execute(
            "UPDATE users SET user_id='not-a-canonical-uuid' "
            "WHERE email='owner@example.com'"
        )
        legacy.execute(
            "INSERT INTO users (email, password_hash, is_active, created_at, user_id) "
            "VALUES ('first@example.com', 'hash', 1, '2026-01-01', ?)",
            (duplicate,),
        )
        legacy.execute(
            "INSERT INTO users (email, password_hash, is_active, created_at, user_id) "
            "VALUES ('second@example.com', 'hash', 1, '2026-01-01', ?)",
            (duplicate,),
        )

    au.init_auth_db()

    with au._conn() as con:
        identities = {
            row["email"]: row["user_id"]
            for row in con.execute(
                "SELECT email, user_id FROM users ORDER BY email"
            ).fetchall()
        }
    assert identities["first@example.com"] == duplicate
    assert identities["second@example.com"] != duplicate
    assert identities["owner@example.com"] != "not-a-canonical-uuid"
    assert all(_is_canonical_uuid(value) for value in identities.values())
    assert uuid.UUID(identities["second@example.com"]).version == 4
    assert uuid.UUID(identities["owner@example.com"]).version == 4


def test_structurally_v2_database_rejects_noncanonical_user_id(auth_db: Path) -> None:
    au.init_auth_db()
    au.create_user("invalid@example.com", "pw")
    with sqlite3.connect(auth_db) as corrupt:
        corrupt.execute(
            "UPDATE users SET user_id='NOT-A-CANONICAL-UUID' "
            "WHERE email='invalid@example.com'"
        )

    with pytest.raises(sqlite3.DatabaseError, match="canonical UUID"):
        with au._conn():
            pass


def test_structurally_v2_database_rejects_duplicate_ids_behind_fake_index(
    auth_db: Path,
) -> None:
    au.init_auth_db()
    au.create_user("first@example.com", "pw")
    au.create_user("second@example.com", "pw")
    first_id = au.resolve_user_id("first@example.com")
    assert first_id is not None
    with sqlite3.connect(auth_db) as corrupt:
        corrupt.execute("DROP INDEX ux_users_user_id")
        corrupt.execute(
            "UPDATE users SET user_id=? WHERE email='second@example.com'",
            (first_id,),
        )
        corrupt.execute(
            "CREATE INDEX ux_users_user_id ON users(email)"
        )

    with pytest.raises(sqlite3.DatabaseError, match="duplicate|unique user_id"):
        with au._conn():
            pass


@pytest.mark.parametrize(
    "index_sql",
    (
        "CREATE UNIQUE INDEX ux_users_user_id ON users(email)",
        "CREATE UNIQUE INDEX ux_users_user_id ON users(user_id) WHERE is_active=1",
    ),
)
def test_structurally_v2_database_requires_exact_unique_user_id_index(
    auth_db: Path, index_sql: str
) -> None:
    au.init_auth_db()
    au.create_user("first@example.com", "pw")
    au.create_user("second@example.com", "pw")
    with sqlite3.connect(auth_db) as corrupt:
        corrupt.execute("DROP INDEX ux_users_user_id")
        corrupt.execute(index_sql)

    with pytest.raises(sqlite3.DatabaseError, match="unique user_id"):
        with au._conn():
            pass


def test_legacy_owner_email_backfills_one_immutable_owner_marker(
    auth_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GESH_AUTH_ENABLED", "1")
    monkeypatch.setenv("GESH_OWNER_EMAIL", "owner@example.com")
    _create_legacy_db(auth_db)

    au.init_auth_db()
    original_id = au.resolve_user_id("owner@example.com")
    assert original_id is not None
    assert au.rename_user_email("owner@example.com", "renamed@example.com") is True
    au.create_user("owner@example.com", "replacement-pw")

    assert au.is_owner("renamed@example.com") is True
    assert au.is_owner("owner@example.com") is False
    with au._conn() as con:
        markers = con.execute(
            "SELECT value FROM auth_metadata WHERE key='owner_user_id'"
        ).fetchall()
    assert [row[0] for row in markers] == [original_id]


def test_delete_user_cascades_all_customer_rows(auth_db: Path) -> None:
    au.init_auth_db()
    au.create_user("owner@example.com", "pw")
    assert au.add_watch("owner@example.com", "NVDA")
    assert au.set_position("owner@example.com", "AAPL", 3)
    au.save_book_analysis("owner@example.com", {"score": 42})
    au.log_usage("owner@example.com", "ticker_card")
    au.set_equal_weight("owner@example.com", True)

    user_id = au.resolve_user_id("owner@example.com")
    with au._conn() as con:
        con.execute("DELETE FROM users WHERE user_id=?", (user_id,))
        for table in _child_tables():
            assert con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_connections_enable_foreign_keys_and_busy_timeout(auth_db: Path) -> None:
    au.init_auth_db()
    with au._conn() as con:
        assert con.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert con.execute("PRAGMA busy_timeout").fetchone()[0] >= 5_000


def test_inactive_user_id_is_not_resolved(auth_db: Path) -> None:
    au.init_auth_db()
    au.create_user("owner@example.com", "pw")
    user_id = au.resolve_user_id("owner@example.com")
    assert user_id is not None
    assert au.get_user_by_id(user_id)["email"] == "owner@example.com"

    au.set_active("owner@example.com", False)

    assert au.resolve_user_id("owner@example.com") is None
    assert au.get_user_by_id(user_id)["is_active"] == 0


def test_get_user_by_id_rejects_noncanonical_values_before_database_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @contextmanager
    def _database_must_not_open() -> Iterator[sqlite3.Connection]:
        raise AssertionError("invalid user IDs must be rejected before database access")
        yield  # pragma: no cover

    monkeypatch.setattr(au, "_conn", _database_must_not_open)
    canonical = str(uuid.uuid4())
    for value in ("arbitrary", f" {canonical}", canonical.upper(), "{canonical}"):
        assert au.get_user_by_id(value) is None
