"""
Shared pytest fixtures + offline guards for the money-moving test suite.

Every test here MUST run with zero network, zero real DB writes, and zero
broker/LLM calls. The fixtures below make that the default:

  * `_no_network` (autouse) hard-fails any accidental socket connection, so a
    missing mock surfaces as an explicit error instead of a silent live call.
  * `repo_root` puts the project root on sys.path (tests live one level down).

The production modules under test (kelly.py, consensus.py, fft_cycles.py,
trading/risk_gate.py) are imported read-only and never modified.
"""

from __future__ import annotations

import os
import shutil
import socket
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


class _NetworkBlocked(RuntimeError):
    """Raised if collection or a test attempts a real network connection."""


def pytest_configure(config: pytest.Config) -> None:
    """Astra R7: temp DB/auth paths and network deny before collection imports."""
    root = Path(tempfile.mkdtemp(prefix="gesh-project-test-"))
    config._gesh_r7_root = root  # type: ignore[attr-defined]
    config._gesh_had_db = "GESH_DB_PATH" in os.environ  # type: ignore[attr-defined]
    config._gesh_prev_db = os.environ.get("GESH_DB_PATH")  # type: ignore[attr-defined]
    config._gesh_had_auth = "GESH_AUTH_DB" in os.environ  # type: ignore[attr-defined]
    config._gesh_prev_auth = os.environ.get("GESH_AUTH_DB")  # type: ignore[attr-defined]
    os.environ["GESH_DB_PATH"] = str(root / "gesh_financial.db")
    os.environ["GESH_AUTH_DB"] = str(root / "gesh_auth.db")

    def _blocked(*_args: object, **_kwargs: object) -> None:
        raise _NetworkBlocked(
            "Network access attempted during collection or an offline unit test — "
            "a dependency (yfinance/Anthropic/Robinhood) was not mocked."
        )

    config._gesh_orig_connect = socket.socket.connect  # type: ignore[attr-defined]
    socket.socket.connect = _blocked  # type: ignore[method-assign]
    try:
        import curl_cffi
    except ImportError:
        config._gesh_orig_curl_perform = None  # type: ignore[attr-defined]
    else:
        config._gesh_orig_curl_perform = curl_cffi.Curl.perform  # type: ignore[attr-defined]
        curl_cffi.Curl.perform = _blocked  # type: ignore[method-assign]


def pytest_unconfigure(config: pytest.Config) -> None:
    orig = getattr(config, "_gesh_orig_connect", None)
    if orig is not None:
        socket.socket.connect = orig  # type: ignore[method-assign]
    orig_curl = getattr(config, "_gesh_orig_curl_perform", None)
    if orig_curl is not None:
        import curl_cffi

        curl_cffi.Curl.perform = orig_curl  # type: ignore[method-assign]
    if getattr(config, "_gesh_had_db", False):
        os.environ["GESH_DB_PATH"] = config._gesh_prev_db  # type: ignore[attr-defined]
    else:
        os.environ.pop("GESH_DB_PATH", None)
    if getattr(config, "_gesh_had_auth", False):
        os.environ["GESH_AUTH_DB"] = config._gesh_prev_auth  # type: ignore[attr-defined]
    else:
        os.environ.pop("GESH_AUTH_DB", None)
    root = getattr(config, "_gesh_r7_root", None)
    if root is not None:
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly if any test opens a real socket — proves tests are offline.

    Blocking `socket.socket.connect` alone is NOT enough: yfinance >= 1.x fetches
    through **curl_cffi**, which drives libcurl in C and never touches Python's
    socket object. An unmocked yfinance call therefore sailed straight past this
    guard and hit Yahoo for real — silently, and only in whichever test forgot the
    stub (`/api/earnings/{symbol}` in the customer leak sweep, which the guard was
    supposed to catch). Block libcurl at `Curl.perform`, the single chokepoint every
    curl_cffi request (sync + async, Session + module-level) funnels through, so a
    missing stub surfaces as a loud error instead of a live call.
    """

    def _blocked(*args: object, **kwargs: object) -> None:
        raise _NetworkBlocked(
            "Network access attempted during an offline unit test — "
            "a dependency (yfinance/Anthropic/Robinhood) was not mocked."
        )

    monkeypatch.setattr(socket.socket, "connect", _blocked)

    try:  # curl_cffi is a yfinance dependency; guard stays valid if it's absent
        import curl_cffi
    except ImportError:
        pass
    else:
        monkeypatch.setattr(curl_cffi.Curl, "perform", _blocked)


@pytest.fixture(autouse=True)
def _isolate_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure unit tests run against mocked Anthropic by default unless explicitly tested."""
    monkeypatch.delenv("GESH_LLM_SKIP_ANTHROPIC", raising=False)


@pytest.fixture(autouse=True)
def _isolate_cf_web_analytics_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Do not inherit a deploy-env analytics token into HTML assertions."""
    monkeypatch.delenv("CF_WEB_ANALYTICS_TOKEN", raising=False)


@pytest.fixture(autouse=True)
def _isolate_auth_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point auth_users at a per-test throwaway DB. Product endpoints now write
    usage_events on every hit (metering), so a test that doesn't stub the auth DB
    would otherwise pollute the developer's REAL gesh_auth.db. Tests that need
    their own DB (the `au` fixtures) still override this — fixture order puts
    autouse first, so their setattr wins."""
    import auth_users
    monkeypatch.setattr(auth_users, "AUTH_DB_PATH", tmp_path / "auth_test_isolated.db")


@pytest.fixture(autouse=True)
def _isolate_signal_history_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point signal_history at a per-test throwaway DB — its `_conn()` runs
    CREATE TABLE IF NOT EXISTS on EVERY connect (even reads), against the
    cwd-relative PRODUCTION gesh_financial.db. Without this, any test touching
    signals/alerts/digest/mybook columns writes DDL — and record_snapshots
    stubs — into the live DB (observed 2026-07-17: a test overwrote the real
    NVDA snapshot row). Same zero-real-DB-writes contract as the auth DB above."""
    import signal_history
    monkeypatch.setattr(signal_history, "DB_PATH", tmp_path / "signal_history_isolated.db",
                        raising=False)


@pytest.fixture(autouse=True)
def _isolate_equity_snapshots_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point db (gesh_core.db via the transparent alias) at a per-test throwaway
    DB. get_equity_history() otherwise reads the cwd-relative PRODUCTION
    gesh_financial.db directly — on a dev machine with a real, populated file,
    /api/ecg's computation silently reflects the operator's actual portfolio
    history (e.g. a genuine SUSPENDED/-47% drawdown) instead of a test's
    synthetic fixture, which reads as a customer-data leak when it is really an
    unisolated data source. Same zero-real-DB-writes contract as the auth and
    signal-history DBs above; db.py's own docstring documents this exact
    monkeypatch as the supported override."""
    book = tmp_path / "equity_snapshots_isolated.db"
    monkeypatch.setenv("GESH_DB_PATH", str(book))
    import db
    monkeypatch.setattr(db, "DB_PATH", book, raising=False)
    # Astra R7: isolate every state writer, not only the db alias. db.py and
    # trading.state must keep the same file (R3 one-book contract).
    for modname in (
        "gesh_core.db",
        "trade_log",
        "trading.state",
        "gesh_core.trading.state",
        "consensus",
        "schedule_gate",
    ):
        mod = sys.modules.get(modname)
        if mod is not None and hasattr(mod, "DB_PATH"):
            monkeypatch.setattr(mod, "DB_PATH", book, raising=False)


@pytest.fixture(autouse=True)
def _clear_server_response_caches() -> None:
    """server.py keeps process-wide TTL caches (/api/chart + the per-ticker analyze
    GETs) and the per-IP rate-limit ledger. Every TestClient shares the constant
    "testclient" IP, so without clearing, one test's cached response (or accumulated
    heavy-bucket hits → spurious 429s) leaks into the next."""
    def _clear() -> None:
        srv = sys.modules.get("server")
        # _ticker_card_flight holds asyncio.Locks bound to one test's event loop —
        # every TestClient spins a fresh loop, so a leaked lock raises "bound to a
        # different event loop" in the next test. MyBook job state is durable and
        # isolated by AUTH_DB_PATH; only its process-local result cache is cleared.
        for name in ("_chart_cache", "_ticker_get_cache", "_RATE_HITS",
                     "_ticker_card_cache", "_ticker_card_flight",
                     "_mybook_cache", "_MYBOOK_FAIL_RETRY_AT",
                     "_LOGIN_FAILS", "_ltm_cache"):
            state = getattr(srv, name, None)
            if state is not None:
                state.clear()
        # The ("close", sym) slice moved out of server into price_cache — same
        # leak class: a close cached by one test must not price the next.
        pc = sys.modules.get("price_cache")
        if pc is not None:
            pc.clear()

    _clear()
    yield
    _clear()


# ── TestClient is not a browser (CSRF hardening, 2026-07-23) ─────────────────
# Cookie-authenticated mutations now require an Origin (or Referer) that matches
# a configured deployment origin. A real browser ALWAYS sends Origin on a
# same-origin POST/PUT/PATCH/DELETE; Starlette's TestClient sends none, so every
# pre-existing customer-flow test suddenly looked like a forged request.
#
# This makes the harness behave like the browser it stands in for. It does NOT
# weaken the guard: the header is only defaulted when the test did not set one,
# and tests/test_csrf_hardening.py opts out to exercise absent / null /
# mismatched / wrong-scheme / wrong-port origins directly.
_DEFAULT_TEST_ORIGIN = {"enabled": True}


@pytest.fixture
def no_default_origin():
    """Opt out of the browser-like Origin default for one test module."""
    _DEFAULT_TEST_ORIGIN["enabled"] = False
    yield
    _DEFAULT_TEST_ORIGIN["enabled"] = True


@pytest.fixture(autouse=True)
def _isolate_deployment_origins(monkeypatch: pytest.MonkeyPatch) -> None:
    """Host `.env` APP_BASE_URL / GESH_ALLOWED_ORIGINS must not leak into CSRF.

    `auth.py` and `ai_analyzer.py` call `load_dotenv()` at import, so a developer
    machine with `APP_BASE_URL=https://pay.geshfinance.com` makes every TestClient
    Origin (`http://testserver`) look forged. CI has no `.env`, which hid ~64
    local failures (CLAUDE.md 2026-07-30). Tests that need a specific origin
    already `setenv` it (test_csrf_hardening, test_billing, …).
    """
    monkeypatch.delenv("APP_BASE_URL", raising=False)
    monkeypatch.delenv("GESH_ALLOWED_ORIGINS", raising=False)


@pytest.fixture(autouse=True)
def _browser_like_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    from starlette.testclient import TestClient

    original = TestClient.request

    def _with_origin(self, method, url, *args, **kwargs):
        if (_DEFAULT_TEST_ORIGIN["enabled"]
                and str(method).upper() in ("POST", "PUT", "PATCH", "DELETE")):
            headers = dict(kwargs.get("headers") or {})
            if not any(k.lower() == "origin" for k in headers):
                headers["Origin"] = str(self.base_url).rstrip("/")
                kwargs["headers"] = headers
        return original(self, method, url, *args, **kwargs)

    monkeypatch.setattr(TestClient, "request", _with_origin)


# ── SEO test fixtures (tests/test_seo_*.py) ────────────────────────────────────

@pytest.fixture
def seo_walled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Auth wall ON with an isolated auth DB (anonymous client = logged-out visitor)."""
    import auth_users  # lazy: conftest must not import app modules before env isolation

    monkeypatch.setenv("GESH_AUTH_DB", str(tmp_path / "auth.db"))
    monkeypatch.setenv("GESH_AUTH_SECRET", "test-secret-deterministic")
    monkeypatch.setenv("GESH_AUTH_ENABLED", "1")
    monkeypatch.setattr(auth_users, "AUTH_DB_PATH", tmp_path / "auth.db")
    auth_users.init_auth_db()


@pytest.fixture
def seo_anon(seo_walled: None):
    from starlette.testclient import TestClient

    import server

    return TestClient(server.app, raise_server_exceptions=False)


@pytest.fixture
def seo_empty_track_record(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import proof_page

    real = proof_page.load_track_record
    monkeypatch.setattr(
        proof_page,
        "load_track_record",
        lambda: real(str(tmp_path / "absent.db"), daily_analysis_db_path=None),
    )
