"""The offline guard itself (conftest `_no_network`) — the thing every other test trusts.

Blocking `socket.socket.connect` alone was NOT enough. yfinance >= 1.x fetches through
**curl_cffi**, which drives libcurl in C and never touches Python's socket object, so an
unmocked yfinance call sailed straight past the guard and hit Yahoo for real. That is how
`/api/earnings/{symbol}` in tests/test_customer_leak_sweep.py — the repo's customer-money
leak guard — ended up making a live ~20s network call whose verdict rode on live data, and
how tests/test_price_sanity.py ended up asserting a "broken feed" case against whatever
price NFLX happened to trade at.

These tests fail if that hole is ever reopened.
"""

from __future__ import annotations

import socket

import pytest


def test_socket_connect_is_blocked():
    with pytest.raises(Exception, match="Network access attempted"):
        socket.socket().connect(("example.com", 80))


def test_curl_cffi_is_blocked():
    """The regression: libcurl bypasses Python sockets, so it needs its own block."""
    curl_cffi = pytest.importorskip("curl_cffi")
    with pytest.raises(Exception, match="Network access attempted"):
        curl_cffi.Curl().perform()


def test_unmocked_yfinance_path_cannot_reach_the_network(monkeypatch):
    """Behavioural lock, feed-agnostic: with no stub, the earnings source must NOT
    return live data. Before the curl_cffi block this returned 8 real quarters of
    NVDA earnings straight from Yahoo — inside a suite advertised as offline.
    """
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)   # force the yfinance path
    import earnings_viz

    out = earnings_viz.get_earnings("NVDA")

    # get_earnings never raises by design; unreachable sources degrade to unavailable.
    # If this ever reports available data, the guard has been breached.
    assert out["available"] is False, f"live network data leaked into an offline test: {out}"
