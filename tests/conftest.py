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
    """Fail loudly if any test opens a real socket — proves tests are offline."""

    def _blocked(*args: object, **kwargs: object) -> None:
        raise _NetworkBlocked(
            "Network access attempted during an offline unit test — "
            "a dependency (yfinance/Anthropic/Robinhood) was not mocked."
        )

    monkeypatch.setattr(socket.socket, "connect", _blocked)


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
def _clear_server_response_caches() -> None:
    """server.py keeps process-wide TTL caches (/api/chart + the per-ticker analyze
    GETs) and the per-IP rate-limit ledger. Every TestClient shares the constant
    "testclient" IP, so without clearing, one test's cached response (or accumulated
    heavy-bucket hits → spurious 429s) leaks into the next."""
    def _clear() -> None:
        srv = sys.modules.get("server")
        # _ticker_card_flight holds asyncio.Locks bound to one test's event loop —
        # every TestClient spins a fresh loop, so a leaked lock raises "bound to a
        # different event loop" in the next test. _mybook_jobs holds background-
        # analysis status; a stale entry would make the next test poll a done job.
        for name in ("_chart_cache", "_ticker_get_cache", "_RATE_HITS",
                     "_ticker_card_cache", "_ticker_card_flight",
                     "_mybook_cache", "_mybook_jobs", "_LOGIN_FAILS", "_ltm_cache"):
            state = getattr(srv, name, None)
            if state is not None:
                state.clear()

    _clear()
    yield
    _clear()
