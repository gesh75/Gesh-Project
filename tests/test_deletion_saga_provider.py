"""Provider adapter, reconciliation, schema upgrade and failure-recovery.

Gate 1 of the continuation audit found three defects in the first saga cut; each has
a named regression test here:

  * `_migrate_to_user_ids` short-circuits on `_schema_v2_ready`, so the saga tables
    were created ONLY on a brand-new database. On a real (already-migrated) DB
    `account_tombstones` was absent, `is_tombstoned()` silently answered False —
    the resurrection guard was disabled — and `write_tombstone()` raised.
    → test_gate1_defect_existing_database_gets_saga_tables
  * `is_tombstoned()` swallowed every error and returned False (fail-OPEN).
    → test_gate1_defect_tombstone_check_fails_closed
  * `cancel_fn=None` advanced straight to cancellation_confirmed, so an account with
    a live subscription could be erased with no cancellation attempt.
    → test_gate1_defect_billed_account_cannot_be_erased_without_adapter

No real network call is made anywhere in this module.
"""

from __future__ import annotations

import json
import sqlite3
import urllib.error
from pathlib import Path

import pytest

import auth_users
import billing_cancel
import deletion_saga


@pytest.fixture
def au(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("GESH_AUTH_ENABLED", raising=False)
    monkeypatch.setattr(auth_users, "AUTH_DB_PATH", tmp_path / "auth.db")
    auth_users._SAGA_SCHEMA_DONE.clear()
    auth_users.init_auth_db()
    auth_users.create_user("cust@x.com", "pw")
    auth_users.set_billing("cust@x.com", tier="trader", sub_id="sub_1", status="active")
    monkeypatch.setenv("LEMON_SQUEEZY_API_KEY", "test-key")
    return auth_users


def _resp(status: int, body: dict):
    """A urlopen stand-in — no socket is ever opened."""
    class _R:
        def __init__(self): self.status = status
        def read(self): return json.dumps(body).encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False
    return _R()


def _patch_urlopen(monkeypatch, fn):
    monkeypatch.setattr(billing_cancel.urllib.request, "urlopen", fn)


# ─── Gate 1 regressions ───────────────────────────────────────────────────────

def test_gate1_defect_existing_database_gets_saga_tables(tmp_path, monkeypatch):
    """Scenario 33 / Gate 7: a PRE-saga database must upgrade in place."""
    db = tmp_path / "old.db"
    monkeypatch.setattr(auth_users, "AUTH_DB_PATH", db)
    auth_users._SAGA_SCHEMA_DONE.clear()
    auth_users.init_auth_db()
    auth_users.create_user("legacy@x.com", "pw")
    auth_users.set_billing("legacy@x.com", tier="trader", sub_id="sub_L", status="active")
    con = sqlite3.connect(db)                       # simulate the pre-saga schema
    con.execute("DROP TABLE IF EXISTS account_tombstones")
    con.execute("DROP TABLE IF EXISTS deletion_sagas")
    con.commit()
    con.close()
    auth_users._SAGA_SCHEMA_DONE.clear()

    auth_users.get_user("legacy@x.com")             # any normal access upgrades it

    names = {r[0] for r in sqlite3.connect(db).execute("SELECT name FROM sqlite_master")}
    assert {"account_tombstones", "deletion_sagas"} <= names
    # Scenario 35: pre-existing data survives the upgrade untouched.
    assert auth_users.get_user("legacy@x.com")["email"] == "legacy@x.com"
    assert auth_users.billing_subscription_status("legacy@x.com") == ("sub_L", "active")


def test_gate1_defect_schema_bootstrap_is_idempotent(au):
    auth_users._SAGA_SCHEMA_DONE.clear()
    auth_users.init_auth_db()
    auth_users._SAGA_SCHEMA_DONE.clear()
    auth_users.init_auth_db()                       # must not raise

    assert auth_users.get_user("cust@x.com") is not None


def test_gate1_defect_tombstone_check_fails_closed(au, monkeypatch):
    """An unverifiable erasure state must STOP the caller, never wave it through."""
    def broken(*a, **k):
        raise sqlite3.OperationalError("no such table: account_tombstones")
    monkeypatch.setattr(auth_users, "_conn", broken)

    with pytest.raises(auth_users.TombstoneUnavailableError):
        auth_users.is_tombstoned("cust@x.com")


def test_gate1_defect_unavailable_tombstone_refuses_creation(au, monkeypatch):
    import billing
    monkeypatch.setattr(auth_users, "is_tombstoned",
                        lambda *_a, **_k: (_ for _ in ()).throw(
                            auth_users.TombstoneUnavailableError("x")))

    out = billing.provision("cust@x.com", "trader", "cus_1", "sub_1", "")

    assert out["action"] == "refused_tombstone_unavailable"
    assert out["created"] is False


def test_gate1_defect_billed_account_cannot_be_erased_without_adapter(au):
    """No adapter + a live subscription => manual_review, NOT erasure."""
    with pytest.raises(deletion_saga.ProviderPermanentError):
        deletion_saga.run("cust@x.com", cancel_fn=None)

    assert au.resolve_user_id("cust@x.com") is not None
    assert au.is_tombstoned("cust@x.com") is False
    # Parked in manual_review, which resumable_sagas() deliberately excludes.
    assert au.resumable_sagas() == []
    with auth_users._conn() as con:
        state = con.execute("SELECT state, last_error FROM deletion_sagas").fetchone()
    assert state["state"] == auth_users.SAGA_MANUAL_REVIEW
    assert state["last_error"] == "no_cancellation_adapter_configured"


def test_account_without_subscription_needs_no_provider_call(au):
    """Gate 1 Q3: no remote subscription => cancellation is not required."""
    auth_users.create_user("free@x.com", "pw")
    called = []

    r = deletion_saga.run("free@x.com", cancel_fn=lambda s: called.append(s))

    assert r["state"] == auth_users.SAGA_COMPLETED
    assert called == []
    assert au.is_tombstoned("free@x.com") is True


# ─── Gate 2: error classification ─────────────────────────────────────────────

def test_cancel_success_validates_the_returned_status(au, monkeypatch):
    _patch_urlopen(monkeypatch, lambda *a, **k: _resp(200, {
        "data": {"type": "subscriptions", "id": "sub_1", "attributes": {"status": "cancelled", "cancelled": True}}}))

    assert billing_cancel.cancel("sub_1") == deletion_saga.CANCEL_OK


def test_cancel_2xx_that_is_not_cancelled_is_not_success(au, monkeypatch):
    """A 200 whose body still says active must never be reported as cancelled."""
    _patch_urlopen(monkeypatch, lambda *a, **k: _resp(200, {
        "data": {"type": "subscriptions", "id": "sub_1", "attributes": {"status": "active"}}}))

    with pytest.raises(deletion_saga.ProviderPermanentError):
        billing_cancel.cancel("sub_1")


def _http_error(code: int):
    def _raise(*a, **k):
        raise urllib.error.HTTPError("u", code, "err", {}, None)
    return _raise


@pytest.mark.parametrize("code,exc", [
    (401, deletion_saga.ProviderPermanentError),
    (403, deletion_saga.ProviderPermanentError),
    (429, deletion_saga.ProviderRetryableError),
    (500, deletion_saga.ProviderRetryableError),
    (503, deletion_saga.ProviderRetryableError),
    (422, deletion_saga.ProviderPermanentError),
])
def test_error_classification(au, monkeypatch, code, exc):
    _patch_urlopen(monkeypatch, _http_error(code))

    with pytest.raises(exc):
        billing_cancel.cancel("sub_1")


def test_404_for_a_recorded_subscription_is_ambiguous_not_absence(au, monkeypatch):
    """P1 (adjudicated): a 404 for a RECORDED subscription is not proof of absence
    — it could be a test/live-mode, store, host or credential mismatch. It must
    NEVER complete a deletion."""
    _patch_urlopen(monkeypatch, _http_error(404))

    with pytest.raises(billing_cancel.ProviderAmbiguousAbsence):
        billing_cancel.cancel("sub_1")


def test_ambiguous_404_parks_manual_review_and_never_erases(au, monkeypatch):
    _patch_urlopen(monkeypatch, _http_error(404))

    with pytest.raises(deletion_saga.ProviderPermanentError):
        deletion_saga.run("cust@x.com", cancel_fn=billing_cancel.cancel)

    assert au.resolve_user_id("cust@x.com") is not None, "no purge after an ambiguous 404"
    assert au.is_tombstoned("cust@x.com") is False
    with auth_users._conn() as con:
        st = con.execute("SELECT state FROM deletion_sagas").fetchone()["state"]
    assert st == auth_users.SAGA_MANUAL_REVIEW


def test_404_with_an_arbitrary_error_body_is_still_ambiguous(au, monkeypatch):
    def _raise(*a, **k):
        raise urllib.error.HTTPError("u", 404, "err", {},
                                     __import__("io").BytesIO(b'{"errors":[{"detail":"x"}]}'))
    _patch_urlopen(monkeypatch, _raise)

    with pytest.raises(billing_cancel.ProviderAmbiguousAbsence):
        billing_cancel.cancel("sub_1")


def test_test_mode_key_seeing_a_live_sub_404s_and_does_not_delete(au, monkeypatch):
    """The canonical config-mismatch: a test-mode credential cannot see a
    live-mode subscription, so the provider returns 404."""
    _patch_urlopen(monkeypatch, _http_error(404))

    with pytest.raises(billing_cancel.ProviderAmbiguousAbsence):
        deletion_saga.run("cust@x.com", cancel_fn=billing_cancel.cancel)
    assert au.resolve_user_id("cust@x.com") is not None


def test_wrong_api_host_override_is_rejected_before_any_destructive_call(au, monkeypatch):
    monkeypatch.setenv("GESH_LS_API_BASE", "not-a-url")

    with pytest.raises(deletion_saga.ProviderPermanentError):
        billing_cancel.cancel("sub_1")


def test_success_with_wrong_resource_id_is_rejected(au, monkeypatch):
    _patch_urlopen(monkeypatch, lambda *a, **k: _resp(200, {
        "data": {"type": "subscriptions", "id": "OTHER",
                 "attributes": {"status": "cancelled", "cancelled": True}}}))

    with pytest.raises(deletion_saga.ProviderPermanentError):
        billing_cancel.cancel("sub_1")


def test_success_with_wrong_store_is_rejected(au, monkeypatch):
    monkeypatch.setenv("LS_STORE_ID", "999")
    _patch_urlopen(monkeypatch, lambda *a, **k: _resp(200, {
        "data": {"type": "subscriptions", "id": "sub_1",
                 "attributes": {"status": "cancelled", "cancelled": True, "store_id": 111}}}))

    with pytest.raises(deletion_saga.ProviderPermanentError):
        billing_cancel.cancel("sub_1")


def test_success_with_wrong_test_mode_is_rejected(au, monkeypatch):
    monkeypatch.setenv("LS_EXPECT_TEST_MODE", "0")   # expect LIVE
    _patch_urlopen(monkeypatch, lambda *a, **k: _resp(200, {
        "data": {"type": "subscriptions", "id": "sub_1",
                 "attributes": {"status": "cancelled", "cancelled": True, "test_mode": True}}}))

    with pytest.raises(deletion_saga.ProviderPermanentError):
        billing_cancel.cancel("sub_1")


def test_success_with_status_cancelled_but_flag_false_is_rejected(au, monkeypatch):
    _patch_urlopen(monkeypatch, lambda *a, **k: _resp(200, {
        "data": {"type": "subscriptions", "id": "sub_1",
                 "attributes": {"status": "cancelled", "cancelled": False}}}))

    with pytest.raises(deletion_saga.ProviderPermanentError):
        billing_cancel.cancel("sub_1")


def test_non_subscription_object_is_rejected(au, monkeypatch):
    _patch_urlopen(monkeypatch, lambda *a, **k: _resp(200, {
        "data": {"type": "orders", "id": "sub_1", "attributes": {"status": "cancelled"}}}))

    with pytest.raises(deletion_saga.ProviderPermanentError):
        billing_cancel.cancel("sub_1")


def test_missing_credentials_is_permanent_and_never_deletes(au, monkeypatch):
    monkeypatch.delenv("LEMON_SQUEEZY_API_KEY", raising=False)

    with pytest.raises(deletion_saga.ProviderPermanentError):
        billing_cancel.cancel("sub_1")
    with pytest.raises(deletion_saga.ProviderPermanentError):
        deletion_saga.run("cust@x.com", cancel_fn=billing_cancel.cancel)
    assert au.resolve_user_id("cust@x.com") is not None, "must not erase without credentials"


def test_malformed_provider_body_is_permanent(au, monkeypatch):
    _patch_urlopen(monkeypatch, lambda *a, **k: _resp(200, {"data": "not-an-object"}))

    with pytest.raises(deletion_saga.ProviderPermanentError):
        billing_cancel.cancel("sub_1")


# ─── Gate 3: reconciliation after an unknown outcome ──────────────────────────

def test_timeout_then_reconcile_reports_cancelled_completes_safely(au, monkeypatch):
    calls = {"n": 0}

    def flow(req, *a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("read timed out")          # DELETE outcome unknown
        return _resp(200, {"data": {"type": "subscriptions", "id": "sub_1", "attributes": {"status": "cancelled", "cancelled": True}}})
    _patch_urlopen(monkeypatch, flow)

    assert billing_cancel.cancel("sub_1") == deletion_saga.CANCEL_ALREADY
    assert calls["n"] == 2, "must reconcile rather than guess"


def test_timeout_then_reconcile_reports_active_blocks_deletion(au, monkeypatch):
    def flow(req, *a, **k):
        if getattr(req, "method", "") == "DELETE":
            raise TimeoutError("read timed out")
        return _resp(200, {"data": {"type": "subscriptions", "id": "sub_1", "attributes": {"status": "active"}}})
    _patch_urlopen(monkeypatch, flow)

    with pytest.raises(deletion_saga.ProviderUnknownOutcome):
        deletion_saga.run("cust@x.com", cancel_fn=billing_cancel.cancel)

    assert au.resolve_user_id("cust@x.com") is not None
    assert au.is_tombstoned("cust@x.com") is False


def test_reconciliation_itself_timing_out_keeps_deletion_blocked(au, monkeypatch):
    def always_timeout(*a, **k):
        raise TimeoutError("read timed out")
    _patch_urlopen(monkeypatch, always_timeout)

    with pytest.raises(deletion_saga.ProviderUnknownOutcome):
        deletion_saga.run("cust@x.com", cancel_fn=billing_cancel.cancel)

    assert au.resolve_user_id("cust@x.com") is not None


def test_connection_failure_is_retryable_and_does_not_delete(au, monkeypatch):
    def refused(*a, **k):
        raise urllib.error.URLError("connection refused")
    _patch_urlopen(monkeypatch, refused)

    with pytest.raises(deletion_saga.ProviderRetryableError):
        deletion_saga.run("cust@x.com", cancel_fn=billing_cancel.cancel)

    assert au.resolve_user_id("cust@x.com") is not None


# ─── Scenario 31 with the REAL adapter ────────────────────────────────────────

def test_31_real_adapter_called_with_no_open_db_transaction(au, monkeypatch):
    """A concurrent writer on a separate connection must succeed DURING the call."""
    observed = {}

    def transport(*a, **k):
        con = sqlite3.connect(auth_users.AUTH_DB_PATH, timeout=2.0)
        con.execute("BEGIN IMMEDIATE")                 # would block if one were open
        con.execute("UPDATE users SET last_login_at='probe' WHERE email='cust@x.com'")
        con.commit()
        con.close()
        observed["wrote"] = True
        return _resp(200, {"data": {"type": "subscriptions", "id": "sub_1", "attributes": {"status": "cancelled", "cancelled": True}}})
    _patch_urlopen(monkeypatch, transport)

    deletion_saga.run("cust@x.com", cancel_fn=billing_cancel.cancel)

    assert observed.get("wrote") is True


# ─── Scenarios 13, 14, 16: failure recovery + resume from every state ─────────

def test_13_db_failure_after_intent_persisted_leaves_intent_durable(au, monkeypatch):
    _patch_urlopen(monkeypatch, lambda *a, **k: _resp(200, {
        "data": {"type": "subscriptions", "id": "sub_1", "attributes": {"status": "cancelled", "cancelled": True}}}))
    boom = {"n": 0}
    real_write = auth_users.write_tombstone

    def failing_write(*a, **k):
        boom["n"] += 1
        raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(auth_users, "write_tombstone", failing_write)

    with pytest.raises(sqlite3.OperationalError):
        deletion_saga.run("cust@x.com", cancel_fn=billing_cancel.cancel)

    open_sagas = au.resumable_sagas()
    assert open_sagas, "intent must remain durable"
    assert open_sagas[0]["completed_at"] is None
    assert au.resolve_user_id("cust@x.com") is not None

    monkeypatch.setattr(auth_users, "write_tombstone", real_write)
    deletion_saga.resume_all(cancel_fn=billing_cancel.cancel)          # new attempt
    assert au.is_tombstoned("cust@x.com") is True
    assert au.resolve_user_id("cust@x.com") is None


def test_13b_resume_does_not_create_a_duplicate_saga(au, monkeypatch):
    uid = au.resolve_user_id("cust@x.com")
    au.start_deletion_saga(uid, "cust@x.com", "sub_1")
    au.start_deletion_saga(uid, "cust@x.com", "sub_1")

    with auth_users._conn() as con:
        n = con.execute("SELECT COUNT(*) FROM deletion_sagas WHERE user_id=?", (uid,)).fetchone()[0]
    assert n == 1


def test_14_remote_cancelled_then_local_write_fails_then_recovers(au, monkeypatch):
    """Provider already cancelled; the confirm-write failed. Recovery must not
    purge during the failure, must reconcile, and must finish."""
    calls = {"delete": 0}

    def flow(req, *a, **k):
        if getattr(req, "method", "") == "DELETE":
            calls["delete"] += 1
        return _resp(200, {"data": {"type": "subscriptions", "id": "sub_1", "attributes": {"status": "cancelled", "cancelled": True}}})
    _patch_urlopen(monkeypatch, flow)

    real_advance = auth_users.advance_saga
    fail_on = {"state": auth_users.SAGA_CANCELLATION_CONFIRMED, "done": False}

    def flaky_advance(saga_id, version, state, **kw):
        if state == fail_on["state"] and not fail_on["done"]:
            fail_on["done"] = True
            raise sqlite3.OperationalError("disk I/O error")
        return real_advance(saga_id, version, state, **kw)
    monkeypatch.setattr(auth_users, "advance_saga", flaky_advance)

    with pytest.raises(sqlite3.OperationalError):
        deletion_saga.run("cust@x.com", cancel_fn=billing_cancel.cancel)
    assert au.resolve_user_id("cust@x.com") is not None, "no purge during the failed write"

    monkeypatch.setattr(auth_users, "advance_saga", real_advance)
    deletion_saga.resume_all(cancel_fn=billing_cancel.cancel)

    assert au.is_tombstoned("cust@x.com") is True
    assert au.resolve_user_id("cust@x.com") is None
    assert calls["delete"] <= 2, "cancellation must not be duplicated unsafely"


@pytest.mark.parametrize("state", [
    auth_users.SAGA_REQUESTED,
    auth_users.SAGA_CANCELLATION_PENDING,
    auth_users.SAGA_CANCELLATION_CONFIRMED,
    auth_users.SAGA_TOMBSTONE_PENDING,
    auth_users.SAGA_TOMBSTONED,
    auth_users.SAGA_RETRYABLE_FAILURE,
])
def test_16_resume_from_every_intermediate_state(au, monkeypatch, state):
    """Models a process restart: the saga is parked in `state`, then resumed."""
    _patch_urlopen(monkeypatch, lambda *a, **k: _resp(200, {
        "data": {"type": "subscriptions", "id": "sub_1", "attributes": {"status": "cancelled", "cancelled": True}}}))
    uid = au.resolve_user_id("cust@x.com")
    claim = au.start_deletion_saga(uid, "cust@x.com", "sub_1")
    saga = claim["saga"]
    if state == auth_users.SAGA_TOMBSTONED:
        au.write_tombstone("cust@x.com", uid, billing_sub_id="sub_1", saga_id=saga["saga_id"])
    au.advance_saga(saga["saga_id"], saga["version"], state,
                    resume_state=auth_users.SAGA_CANCELLATION_PENDING
                    if state == auth_users.SAGA_RETRYABLE_FAILURE else state)

    deletion_saga.resume_all(cancel_fn=billing_cancel.cancel)

    assert au.resolve_user_id("cust@x.com") is None, f"did not finish from {state}"
    assert au.is_tombstoned("cust@x.com") is True


def test_16b_manual_review_is_never_auto_retried(au):
    uid = au.resolve_user_id("cust@x.com")
    saga = au.start_deletion_saga(uid, "cust@x.com", "sub_1")["saga"]
    au.advance_saga(saga["saga_id"], saga["version"], auth_users.SAGA_MANUAL_REVIEW,
                    last_error="provider_permanent")

    assert au.resumable_sagas() == []
    deletion_saga.resume_all(cancel_fn=lambda s: deletion_saga.CANCEL_OK)
    assert au.resolve_user_id("cust@x.com") is not None, "manual_review must not auto-delete"


# ─── Gate 8: audit-field hygiene ──────────────────────────────────────────────

def test_saga_audit_fields_contain_no_payload_or_identity(au, monkeypatch):
    def refused(*a, **k):
        raise urllib.error.URLError("connection refused to secret-host")
    _patch_urlopen(monkeypatch, refused)

    with pytest.raises(deletion_saga.ProviderRetryableError):
        deletion_saga.run("cust@x.com", cancel_fn=billing_cancel.cancel)

    saga = au.resumable_sagas()[0]
    assert saga["last_error"] == "provider_retryable", "classification code only"
    blob = repr(dict(saga))
    assert "cust@x.com" not in blob
    assert "secret-host" not in blob
    assert "Traceback" not in blob
