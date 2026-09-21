"""D-07 equity sentinels must never be persisted as a customer ECG observation.

``auth_users._finite_positive`` is the write-path guard: price<=0 / NaN / bool
is MISSING data, never a loss. Persisting ``True`` as $1 or ``0.0`` as a
session is what later read back as a fake crash on the owner curve (audit D-07).
``record_equity_snapshots`` drops those rows rather than storing them.

No network. Temp SQLite. Does not import server / gesh-core.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import auth_users as au

CUSTOMER = "cust@x.com"


@pytest.fixture(autouse=True)
def isolated_auth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GESH_AUTH_DB", str(tmp_path / "auth.db"))
    monkeypatch.setenv("GESH_AUTH_SECRET", "test-secret-deterministic")
    monkeypatch.setattr(au, "AUTH_DB_PATH", tmp_path / "auth.db")
    au.init_auth_db()
    au.create_user(CUSTOMER, "test-password")
    yield


@pytest.mark.parametrize(
    "value",
    [True, False, 0, 0.0, -1, float("nan"), float("inf"), float("-inf"), "10", None, ""],
)
def test_finite_positive_rejects_sentinels_and_non_numbers(value):
    assert au._finite_positive(value) is None


def test_finite_positive_keeps_real_observations():
    assert au._finite_positive(10.5) == 10.5
    assert au._finite_positive(1) == 1.0


def test_record_equity_snapshots_drops_bool_zero_and_nan():
    written = au.record_equity_snapshots(
        CUSTOMER,
        [
            {"symbol": "BOOL", "equity": True, "current_price": 10.0},
            {"symbol": "ZERO", "equity": 100.0, "current_price": 0.0},
            {"symbol": "NAN", "equity": float("nan"), "current_price": 10.0},
            {"symbol": "KEEP", "equity": 100.0, "current_price": 10.0},
        ],
        at="2026-01-01T00:00:00+00:00",
    )
    assert written == 1
    assert au.get_user_equity_history(CUSTOMER, "BOOL") == []
    assert au.get_user_equity_history(CUSTOMER, "ZERO") == []
    assert au.get_user_equity_history(CUSTOMER, "NAN") == []
    kept = au.get_user_equity_history(CUSTOMER, "KEEP")
    assert len(kept) == 1
    assert kept[0]["equity"] == 100.0
    assert kept[0]["current_price"] == 10.0


def test_record_equity_snapshots_blank_symbol_is_not_written():
    written = au.record_equity_snapshots(
        CUSTOMER,
        [{"symbol": "  ", "equity": 100.0, "current_price": 10.0}],
        at="2026-01-01T00:00:00+00:00",
    )
    assert written == 0
