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

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from starlette.testclient import TestClient

REPO = Path(__file__).resolve().parents[1]


def _write_executable(p: Path, content: str) -> None:
    """Create an executable shell script (or python stub) at p."""
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    p.chmod(0o755)


def _create_setup_harness(
    tmp: Path,
    declared: str = "3.12",
    venv_version: str | None = None,
) -> dict[str, Path]:
    """Build a minimal temp harness for testing setup.sh and run_daily_analysis.sh
    behavior under controlled Python runtimes. All paths are inside tmp.
    """
    # declared runtime
    (tmp / ".python-version").write_text(f"{declared}\n")

    # empty lock (setup will try to install from it)
    (tmp / "requirements.lock").write_text("")

    # minimal valid .env so setup does not abort on missing keys
    (tmp / ".env").write_text(
        "ROBINHOOD_USER=test@example.com\n"
        "ANTHROPIC_API_KEY=test\n"
        "FINNHUB_API_KEY=test\n"
        "TELEGRAM_BOT_TOKEN=test\n"
        "TELEGRAM_CHAT_ID=0\n"
    )

    venv_bin = tmp / "venv" / "bin"
    venv_bin.mkdir(parents=True, exist_ok=True)

    log = tmp / "venv_python_invocations.log"
    bare_pip_log = tmp / "bare_pip.log"
    bare_py3_log = tmp / "bare_python3.log"
    log.touch()
    bare_pip_log.touch()
    bare_py3_log.touch()

    # existing venv/bin/python: reports the supplied version for --version
    # and for any -c query containing version_info; logs every argv
    v_major, v_minor = (venv_version or declared).split(".")[:2]
    _write_executable(
        venv_bin / "python",
        f'''#!/usr/bin/env bash
echo "$(date +%s) $0 $*" >> "{log}"
if [ "$1" = "--version" ]; then
    echo "Python {v_major}.{v_minor}.0"
    exit 0
fi
if [ "$1" = "-c" ]; then
    if echo "$*" | grep -q "version_info"; then
        echo "{v_major}.{v_minor}"
        exit 0
    fi
    # other -c (e.g. import nltk) just succeed
    exit 0
fi
# python -m pip ... etc. — succeed without real work
exit 0
''',
    )

    # no-op activate (setup may source it)
    _write_executable(
        venv_bin / "activate",
        "#!/usr/bin/env bash\n# no-op for harness\nreturn 0\n",
    )

    # fakebin/ for PATH override: python3.12 returns 3.12, bare pip/python3 only log+exit
    fakebin = tmp / "fakebin"
    fakebin.mkdir(parents=True, exist_ok=True)

    _write_executable(
        fakebin / "python3.12",
        '''#!/usr/bin/env bash
echo "Python 3.12.0"
exit 0
''',
    )

    _write_executable(
        fakebin / "pip",
        f'''#!/usr/bin/env bash
echo "$(date +%s) $0 $*" >> "{bare_pip_log}"
exit 0
''',
    )

    _write_executable(
        fakebin / "python3",
        f'''#!/usr/bin/env bash
echo "$(date +%s) $0 $*" >> "{bare_py3_log}"
exit 0
''',
    )

    # also expose mkdir that can be overridden per-test to write marker + fail
    _write_executable(
        fakebin / "mkdir",
        '#!/usr/bin/env bash\n# default no-op mkdir for most tests\nexit 0\n',
    )

    return {
        "log": log,
        "bare_pip_log": bare_pip_log,
        "bare_py3_log": bare_py3_log,
        "fakebin": fakebin,
        "venv_bin": venv_bin,
        "venv_python": venv_bin / "python",
    }


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


def test_daily_chain_log_rotation_uses_an_unpredictable_temp_file():
    """A fixed .tmp path in the shared /tmp directory permits a local attacker
    to redirect log rotation through a pre-created symlink."""
    sh = (REPO / "run_daily_analysis.sh").read_text()

    assert 'mktemp "${LOG}.tmp.XXXXXX"' in sh
    assert '> "$LOG.tmp"' not in sh


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


# ─── PR105-RUN-01 executable harness tests (setup + daily chain) ──────────────

def test_setup_rejects_existing_venv_314(tmp_path):
    """setup.sh must reject a venv whose python reports 3.14 when .python-version
    declares 3.12, exit 1, print expected/actual + recreate guidance, and leave
    the venv directory untouched."""
    h = _create_setup_harness(tmp_path, declared="3.12", venv_version="3.14")
    setup_script = tmp_path / "setup.sh"
    shutil.copy(REPO / "setup.sh", setup_script)
    setup_script.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = str(h["fakebin"]) + ":" + env.get("PATH", "")

    proc = subprocess.run(
        ["bash", str(setup_script)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    combined = (proc.stdout + proc.stderr).lower()
    assert "3.12" in combined and "3.14" in combined
    assert "recreate" in combined
    # does not delete venv
    assert (tmp_path / "venv" / "bin" / "python").exists()


def test_setup_accepts_existing_venv_312(tmp_path):
    """When the existing venv matches the declared 3.12, setup exits 0."""
    h = _create_setup_harness(tmp_path, declared="3.12", venv_version="3.12")
    setup_script = tmp_path / "setup.sh"
    shutil.copy(REPO / "setup.sh", setup_script)
    setup_script.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = str(h["fakebin"]) + ":" + env.get("PATH", "")

    proc = subprocess.run(
        ["bash", str(setup_script)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0


def test_run_daily_rejects_fake_venv_314_before_lock(tmp_path):
    """A temp copy of run_daily_analysis.sh must reject a 3.14 venv (rc 1) before
    it reaches any lock/mkdir step. The fake mkdir (first in PATH) writes a
    marker only if executed; we assert the marker is absent and the output
    contains the expected/actual/recreate guidance. Uses GESH_CAFFEINATED=1
    and GESH_SNAPSHOT_RETRY_WAITS=0 to keep the run deterministic and fast."""
    h = _create_setup_harness(tmp_path, declared="3.12", venv_version="3.14")
    daily_script = tmp_path / "run_daily_analysis.sh"
    shutil.copy(REPO / "run_daily_analysis.sh", daily_script)
    daily_script.chmod(0o755)

    # override mkdir to write marker + fail (proves early exit before lock)
    marker = tmp_path / "mkdir_marker"
    _write_executable(
        h["fakebin"] / "mkdir",
        f'''#!/usr/bin/env bash
echo "RAN" > "{marker}"
exit 1
''',
    )

    env = os.environ.copy()
    env["PATH"] = str(h["fakebin"]) + ":" + env.get("PATH", "")
    env["GESH_CAFFEINATED"] = "1"
    env["GESH_SNAPSHOT_RETRY_WAITS"] = "0"

    proc = subprocess.run(
        ["bash", str(daily_script)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    assert not marker.exists(), "rejection must happen before any mkdir/lock"
    combined = (proc.stdout + proc.stderr).lower()
    assert "expected" in combined and "3.12" in combined
    assert "actual" in combined and "3.14" in combined
    assert "recreate" in combined


def test_accepted_setup_logs_direct_venv_python_calls(tmp_path):
    """When setup accepts a matching 3.12 venv it must invoke the *direct*
    venv/bin/python (logged) for the pip upgrade, pip install -r lock, and
    the nltk import check. Bare pip/python3 in PATH must never be called
    (their logs stay empty)."""
    h = _create_setup_harness(tmp_path, declared="3.12", venv_version="3.12")
    setup_script = tmp_path / "setup.sh"
    shutil.copy(REPO / "setup.sh", setup_script)
    setup_script.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = str(h["fakebin"]) + ":" + env.get("PATH", "")

    proc = subprocess.run(
        ["bash", str(setup_script)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0

    log = h["log"].read_text()
    assert "-m pip install --quiet --upgrade pip" in log
    assert "-m pip install --quiet -r requirements.lock" in log
    assert "-c import nltk" in log

    assert h["bare_pip_log"].read_text().strip() == ""
    assert h["bare_py3_log"].read_text().strip() == ""
