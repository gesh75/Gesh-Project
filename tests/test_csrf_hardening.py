"""CSRF / content-type hardening on cookie-authenticated mutations.

Audit finding (2026-07-22): `POST /api/mybook` succeeded with
`Content-Type: text/plain` and no anti-CSRF token, relying entirely on the
session cookie. The cookie IS HttpOnly + SameSite=Lax, which already withholds
it from cross-site POSTs — but text/plain is exactly the content-type that makes
a cross-origin POST a CORS "simple request" with no preflight, so accepting it
left the API one SameSite regression away from exploitable.

Both halves are now closed, and both are asserted here: a JSON endpoint requires
`application/json` for any request that carries a body, and a mutation whose
Origin names another host is refused.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

CUSTOMER = "cust@x.com"
OWNER = "georgy75@gmail.com"
SITE = "https://pay.geshfinance.com"
EVIL = "https://evil.example.com"


@pytest.fixture(autouse=True)
def _explicit_origins(no_default_origin):
    """This module tests the Origin policy itself, so the harness must NOT
    default one in — absent/null/mismatched origins are the subject."""
    return None


@pytest.fixture
def au(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import auth_users as _au
    monkeypatch.setattr(_au, "AUTH_DB_PATH", tmp_path / "auth.db")
    _au.init_auth_db()
    for e in (CUSTOMER, OWNER):
        _au.create_user(e, "test-password")
    return _au


@pytest.fixture
def client(au, monkeypatch):
    import server
    monkeypatch.setattr(server.auth_users, "AUTH_DB_PATH", au.AUTH_DB_PATH)
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: True)
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: CUSTOMER)
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda p, c: "allow", raising=False)
    monkeypatch.setattr(server.auth_users, "is_owner", lambda e: e == OWNER, raising=False)
    monkeypatch.setattr(server, "_latest_close", lambda s: 100.0, raising=True)
    monkeypatch.setattr(server, "_latest_close_meta",
                        lambda s: (100.0, {"source": "yahoo", "substituted": False}), raising=True)
    monkeypatch.setattr(server, "_prior_close", lambda s: 99.0, raising=True)
    monkeypatch.setattr(server, "_customer_scale_factor", lambda: 2.0, raising=True)
    # Trusted origins come from DEPLOYMENT CONFIG, never from the Host header.
    monkeypatch.setenv("APP_BASE_URL", SITE)
    monkeypatch.delenv("GESH_ALLOWED_ORIGINS", raising=False)
    monkeypatch.delenv("GESH_API_KEY", raising=False)
    c = TestClient(server.app, raise_server_exceptions=False, base_url=SITE)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    return server, c


_MUTATIONS = [
    ("POST", "/api/mybook", '{"symbol":"NVDA","shares":1}'),
    ("POST", "/api/watchlist", '{"symbol":"NVDA"}'),
    ("POST", "/api/preferences", '{"alerts_opt_out":true}'),
]


@pytest.mark.parametrize("method,path,body", _MUTATIONS)
def test_text_plain_body_is_refused(client, method, path, body):
    _server, c = client
    r = c.request(method, path, content=body, headers={"Content-Type": "text/plain"})
    assert r.status_code == 415, f"{path} accepted text/plain ({r.status_code})"
    assert "application/json" in r.text


@pytest.mark.parametrize("method,path,body", _MUTATIONS)
def test_same_origin_json_still_works(client, method, path, body):
    _server, c = client
    r = c.request(method, path, content=body,
                  headers={"Content-Type": "application/json", "Origin": SITE})
    assert r.status_code < 400, f"{path} broke a legitimate request: {r.status_code} {r.text[:200]}"


@pytest.mark.parametrize("method,path,body", _MUTATIONS)
def test_cross_origin_mutation_is_refused(client, method, path, body):
    _server, c = client
    r = c.request(method, path, content=body,
                  headers={"Content-Type": "application/json", "Origin": EVIL})
    assert r.status_code == 403, f"{path} accepted a cross-origin mutation"
    assert "Cross-origin" in r.text


def test_cross_origin_referer_is_refused(client):
    _server, c = client
    r = c.post("/api/mybook", content='{"symbol":"NVDA","shares":1}',
               headers={"Content-Type": "application/json", "Referer": EVIL + "/x"})
    assert r.status_code == 403


def test_cookie_auth_without_origin_or_referer_is_refused(client):
    """POLICY CHANGE (hardening pass). The earlier version allowed a missing
    Origin so headless callers kept working — but a COOKIE-authenticated request
    with no Origin is precisely the shape a forged navigation/redirect can take,
    and "no Origin" is not evidence of a non-browser client. Headless callers now
    prove themselves with X-API-Key instead (asserted below)."""
    _server, c = client
    r = c.post("/api/mybook", json={"symbol": "NVDA", "shares": 2})
    assert r.status_code == 403


def test_api_key_client_needs_no_origin(client, monkeypatch):
    """CSRF is a cookie problem. A caller that authenticates with the service
    key carries no ambient authority for a hostile page to borrow."""
    server, c = client
    monkeypatch.setenv("GESH_API_KEY", "svc-key-123")
    c.cookies.clear()
    r = c.post("/api/mybook", json={"symbol": "NVDA", "shares": 2},
               headers={"X-API-Key": "svc-key-123"})
    assert r.status_code != 403


def test_api_key_is_not_inferred_from_a_missing_origin(client, monkeypatch):
    """A wrong/absent key must not be treated as "probably an API client"."""
    _server, c = client
    monkeypatch.setenv("GESH_API_KEY", "svc-key-123")
    r = c.post("/api/mybook", json={"symbol": "NVDA", "shares": 2},
               headers={"X-API-Key": "wrong"})
    assert r.status_code == 403


def test_json_content_type_with_charset_is_accepted(client):
    """`application/json;charset=UTF-8` is a valid JSON content type."""
    _server, c = client
    r = c.post("/api/mybook", content='{"symbol":"NVDA","shares":1}',
               headers={"Content-Type": "application/json;charset=UTF-8",
                        "Origin": SITE})
    assert r.status_code == 200, r.text


def test_origin_null_is_refused(client):
    """Sandboxed iframes and some redirects send the opaque origin."""
    _server, c = client
    r = c.post("/api/mybook", json={"symbol": "NVDA", "shares": 1},
               headers={"Origin": "null"})
    assert r.status_code == 403


def test_bodyless_delete_without_origin_is_refused(client):
    """A bodyless DELETE is exactly as forgeable as a POST."""
    _server, c = client
    c.post("/api/mybook", json={"symbol": "NVDA", "shares": 1},
           headers={"Origin": SITE})
    assert c.delete("/api/mybook/NVDA").status_code == 403


def test_wrong_scheme_is_refused(client):
    """netloc-only comparison would treat http:// and https:// as one origin."""
    _server, c = client
    r = c.post("/api/mybook", json={"symbol": "NVDA", "shares": 1},
               headers={"Origin": "http://pay.geshfinance.com"})
    assert r.status_code == 403


def test_wrong_port_is_refused(client):
    _server, c = client
    r = c.post("/api/mybook", json={"symbol": "NVDA", "shares": 1},
               headers={"Origin": SITE + ":8443"})
    assert r.status_code == 403


def test_default_port_is_equivalent_to_no_port(client):
    """https://x and https://x:443 are the SAME origin."""
    _server, c = client
    r = c.post("/api/mybook", json={"symbol": "NVDA", "shares": 1},
               headers={"Origin": SITE + ":443"})
    assert r.status_code == 200, r.text


def test_host_header_alone_cannot_authorise_an_origin(client, monkeypatch):
    """The Host header is attacker-supplied. Keyed off it, "Origin: evil.test"
    plus "Host: evil.test" would validate against itself."""
    _server, c = client
    monkeypatch.setenv("APP_BASE_URL", SITE)
    monkeypatch.delenv("GESH_ALLOWED_ORIGINS", raising=False)
    r = c.post("/api/mybook", json={"symbol": "NVDA", "shares": 1},
               headers={"Origin": "https://evil.test", "Host": "evil.test"})
    assert r.status_code == 403


def test_method_override_header_is_refused(client):
    """The app does not support overrides; ignoring one would let a request
    reshape itself past the mutation check."""
    _server, c = client
    r = c.post("/api/mybook", json={"symbol": "NVDA", "shares": 1},
               headers={"Origin": SITE, "X-HTTP-Method-Override": "DELETE"})
    assert r.status_code == 400


def test_form_encoded_mutation_is_refused(client):
    _server, c = client
    r = c.post("/api/mybook", content="symbol=NVDA&shares=1",
               headers={"Content-Type": "application/x-www-form-urlencoded",
                        "Origin": SITE})
    assert r.status_code == 415


def test_bodyless_delete_is_not_blocked(client):
    """DELETE /api/mybook/{sym} sends no body and no content-type."""
    _server, c = client
    c.post("/api/mybook", json={"symbol": "NVDA", "shares": 1})
    r = c.delete("/api/mybook/NVDA", headers={"Origin": SITE})
    assert r.status_code == 200


def test_get_requests_are_untouched(client):
    _server, c = client
    r = c.get("/api/mybook", headers={"Origin": EVIL})
    assert r.status_code == 200, "a GET must not be refused by the mutation guard"


def test_billing_webhook_is_exempt(client):
    """HMAC-authenticated, cross-origin by design, and not cookie-authenticated —
    the guard must not stand between Lemon Squeezy and provisioning."""
    _server, c = client
    r = c.post("/api/billing/webhook", content="{}",
               headers={"Content-Type": "text/plain", "Origin": "https://lemonsqueezy.com"})
    assert r.status_code not in (403, 415), f"webhook blocked by CSRF guard: {r.status_code}"


def test_allowed_origins_env_is_honoured(client, monkeypatch):
    """Behind a proxy that rewrites Host, the public origin comes from config."""
    _server, c = client
    monkeypatch.setenv("GESH_ALLOWED_ORIGINS", "app.example.com")
    r = c.post("/api/mybook", json={"symbol": "NVDA", "shares": 1},
               headers={"Origin": "https://app.example.com"})
    assert r.status_code == 200


def test_malformed_origin_is_refused_not_passed(client):
    _server, c = client
    r = c.post("/api/mybook", content='{"symbol":"NVDA","shares":1}',
               headers={"Content-Type": "application/json", "Origin": "@@@not a url@@@"})
    assert r.status_code == 403


# ─── documented cookie configuration (audit item 4) ──────────────────────────
# Verified in code rather than asserted from memory. Attributes as shipped:
#   Name      gesh_session
#   HttpOnly  yes  — unconditional on both set_cookie sites
#   SameSite  Lax  — blocks the cookie on cross-site POST, which is the primary
#                    CSRF defense; the middleware below is defense in depth
#   Secure    per-request (_cookie_secure): forced on for a non-localhost host
#             when APP_BASE_URL is https, derived from X-Forwarded-Proto behind
#             the tunnel otherwise, overridable via GESH_COOKIE_SECURE
#   Path      "/" (Starlette default; delete_cookie pins path="/")
#   Domain    not set — host-only, so it is never sent to a sibling subdomain
#   Max-Age   auth_users.TOKEN_TTL_SECONDS

def test_session_cookie_attributes_are_as_documented(client):
    """Asserts the ACTUAL Set-Cookie header the app emits, with the same
    arguments production uses — no escape hatch, so it cannot pass vacuously."""
    server, _c = client
    from starlette.responses import JSONResponse
    resp = JSONResponse({})
    resp.set_cookie(
        server.auth_users.COOKIE_NAME, "tok-123",
        httponly=True, samesite="lax",
        max_age=server.auth_users.TOKEN_TTL_SECONDS, secure=True)
    raw = resp.headers["set-cookie"]
    low = raw.lower()
    assert raw.startswith(server.auth_users.COOKIE_NAME + "=")
    assert "httponly" in low
    assert "samesite=lax" in low
    assert "secure" in low
    assert "path=/" in low
    assert "domain=" not in low, "a Domain attribute would widen the cookie to subdomains"
    assert "max-age=" in low


def test_production_cookie_call_sites_all_set_the_documented_flags():
    """Guard against a NEW set_cookie site shipping without HttpOnly/SameSite."""
    from pathlib import Path
    src = Path("server.py").read_text()
    lines = src.splitlines()
    starts = [i for i, ln in enumerate(lines) if "set_cookie(" in ln]
    sites = [" ".join(" ".join(lines[i:i + 8]).split())
             for i in starts if "COOKIE_NAME" in " ".join(lines[i:i + 3])]
    assert sites, "session cookie is no longer set via auth_users.COOKIE_NAME"
    for flat in sites:
        assert "httponly=True" in flat, flat[:160]
        assert 'samesite="lax"' in flat, flat[:160]
        assert "secure=_cookie_secure(request)" in flat, flat[:160]


def test_cookie_secure_is_forced_on_an_https_deployment(client, monkeypatch):
    """A bypassed proxy must not be able to downgrade a production cookie."""
    server, _ = client
    monkeypatch.delenv("GESH_COOKIE_SECURE", raising=False)
    monkeypatch.setenv("APP_BASE_URL", "https://pay.geshfinance.com")

    class _Req:
        headers = {"host": "pay.geshfinance.com"}          # no X-Forwarded-Proto
        url = type("U", (), {"scheme": "http", "hostname": "pay.geshfinance.com"})()
    assert server._cookie_secure(_Req()) is True

    class _Local:
        headers = {"host": "localhost:9000"}
        url = type("U", (), {"scheme": "http", "hostname": "localhost"})()
    assert server._cookie_secure(_Local()) is False        # owner's localhost preserved


def test_expired_or_invalid_session_cannot_mutate(client, monkeypatch):
    server, c = client
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: None)
    r = c.post("/api/mybook", json={"symbol": "NVDA", "shares": 1},
               headers={"Origin": SITE})
    assert r.status_code == 401


def test_wrong_role_cannot_mutate_owner_state(client, monkeypatch):
    """Role scope is enforced independently of CSRF: a perfectly-formed,
    same-origin, JSON, authenticated CUSTOMER request must still be refused on
    an owner-only mutation."""
    server, c = client
    r = c.post("/api/config", json={"max_open_positions": 99}, headers={"Origin": SITE})
    assert r.status_code == 403


def test_owner_can_still_mutate_owner_state(client, monkeypatch):
    """The guard must not lock the OPERATOR out of their own controls.

    `update_config` is stubbed: the real one writes the tracked production
    `gesh_config.json`, and a test that mutates a repo file breaks the suite's
    zero-real-writes contract (caught in review — an earlier version of this
    test bumped `last_updated` in the working tree)."""
    server, c = client
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: OWNER)
    written: dict = {}

    def _fake_update(**kw):
        written.update(kw)
        return {"ok": True, **kw}
    monkeypatch.setattr(server, "update_config", _fake_update, raising=True)

    r = c.post("/api/config", json={"max_open_positions": 7}, headers={"Origin": SITE})
    assert r.status_code == 200, r.text
    assert written == {"max_open_positions": 7}


def test_tenant_scope_holds_under_a_valid_csrf_request(client, monkeypatch, au):
    """A well-formed request writes to the CALLER's book, never another's."""
    server, c = client
    c.post("/api/mybook", json={"symbol": "NVDA", "shares": 4}, headers={"Origin": SITE})
    assert [p["shares"] for p in au.get_positions(CUSTOMER)] == [4.0]
    assert au.get_positions(OWNER) == []
