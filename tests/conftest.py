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
