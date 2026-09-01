"""RH public-quote fallback: last-price parse, halt skip, chunk fail-soft.

`trading/robinhood_quotes.py` is the VPN-resilient price source for paper
close-scan and ATR exits. A missing/wrong last price freezes the autonomy
gate (2026-06-23 incident). Loaded by file path so this suite does not
need gesh-core (`trading/__init__.py` aliases `trading.state`).

`fetch_daily_ohlcv` symbol-echo rejection already lives in
`test_price_thread_safety.py`; this file covers the last-price path and
the remaining historicals degrade edges.
"""

from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "trading" / "robinhood_quotes.py"
_SPEC = importlib.util.spec_from_file_location("gesh_robinhood_quotes", _SRC)
assert _SPEC and _SPEC.loader
rq = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rq)


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _quotes_payload(results):
    return _Resp(json.dumps({"results": results}).encode())


# ─── _coerce_price ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "entry, expected",
    [
        ({"last_trade_price": "12.50"}, 12.50),
        ({"last_trade_price": None, "last_extended_hours_trade_price": "11.00"}, 11.00),
        (
            {
                "last_trade_price": "",
                "last_extended_hours_trade_price": "",
                "previous_close": "10.25",
            },
            10.25,
        ),
        ({"last_trade_price": "bad", "previous_close": "9.00"}, 9.00),
        (
            {
                "last_trade_price": None,
                "last_extended_hours_trade_price": None,
                "previous_close": None,
            },
            None,
        ),
        ({}, None),
        ({"last_trade_price": "not-a-number"}, None),
    ],
)
def test_coerce_price_priority_and_unparseable(entry, expected):
    got = rq._coerce_price(entry)
    if expected is None:
        assert got is None
    else:
        assert got == pytest.approx(expected)


def test_coerce_price_prefers_last_trade_over_extended_and_prev():
    assert rq._coerce_price(
        {
            "last_trade_price": "5.00",
            "last_extended_hours_trade_price": "99.00",
            "previous_close": "88.00",
        }
    ) == pytest.approx(5.00)


# ─── _fetch_chunk / fetch_last_prices ─────────────────────────────────────────


def test_fetch_last_prices_empty_or_none_returns_empty_without_network(monkeypatch):
    monkeypatch.setattr(
        rq, "_urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network"))
    )
    assert rq.fetch_last_prices([]) == {}
    assert rq.fetch_last_prices(None) == {}


def test_fetch_chunk_skips_null_halted_nonpositive_and_unparseable(monkeypatch):
    results = [
        None,  # unknown ticker
        {"symbol": "HALT", "trading_halted": True, "last_trade_price": "10.00"},
        {"symbol": "ZERO", "last_trade_price": "0"},
        {"symbol": "NEG", "last_trade_price": "-1.5"},
        {"symbol": "BAD", "last_trade_price": "n/a", "previous_close": ""},
        {"symbol": None, "last_trade_price": "7.00"},
        {"symbol": "OK", "last_trade_price": "42.10"},
        {"symbol": "EXT", "last_trade_price": None, "last_extended_hours_trade_price": "3.25"},
    ]
    monkeypatch.setattr(rq, "_urlopen", lambda url, timeout: _quotes_payload(results))
    prices = rq._fetch_chunk(["HALT", "ZERO", "NEG", "BAD", "OK", "EXT"])
    assert set(prices) == {"OK", "EXT"}
    assert prices["OK"] == pytest.approx(42.10)
    assert prices["EXT"] == pytest.approx(3.25)


def test_fetch_last_prices_chunks_at_50(monkeypatch):
    calls = []

    def _fake_chunk(symbols):
        calls.append(list(symbols))
        return {symbols[0]: 1.0}

    monkeypatch.setattr(rq, "_fetch_chunk", _fake_chunk)
    symbols = [f"S{i:03d}" for i in range(51)]
    prices = rq.fetch_last_prices(symbols)
    assert len(calls) == 2
    assert calls[0] == symbols[:50]
    assert calls[1] == symbols[50:]
    assert prices == {"S000": 1.0, "S050": 1.0}


def test_fetch_last_prices_chunk_exception_keeps_prior_and_never_raises(monkeypatch):
    def _fake_chunk(symbols):
        if symbols[0] == "GOOD":
            return {"GOOD": 11.0}
        raise RuntimeError("tls reset")

    monkeypatch.setattr(rq, "_fetch_chunk", _fake_chunk)
    # Force two chunks by temporarily shrinking _CHUNK
    monkeypatch.setattr(rq, "_CHUNK", 1)
    prices = rq.fetch_last_prices(["GOOD", "DEAD"])
    assert prices == {"GOOD": 11.0}


def test_fetch_last_prices_total_failure_is_empty_dict(monkeypatch):
    monkeypatch.setattr(rq, "_fetch_chunk", lambda symbols: (_ for _ in ()).throw(OSError("down")))
    assert rq.fetch_last_prices(["AAPL"]) == {}


# ─── fetch_daily_ohlcv remaining degrade edges ────────────────────────────────


def test_fetch_daily_ohlcv_blank_symbol_is_none_without_network(monkeypatch):
    monkeypatch.setattr(
        rq, "_urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network"))
    )
    assert rq.fetch_daily_ohlcv("") is None
    assert rq.fetch_daily_ohlcv(None) is None


def test_fetch_daily_ohlcv_network_error_is_none(monkeypatch):
    monkeypatch.setattr(rq, "_urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError("vpn")))
    assert rq.fetch_daily_ohlcv("NVDA") is None


@pytest.mark.parametrize(
    "payload",
    [
        {"results": []},
        {"results": [None]},
        {"results": [{"symbol": "NVDA", "historicals": []}]},
        {"results": [{"symbol": "NVDA", "historicals": None}]},
        {},
    ],
)
def test_fetch_daily_ohlcv_empty_or_null_results_are_none(monkeypatch, payload):
    monkeypatch.setattr(rq, "_urlopen", lambda url, timeout: _Resp(json.dumps(payload).encode()))
    assert rq.fetch_daily_ohlcv("NVDA") is None


def test_fetch_daily_ohlcv_skips_bad_bars_keeps_dates_and_default_volume(monkeypatch):
    bars = [
        None,
        {"open_price": None, "high_price": "2", "low_price": "1", "close_price": "1.5"},
        {"open_price": "bad", "high_price": "2", "low_price": "1", "close_price": "1.5"},
        {
            "begins_at": "2026-06-02T00:00:00Z",
            "open_price": "10",
            "high_price": "11",
            "low_price": "9",
            "close_price": "10.5",
            # volume omitted → 0.0
        },
    ]
    payload = {"results": [{"symbol": "nvda", "historicals": bars}]}
    monkeypatch.setattr(rq, "_urlopen", lambda url, timeout: _Resp(json.dumps(payload).encode()))
    out = rq.fetch_daily_ohlcv("NVDA")
    assert out is not None
    assert out["close"] == [10.5]
    assert out["volume"] == [0.0]
    assert out["date"] == ["2026-06-02T00:00:00Z"]
    assert out["open"] == [10.0]


def test_fetch_daily_ohlcv_all_skipped_bars_is_none(monkeypatch):
    payload = {
        "results": [
            {
                "symbol": "NVDA",
                "historicals": [
                    {"open_price": None},
                    {"open_price": "x", "high_price": "1", "low_price": "1", "close_price": "1"},
                ],
            }
        ]
    }
    monkeypatch.setattr(rq, "_urlopen", lambda url, timeout: _Resp(json.dumps(payload).encode()))
    assert rq.fetch_daily_ohlcv("NVDA") is None


def test_fetch_daily_ohlcv_echo_mismatch_is_none(monkeypatch):
    bars = [
        {
            "begins_at": "2026-06-01T00:00:00Z",
            "open_price": "1",
            "high_price": "2",
            "low_price": "0.5",
            "close_price": "1.5",
            "volume": "100",
        }
    ]
    payload = {"results": [{"symbol": "TSLA", "historicals": bars}]}
    monkeypatch.setattr(rq, "_urlopen", lambda url, timeout: _Resp(json.dumps(payload).encode()))
    assert rq.fetch_daily_ohlcv("NVDA") is None
