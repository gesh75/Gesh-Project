"""Deletion/billing saga — cancel-then-erase, tombstone, replay safety.

The defect this closes (reproduced end-to-end before the fix): `delete_user_data()`
dropped the `users` row, freeing the unique `billing_sub_id` index. The next Lemon
Squeezy renewal found no account, fell through to `billing.provision()` →
`create_magic_link_user()`, and **recreated the account plus emailed a magic link to
someone who had asked to be erased.** The variant-less invoice path instead raised
`RetryableError` → 503, so the provider retried forever.

Either the erasure was undone or the webhook went permanently red.

Scenario numbers refer to the Stage-9 matrix in the task brief.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import auth_users
import deletion_saga


@pytest.fixture
def au(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("GESH_AUTH_ENABLED", raising=False)
    monkeypatch.setattr(auth_users, "AUTH_DB_PATH", tmp_path / "auth.db")
    auth_users.init_auth_db()
    auth_users.create_user("cust@x.com", "pw")
    auth_users.create_user("other@x.com", "pw")
    auth_users.set_billing("cust@x.com", tier="trader", sub_id="sub_1", status="active")
    return auth_users


def _ok(_sub_id):
    return deletion_saga.CANCEL_OK


# ─── 1, 28: happy path + replay ───────────────────────────────────────────────

def test_1_cancellation_then_tombstone_then_purge(au):
    calls = []
    r = deletion_saga.run("cust@x.com", cancel_fn=lambda s: calls.append(s) or deletion_saga.CANCEL_OK)

    assert r["state"] == auth_users.SAGA_COMPLETED
    assert calls == ["sub_1"], "provider cancellation must be attempted before erase"
    assert au.resolve_user_id("cust@x.com") is None
    assert au.is_tombstoned("cust@x.com") is True


def test_2_28_completed_replay_is_a_success_noop(au):
    deletion_saga.run("cust@x.com", cancel_fn=_ok)
    again = deletion_saga.run("cust@x.com", cancel_fn=_ok)

    assert again["status"] == "ok"
    assert again["already_completed"] is True


def test_3_4_concurrent_duplicate_requests_converge_on_one_saga(au):
    uid = au.resolve_user_id("cust@x.com")
    a = au.start_deletion_saga(uid, "cust@x.com", "sub_1")
    b = au.start_deletion_saga(uid, "cust@x.com", "sub_1")

    assert a["created"] is True and b["created"] is False
    assert a["saga"]["saga_id"] == b["saga"]["saga_id"]


# ─── 5, 6, 18, 19, 27: the resurrection defect ────────────────────────────────

def test_19_27_reactivation_after_tombstone_cannot_recreate(au):
    deletion_saga.run("cust@x.com", cancel_fn=_ok)

    with pytest.raises(au.TombstonedIdentityError):
        au.create_magic_link_user("cust@x.com", tier="trader")
    assert au.resolve_user_id("cust@x.com") is None


def test_18_provision_after_tombstone_is_a_terminal_noop_not_a_retry(au, monkeypatch):
    """Must NOT raise RetryableError — that would make the webhook permanently red."""
    import billing

    sent = []
    monkeypatch.setattr(billing.email_sender, "send_magic_link",
                        lambda *a, **k: sent.append(a) or True)
    deletion_saga.run("cust@x.com", cancel_fn=_ok)

    out = billing.provision("cust@x.com", "trader", "cus_1", "sub_1", "2026-07-30T00:00:00Z")

    assert out["action"] == "refused_tombstoned"
    assert out["created"] is False
    assert sent == [], "no magic link may be emailed to an erased identity"
    assert au.resolve_user_id("cust@x.com") is None


def test_18b_tombstone_matches_on_subscription_id_when_email_differs(au):
    """A webhook often knows only the sub id, or carries a changed email."""
    deletion_saga.run("cust@x.com", cancel_fn=_ok)

    assert au.tombstone_for_sub_id("sub_1") is not None
    assert au.tombstone_for_sub_id("sub_other") is None


# ─── 7, 8: provider says already-cancelled / gone ─────────────────────────────

@pytest.mark.parametrize("outcome", [deletion_saga.CANCEL_ALREADY,
                                     deletion_saga.CANCEL_NOT_FOUND])
def test_7_8_already_cancelled_or_missing_is_idempotent_success(au, outcome):
    r = deletion_saga.run("cust@x.com", cancel_fn=lambda s: outcome)

    assert r["state"] == auth_users.SAGA_COMPLETED
    assert au.is_tombstoned("cust@x.com") is True


# ─── 9, 10, 11, 12: provider failures must not delete ─────────────────────────

@pytest.mark.parametrize("exc,expect_state", [
    (deletion_saga.ProviderRetryableError, auth_users.SAGA_RETRYABLE_FAILURE),
    (deletion_saga.ProviderUnknownOutcome, auth_users.SAGA_RETRYABLE_FAILURE),
    (deletion_saga.ProviderPermanentError, auth_users.SAGA_MANUAL_REVIEW),
])
def test_9_12_provider_failure_never_erases(au, exc, expect_state):
    def boom(_s):
        raise exc("x")

    with pytest.raises(exc):
        deletion_saga.run("cust@x.com", cancel_fn=boom)

    assert au.resolve_user_id("cust@x.com") is not None, "account must survive"
    assert au.is_tombstoned("cust@x.com") is False
    sagas = au.resumable_sagas()
    if expect_state == auth_users.SAGA_RETRYABLE_FAILURE:
        assert sagas and sagas[0]["state"] == expect_state
        assert sagas[0]["resume_state"] == auth_users.SAGA_CANCELLATION_PENDING


def test_10_unknown_outcome_records_its_own_classification(au):
    def timeout(_s):
        raise deletion_saga.ProviderUnknownOutcome("timeout")

    with pytest.raises(deletion_saga.ProviderUnknownOutcome):
        deletion_saga.run("cust@x.com", cancel_fn=timeout)

    assert au.resumable_sagas()[0]["last_error"] == "provider_unknown_outcome"


# ─── 16: resume from persisted state ──────────────────────────────────────────

def test_16_retryable_failure_resumes_and_completes(au):
    state = {"fail": True}

    def flaky(_s):
        if state["fail"]:
            state["fail"] = False
            raise deletion_saga.ProviderRetryableError("boom")
        return deletion_saga.CANCEL_OK

    with pytest.raises(deletion_saga.ProviderRetryableError):
        deletion_saga.run("cust@x.com", cancel_fn=flaky)
    assert au.resolve_user_id("cust@x.com") is not None

    out = deletion_saga.resume_all(cancel_fn=flaky)

    assert au.resolve_user_id("cust@x.com") is None
    assert au.is_tombstoned("cust@x.com") is True
    assert out


def test_15_crash_between_tombstone_and_purge_leaves_recreation_blocked(au):
    """Fail-safe ordering: tombstone is written BEFORE rows are purged."""
    def exploding_purge(_email):
        raise RuntimeError("db died mid-purge")

    with pytest.raises(RuntimeError):
        deletion_saga.run("cust@x.com", cancel_fn=_ok, purge_fn=exploding_purge)

    assert au.is_tombstoned("cust@x.com") is True
    with pytest.raises(au.TombstonedIdentityError):
        au.create_magic_link_user("cust@x.com", tier="trader")


# ─── 22: tenant isolation ─────────────────────────────────────────────────────

def test_22_deleting_one_account_never_touches_another(au):
    au.set_position("other@x.com", "NVDA", 5)

    deletion_saga.run("cust@x.com", cancel_fn=_ok)

    assert au.resolve_user_id("other@x.com") is not None
    assert [p["symbol"] for p in au.get_positions("other@x.com")] == ["NVDA"]
    assert au.is_tombstoned("other@x.com") is False


# ─── 29, 30: data minimisation ────────────────────────────────────────────────

def test_29_no_sensitive_data_remains_after_completion(au):
    au.set_position("cust@x.com", "NVDA", 10)
    au.add_watch("cust@x.com", "NVDA")

    deletion_saga.run("cust@x.com", cancel_fn=_ok)

    uid_gone = au.resolve_user_id("cust@x.com") is None
    assert uid_gone
    with auth_users._conn() as con:
        for table in auth_users._USER_DATA_TABLES:
            assert con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_30_tombstone_stores_no_cleartext_identity(au):
    deletion_saga.run("cust@x.com", cancel_fn=_ok)

    with auth_users._conn() as con:
        row = dict(con.execute("SELECT * FROM account_tombstones").fetchone())

    assert "cust@x.com" not in repr(row), "tombstone must not hold a clear-text email"
    assert len(row["email_digest"]) == 64
    assert row["email_digest"] != auth_users._norm("cust@x.com")


def test_30b_identity_digest_is_keyed_not_a_bare_hash(au):
    """A bare SHA-256 of an email is trivially reversible by dictionary attack."""
    import hashlib

    bare = hashlib.sha256(b"cust@x.com").hexdigest()
    assert auth_users.identity_digest("cust@x.com") != bare
    assert auth_users.identity_digest("cust@x.com") == auth_users.identity_digest("CUST@X.COM ")


# ─── 31: external call outside a DB transaction ───────────────────────────────

def test_31_provider_call_happens_outside_any_db_transaction(au):
    """If a transaction were open across the network call, this write would block
    or fail; it succeeds because intent is committed before calling out."""
    observed = {}

    def cancel_and_write(sub_id):
        auth_users.set_position("other@x.com", "TSLA", 1)
        observed["wrote_during_call"] = True
        return deletion_saga.CANCEL_OK

    deletion_saga.run("cust@x.com", cancel_fn=cancel_and_write)

    assert observed.get("wrote_during_call") is True
    assert [p["symbol"] for p in au.get_positions("other@x.com")] == ["TSLA"]


# ─── 32: additive migration ───────────────────────────────────────────────────

def test_32_migration_creates_saga_tables_on_a_fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_users, "AUTH_DB_PATH", tmp_path / "fresh.db")
    auth_users.init_auth_db()

    with auth_users._conn() as con:
        names = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}

    assert {"account_tombstones", "deletion_sagas"} <= names


def test_34_existing_billing_provision_still_works_for_a_clean_identity(au, monkeypatch):
    """Compatibility: a NON-tombstoned identity must provision exactly as before."""
    import billing

    monkeypatch.setattr(billing.email_sender, "send_magic_link", lambda *a, **k: True)

    out = billing.provision("brandnew@x.com", "trader", "cus_9", "sub_9", "2026-07-30T00:00:00Z")

    assert out["action"] == "provision"
    assert out["created"] is True
    assert au.resolve_user_id("brandnew@x.com") is not None
