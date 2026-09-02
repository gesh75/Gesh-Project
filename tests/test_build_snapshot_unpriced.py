"""A missing MCP quote must not become a $0 holding.

`build_snapshot_from_mcp._f()` used to default unparseable values to 0.0.
A position present in `get_equity_positions` but absent (or blank) in
`get_equity_quotes` was written as price=0, equity=0, percent_change=-100
and folded into portfolio_equity. That understated the book and skewed
every Kelly / Monte Carlo weight. The daily chain has three snapshot
tiers, so the correct response is to refuse the write (exit 1) rather
than persist a fabricated mark.

These tests import only the snapshot builder (stdlib). Run with
`--noconftest` if gesh-core is not installed.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import build_snapshot_from_mcp as builder


def _raw(*, quotes, extra_positions=None):
    positions = [
        {
            "symbol": "NVDA",
            "quantity": "10",
            "average_buy_price": "100",
        },
        *(extra_positions or []),
    ]
    return {
        "session_id": "test-session",
        "portfolio": {
            "buying_power": {"buying_power": "5000"},
            "pending_deposits": "0",
            "total_value": "0",
        },
        "positions": positions,
        "quotes": quotes,
    }


def _nvda_quote(*, last="214.61", prev="210.00"):
    return {
        "quote": {
            "symbol": "NVDA",
            "last_trade_price": last,
            "previous_close": prev,
            "venue_last_trade_time": "2026-08-21T20:00:00Z",
        }
    }


def test_priced_book_writes_real_marks(tmp_path: Path):
    out = tmp_path / "snap.json"
    snap = builder.build(
        raw_data=_raw(quotes=[_nvda_quote()]),
        output=out,
        now=datetime(2026, 8, 21, 20, 5, tzinfo=timezone.utc),
    )
    assert out.is_file()
    nvda = snap["holdings"][0]
    assert nvda["symbol"] == "NVDA"
    assert nvda["price"] == pytest.approx(214.61)
    assert nvda["equity"] == pytest.approx(2146.1)
    assert nvda["percent_change"] == pytest.approx(114.61)
    assert snap["equity_summary"]["portfolio_equity"] == pytest.approx(2146.1)
    assert snap["equity_summary"]["deployable"] == pytest.approx(5000.0)
    written = json.loads(out.read_text())
    assert written["holdings"][0]["price"] == pytest.approx(214.61)


@pytest.mark.parametrize(
    "quotes,extra,missing",
    [
        ([], None, "NVDA"),  # quote list empty
        ([{"quote": {"symbol": "NVDA", "last_trade_price": ""}}], None, "NVDA"),
        ([{"quote": {"symbol": "NVDA", "last_trade_price": "n/a"}}], None, "NVDA"),
        ([{"quote": {"symbol": "NVDA", "last_trade_price": "0"}}], None, "NVDA"),
        ([{"quote": {"symbol": "NVDA", "last_trade_price": "-1"}}], None, "NVDA"),
        (
            [_nvda_quote()],
            [{"symbol": "MU", "quantity": "5", "average_buy_price": "120"}],
            "MU",
        ),
    ],
)
def test_missing_or_zero_quote_refuses_to_write(tmp_path: Path, quotes, extra, missing):
    out = tmp_path / "snap.json"
    with pytest.raises(SystemExit) as exc:
        builder.build(raw_data=_raw(quotes=quotes, extra_positions=extra), output=out)
    assert exc.value.code == 1
    assert not out.exists(), "a refused build must not persist a $0 snapshot"


def test_partial_quote_does_not_price_the_missing_name_at_zero(tmp_path: Path):
    """Concrete trigger: 2 positions, 1 quote. Must not write MU at $0."""
    out = tmp_path / "snap.json"
    raw = _raw(
        quotes=[_nvda_quote()],
        extra_positions=[{"symbol": "MU", "quantity": "5", "average_buy_price": "120"}],
    )
    with pytest.raises(SystemExit):
        builder.build(raw_data=raw, output=out)
    assert not out.exists()


def test_positive_price_helper_never_invents_zero():
    assert builder._positive_price(None) is None
    assert builder._positive_price("") is None
    assert builder._positive_price("n/a") is None
    assert builder._positive_price("0") is None
    assert builder._positive_price(0) is None
    assert builder._positive_price(-3.2) is None
    assert builder._positive_price(float("nan")) is None
    assert builder._positive_price(float("inf")) is None
    assert builder._positive_price("214.61") == pytest.approx(214.61)
