"""Reclaim-before-repeat: a worker reclaiming an expired cancellation claim must
reconcile provider state BEFORE issuing another DELETE.

WHY (evidence, 2026-07-24). Two reviews disagreed. Resolved from executable code:
`saga_recovery._run_remediations` called `cancel_fn(sub_id)` directly after a
successful reclaim CAS, and `billing_cancel.cancel` issues `DELETE` as its FIRST
action — the GET in `reconcile()` runs only inside `except ProviderUnknownOutcome`,
i.e. only when THAT SAME call timed out. A worker that crashed after its DELETE was
applied left the row reclaimable, and the next worker issued a SECOND DELETE with no
prior read. Lemon Squeezy does not document DELETE idempotency, so the outcome of
that second DELETE is undefined — it must not be relied upon.

These tests use the production recovery code and the production cancellation adapter
with a deterministic fake transport. No socket is ever opened.
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
import saga_recovery


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("GESH_AUTH_ENABLED", raising=False)
    monkeypatch.setenv("LEMON_SQUEEZY_API_KEY", "test-key")
    monkeypatch.setattr(auth_users, "AUTH_DB_PATH", tmp_path / "auth.db")
    auth_users._SAGA_SCHEMA_DONE.clear()
    auth_users.init_auth_db()
    auth_users.create_user("erased@x.com", "pw")
    deletion_saga.run("erased@x.com", cancel_fn=lambda s: deletion_saga.CANCEL_OK)
    auth_users.record_billing_remediation("erased@x.com", "sub_L")
    return {"tmp": tmp_path}


def _ok(body: dict):
    class _R:
        status = 200
        def read(self): return json.dumps(body).encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False
    return _R()


def _sub(status: str, sub_id: str = "sub_L"):
    return {"data": {"type": "subscriptions", "id": sub_id,
                     "attributes": {"status": status, "cancelled": status == "cancelled"}}}


def _expire_claim(tmp_path: Path):
    con = sqlite3.connect(tmp_path / "auth.db")
    con.execute("UPDATE billing_remediations "
                "SET next_attempt_at='2000-01-01T00:00:00+00:00'")
    con.commit()
    con.close()


def _worker_a_crashes_after_applying(monkeypatch, calls: list, provider: dict):
    """Model a true CRASH: worker A claims, its DELETE reaches the provider and is
    applied, then the process dies before recording anything and before its own
    in-call reconciliation could run. The row is left claimed with attempts>0.

    (If A had survived to reconcile, `cancel()` handles the timeout itself — that
    path is covered in tests/test_deletion_saga_provider.py. The dangerous case is
    the one where A never comes back, leaving the claim to expire.)
    """
    rem = auth_users.pending_remediations()[0]
    claimed = auth_users.advance_remediation(
        rem["remediation_id"], rem["version"], auth_users.REMEDIATION_PENDING,
        next_attempt_at="2999-01-01T00:00:00+00:00")
    calls.append("DELETE")                 # A's DELETE went out...
    provider["status"] = "cancelled"       # ...and the provider APPLIED it
    return rem["remediation_id"], claimed["version"]


def test_reclaimed_worker_reconciles_before_any_repeat_delete(env, monkeypatch):
    """THE INVARIANT. Reconciliation says cancelled → no second DELETE at all."""
    calls: list[str] = []
    provider = {"status": "active"}
    rid, _ver = _worker_a_crashes_after_applying(monkeypatch, calls, provider)
    assert provider["status"] == "cancelled"
    _expire_claim(env["tmp"])

    def transport(req, *a, **k):
        calls.append(req.method)
        return _ok(_sub(provider["status"]))
    monkeypatch.setattr(billing_cancel.urllib.request, "urlopen", transport)

    calls.clear()
    out = saga_recovery._run_remediations(billing_cancel.cancel, batch=5,
                                          reconcile_fn=billing_cancel.reconcile)

    assert calls and calls[0] == "GET", f"must reconcile first, got {calls}"
    assert "DELETE" not in calls, "must NOT repeat DELETE on an already-cancelled sub"
    assert out[0]["result"] == deletion_saga.CANCEL_ALREADY
    with auth_users._conn() as con:
        row = con.execute("SELECT state, completed_at, sub_id FROM billing_remediations "
                          "WHERE remediation_id=?", (rid,)).fetchone()
    assert row["state"] == auth_users.REMEDIATION_COMPLETED
    assert row["completed_at"] is not None
    assert row["sub_id"] is None, "raw provider id must be scrubbed at completion"


def test_reconcile_active_permits_one_fenced_delete(env, monkeypatch):
    """Genuinely still active → a DELETE may be issued, but only after the GET."""
    calls: list[str] = []
    provider = {"status": "active"}
    _worker_a_crashes_after_applying(monkeypatch, calls, provider)
    provider["status"] = "active"          # remote did NOT actually cancel
    _expire_claim(env["tmp"])

    def transport(req, *a, **k):
        calls.append(req.method)
        if req.method == "DELETE":
            provider["status"] = "cancelled"
        return _ok(_sub(provider["status"]))
    monkeypatch.setattr(billing_cancel.urllib.request, "urlopen", transport)

    calls.clear()
    saga_recovery._run_remediations(billing_cancel.cancel, batch=5,
                                    reconcile_fn=billing_cancel.reconcile)

    assert calls[0] == "GET" and "DELETE" in calls, calls
    assert calls.index("GET") < calls.index("DELETE"), "GET must precede DELETE"


def _reclaim_with_reconcile_transport(env, monkeypatch, transport):
    calls: list[str] = []
    provider = {"status": "active"}
    _worker_a_crashes_after_applying(monkeypatch, calls, provider)
    _expire_claim(env["tmp"])
    monkeypatch.setattr(billing_cancel.urllib.request, "urlopen", transport)
    return saga_recovery._run_remediations(billing_cancel.cancel, batch=5,
                                            reconcile_fn=billing_cancel.reconcile)


def _http(code: int):
    def _raise(*a, **k):
        raise urllib.error.HTTPError("u", code, "err", {}, None)
    return _raise


def test_reconcile_404_is_ambiguous_absence_manual_review(env, monkeypatch):
    out = _reclaim_with_reconcile_transport(env, monkeypatch, _http(404))
    assert out[0]["result"] == "manual_review"
    with auth_users._conn() as con:
        row = con.execute("SELECT state, completed_at FROM billing_remediations").fetchone()
    assert row["state"] == auth_users.REMEDIATION_MANUAL_REVIEW
    assert row["completed_at"] is None, "ambiguous absence must NOT complete"


def test_reconcile_timeout_retries_without_delete(env, monkeypatch):
    def timeout(*a, **k):
        raise TimeoutError("read timed out")
    out = _reclaim_with_reconcile_transport(env, monkeypatch, timeout)
    assert out[0]["result"] == "retry_reconcile_unknown"
    with auth_users._conn() as con:
        row = con.execute("SELECT state, last_error, completed_at "
                          "FROM billing_remediations").fetchone()
    assert row["state"] == auth_users.REMEDIATION_PENDING
    assert row["last_error"] == "reconcile_unknown_outcome"
    assert row["completed_at"] is None


def test_reconcile_malformed_body_goes_to_manual_review(env, monkeypatch):
    out = _reclaim_with_reconcile_transport(
        env, monkeypatch, lambda *a, **k: _ok({"data": "not-an-object"}))
    assert out[0]["result"] == "manual_review"


def test_reconcile_wrong_resource_id_goes_to_manual_review(env, monkeypatch):
    out = _reclaim_with_reconcile_transport(
        env, monkeypatch, lambda *a, **k: _ok(_sub("active", sub_id="OTHER")))
    assert out[0]["result"] == "manual_review"


@pytest.mark.parametrize("code,expected", [
    (429, "retry_reconcile"),
    (500, "retry_reconcile"),
    (503, "retry_reconcile"),
    (401, "manual_review"),
    (403, "manual_review"),
    (409, "manual_review"),
    (422, "manual_review"),
    (400, "manual_review"),
])
def test_reconcile_http_classification_is_bounded_and_never_completes(
        env, monkeypatch, code, expected):
    """409 included: classified PERMANENT → manual_review. Bounded (no infinite
    retry), never purges, never overwrites a successful cancellation."""
    out = _reclaim_with_reconcile_transport(env, monkeypatch, _http(code))
    assert out[0]["result"] == expected
    with auth_users._conn() as con:
        row = con.execute("SELECT completed_at FROM billing_remediations").fetchone()
    assert row["completed_at"] is None, "no classification may complete the row"


def test_reconcile_connection_reset_is_retryable_no_delete(env, monkeypatch):
    def reset(*a, **k):
        raise urllib.error.URLError("connection reset by peer")
    out = _reclaim_with_reconcile_transport(env, monkeypatch, reset)
    assert out[0]["result"] == "retry_reconcile"


def test_first_attempt_still_deletes_directly(env, monkeypatch):
    """Narrowness check: a FRESH remediation (attempts==0) has no prior remote
    outcome, so it must not pay for an extra GET."""
    calls: list[str] = []

    def transport(req, *a, **k):
        calls.append(req.method)
        return _ok(_sub("cancelled"))
    monkeypatch.setattr(billing_cancel.urllib.request, "urlopen", transport)

    saga_recovery._run_remediations(billing_cancel.cancel, batch=5,
                                    reconcile_fn=billing_cancel.reconcile)

    assert calls == ["DELETE"], f"fresh attempt should DELETE directly, got {calls}"


def test_stale_worker_cannot_complete_after_reclaim(env, monkeypatch):
    """Fencing still holds alongside the new reconcile step."""
    calls: list[str] = []
    provider = {"status": "active"}
    rid, stale_ver = _worker_a_crashes_after_applying(monkeypatch, calls, provider)
    _expire_claim(env["tmp"])
    monkeypatch.setattr(billing_cancel.urllib.request, "urlopen",
                        lambda req, *a, **k: _ok(_sub(provider["status"])))
    saga_recovery._run_remediations(billing_cancel.cancel, batch=5,
                                    reconcile_fn=billing_cancel.reconcile)

    lost = auth_users.advance_remediation(rid, stale_ver, auth_users.REMEDIATION_COMPLETED,
                                          completed=True, scrub_sub=True)

    assert lost is None, "stale worker must be fenced out by the version CAS"


def test_production_run_recovery_supplies_the_reconciler(env, monkeypatch):
    """The fix must be reachable through the PRODUCTION entry point, not only when
    a test passes reconcile_fn explicitly."""
    seen: dict = {}

    def fake_run(cancel_fn, batch, reconcile_fn=None):
        seen["reconcile_fn"] = reconcile_fn
        return []
    monkeypatch.setattr(saga_recovery, "_run_remediations", fake_run)
    monkeypatch.setattr(deletion_saga, "resume_all", lambda **k: [])

    saga_recovery.run_recovery()

    assert seen["reconcile_fn"] is billing_cancel.reconcile
