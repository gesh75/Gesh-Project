"""Offline-degradation guard.

When the Robinhood session is dead (401 → robin_stocks returns None for the
profile/account calls), get_total_equity() used to do None.get(...) and raise a
bare AttributeError, which surfaced as an HTTP 500 on /api/portfolio. It must
instead raise a typed RobinhoodOfflineError so the server can degrade to a clean
"session expired" 503 (Offline) rather than crash.
"""
from __future__ import annotations

import pytest

import data_fetcher as df


def test_robinhood_offline_error_is_runtimeerror() -> None:
    assert issubclass(df.RobinhoodOfflineError, RuntimeError)


def test_get_total_equity_raises_offline_when_session_dead(monkeypatch: pytest.MonkeyPatch) -> None:
    # Unauthorized session → robin_stocks returns None for every profile call.
    monkeypatch.setattr(df.rh, "load_portfolio_profile", lambda: None)
    monkeypatch.setattr(df.rh, "load_account_profile", lambda: None)
    with pytest.raises(df.RobinhoodOfflineError):
        df.get_total_equity()


def test_get_total_equity_ok_when_session_live(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        df.rh, "load_portfolio_profile",
        lambda: {"equity": "100", "adjusted_equity_previous_close": "90"},
    )
    monkeypatch.setattr(
        df.rh, "load_account_profile",
        lambda: {"margin_balances": {"portfolio_cash": "25"}},
    )
    eq = df.get_total_equity()
    assert eq["portfolio_equity"] == 100.0
    assert eq["cash"] == 25.0
