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

import socket
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


class _NetworkBlocked(RuntimeError):
    """Raised if a test attempts a real network connection."""


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
    import db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "equity_snapshots_isolated.db", raising=False)


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
                     "_mybook_cache", "_LOGIN_FAILS", "_ltm_cache"):
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
