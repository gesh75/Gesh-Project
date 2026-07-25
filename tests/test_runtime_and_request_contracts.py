"""PR #105 blocker regressions: runtime contract, dependency contract, and the
request-boundary hardening (malformed Origin, declared Content-Type, duplicate
singleton headers).

Each test names the blocker it closes:

  PR105-RUN-01  the repo declares Python 3.12 but the daily chain hardcoded a
                python3.14 binary, so the deterministic chain could not launch.
  PR105-RUN-02  CI installed requirements.lock while setup/docs installed
                requirements.txt — the tested tree was not the shipped tree.
  PR105-SEC-01  `u.port` was read OUTSIDE the try, so a malformed Origin port
                raised ValueError and returned a pre-auth HTTP 500.
  PR105-SEC-02  a body with NO Content-Type skipped the JSON-only contract.
  PR105-SEC-02b a duplicated singleton security header was silently resolved to
                its first value.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from starlette.testclient import TestClient

REPO = Path(__file__).resolve().parents[1]


# ─── PR105-RUN-01: one coherent runtime ───────────────────────────────────────

def test_declared_runtime_is_312():
    assert (REPO / ".python-version").read_text().strip() == "3.12"


def test_daily_chain_uses_the_generic_venv_interpreter():
    """A hardcoded minor-version binary is exactly what broke: it does not exist
    in a conformant 3.12 environment."""
    sh = (REPO / "run_daily_analysis.sh").read_text()

    assert 'PY="$GP/venv/bin/python"' in sh
    # Assert on the executable PATH, not the bare token: the fix comment
    # legitimately names the old binary while documenting why it was removed.
    assert "venv/bin/python3.14" not in sh


def test_no_production_launch_script_hardcodes_a_minor_version_binary():
    """The contract must hold across EVERY production launch path, not just the
    one line the review happened to quote."""
    offenders = []
    for sh in REPO.glob("*.sh"):
        text = sh.read_text()
        if re.search(r"venv/bin/python3\.\d+", text):
            offenders.append(sh.name)
    assert not offenders, f"hardcoded interpreter in: {offenders}"


def test_daily_chain_fails_clearly_when_the_interpreter_is_missing():
    """Executable assertion for a nonexistent interpreter — the failure mode this
    blocker describes. Shell-level only; the live chain is never run."""
    sh = (REPO / "run_daily_analysis.sh").read_text()
    idx = sh.index('PY="$GP/venv/bin/python"')
    following = sh[idx:idx + 400]

    assert '[ ! -x "$PY" ]' in following, "missing interpreter must be detected"
    assert "exit 1" in following, "missing interpreter must abort the chain"


def test_setup_creates_the_declared_runtime_and_rejects_others():
    sh = (REPO / "setup.sh").read_text()

    assert ".python-version" in sh, "setup must read the declared runtime"
    assert 'PY_MM' in sh and 'DECLARED' in sh, "setup must compare major.minor"
    assert "exit 1" in sh


def test_readme_states_python_312():
    readme = (REPO / "README.md").read_text()

    assert "Python 3.12" in readme
    assert "Python 3.14+" not in readme


# ─── PR105-RUN-02: one dependency contract ────────────────────────────────────

def _direct_requirements() -> list[str]:
    lines = (REPO / "requirements.txt").read_text().splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]


def test_every_direct_requirement_is_pinned_or_an_immutable_sha():
    unpinned = [ln for ln in _direct_requirements()
                if "==" not in ln and not re.search(r"@ git\+.*@[0-9a-f]{40}$", ln)]
    assert not unpinned, f"unpinned direct requirements: {unpinned}"


def test_every_direct_requirement_appears_in_the_lock():
    lock = (REPO / "requirements.lock").read_text().lower()
    missing = []
    for ln in _direct_requirements():
        name = re.split(r"[=<>\[ ]|@ git", ln, maxsplit=1)[0].strip().lower()
        if name and name not in lock:
            missing.append(name)
    assert not missing, f"direct requirements absent from the lock: {missing}"


def test_setup_installs_the_lock():
    assert "requirements.lock" in (REPO / "setup.sh").read_text()


def test_readme_install_command_names_the_lock():
    readme = (REPO / "README.md").read_text()
    install = [ln for ln in readme.splitlines() if ln.strip().startswith("pip install -r")]

    assert install, "README must show an install command"
    assert all("requirements.lock" in ln for ln in install), install


def test_ci_installs_the_lock():
    ci = (REPO / ".github/workflows/tests.yml").read_text()

    assert "requirements.lock" in ci
    assert "pip install -r requirements.txt" not in ci


def test_gesh_core_sha_matches_between_txt_and_lock():
    pat = re.compile(r"gesh-core @ git\+\S+@([0-9a-f]{40})")
    txt = pat.search((REPO / "requirements.txt").read_text())
    lock = pat.search((REPO / "requirements.lock").read_text())

    assert txt and lock, "gesh-core must be an immutable SHA in both files"
    assert txt.group(1) == lock.group(1), "gesh-core SHA drift between txt and lock"


# ─── PR105-SEC-01: malformed Origin cannot 500 ────────────────────────────────

@pytest.fixture
def client(tmp_path, monkeypatch):
    import auth_users
    monkeypatch.setattr(auth_users, "AUTH_DB_PATH", tmp_path / "auth.db")
    auth_users.init_auth_db()
    import server
    return TestClient(server.app, raise_server_exceptions=False)


@pytest.mark.parametrize("origin", [
    "https://x:notaport",
    "https://x:65536",
    "https://x:-1",
    "ftp://x.test",
    "https://user:pass@x.test",
    "https://",
    "https://[bad",
])
def test_malformed_origin_never_500s(client, origin):
    """Pre-auth 500s leak internals and turn a refusal into a server error."""
    r = client.post("/api/watchlist", json={"symbol": "AAA"},
                    headers={"Origin": origin})

    assert r.status_code != 500, f"{origin} produced a 500"
    assert r.status_code < 500
    body = r.text.lower()
    assert "traceback" not in body and "valueerror" not in body


def test_canonical_origin_is_total_and_fails_closed():
    import server
    for raw in ["https://x:notaport", "https://x:65536", "ftp://x",
                "https://u:p@x", "https://", "https://[bad", "", "null"]:
        assert server._canonical_origin(raw) == "", raw


def test_valid_origins_still_canonicalize():
    import server

    assert server._canonical_origin("https://X.TEST") == "https://x.test:443"
    assert server._canonical_origin("http://x.test") == "http://x.test:80"
    assert server._canonical_origin("http://x.test:8080") == "http://x.test:8080"


@pytest.mark.parametrize("origin", [
    "https://x:notaport",
    "https://x:65536",
])
def test_cookie_authenticated_malformed_origin_is_controlled_refusal(
    client, origin
):
    """The exact original failure sequence, end to end through the real ASGI
    middleware: a session cookie is PRESENT, so the CSRF guard must actually
    validate the Origin (a cookieless request carries no ambient authority and
    is deliberately passed through). On the unfixed code `u.port` raised
    ValueError OUTSIDE the try, so this exact request answered a pre-auth HTTP
    500; the contract is a controlled cross-origin refusal — 403, no internals."""
    import auth_users

    response = client.post(
        "/api/watchlist",
        json={"symbol": "AAA"},
        headers={"Origin": origin},
        cookies={auth_users.COOKIE_NAME: "invalid-but-present-session"},
    )

    assert response.status_code == 403
    assert "traceback" not in response.text.lower()
    assert "valueerror" not in response.text.lower()


# ─── PR105-SEC-02: declared Content-Type ──────────────────────────────────────

def test_body_without_content_type_is_rejected(client):
    """The exact bypass: raw JSON bytes, no declared media type."""
    r = client.post("/api/watchlist", content=b'{"symbol":"AAA"}')

    assert r.status_code == 415, r.status_code


@pytest.mark.parametrize("ctype", ["text/plain", "application/x-www-form-urlencoded",
                                   "multipart/form-data", "application/xml"])
def test_non_json_content_types_are_rejected(client, ctype):
    r = client.post("/api/watchlist", content=b'{"symbol":"AAA"}',
                    headers={"Content-Type": ctype})

    assert r.status_code == 415


def test_json_with_charset_parameter_is_accepted(client):
    r = client.post("/api/watchlist", content=b'{"symbol":"AAA"}',
                    headers={"Content-Type": "application/json;charset=UTF-8"})

    assert r.status_code != 415


def test_bodyless_mutation_is_not_content_type_gated(client):
    """Deliberate: a bodyless mutation declares no media type and must not 415."""
    r = client.post("/api/watchlist")

    assert r.status_code != 415


# ─── PR105-SEC-02b: duplicate singleton headers ───────────────────────────────

def _raw_post(client, headers: list[tuple[bytes, bytes]], body: bytes = b'{"symbol":"AAA"}'):
    """httpx keeps duplicate headers when given a list of tuples."""
    return client.post("/api/watchlist", content=body,
                       headers=[(k.decode(), v.decode()) for k, v in headers])


def test_duplicate_conflicting_origin_is_refused(client):
    r = _raw_post(client, [(b"content-type", b"application/json"),
                           (b"origin", b"https://a.test"),
                           (b"origin", b"https://b.test")])

    assert r.status_code == 400
    assert "ambiguous" in r.text.lower()


def test_duplicate_matching_origin_is_not_ambiguous(client):
    r = _raw_post(client, [(b"content-type", b"application/json"),
                           (b"origin", b"https://a.test"),
                           (b"origin", b"https://a.test")])

    assert r.status_code != 400 or "ambiguous" not in r.text.lower()


def test_duplicate_conflicting_content_type_is_refused(client):
    # An Origin is included deliberately: tests/conftest.py::_browser_like_origin
    # rebuilds `headers` as a dict when Origin is absent, which would collapse the
    # duplicate before it ever reached the middleware under test.
    r = _raw_post(client, [(b"origin", b"https://a.test"),
                           (b"content-type", b"application/json"),
                           (b"content-type", b"text/plain")])

    assert r.status_code == 400
    assert "ambiguous" in r.text.lower()


def test_raw_header_helper_sees_duplicates():
    import server

    class _Req:
        scope = {"headers": [(b"origin", b"https://a.test"), (b"Origin", b"https://b.test")]}

    assert server._raw_header_values(_Req(), "origin") == ["https://a.test", "https://b.test"]


def test_raw_header_helper_tolerates_a_broken_scope():
    import server

    class _Req:
        scope: dict = {}

    assert server._raw_header_values(_Req(), "origin") == []


# ─── exemptions must survive ──────────────────────────────────────────────────

def test_webhook_without_browser_origin_is_not_csrf_refused(client):
    """The billing webhook carries its own HMAC auth and no cookie."""
    r = client.post("/api/billing/webhook", content=b"{}",
                    headers={"Content-Type": "application/json"})

    assert r.status_code != 403, "webhook must not be CSRF-refused"


def test_cookieless_request_is_not_csrf_refused(client):
    """A request with no session cookie carries no ambient authority to forge, so
    the CSRF guard must pass it through and let route auth decide.

    (With the login wall off, `_session_email` resolves to the OWNER, so the route
    itself answers 200 — asserting 401 here would be asserting the wall, not the
    CSRF contract.)"""
    r = client.post("/api/watchlist", json={"symbol": "AAA"})

    assert r.status_code != 403, "cookieless request must not be CSRF-refused"
    assert "cross-origin" not in r.text.lower()
