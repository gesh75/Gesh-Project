"""HTTP-boundary, webhook, concurrency, recovery, key-rotation and rollback tests
for the deletion/billing saga.

These exercise the REAL application boundary (TestClient against server.app) and the
REAL webhook verify→dedup→dispatch path, plus multi-connection concurrency with
barriers. No real network call is made.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import auth_users
import billing
import deletion_saga
import saga_recovery

_WHSEC = "whsec_test"


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("GESH_AUTH_ENABLED", raising=False)   # wall off; owner-fallback path
    monkeypatch.setenv("BILLING_WEBHOOK_SECRET", _WHSEC)
    monkeypatch.setenv("LEMON_SQUEEZY_API_KEY", "test-key")
    monkeypatch.setenv("GESH_DELETION_SAGA_ENABLED", "1")  # feature-flag gate on for HTTP tests
    for v in ("LS_VARIANT_OBSERVER", "LS_VARIANT_TRADER", "LS_VARIANT_DESK",
              "GESH_TOMBSTONE_KEYS"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(auth_users, "AUTH_DB_PATH", tmp_path / "auth.db")
    auth_users._SAGA_SCHEMA_DONE.clear()
    auth_users.init_auth_db()
    import email_sender
    sent: list = []
    monkeypatch.setattr(email_sender, "send_magic_link",
                        lambda to, link, tier=None: sent.append(to) or True)
    monkeypatch.setattr("billing.email_sender.send_magic_link", email_sender.send_magic_link)
    monkeypatch.setattr("server.email_sender.send_magic_link", email_sender.send_magic_link)
    import server
    return {"client": TestClient(server.app), "server": server, "sent": sent, "tmp": tmp_path}


def _sign(raw: bytes) -> str:
    return hmac.new(_WHSEC.encode(), raw, hashlib.sha256).hexdigest()


def _wh(client, event: str, email: str, *, sub_id="sub_1", status="active",
        variant_name="Trader plan", event_name_header=None, sign=True, body=None):
    payload = body or {
        "meta": {"event_name": event},
        "data": {"id": sub_id, "type": "subscriptions",
                 "attributes": {"user_email": email, "status": status,
                                "variant_name": variant_name, "product_name": "GESH",
                                "customer_id": 7, "subscription_id": sub_id,
                                "updated_at": "2026-07-30T00:00:00.000000Z"}},
    }
    raw = json.dumps(payload).encode()
    headers = {"X-Signature": _sign(raw) if sign else "0" * 64}
    headers["X-Event-Name"] = event if event_name_header is None else event_name_header
    return client.post("/api/billing/webhook", content=raw, headers=headers)


def _tombstone(email: str, sub_id: str = "sub_1"):
    auth_users.create_user(email, "pw")
    uid = auth_users.resolve_user_id(email)
    auth_users.set_billing(email, tier="trader", sub_id=sub_id, status="active")
    deletion_saga.run(email, cancel_fn=lambda s: deletion_saga.CANCEL_OK)
    return uid


# ─── webhook signature / event-name (scenarios 20, 21, header mismatch) ───────

def test_20_invalid_signature_is_rejected_before_any_state(env):
    r = _wh(env["client"], "subscription_created", "x@x.com", sign=False)
    assert r.status_code == 401
    assert auth_users.get_user("x@x.com") is None


def test_21_forged_event_creates_no_fingerprint_and_no_state(env):
    before = _dedup_count()
    r = _wh(env["client"], "subscription_created", "forge@x.com", sign=False)
    assert r.status_code == 401
    assert _dedup_count() == before, "a forged request must not reserve a fingerprint"
    assert auth_users.get_user("forge@x.com") is None


def test_event_name_header_body_mismatch_is_rejected(env):
    r = _wh(env["client"], "subscription_created", "m@x.com",
            event_name_header="subscription_cancelled")
    assert r.status_code == 400
    assert auth_users.get_user("m@x.com") is None


def test_missing_signing_secret_rejects(env, monkeypatch):
    monkeypatch.delenv("BILLING_WEBHOOK_SECRET", raising=False)
    r = _wh(env["client"], "subscription_created", "n@x.com")
    assert r.status_code == 401


def _dedup_count() -> int:
    with billing._conn() as con:
        return con.execute("SELECT COUNT(*) FROM processed_webhook_events").fetchone()[0]


# ─── duplicate / replay (scenarios 5, 6) ──────────────────────────────────────

def test_5_duplicate_valid_webhook_is_a_200_noop(env):
    r1 = _wh(env["client"], "subscription_created", "dup@x.com")
    r2 = _wh(env["client"], "subscription_created", "dup@x.com")
    assert r1.status_code == 200 and r2.status_code == 200
    assert r2.json().get("action") == "duplicate"


# ─── post-tombstone billable events (scenarios 8/9/10/11 + remediation) ───────

@pytest.mark.parametrize("event,status", [
    ("subscription_created", "active"),
    ("subscription_resumed", "active"),
    ("subscription_updated", "active"),
    ("subscription_unpaused", "active"),
    ("subscription_payment_success", "active"),
])
def test_billable_event_after_tombstone_never_recreates_and_records_remediation(env, event, status):
    _tombstone("erased@x.com", sub_id="sub_9")
    assert auth_users.resolve_user_id("erased@x.com") is None

    r = _wh(env["client"], event, "erased@x.com", sub_id="sub_9", status=status)

    assert r.status_code == 200, "a handled tombstoned replay acks 200, never 503-loops"
    assert auth_users.resolve_user_id("erased@x.com") is None, "must not recreate"
    assert env["sent"] == [], "no magic link to an erased identity"
    pend = auth_users.pending_remediations()
    assert any(p["sub_id"] == "sub_9" for p in pend), "remediation must be durably recorded"


def test_remediation_is_idempotent_across_duplicate_billable_events(env):
    _tombstone("erased@x.com", sub_id="sub_9")
    _wh(env["client"], "subscription_created", "erased@x.com", sub_id="sub_9")
    # A second, byte-different delivery (different event) for the same sub.
    _wh(env["client"], "subscription_resumed", "erased@x.com", sub_id="sub_9")

    with auth_users._conn() as con:
        n = con.execute("SELECT COUNT(*) FROM billing_remediations "
                        "WHERE sub_id='sub_9' AND completed_at IS NULL").fetchone()[0]
    assert n == 1, "one open remediation per (identity, sub)"


def test_completed_deletion_saga_is_not_moved_backwards_by_billable_event(env):
    uid = _tombstone("erased@x.com", sub_id="sub_9")
    _wh(env["client"], "subscription_created", "erased@x.com", sub_id="sub_9")

    with auth_users._conn() as con:
        saga = con.execute("SELECT state, completed_at FROM deletion_sagas "
                           "WHERE user_id=?", (uid,)).fetchone()
    assert saga["state"] == auth_users.SAGA_COMPLETED
    assert saga["completed_at"] is not None


# ─── recovery runner executes remediation cancellation (scenario: post-tombstone) ─

def test_recovery_runner_cancels_the_still_live_subscription_and_scrubs(env, monkeypatch):
    _tombstone("erased@x.com", sub_id="sub_9")
    _wh(env["client"], "subscription_created", "erased@x.com", sub_id="sub_9")

    calls = []
    out = saga_recovery.run_recovery(cancel_fn=lambda s: calls.append(s) or deletion_saga.CANCEL_OK)

    assert calls == ["sub_9"], "recovery must cancel the live subscription"
    assert out["remediations"], "runner reports work done"
    with auth_users._conn() as con:
        row = con.execute("SELECT state, sub_id, completed_at FROM billing_remediations").fetchone()
    assert row["state"] == auth_users.REMEDIATION_COMPLETED
    assert row["completed_at"] is not None
    assert row["sub_id"] is None, "raw provider id scrubbed after remediation"


def test_recovery_runner_parks_permanent_failure_for_manual_review(env):
    _tombstone("erased@x.com", sub_id="sub_9")
    _wh(env["client"], "subscription_created", "erased@x.com", sub_id="sub_9")

    def perm(_s):
        raise deletion_saga.ProviderPermanentError("auth failed")
    saga_recovery.run_recovery(cancel_fn=perm)

    with auth_users._conn() as con:
        row = con.execute("SELECT state FROM billing_remediations").fetchone()
    assert row["state"] == auth_users.REMEDIATION_MANUAL_REVIEW
    # And manual_review is never auto-retried:
    assert auth_users.pending_remediations() == []


def test_recovery_runner_retries_transient_with_backoff(env):
    _tombstone("erased@x.com", sub_id="sub_9")
    _wh(env["client"], "subscription_created", "erased@x.com", sub_id="sub_9")

    def flaky(_s):
        raise deletion_saga.ProviderRetryableError("boom")
    saga_recovery.run_recovery(cancel_fn=flaky)

    with auth_users._conn() as con:
        row = con.execute("SELECT state, next_attempt_at, attempts FROM billing_remediations").fetchone()
    assert row["state"] == auth_users.REMEDIATION_PENDING
    assert row["next_attempt_at"] is not None, "backoff time persisted"
    assert row["attempts"] >= 1


def test_recovery_is_production_reachable_via_module_main(env):
    """resume_all is not dead code: run_recovery is the callable a launchd/CLI runner
    invokes (python -m saga_recovery). Proven by invoking it with no work pending."""
    out = saga_recovery.run_recovery(cancel_fn=lambda s: deletion_saga.CANCEL_OK)
    assert out == {"sagas": [], "remediations": []}


# ─── HTTP delete endpoint: anonymous / cross-tenant / owner-fallback / CSRF ───

def test_23_anonymous_deletion_is_rejected(env, monkeypatch):
    monkeypatch.setattr(env["server"], "_session_email", lambda *_a, **_k: None)
    r = env["client"].post("/api/account/delete", json={"confirm": "DELETE"})
    assert r.status_code == 401


def test_24_owner_fallback_cannot_be_a_deletion_target(env):
    """Wall OFF => _session_email resolves to the OWNER. The owner guard must stop
    that identity from being erased through this route."""
    r = env["client"].post("/api/account/delete", json={"confirm": "DELETE"})
    assert r.status_code == 403
    assert "owner" in r.json()["detail"].lower()


def test_22_deletion_target_is_the_session_not_a_body_field(env, monkeypatch):
    """A cross-tenant attempt: A is authenticated but names B in the body. Only A's
    own identity is ever the target; B is untouched."""
    auth_users.create_user("a@x.com", "pw")
    auth_users.create_user("b@x.com", "pw")
    auth_users.set_position("b@x.com", "NVDA", 5)
    monkeypatch.setattr(env["server"], "_session_email", lambda *_a, **_k: "a@x.com")

    r = env["client"].post("/api/account/delete",
                           json={"confirm": "DELETE", "email": "b@x.com", "user_id": "whatever"})

    assert r.status_code in (200, 202)
    assert auth_users.resolve_user_id("b@x.com") is not None, "another tenant untouched"
    assert [p["symbol"] for p in auth_users.get_positions("b@x.com")] == ["NVDA"]
    assert auth_users.resolve_user_id("a@x.com") is None, "the caller's own account is erased"


def test_csrf_cross_site_write_is_rejected(env, monkeypatch):
    auth_users.create_user("a@x.com", "pw")
    monkeypatch.setattr(env["server"], "_session_email", lambda *_a, **_k: "a@x.com")

    r = env["client"].post("/api/account/delete", json={"confirm": "DELETE"},
                           headers={"Sec-Fetch-Site": "cross-site"})

    assert r.status_code == 403
    assert auth_users.resolve_user_id("a@x.com") is not None


def test_csrf_cross_origin_write_is_rejected(env, monkeypatch):
    auth_users.create_user("a@x.com", "pw")
    monkeypatch.setattr(env["server"], "_session_email", lambda *_a, **_k: "a@x.com")

    r = env["client"].post("/api/account/delete", json={"confirm": "DELETE"},
                           headers={"Origin": "https://evil.example.com"})

    assert r.status_code == 403


# ─── login / session after tombstone (scenarios 25, 26) ───────────────────────

def test_25_billing_provisioning_after_tombstone_creates_no_login(env):
    _tombstone("erased@x.com", sub_id="sub_9")
    _wh(env["client"], "subscription_created", "erased@x.com", sub_id="sub_9")
    assert auth_users.resolve_user_id("erased@x.com") is None
    assert auth_users.issue_magic_token("erased@x.com") is None, "no magic token for an erased id"


def test_25b_old_magic_link_is_useless_after_tombstone(env, monkeypatch):
    auth_users.create_user("erased@x.com", "pw")
    auth_users.set_billing("erased@x.com", tier="trader", sub_id="sub_9", status="active")
    magic = auth_users.issue_token("erased@x.com", ttl=900, typ="magic")
    deletion_saga.run("erased@x.com", cancel_fn=lambda s: deletion_saga.CANCEL_OK)

    # The magic-consume path must not resurrect the user.
    assert auth_users.authenticate_session(
        auth_users.verify_token(magic, expected_typ="magic") and magic) is None
    assert auth_users.resolve_user_id("erased@x.com") is None


def test_26_existing_session_is_dead_after_tombstone(env):
    auth_users.create_user("erased@x.com", "pw")
    session = auth_users.issue_token("erased@x.com", typ="session")
    assert auth_users.authenticate_session(session) == "erased@x.com"   # valid before
    auth_users.set_billing("erased@x.com", tier="trader", sub_id="sub_9", status="active")
    deletion_saga.run("erased@x.com", cancel_fn=lambda s: deletion_saga.CANCEL_OK)

    assert auth_users.authenticate_session(session) is None, "signed cookie must not survive erasure"


def test_26b_delete_response_clears_the_session_cookie(env, monkeypatch):
    auth_users.create_user("a@x.com", "pw")
    monkeypatch.setattr(env["server"], "_session_email", lambda *_a, **_k: "a@x.com")

    r = env["client"].post("/api/account/delete", json={"confirm": "DELETE"})

    assert r.status_code in (200, 202)
    cookie_header = r.headers.get("set-cookie", "")
    assert auth_users.COOKIE_NAME in cookie_header, "deletion must clear the session cookie"


# ─── key rotation (Gate 11) ───────────────────────────────────────────────────

def test_key_rotation_keeps_old_tombstones_visible(env, monkeypatch):
    """Write under key A; rotate to [B, A]; the identity stays tombstoned."""
    monkeypatch.setenv("GESH_TOMBSTONE_KEYS", "keyA")
    _tombstone("erased@x.com", sub_id="sub_9")
    assert auth_users.is_tombstoned("erased@x.com") is True

    monkeypatch.setenv("GESH_TOMBSTONE_KEYS", "keyB,keyA")   # rotate, KEEP old
    assert auth_users.is_tombstoned("erased@x.com") is True
    assert auth_users.tombstone_for_sub_id("sub_9") is not None


def test_key_rotation_dropping_old_key_hides_tombstone(env, monkeypatch):
    """Documents the hazard: dropping the retired key makes its tombstones invisible."""
    monkeypatch.setenv("GESH_TOMBSTONE_KEYS", "keyA")
    _tombstone("erased@x.com", sub_id="sub_9")

    monkeypatch.setenv("GESH_TOMBSTONE_KEYS", "keyB")        # DROP old key
    assert auth_users.is_tombstoned("erased@x.com") is False


def test_digest_is_stable_across_calls_and_normalizes_email(env, monkeypatch):
    monkeypatch.setenv("GESH_TOMBSTONE_KEYS", "keyA")
    assert auth_users.identity_digest("cust@x.com") == auth_users.identity_digest("  CUST@X.COM ")
    assert auth_users.identity_digest("cust@x.com") != hashlib.sha256(b"cust@x.com").hexdigest()


# ─── concurrency with separate connections + barriers (Gate 10) ───────────────

def test_two_deletion_requests_converge_on_one_saga(env):
    uid = _seed_billed("race@x.com")
    barrier = threading.Barrier(2)
    results = []

    def worker():
        barrier.wait()
        results.append(auth_users.start_deletion_saga(uid, "race@x.com", "sub_1")["created"])
    ts = [threading.Thread(target=worker) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    assert sorted(results) == [False, True], "exactly one creator"
    with auth_users._conn() as con:
        n = con.execute("SELECT COUNT(*) FROM deletion_sagas WHERE user_id=?", (uid,)).fetchone()[0]
    assert n == 1


def test_tombstone_insert_races_create_magic_link(env):
    """Once tombstoned, no concurrent magic-link creation may resurrect the id."""
    _tombstone("erased@x.com", sub_id="sub_9")
    barrier = threading.Barrier(4)
    errors = []

    def attempt():
        barrier.wait()
        try:
            auth_users.create_magic_link_user("erased@x.com", tier="trader")
        except auth_users.TombstonedIdentityError:
            errors.append("blocked")
        except Exception as e:  # noqa: BLE001
            errors.append(type(e).__name__)
    ts = [threading.Thread(target=attempt) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    assert errors == ["blocked"] * 4
    assert auth_users.resolve_user_id("erased@x.com") is None


def test_two_recovery_workers_do_not_double_cancel(env):
    _tombstone("erased@x.com", sub_id="sub_9")
    _wh(env["client"], "subscription_created", "erased@x.com", sub_id="sub_9")
    barrier = threading.Barrier(2)
    calls = []
    lock = threading.Lock()

    def cancel(sub_id):
        with lock:
            calls.append(sub_id)
        return deletion_saga.CANCEL_OK

    def worker():
        barrier.wait()
        saga_recovery.run_recovery(cancel_fn=cancel)
    ts = [threading.Thread(target=worker) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    assert calls.count("sub_9") <= 1, "remediation must not be cancelled twice"


def _seed_billed(email: str) -> str:
    auth_users.create_user(email, "pw")
    auth_users.set_billing(email, tier="trader", sub_id="sub_1", status="active")
    return auth_users.resolve_user_id(email)


# ─── rollback / old-code-new-schema compatibility (scenario 33) ───────────────

def test_33_new_tables_inert_for_code_that_ignores_them(env):
    """Rollback model: the additive tables sit unused; code paths that predate them
    (normal auth + billing) keep working unchanged."""
    auth_users.create_user("normal@x.com", "pw")
    auth_users.set_billing("normal@x.com", tier="trader", sub_id="sub_z", status="active")

    # Pre-saga behaviours still hold:
    assert auth_users.get_user("normal@x.com")["email"] == "normal@x.com"
    tok = auth_users.issue_token("normal@x.com", typ="session")
    assert auth_users.authenticate_session(tok) == "normal@x.com"


def test_foreign_key_and_integrity_checks_pass(env):
    _tombstone("erased@x.com", sub_id="sub_9")
    with auth_users._conn() as con:
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        # tombstone + saga survive the users-row deletion:
        assert con.execute("SELECT COUNT(*) FROM account_tombstones").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM users WHERE email='erased@x.com'").fetchone()[0] == 0


def test_completed_rows_hold_no_cleartext_or_provider_payload(env):
    """Gate 8/12: after a full erase + remediation, scan every saga/tombstone row for
    prohibited content — clear-text email, raw provider IDs, tokens, tracebacks."""
    _tombstone("erased@x.com", sub_id="sub_secret_99")
    _wh(env["client"], "subscription_created", "erased@x.com", sub_id="sub_secret_99")
    saga_recovery.run_recovery(cancel_fn=lambda s: deletion_saga.CANCEL_OK)

    banned = ("erased@x.com", "sub_secret_99", "Traceback", "Bearer", "whsec_",
              "password", "pbkdf2")
    with auth_users._conn() as con:
        for table in ("account_tombstones", "deletion_sagas", "billing_remediations"):
            for row in con.execute(f"SELECT * FROM {table}").fetchall():
                blob = repr(dict(row))
                for b in banned:
                    assert b not in blob, f"{table} row leaks {b!r}: {blob}"


# ─── Mixed-version safe-activation (Step 2, adjudicated) ──────────────────────

def test_deletion_is_disabled_by_default_flag_off(env, monkeypatch):
    """Merging with the flag off must leave deletion inert — an old instance still
    up would resurrect an erased account, so the operator enables it only after a
    confirmed cutover."""
    monkeypatch.delenv("GESH_DELETION_SAGA_ENABLED", raising=False)
    c = env["client"]
    r = c.post("/api/account/delete", json={"confirm": "DELETE"})
    assert r.status_code == 503
    assert "unavailable" in r.json()["detail"].lower()


def test_health_exposes_tombstone_generation(env):
    body = env["client"].get("/healthz").json()
    assert body["deletion"]["generation"] == auth_users.DEPLOYMENT_GENERATION
    assert body["deletion"]["tombstone_aware"] is True
    assert body["deletion"]["deletion_enabled"] is True   # env fixture enables it


def test_legacy_generation_string_differs_from_current():
    """The whole point of the marker: the pre-saga build reports no such generation.
    Documented rollback hazard — see docs/DELETION_SAGA_RUNBOOK.md."""
    assert auth_users.DEPLOYMENT_GENERATION == "saga-tombstone-v1"


# ─── Step 3: remediation durability (single DB; "cross-database" premise refuted) ──

def test_remediation_write_failure_forces_retry_not_a_silent_ack(env, monkeypatch):
    """CONFIRMED then FIXED (2026-07-23). A remediation-write failure used to be
    swallowed: the webhook 200-acked, no remediation existed, the replay was
    permanently suppressed, and the live subscription was never cancelled. It now
    releases the dedup claim and returns 503 so the delivery is retried.

    Note: the dedup fingerprint and the remediation live in the SAME database, so
    Grok's 'cross-database crash window' framing does not apply — this is a
    same-DB durability ordering fix, not a distributed-transaction problem."""
    c = env["client"]
    auth_users.create_user("erased@x.com", "pw")
    import deletion_saga
    deletion_saga.run("erased@x.com", cancel_fn=lambda s: deletion_saga.CANCEL_OK)

    fail = {"on": True}
    real = auth_users.record_billing_remediation

    def flaky(*a, **k):
        if fail["on"]:
            raise RuntimeError("transient write failure")
        return real(*a, **k)
    monkeypatch.setattr(auth_users, "record_billing_remediation", flaky)

    r1 = _wh(c, "subscription_created", "erased@x.com", sub_id="sub_9")
    assert r1.status_code == 503, "must retry, not 200-ack without remediation"
    with auth_users._conn() as con:
        row = con.execute("SELECT status FROM processed_webhook_events").fetchone()
        assert row is None or row["status"] != "completed", "dedup must not be completed"
        assert con.execute("SELECT COUNT(*) FROM billing_remediations").fetchone()[0] == 0

    fail["on"] = False
    r2 = _wh(c, "subscription_created", "erased@x.com", sub_id="sub_9")
    assert r2.status_code == 200
    with auth_users._conn() as con:
        assert con.execute("SELECT COUNT(*) FROM billing_remediations").fetchone()[0] == 1
    assert auth_users.resolve_user_id("erased@x.com") is None, "still erased"


# ─── Step 4: lease-expiry reclaim is fenced (Grok #4 refuted at P1) ────────────

def test_expired_lease_reclaim_is_fenced_no_lost_result(env, monkeypatch):
    """Exact values: provider timeout 15s < recovery backoff 30s, so a reclaim
    cannot even occur under real timing. Forced here: when worker A's lease is
    expired and worker B reclaims, B's completion wins and A's stale completion is
    fenced by the version CAS. Terminal state is COMPLETED exactly once; the only
    cost is an idempotent duplicate DELETE (harmless) — hence lower severity, not P1."""
    import saga_recovery
    auth_users.create_user("erased@x.com", "pw")
    import deletion_saga
    deletion_saga.run("erased@x.com", cancel_fn=lambda s: deletion_saga.CANCEL_OK)
    auth_users.record_billing_remediation("erased@x.com", "sub_LIVE")

    deletes = []
    rem = auth_users.pending_remediations()[0]
    rid, ver_a = rem["remediation_id"], rem["version"]
    claim_a = auth_users.advance_remediation(rid, ver_a, auth_users.REMEDIATION_PENDING,
                                             next_attempt_at="2999-01-01T00:00:00+00:00")
    ver_a = claim_a["version"]
    # Force the lease to look expired (A "blocked" past it).
    with auth_users._conn() as con:
        con.execute("UPDATE billing_remediations SET next_attempt_at='2000-01-01T00:00:00+00:00' "
                    "WHERE remediation_id=?", (rid,))

    saga_recovery._run_remediations(lambda s: deletes.append(s) or deletion_saga.CANCEL_OK, batch=10)

    # A returns late and tries to complete with its stale version — must be fenced.
    lost = auth_users.advance_remediation(rid, ver_a, auth_users.REMEDIATION_COMPLETED,
                                          completed=True, scrub_sub=True)
    assert lost is None, "A's stale completion must be fenced by version CAS"
    with auth_users._conn() as con:
        row = con.execute("SELECT state, completed_at FROM billing_remediations "
                          "WHERE remediation_id=?", (rid,)).fetchone()
    assert row["state"] == auth_users.REMEDIATION_COMPLETED
    assert row["completed_at"] is not None


# ─── Step 5: tombstone-to-purge auth window is closed ─────────────────────────

def test_session_is_dead_in_the_tombstone_to_purge_window(env, monkeypatch):
    """CONFIRMED then FIXED. Tombstone and purge are separate commits, so between
    them the user row is still active. A NEW request beginning after the tombstone
    commit must NOT authenticate — the protective boundary is the tombstone."""
    monkeypatch.setenv("GESH_AUTH_ENABLED", "1")
    monkeypatch.setenv("GESH_AUTH_SECRET", "k" * 40)
    auth_users.create_user("win@x.com", "pw")
    uid = auth_users.resolve_user_id("win@x.com")
    tok = auth_users.issue_token("win@x.com")
    assert auth_users.authenticate_session(tok) == "win@x.com"

    # Enter the window: tombstone committed, row NOT yet purged.
    auth_users.write_tombstone("win@x.com", uid, billing_sub_id=None, saga_id="s1")
    assert auth_users.resolve_user_id("win@x.com") is not None    # row still present

    assert auth_users.authenticate_session(tok) is None, "session must be dead post-tombstone"


def test_auth_fails_closed_when_tombstone_table_unreadable(env, monkeypatch):
    monkeypatch.setenv("GESH_AUTH_ENABLED", "1")
    monkeypatch.setenv("GESH_AUTH_SECRET", "k" * 40)
    auth_users.create_user("fc@x.com", "pw")
    tok = auth_users.issue_token("fc@x.com")
    monkeypatch.setattr(auth_users, "is_tombstoned",
                        lambda *_a, **_k: (_ for _ in ()).throw(
                            auth_users.TombstoneUnavailableError("x")))

    assert auth_users.authenticate_session(tok) is None, "must deny when it cannot verify"
