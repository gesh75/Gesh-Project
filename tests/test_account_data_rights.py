"""L3 — data-subject rights: Export my data + Delete my account.

The published Privacy Policy (§8) already PROMISES access/export/delete. Until now
the product could not deliver them, which is worse than not promising: it is a
written commitment the software cannot honour.

Scope note verified 2026-07-19: the Legal Hub says "primitives exist
(export_portfolio.py, clear_positions)". Half true — `export_portfolio.py` is the
OWNER's Robinhood exporter and has nothing to do with customer data. There was NO
customer export primitive at all, so it is built here rather than wired up.

Deletion must be a real purge across every table that keys on user_id, must only
ever touch the CALLER's own rows, and must not silently leave a paid subscription
billing against a deleted account.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def au(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import auth_users as _au

    # Pin the wall OFF. Some module in the suite calls load_dotenv(), which pulls
    # the real .env (GESH_AUTH_ENABLED=1) into os.environ — so whether the login
    # middleware intercepts before our handler depends on TEST ORDER unless it is
    # set explicitly here. Same convention as tests/test_auth_users.py:29.
    monkeypatch.delenv("GESH_AUTH_ENABLED", raising=False)
    # Deletion is behind a default-OFF mixed-version safety flag; these tests
    # exercise the endpoint's behaviour once the operator has enabled it.
    monkeypatch.setenv("GESH_DELETION_SAGA_ENABLED", "1")
    monkeypatch.setattr(_au, "AUTH_DB_PATH", tmp_path / "auth.db")
    _au.init_auth_db()
    _au.create_user("cust@x.com", "pw")
    _au.create_user("other@x.com", "pw")
    return _au


def _seed(au, email: str, sym: str = "NVDA") -> str:
    """Give a user data in every customer-facing table."""
    uid = au.resolve_user_id(email)
    au.set_position(email, sym, 10)
    au.add_watch(email, sym)
    au.log_usage(email, "ticker_card")
    au.save_book_analysis(email, {"analysis": {"portfolio_score": 50}, "_fp": "fp-1"})
    au.start_mybook_job(uid, f"job-{email}", "fp-1", "book_analysis", 10)
    return uid


# ─── export ───────────────────────────────────────────────────────────────────

def test_export_returns_every_category_we_hold(au):
    import auth_users

    _seed(au, "cust@x.com")
    data = auth_users.export_user_data("cust@x.com")

    for key in ("account", "positions", "watchlist", "analyses", "usage"):
        assert key in data, f"export is missing {key}"
    assert data["positions"][0]["symbol"] == "NVDA"
    assert data["watchlist"] == ["NVDA"]
    assert len(data["analyses"]) == 1


def test_export_never_includes_the_password_hash(au):
    """An export is handed to the user — it must not carry a credential."""
    import auth_users

    _seed(au, "cust@x.com")
    blob = repr(auth_users.export_user_data("cust@x.com"))

    assert "password_hash" not in blob
    assert "pbkdf2" not in blob.lower()


def test_export_is_scoped_to_the_caller_only(au):
    """The classic multi-tenant leak — one user's export containing another's."""
    import auth_users

    _seed(au, "cust@x.com", "NVDA")
    _seed(au, "other@x.com", "TSLA")

    data = auth_users.export_user_data("cust@x.com")

    symbols = {p["symbol"] for p in data["positions"]}
    assert symbols == {"NVDA"}
    assert "TSLA" not in repr(data)


def test_export_endpoint_requires_a_session(au, monkeypatch):
    """No resolvable session → 401. (With the auth wall OFF the caller resolves
    to the owner by design, so the guard is asserted directly rather than via
    wall config — that is what a real anonymous request hits.)"""
    import server

    monkeypatch.setattr(server, "_session_email", lambda *_a, **_k: None)

    assert TestClient(server.app).get("/api/account/export").status_code == 401


def test_export_of_an_unknown_user_is_empty_not_an_error(au):
    import auth_users

    assert auth_users.export_user_data("ghost@x.com") == {}


# ─── deletion ─────────────────────────────────────────────────────────────────

def test_delete_purges_every_table_keyed_on_the_user(au):
    """A deletion that leaves rows behind is not a deletion."""
    import auth_users

    uid = _seed(au, "cust@x.com")
    auth_users.delete_user_data("cust@x.com")

    with auth_users._conn() as con:
        for table in ("customer_positions", "watchlist", "customer_analyses",
                      "usage_events", "mybook_jobs", "customer_book_prefs"):
            n = con.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id=?", (uid,)).fetchone()[0]
            assert n == 0, f"{table} still holds {n} rows after deletion"


def test_delete_removes_the_account_itself(au):
    import auth_users

    auth_users.delete_user_data("cust@x.com")

    assert auth_users.resolve_user_id("cust@x.com") is None


def test_delete_never_touches_another_users_data(au):
    """The single most important property of this endpoint."""
    import auth_users

    _seed(au, "cust@x.com", "NVDA")
    other_uid = _seed(au, "other@x.com", "TSLA")

    auth_users.delete_user_data("cust@x.com")

    assert auth_users.resolve_user_id("other@x.com") == other_uid
    assert [p["symbol"] for p in auth_users.get_positions("other@x.com")] == ["TSLA"]


def test_delete_reports_what_it_removed(au):
    """Receipts: the user is told what was destroyed, not just 'ok'."""
    import auth_users

    _seed(au, "cust@x.com")
    report = auth_users.delete_user_data("cust@x.com")

    assert report["deleted"] is True
    assert report["counts"]["customer_positions"] == 1
    assert report["counts"]["watchlist"] == 1


def test_deleting_an_unknown_user_is_a_no_op_not_a_crash(au):
    import auth_users

    assert auth_users.delete_user_data("ghost@x.com")["deleted"] is False


def test_delete_endpoint_requires_explicit_typed_confirmation(au, monkeypatch):
    """Irreversible + one click = accident. Require the word."""
    import server

    monkeypatch.setattr(server, "_session_email", lambda *_a, **_k: "cust@x.com")
    c = TestClient(server.app)

    assert c.post("/api/account/delete", json={}).status_code == 400
    assert c.post("/api/account/delete", json={"confirm": "yes"}).status_code == 400


def test_delete_endpoint_purges_when_confirmed(au, monkeypatch):
    import auth_users
    import server

    _seed(au, "cust@x.com")
    monkeypatch.setattr(server, "_session_email", lambda *_a, **_k: "cust@x.com")

    r = TestClient(server.app).post("/api/account/delete", json={"confirm": "DELETE"})

    assert r.status_code == 200
    assert auth_users.resolve_user_id("cust@x.com") is None


def test_delete_with_active_subscription_fails_closed_instead_of_warning(au, monkeypatch):
    """CONTRACT CHANGE (2026-07-23, deletion saga).

    Previously this endpoint erased the account and merely *warned* that Lemon
    Squeezy was still billing. That left a real person paying for an account that
    no longer existed. The saga now REFUSES to erase a subscribed account unless
    cancellation is confirmed (or the subscription is confirmed absent).

    With no reachable provider the request fails closed: nothing is deleted, the
    saga is parked, and the response carries no provider/config detail."""
    import server

    _seed(au, "cust@x.com")
    with au._conn() as con:
        con.execute("UPDATE users SET billing_sub_id='sub_123', billing_status='active' "
                    "WHERE email='cust@x.com'")
    monkeypatch.setattr(server, "_session_email", lambda *_a, **_k: "cust@x.com")
    monkeypatch.delenv("LEMON_SQUEEZY_API_KEY", raising=False)

    r = TestClient(server.app).post("/api/account/delete", json={"confirm": "DELETE"})

    assert r.status_code in (202, 409), "must not 200-and-erase a billed account"
    assert au.resolve_user_id("cust@x.com") is not None, "nothing may be erased"
    assert au.is_tombstoned("cust@x.com") is False
    body = r.json().get("detail", "")
    for leak in ("LEMON_SQUEEZY", "api.lemonsqueezy", "Traceback", "sub_123"):
        assert leak not in body, f"response leaks {leak}"


def test_owner_account_cannot_delete_itself_through_this_endpoint(au, monkeypatch):
    """The owner deleting their own account locks them out of their own product."""
    import server

    monkeypatch.setattr(server, "_session_email", lambda *_a, **_k: "georgy75@gmail.com")

    r = TestClient(server.app).post("/api/account/delete", json={"confirm": "DELETE"})

    assert r.status_code == 403


def test_delete_endpoint_requires_a_session(au, monkeypatch):
    import server

    monkeypatch.setattr(server, "_session_email", lambda *_a, **_k: None)

    r = TestClient(server.app).post("/api/account/delete", json={"confirm": "DELETE"})
    assert r.status_code == 401


def test_wall_off_resolves_to_owner_so_delete_is_refused(au):
    """Documents the observed behaviour: with GESH_AUTH_ENABLED off the caller
    resolves to the owner, and the owner-self-delete guard is what stops an
    anonymous local request from erasing the operator's account."""
    import server

    r = TestClient(server.app).post("/api/account/delete", json={"confirm": "DELETE"})

    assert r.status_code == 403


# ─── UI wiring ────────────────────────────────────────────────────────────────

def test_dashboard_exposes_export_and_delete_controls():
    """Rights the policy promises must be reachable in the product, not only by
    emailing support — the privacy policy points users at in-app controls."""
    html = (REPO / "templates" / "dashboard.html").read_text()

    assert "gesh-data-rights" in html
    assert "/api/account/export" in html
    assert "accountDelete" in html


# ─── L5 follow-up: the signup pause I claimed but never shipped ───────────────

def test_signup_pause_is_off_by_default(au):
    """Pausing revenue is the owner's decision, not a default I impose."""
    import server

    assert server._signups_paused() is False


def test_signup_pause_flag_hides_the_checkout(au, monkeypatch):
    import server

    monkeypatch.setenv("GESH_SIGNUPS_PAUSED", "1")
    body = TestClient(server.app).get("/pricing").text

    assert "not accepting new subscribers" in body.lower()
    assert "lemonsqueezy.com/checkout" not in body
