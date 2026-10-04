"""B8: double opt-in email capture for the free tools (marketing_capture + routers.marketing).

Offline: the mail provider is replaced by a recorder, storage is a per-test SQLite file,
and the auth wall is ON so the public-path allowlist is exercised for real.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from starlette.testclient import TestClient

import auth_users
import email_sender
import marketing_capture as mc

EMAIL = "Learner@Example.com"
NORM = "learner@example.com"
OTHER_PATHS = [
    "/marketing",
    "/marketing/",
    "/marketing/confirm/x",
    "/marketing/unsubscribe/x",
    "/marketing/subscribe/x",
    "/marketing/confirmx",
    "/api/marketing",
    "/api/marketing/subscribe/x",
    "/api/marketing/other",
]


class Outbox:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.ok = True

    def send(self, to, subject, html, headers=None, log_recipient=True):  # noqa: ANN001
        self.sent.append(
            {
                "to": to,
                "subject": subject,
                "html": html,
                "headers": headers or {},
                "log_recipient": log_recipient,
            }
        )
        return self.ok

    def token(self, which: str, idx: int = -1) -> str:
        html = self.sent[idx]["html"]
        m = re.search(rf"/marketing/{which}\?token=([^\"&<]+)", html)
        assert m, which
        return m.group(1)


@pytest.fixture
def box(monkeypatch: pytest.MonkeyPatch) -> Outbox:
    out = Outbox()
    monkeypatch.setenv("RESEND_API_KEY", "re_fake_key_for_tests")
    monkeypatch.setattr(email_sender, "send_email", out.send)
    return out


@pytest.fixture(autouse=True)
def mkt_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "marketing.db"
    monkeypatch.setattr(mc, "MARKETING_DB_PATH", path)
    monkeypatch.setenv("GESH_AUTH_DB", str(tmp_path / "auth.db"))
    monkeypatch.setenv("GESH_AUTH_SECRET", "test-secret-deterministic")
    monkeypatch.setenv("GESH_AUTH_ENABLED", "1")
    monkeypatch.setattr(auth_users, "AUTH_DB_PATH", tmp_path / "auth.db")
    auth_users.init_auth_db()
    return path


@pytest.fixture
def client() -> TestClient:
    import server

    return TestClient(server.app, raise_server_exceptions=False)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> dict:
    state = {"t": 1_800_000_000}
    monkeypatch.setattr(mc, "_now", lambda: state["t"])
    return state


def _rows(path: Path) -> list[sqlite3.Row]:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        return con.execute("SELECT * FROM marketing_subscribers").fetchall()
    finally:
        con.close()


def _post(client: TestClient, **over):
    body = {"email": EMAIL, "consent": True, "source": "kelly-calculator", "website": ""} | over
    return client.post("/api/marketing/subscribe", json=body)


# ── tokens ──────────────────────────────────────────────────────────────────────


def test_token_roundtrip_and_purpose_binding(clock) -> None:
    sub = "a" * 32
    tok = mc.issue_token(sub, mc.TYP_CONFIRM, 60)
    assert mc.verify_token(tok, mc.TYP_CONFIRM) == sub
    assert mc.verify_token(tok, mc.TYP_UNSUB) is None  # confirm link is not an unsubscribe link


def test_token_tamper_is_rejected(clock) -> None:
    tok = mc.issue_token("a" * 32, mc.TYP_CONFIRM, 60)
    payload, sig = tok.split(".")
    other = mc.issue_token("b" * 32, mc.TYP_CONFIRM, 60).split(".")[0]
    bad_sig = sig[:-2] + ("AA" if sig[-2:] != "AA" else "BB")
    for bad in (
        f"{other}.{sig}",
        f"{payload}.{bad_sig}",
        payload,
        f"{payload}.",
        "",
        "..",
        "x" * 600,
        f"{payload}.{sig}.extra",
    ):
        assert mc.verify_token(bad, mc.TYP_CONFIRM) is None
    assert mc.verify_token(None, mc.TYP_CONFIRM) is None


def test_token_expires(clock) -> None:
    tok = mc.issue_token("a" * 32, mc.TYP_CONFIRM, mc.CONFIRM_TTL_SECONDS)
    clock["t"] += mc.CONFIRM_TTL_SECONDS - 1
    assert mc.verify_token(tok, mc.TYP_CONFIRM) == "a" * 32
    clock["t"] += 2
    assert mc.verify_token(tok, mc.TYP_CONFIRM) is None


def test_token_signed_with_other_secret_is_rejected(monkeypatch: pytest.MonkeyPatch, clock) -> None:
    tok = mc.issue_token("a" * 32, mc.TYP_CONFIRM, 60)
    monkeypatch.setenv("GESH_AUTH_SECRET", "a-different-secret")
    assert mc.verify_token(tok, mc.TYP_CONFIRM) is None


def test_token_comparison_is_constant_time(monkeypatch: pytest.MonkeyPatch, clock) -> None:
    calls: list[int] = []
    real = mc.hmac.compare_digest
    monkeypatch.setattr(mc.hmac, "compare_digest", lambda a, b: calls.append(1) or real(a, b))
    mc.verify_token(mc.issue_token("a" * 32, mc.TYP_CONFIRM, 60), mc.TYP_CONFIRM)
    assert calls


def test_session_tokens_do_not_validate_as_marketing_tokens(clock) -> None:
    auth_users.create_user("owner@example.com", "pw-not-real-123")
    session = auth_users.issue_token("owner@example.com")
    assert mc.verify_token(session, mc.TYP_CONFIRM) is None
    assert mc.verify_token(session, mc.TYP_UNSUB) is None


# ── validation ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "plain",
        "a@b",
        "a@b.c",
        "@example.com",
        "a@@example.com",
        "a b@example.com",
        "a@exa mple.com",
        ".a@example.com",
        "a.@example.com",
        "a..b@example.com",
        "a@-example.com",
        "a@example..com",
        "a@example.com\r\nBcc: x@y.com",
        "a@exam\nple.com",
        "ünï@example.com",
        "a@exämple.com",
        "<script>@example.com",
        "a@example.c0m",
        "x" * 250 + "@example.com",
        "a" * 65 + "@example.com",
        None,
        5,
        ["a@example.com"],
    ],
)
def test_email_validation_rejects(bad) -> None:
    assert mc.normalize_email(bad) is None


def test_email_validation_normalises_lowercase() -> None:
    assert mc.normalize_email("  Learner+tag@Example.COM ") == "learner+tag@example.com"


# ── double opt-in state machine ─────────────────────────────────────────────────


def test_happy_path_pending_then_confirmed_then_unsubscribed(client, box, mkt_db, clock) -> None:
    r = _post(client)
    assert r.status_code == 200 and r.json()["ok"] is True
    [row] = _rows(mkt_db)
    assert row["status"] == "pending" and row["email"] == NORM
    assert row["source"] == "kelly-calculator" and row["confirmed_at"] is None
    assert row["consent_text"] == mc.CONSENT_TEXT
    assert len(box.sent) == 1 and box.sent[0]["to"] == NORM

    # GET on the link never mutates (mail scanners prefetch)
    tok = box.token("confirm")
    page = client.get("/marketing/confirm", params={"token": tok})
    assert page.status_code == 200 and _rows(mkt_db)[0]["status"] == "pending"
    done = client.post("/marketing/confirm", params={"token": tok})
    assert done.status_code == 200
    row = _rows(mkt_db)[0]
    assert row["status"] == "confirmed" and row["confirmed_at"].endswith("Z")
    # idempotent
    assert client.post("/marketing/confirm", params={"token": tok}).status_code == 200
    assert _rows(mkt_db)[0]["status"] == "confirmed"

    utok = box.token("unsubscribe")
    assert client.get("/marketing/unsubscribe", params={"token": utok}).status_code == 200
    assert _rows(mkt_db)[0]["status"] == "confirmed"
    assert client.post("/marketing/unsubscribe", params={"token": utok}).status_code == 200
    row = _rows(mkt_db)[0]
    assert row["status"] == "unsubscribed" and row["unsubscribed_at"].endswith("Z")
    # a stale confirm link cannot resurrect an unsubscribed address
    assert client.post("/marketing/confirm", params={"token": tok}).status_code == 400
    assert _rows(mkt_db)[0]["status"] == "unsubscribed"
    assert client.get("/marketing/confirm", params={"token": tok}).status_code == 400


def test_expired_and_forged_confirm_links_do_not_confirm(client, box, mkt_db, clock) -> None:
    _post(client)
    tok = box.token("confirm")
    clock["t"] += mc.CONFIRM_TTL_SECONDS + 5
    assert client.post("/marketing/confirm", params={"token": tok}).status_code == 400
    assert client.get("/marketing/confirm", params={"token": tok}).status_code == 400
    assert client.post("/marketing/confirm", params={"token": tok[:-3] + "AAA"}).status_code == 400
    assert client.post("/marketing/confirm").status_code == 400
    assert _rows(mkt_db)[0]["status"] == "pending"


def test_unsubscribe_one_click_post_body_and_headers(client, box, mkt_db, clock) -> None:
    _post(client)
    sent = box.sent[0]
    assert sent["log_recipient"] is False
    url = sent["headers"]["List-Unsubscribe"].strip("<>")
    assert sent["headers"]["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    token = parse_qs(urlparse(url).query)["token"][0]
    assert urlparse(url).path == "/marketing/unsubscribe"
    r = client.post(
        urlparse(url).path + "?" + urlparse(url).query,
        content=b"List-Unsubscribe=One-Click",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code == 200
    assert _rows(mkt_db)[0]["status"] == "unsubscribed"
    assert box.token("unsubscribe") == token  # same link is in the visible body too


def test_resubscribe_after_unsubscribe_needs_fresh_confirmation(client, box, mkt_db, clock) -> None:
    _post(client)
    client.post("/marketing/confirm", params={"token": box.token("confirm")})
    client.post("/marketing/unsubscribe", params={"token": box.token("unsubscribe")})
    clock["t"] += mc.EMAIL_SEND_COOLDOWN + 1
    _post(client)
    assert _rows(mkt_db)[0]["status"] == "pending" and len(box.sent) == 2


def test_confirmation_email_content_is_honest_and_escaped(client, box, clock) -> None:
    _post(client)
    html = box.sent[0]["html"]
    assert "at most once a week" in html and "not investment advice" in html
    assert "Unsubscribe" in html and "/marketing/confirm?token=" in html
    assert "guarantee" not in html.lower() or "no returns" in html.lower()
    assert "<script" not in html


# ── users table untouched ───────────────────────────────────────────────────────


def test_signup_never_writes_users_table_or_creates_accounts(client, box, mkt_db, clock) -> None:
    def snapshot() -> list[tuple]:
        con = sqlite3.connect(auth_users.AUTH_DB_PATH)
        try:
            names = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            return [
                (n, con.execute(f"SELECT * FROM {n} ORDER BY 1").fetchall())
                for n in sorted(names)
                if n != "sqlite_sequence"
            ]
        finally:
            con.close()

    before = snapshot()
    _post(client)
    client.post("/marketing/confirm", params={"token": box.token("confirm")})
    client.post("/marketing/unsubscribe", params={"token": box.token("unsubscribe")})
    assert snapshot() == before
    con = sqlite3.connect(auth_users.AUTH_DB_PATH)
    try:
        assert con.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        con.close()
    assert not any(t.startswith("marketing") for t in tables)
    assert (
        auth_users.get_user_by_email(NORM) is None
        if hasattr(auth_users, "get_user_by_email")
        else True
    )


def test_table_schema_and_idempotent_init(mkt_db) -> None:
    mc._connect().close()
    mc._connect().close()  # CREATE TABLE IF NOT EXISTS: re-running is a no-op
    con = sqlite3.connect(mkt_db)
    cols = {r[1] for r in con.execute("PRAGMA table_info(marketing_subscribers)")}
    con.close()
    assert cols == {
        "id",
        "email",
        "status",
        "source",
        "created_at",
        "confirmed_at",
        "unsubscribed_at",
        "consent_text",
        "ip_hash",
    }
    assert (mkt_db.stat().st_mode & 0o077) == 0


def test_ip_is_stored_hashed_not_raw(client, box, mkt_db, clock) -> None:
    _post(client)
    row = _rows(mkt_db)[0]
    assert row["ip_hash"] and "testclient" not in row["ip_hash"] and len(row["ip_hash"]) == 32


# ── abuse controls ──────────────────────────────────────────────────────────────


def test_honeypot_gets_identical_success_and_stores_nothing(client, box, mkt_db, clock) -> None:
    real = _post(client, email="real@example.com")
    bot = _post(client, email="bot@example.com", website="http://spam.example")
    assert bot.status_code == real.status_code == 200
    assert bot.json() == real.json()
    assert [r["email"] for r in _rows(mkt_db)] == ["real@example.com"]
    assert len(box.sent) == 1


def test_missing_consent_and_bad_email_are_rejected_without_storing(
    client, box, mkt_db, clock
) -> None:
    assert _post(client, consent=False).status_code == 400
    assert _post(client, email="nope").status_code == 400
    assert _post(client, consent="false").status_code == 400
    assert _rows(mkt_db) == [] and box.sent == []


def test_per_ip_rate_limit(client, box, mkt_db, clock) -> None:
    codes = [
        _post(client, email=f"user{i}@example.com").status_code for i in range(mc.IP_LIMIT + 2)
    ]
    assert codes[: mc.IP_LIMIT] == [200] * mc.IP_LIMIT
    assert codes[mc.IP_LIMIT :] == [429, 429]
    assert len(_rows(mkt_db)) == mc.IP_LIMIT
    clock["t"] += mc.IP_WINDOW + 1  # window slides
    assert _post(client, email="later@example.com").status_code == 200


def test_invalid_attempts_also_count_toward_ip_limit(client, box, clock) -> None:
    for _ in range(mc.IP_LIMIT):
        assert _post(client, email="bad").status_code == 400
    assert _post(client).status_code == 429


def test_different_ips_have_independent_limits(box, clock) -> None:
    for _ in range(mc.IP_LIMIT):
        mc.request_subscription(email="x", consent=True, source="", ip="1.1.1.1")
    assert (
        mc.request_subscription(email="x", consent=True, source="", ip="1.1.1.1").kind
        == "rate_limited"
    )
    assert (
        mc.request_subscription(email="x", consent=True, source="", ip="2.2.2.2").kind == "invalid"
    )


def test_per_email_resend_cooldown_and_daily_cap(box, mkt_db, clock) -> None:
    def go(i: int):
        return mc.request_subscription(email=EMAIL, consent=True, source="", ip=f"10.0.0.{i}")

    first = go(1)
    assert first.kind == "accepted" and first.job is not None
    again = go(2)  # inside the cooldown: same answer, no new mail job
    assert again.kind == "accepted" and again.job is None
    for n in range(2, mc.EMAIL_SEND_LIMIT + 1):
        clock["t"] += mc.EMAIL_SEND_COOLDOWN + 1
        assert go(10 + n).job is not None
    clock["t"] += mc.EMAIL_SEND_COOLDOWN + 1
    capped = go(99)
    assert capped.kind == "accepted" and capped.job is None  # daily cap reached
    clock["t"] += mc.EMAIL_SEND_WINDOW + 1
    assert go(100).job is not None
    assert len(_rows(mkt_db)) == 1


def test_global_daily_send_cap(box, mkt_db, clock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mc, "GLOBAL_SEND_LIMIT", 2)
    jobs = [
        mc.request_subscription(
            email=f"u{i}@example.com", consent=True, source="", ip=f"9.9.9.{i}"
        ).job
        for i in range(4)
    ]
    assert [j is not None for j in jobs] == [True, True, False, False]


def test_confirmed_address_is_never_mailed_again(client, box, mkt_db, clock) -> None:
    _post(client)
    client.post("/marketing/confirm", params={"token": box.token("confirm")})
    clock["t"] += mc.EMAIL_SEND_WINDOW + 1
    r = _post(client, email=EMAIL.upper())
    assert r.status_code == 200 and len(box.sent) == 1
    assert _rows(mkt_db)[0]["status"] == "confirmed"


def test_unconfirmed_rows_are_purged_after_retention(client, box, mkt_db, clock) -> None:
    _post(client, email="old@example.com")
    clock["t"] += mc.PENDING_RETENTION_SECONDS + 10
    _post(client, email="new@example.com")
    assert [r["email"] for r in _rows(mkt_db)] == ["new@example.com"]


def test_oversized_body_is_refused(client, box, mkt_db) -> None:
    r = client.post("/api/marketing/subscribe", json={"email": "a@example.com", "pad": "x" * 6000})
    assert r.status_code == 413 and not mkt_db.exists()


def test_unknown_source_is_normalised(client, box, mkt_db, clock) -> None:
    _post(client, source="<script>alert(1)</script>")
    assert _rows(mkt_db)[0]["source"] == mc.DEFAULT_SOURCE


# ── enumeration safety ──────────────────────────────────────────────────────────


def test_responses_identical_for_new_pending_confirmed_and_cooldown(
    client, box, mkt_db, clock
) -> None:
    new = _post(client, email="fresh@example.com")
    pending_again = _post(client, email="fresh@example.com")  # cooldown path
    _post(client, email="member@example.com")
    client.post("/marketing/confirm", params={"token": box.token("confirm")})
    confirmed = _post(client, email="member@example.com")
    for r in (pending_again, confirmed):
        assert (r.status_code, r.json(), r.headers.get("content-type")) == (
            new.status_code,
            new.json(),
            new.headers.get("content-type"),
        )
    assert new.json()["message"] == mc.GENERIC_ACCEPTED


def test_form_fallback_responses_identical_too(client, box, mkt_db, clock) -> None:
    def post(email: str):
        return client.post(
            "/marketing/subscribe",
            data={"email": email, "consent": "1", "source": "portfolio-xray", "website": ""},
        )

    a, b = post("one@example.com"), post("one@example.com")
    assert a.status_code == b.status_code == 200 and a.text == b.text
    assert "confirmation link" in a.text and len(box.sent) == 1


# ── graceful no-sender path ─────────────────────────────────────────────────────


def test_no_sender_configured_degrades_gracefully(
    client, mkt_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    r = _post(client)
    assert r.status_code == 200
    body = r.json()
    assert (
        body["ok"] is False
        and body["unavailable"] is True
        and body["message"] == mc.MSG_UNAVAILABLE
    )
    assert _rows(mkt_db) == []  # no address collected that we cannot confirm
    form = client.post("/marketing/subscribe", data={"email": EMAIL, "consent": "1"})
    assert form.status_code == 200 and "not open yet" in form.text


def test_provider_failure_never_500s_or_logs_address(client, box, mkt_db, clock, caplog) -> None:
    box.ok = False
    with caplog.at_level(logging.DEBUG):
        r = _post(client)
    assert r.status_code == 200
    assert NORM not in caplog.text and "example.com" not in caplog.text


def test_email_sender_redacts_recipient_in_logs(monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    with caplog.at_level(logging.DEBUG):
        assert (
            email_sender.send_email(
                "secret.person@example.com", "s", "<p>x</p>", log_recipient=False
            )
            is False
        )
    assert "secret.person" not in caplog.text


def test_storage_failure_returns_friendly_message(
    client, box, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mc, "MARKETING_DB_PATH", Path("/nonexistent-dir/x.db"))
    r = _post(client)
    assert r.status_code == 200 and r.json()["unavailable"] is True


# ── response hygiene ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "method,path,kw",
    [
        ("post", "/api/marketing/subscribe", {"json": {"email": "x"}}),
        ("post", "/marketing/subscribe", {"data": {"email": "x"}}),
        ("get", "/marketing/confirm?token=bad", {}),
        ("post", "/marketing/confirm?token=bad", {}),
        ("get", "/marketing/unsubscribe?token=bad", {}),
        ("post", "/marketing/unsubscribe?token=bad", {}),
    ],
)
def test_responses_are_noindex_nostore_and_bounded(client, method, path, kw) -> None:
    r = getattr(client, method)(path, **kw)
    assert r.status_code != 500
    assert r.headers["x-robots-tag"] == "noindex, nofollow"
    assert "no-store" in r.headers["cache-control"]
    assert r.headers["referrer-policy"] == "no-referrer"


def test_token_pages_escape_everything_and_have_one_h1(client, box, clock) -> None:
    _post(client)
    tok = box.token("confirm")
    page = client.get("/marketing/confirm", params={"token": tok})
    assert len(re.findall(r"<h1[ >]", page.text)) == 1
    evil = client.get("/marketing/unsubscribe", params={"token": '"><script>alert(1)</script>'})
    assert "<script>alert(1)" not in evil.text and evil.status_code == 400


def test_robots_and_sitemap_do_not_mention_marketing(client) -> None:
    assert "/marketing" not in client.get("/robots.txt").text
    assert "/marketing" not in client.get("/sitemap.xml").text


# ── auth wall: exact public paths only ─────────────────────────────────────────


def test_exact_public_paths_registered() -> None:
    added = {
        "/api/marketing/subscribe",
        "/marketing/subscribe",
        "/marketing/confirm",
        "/marketing/unsubscribe",
    }
    assert added <= set(auth_users.PUBLIC_EXACT_PATHS)
    assert not any(p.endswith("*") or p.endswith("/") for p in added)
    assert not any("marketing" in p for p in auth_users.PUBLIC_PREFIXES)


@pytest.mark.parametrize("path", OTHER_PATHS)
def test_neighbouring_paths_stay_gated(client, path) -> None:
    assert auth_users.is_public_path(path) is False
    assert client.get(path, follow_redirects=False).status_code in (302, 401)
    assert client.post(path, follow_redirects=False).status_code in (302, 401, 403)


def test_private_routes_still_gated(client) -> None:
    assert client.get("/api/portfolio", follow_redirects=False).status_code == 401
    assert client.get("/api/me", follow_redirects=False).status_code in (200, 401)
    assert client.post("/api/analyze-ticker", json={}, follow_redirects=False).status_code == 401
    assert client.get("/mybook", follow_redirects=False).status_code == 302


def test_public_endpoints_reachable_logged_out(client, box, clock) -> None:
    assert _post(client).status_code == 200
    assert (
        client.get("/marketing/confirm?token=x").status_code == 400
    )  # handler reached, not 401/302


def test_get_on_subscribe_endpoints_is_not_a_signup(client, mkt_db) -> None:
    assert client.get("/api/marketing/subscribe").status_code == 405
    assert client.get("/marketing/subscribe").status_code == 405
    assert not mkt_db.exists() or _rows(mkt_db) == []


# ── tool pages: UI contract ─────────────────────────────────────────────────────

TOOL_SLUGS = ["portfolio-xray", "monte-carlo-simulator", "kelly-calculator"]


@pytest.mark.parametrize("slug", TOOL_SLUGS)
def test_tool_page_has_accessible_form_and_keeps_contract(client, slug) -> None:
    html = client.get(f"/tools/{slug}").text
    assert len(re.findall(r"<h1[ >]", html)) == 1
    assert '<form id="capture-form" method="post" action="/marketing/subscribe"' in html
    assert 'for="cap-email"' in html and 'id="cap-email"' in html and 'type="email"' in html
    assert 'for="cap-ok"' in html and 'role="status" aria-live="polite"' in html
    assert f'name="source" value="{slug}"' in html
    for needed in ("at most once a week", "not investment advice", "unsubscribe"):
        assert needed in html
    assert 'id="tool-form"' in html and 'id="tool-out"' in html  # calculator untouched
    # calculator scripts remain network-free; only the identified capture script may fetch
    plain = "\n".join(re.findall(r"<script>(.*?)</script>", html, re.S))
    assert "fetch(" not in plain and "XMLHttpRequest" not in plain
    cap = re.findall(r'<script id="capture-js">(.*?)</script>', html, re.S)
    assert len(cap) == 1 and cap[0].count("fetch(") == 1
    assert "/api/marketing/subscribe" in cap[0] and "innerHTML" not in cap[0]
    assert not re.search(r"<script[^>]+src=", html)
    assert "text/javascript" not in html and "onsubmit=" not in html and "onclick=" not in html
    assert "x-robots-tag" not in client.get(f"/tools/{slug}").headers


def test_tools_index_has_no_signup_form(client) -> None:
    html = client.get("/tools").text
    assert "capture-form" not in html and "capture-js" not in html


def test_csp_allows_the_inline_enhancement_and_same_origin_post(client) -> None:
    csp = client.get("/tools/kelly-calculator").headers["content-security-policy"]
    assert "script-src 'self' 'unsafe-inline'" in csp
    assert "connect-src 'self'" in csp and "form-action 'self'" in csp


def test_json_endpoint_rejects_non_json_content_type(client, mkt_db) -> None:
    # the repo's CSRF guard keeps /api/* JSON-only; the no-JS form therefore uses /marketing/subscribe
    r = client.post("/api/marketing/subscribe", data={"email": EMAIL, "consent": "1"})
    assert r.status_code == 415 and not mkt_db.exists()
