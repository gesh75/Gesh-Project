"""Quote as-of stamp + watchlist day-change honesty.

`data_fetcher._newest_quote_ts` is the DATA_FRESH input for live dashboard
payloads. Stamping fetch-time `now()` on an unparseable / missing `updated_at`
made hours-old pre-market quotes read as 0 seconds old (engine rule #37 twin).

`get_watchlist_quotes` previously could emit inf/-inf day-change when
`previous_close` was $0. A $0 last price must not invent a move either.
"""

from __future__ import annotations

import sys
from types import ModuleType

import pytest

# robin_stocks is a live-broker SDK; stub it before import so this file stays
# offline and does not require the package in a bare VM.
if "robin_stocks.robinhood" not in sys.modules:
    _rh = ModuleType("robin_stocks.robinhood")
    _pkg = ModuleType("robin_stocks")
    _pkg.robinhood = _rh
    sys.modules["robin_stocks"] = _pkg
    sys.modules["robin_stocks.robinhood"] = _rh

import data_fetcher as df  # noqa: E402


def test_newest_quote_ts_empty_or_unparseable_is_none_not_now():
    assert df._newest_quote_ts([]) is None
    assert df._newest_quote_ts([{}]) is None
    assert df._newest_quote_ts([{"updated_at": ""}]) is None
    assert df._newest_quote_ts([{"updated_at": "not-a-date"}]) is None
    assert df._newest_quote_ts([{"updated_at": None}]) is None


def test_newest_quote_ts_zulu_and_naive_are_utc():
    zulu = df._newest_quote_ts([{"updated_at": "2026-09-08T14:30:00Z"}])
    assert zulu == "2026-09-08T14:30:00+00:00"

    naive = df._newest_quote_ts([{"updated_at": "2026-09-08T14:30:00"}])
    assert naive == "2026-09-08T14:30:00+00:00"


def test_newest_quote_ts_picks_the_latest_parseable_stamp():
    stamp = df._newest_quote_ts(
        [
            {"updated_at": "2026-09-08T13:00:00Z"},
            {"updated_at": "bogus"},
            {"updated_at": "2026-09-08T15:01:00+00:00"},
            {"updated_at": "2026-09-08T14:59:00Z"},
            {"symbol": "SPY"},
        ]
    )
    assert stamp == "2026-09-08T15:01:00+00:00"


def test_watchlist_quotes_skips_blank_rows_and_defaults_symbols(monkeypatch):
    seen = {}

    def _quotes(symbols):
        seen["symbols"] = list(symbols)
        return [
            None,
            {
                "last_trade_price": "10.00",
                "previous_close": "8.00",
                "ask_price": "10.05",
                "bid_price": "9.95",
                "updated_at": "2026-09-08T14:00:00Z",
            },
            {},
        ]

    monkeypatch.setattr(df.rh, "get_quotes", _quotes, raising=False)
    frame = df.get_watchlist_quotes()
    assert seen["symbols"] == df.DEFAULT_WATCHLIST
    assert list(frame["symbol"]) == ["SPY"]
    assert frame.iloc[0]["change"] == pytest.approx(2.0)
    assert frame.iloc[0]["change_pct"] == pytest.approx(25.0)


def test_watchlist_quotes_zero_prev_close_is_zero_pct_not_inf(monkeypatch):
    monkeypatch.setattr(
        df.rh,
        "get_quotes",
        lambda _syms: [
            {
                "last_trade_price": "12.50",
                "previous_close": "0",
                "ask_price": "0",
                "bid_price": "0",
                "updated_at": "2026-09-08T14:00:00Z",
            }
        ],
        raising=False,
    )
    frame = df.get_watchlist_quotes(["NVDA"])
    assert frame.iloc[0]["change_pct"] == 0.0
    assert frame.iloc[0]["change"] == pytest.approx(12.50)


def test_watchlist_quotes_extended_hours_when_last_missing(monkeypatch):
    monkeypatch.setattr(
        df.rh,
        "get_quotes",
        lambda _syms: [
            {
                "last_trade_price": None,
                "last_extended_hours_trade_price": "101.25",
                "previous_close": "100.00",
                "ask_price": "0",
                "bid_price": "0",
                "updated_at": "2026-09-08T20:10:00Z",
            }
        ],
        raising=False,
    )
    frame = df.get_watchlist_quotes(["HOOD"])
    assert frame.iloc[0]["last_trade_price"] == pytest.approx(101.25)
    assert frame.iloc[0]["change_pct"] == pytest.approx(1.25)
