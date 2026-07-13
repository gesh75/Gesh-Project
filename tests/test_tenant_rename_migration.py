"""Tenant isolation across email renames after schema-v2 UUID migration."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Generator

import pytest

import auth_users as au
import billing


@pytest.fixture(autouse=True)
def isolated_auth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    monkeypatch.setenv("GESH_AUTH_DB", str(tmp_path / "auth.db"))
    monkeypatch.setenv("GESH_AUTH_SECRET", "test-secret-deterministic")
    monkeypatch.delenv("GESH_AUTH_ENABLED", raising=False)
    monkeypatch.setattr(au, "AUTH_DB_PATH", tmp_path / "auth.db")
    au.init_auth_db()
    yield


OLD = "alice@x.com"
NEW = "alice-new@x.com"


def _child_tables() -> tuple[str, ...]:
    return (
        "watchlist",
        "customer_positions",
        "customer_analyses",
        "usage_events",
        "customer_book_prefs",
    )


def _seed_full_tenant(email: str) -> str:
    au.create_user(email, "pw")
    user_id = au.resolve_user_id(email)
    assert user_id is not None
    assert au.add_watch(email, "SPY")
    assert au.set_position(email, "MSTR", 10, 250.0)
    au.save_book_analysis(email, {"score": 42})
    au.log_usage(email, "ticker_card", "SPY")
    au.set_equal_weight(email, True)
    return user_id


def _owned_user_ids() -> dict[str, list[str]]:
    with au._conn() as con:
        return {
            table: [row[0] for row in con.execute(f"SELECT user_id FROM {table}").fetchall()]
            for table in _child_tables()
        }


def test_rename_keeps_immutable_id_and_all_child_data() -> None:
    original_id = _seed_full_tenant(OLD)
    child_owners = _owned_user_ids()

    assert au.rename_user_email(OLD, NEW) is True

    assert au.resolve_user_id(NEW) == original_id
    assert au.get_user(OLD) is None
    assert au.get_watch(NEW) == ["SPY"]
    assert au.get_positions(NEW) == [
        {"symbol": "MSTR", "shares": 10.0, "avg_cost": 250.0}
    ]
    assert au.get_latest_book_analysis(NEW)["score"] == 42
    assert au.usage_count_24h(NEW, "ticker_card") == 1
    assert au.get_equal_weight(NEW) is True
    assert _owned_user_ids() == child_owners


def test_new_user_with_old_email_sees_empty_tenant() -> None:
    original_id = _seed_full_tenant(OLD)
    assert au.rename_user_email(OLD, NEW) is True

    au.create_user(OLD, "different-pw")

    assert au.resolve_user_id(OLD) != original_id
    assert au.get_watch(OLD) == []
    assert au.get_positions(OLD) == []
    assert au.get_latest_book_analysis(OLD) is None
    assert au.usage_count_24h(OLD, "ticker_card") == 0
    assert au.get_equal_weight(OLD) is False


def test_pre_rename_session_and_magic_tokens_follow_original_user_id() -> None:
    au.create_user(OLD, "pw")
    original_id = au.resolve_user_id(OLD)
    session = au.issue_token(OLD, typ="session")
    magic = au.issue_magic_token(OLD)

    assert original_id is not None
    assert magic is not None
    assert au.rename_user_email(OLD, NEW) is True
    au.create_user(OLD, "replacement-pw")

    assert au.verify_token(session) == original_id
    assert au.authenticate_session(session) == NEW
    assert au.consume_magic_token(magic) == NEW
    assert au.consume_magic_token(magic) is None
    assert au.resolve_user_id(OLD) != original_id


def test_inactive_or_deleted_token_subject_never_authenticates_reused_email() -> None:
    au.create_user(OLD, "pw")
    original_id = au.resolve_user_id(OLD)
    session = au.issue_token(OLD)
    inactive_magic = au.issue_magic_token(OLD)
    deleted_magic = au.issue_magic_token(OLD)

    assert original_id is not None
    assert inactive_magic is not None
    assert deleted_magic is not None
    au.set_active(OLD, False)
    assert au.authenticate_session(session) is None
    assert au.consume_magic_token(inactive_magic) is None

    with au._conn() as con:
        con.execute("DELETE FROM users WHERE user_id=?", (original_id,))
    au.create_user(OLD, "replacement-pw")

    assert au.authenticate_session(session) is None
    assert au.consume_magic_token(deleted_magic) is None


def test_owner_marker_survives_rename_and_email_reuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GESH_AUTH_ENABLED", "1")
    monkeypatch.setenv("GESH_OWNER_EMAIL", OLD)
    au.create_user(OLD, "pw")
    original_id = au.resolve_user_id(OLD)

    assert original_id is not None
    assert au.is_owner(OLD) is True
    assert au.rename_user_email(OLD, NEW) is True
    au.create_user(OLD, "replacement-pw")
    au.init_auth_db()

    assert au.is_owner(NEW) is True
    assert au.is_owner(OLD) is False
    with au._conn() as con:
        marker = con.execute(
            "SELECT value FROM auth_metadata WHERE key='owner_user_id'"
        ).fetchone()
    assert marker is not None and marker[0] == original_id


def test_auth_disabled_owner_check_keeps_local_email_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GESH_AUTH_ENABLED", raising=False)
    monkeypatch.setenv("GESH_OWNER_EMAIL", OLD)

    assert au.is_owner(OLD.upper()) is True
    assert au.is_owner(NEW) is False


def test_target_users_row_rejects_rename() -> None:
    _seed_full_tenant(OLD)
    au.create_user(NEW, "pw2")

    assert au.rename_user_email(OLD, NEW) is False
    assert au.get_user(OLD) is not None
    assert au.get_watch(OLD) == ["SPY"]


def test_missing_source_rejects_rename() -> None:
    assert au.rename_user_email("missing@example.com", NEW) is False


def test_rename_rolls_back_when_profile_update_fails() -> None:
    _seed_full_tenant(OLD)
    with au._conn() as con:
        con.execute(
            "CREATE TRIGGER reject_email_change BEFORE UPDATE OF email ON users "
            "BEGIN SELECT RAISE(ABORT, 'boom'); END"
        )

    with pytest.raises(sqlite3.Error):
        au.rename_user_email(OLD, NEW)

    assert au.get_user(OLD) is not None
    assert au.get_user(NEW) is None
    assert au.get_watch(OLD) == ["SPY"]


def test_billing_subscription_rename_updates_only_user_profile() -> None:
    original_id = _seed_full_tenant(OLD)
    au.set_billing(OLD, sub_id="sub_x", status="active")
    before = _owned_user_ids()
    user = au.get_user_by_sub_id("sub_x")
    assert user is not None

    email, renamed = billing._apply_email_change(
        {"email": NEW, "sub_id": "sub_x"}, user
    )

    assert email == NEW
    assert renamed is not None and renamed["user_id"] == original_id
    assert au.get_user(OLD) is None
    assert au.resolve_user_id(NEW) == original_id
    assert _owned_user_ids() == before
