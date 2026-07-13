"""
tests/test_tenant_rename_migration.py — tenant isolation across email renames.

STOP-SHIP guard: rename_user_email() must move EVERY email-keyed child row
(watchlist, customer_positions, customer_analyses, usage_events,
customer_book_prefs) with the users row, atomically. If it moves only the
users row, a NEW account later registered with the OLD address inherits the
previous customer's data — a cross-tenant leak.

No network. Temp SQLite + fixed secret (same isolation as test_auth_users.py).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Generator

import pytest


@pytest.fixture(autouse=True)
def isolated_auth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator:
    monkeypatch.setenv("GESH_AUTH_DB", str(tmp_path / "auth.db"))
    monkeypatch.setenv("GESH_AUTH_SECRET", "test-secret-deterministic")
    monkeypatch.delenv("GESH_AUTH_ENABLED", raising=False)
    import auth_users as au
    monkeypatch.setattr(au, "AUTH_DB_PATH", tmp_path / "auth.db")
    au.init_auth_db()
    yield


import auth_users as au

OLD = "alice@x.com"
NEW = "alice-new@x.com"


def _email_keyed_tables() -> list[str]:
    """Every table in the live schema (except users) that carries an email column —
    derived from the DB itself so a new table added to _DDL is caught automatically."""
    con = sqlite3.connect(au.AUTH_DB_PATH)
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        return [t for t in tables if t != "users"
                and any(row[1] == "email" for row in con.execute(f"PRAGMA table_info({t})"))]
    finally:
        con.close()


def _rows_for(email: str, table: str) -> int:
    con = sqlite3.connect(au.AUTH_DB_PATH)
    try:
        return con.execute(f"SELECT COUNT(*) FROM {table} WHERE email=?", (email,)).fetchone()[0]
    finally:
        con.close()


def _seed_full_tenant(email: str) -> None:
    """One row in every email-keyed child table for the user."""
    au.create_user(email, "pw")
    assert au.add_watch(email, "SPY")
    assert au.set_position(email, "MSTR", 10, 250.0)
    au.save_book_analysis(email, {"score": 42})
    au.log_usage(email, "ticker_card", "SPY")
    au.set_equal_weight(email, True)


# ── (a) rename migrates every child table ─────────────────────────────────────

class TestRenameMigratesChildRows:
    def test_all_child_rows_move_to_new_email(self):
        _seed_full_tenant(OLD)
        assert au.rename_user_email(OLD, NEW) is True
        # Data is reachable under the NEW email through the public API…
        assert au.get_watch(NEW) == ["SPY"]
        assert au.get_positions(NEW) == [{"symbol": "MSTR", "shares": 10.0, "avg_cost": 250.0}]
        assert au.get_latest_book_analysis(NEW)["score"] == 42
        assert au.usage_count_24h(NEW, "ticker_card") == 1
        assert au.get_equal_weight(NEW) is True
        # …and the OLD email owns ZERO rows in ANY email-keyed table.
        for table in _email_keyed_tables():
            assert _rows_for(OLD, table) == 0, f"{table} still keyed by the old email"

    def test_migration_list_covers_every_email_keyed_table(self):
        # Fails when a new email-keyed table lands in _DDL without joining the
        # rename migration — the exact omission that caused the leak.
        assert set(au._EMAIL_KEYED_TABLES) == set(_email_keyed_tables())

    def test_rename_is_atomic_on_midway_failure(self, monkeypatch):
        _seed_full_tenant(OLD)
        # A table whose UPDATE always aborts, appended LAST — the users row and the
        # real child tables are updated first, so a raise here proves rollback.
        con = sqlite3.connect(au.AUTH_DB_PATH)
        con.execute("CREATE TABLE boom (email TEXT)")
        con.execute("CREATE TRIGGER boom_no_update BEFORE UPDATE ON boom "
                    "BEGIN SELECT RAISE(ABORT, 'boom'); END")
        con.execute("INSERT INTO boom VALUES (?)", (OLD,))
        con.commit()
        con.close()
        monkeypatch.setattr(au, "_EMAIL_KEYED_TABLES", (*au._EMAIL_KEYED_TABLES, "boom"))
        with pytest.raises(sqlite3.Error):
            au.rename_user_email(OLD, NEW)
        # Nothing moved — the whole transaction rolled back.
        assert au.get_user(OLD) is not None
        assert au.get_user(NEW) is None
        assert au.get_watch(OLD) == ["SPY"]
        assert au.get_watch(NEW) == []


# ── (b) a later signup with the old address inherits NOTHING ──────────────────

class TestOldEmailIsCleanForNewTenant:
    def test_new_user_with_old_email_sees_empty_tenant(self):
        _seed_full_tenant(OLD)
        assert au.rename_user_email(OLD, NEW) is True
        au.create_user(OLD, "different-pw")   # a brand-new, unrelated customer
        assert au.get_watch(OLD) == []
        assert au.get_positions(OLD) == []
        assert au.get_latest_book_analysis(OLD) is None
        assert au.usage_count_24h(OLD, "ticker_card") == 0
        assert au.get_equal_weight(OLD) is False


# ── (c) rename onto an email that already has rows is REJECTED (no merge) ─────

class TestRenameOntoExistingRowsRejected:
    def test_target_users_row_rejects_rename(self):
        _seed_full_tenant(OLD)
        au.create_user(NEW, "pw2")
        assert au.rename_user_email(OLD, NEW) is False
        # Source tenant untouched.
        assert au.get_user(OLD) is not None
        assert au.get_watch(OLD) == ["SPY"]

    def test_target_orphan_child_rows_reject_rename(self):
        # Child rows CAN exist without a users row (no FK). Renaming onto them
        # would silently merge two tenants' data — must be refused instead.
        _seed_full_tenant(OLD)
        assert au.add_watch(NEW, "QQQ")       # orphan tenant data at the target
        assert au.rename_user_email(OLD, NEW) is False
        # Neither side changed: no merge, no move.
        assert au.get_user(OLD) is not None
        assert au.get_user(NEW) is None
        assert au.get_watch(OLD) == ["SPY"]
        assert au.get_watch(NEW) == ["QQQ"]
