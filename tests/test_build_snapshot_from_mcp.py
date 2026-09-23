"""Execute `build_snapshot_from_mcp.build()` — the money-math writer for
daily-chain snapshot tiers 1 and 2.

`tests/test_raw_snapshot_scaling.py` only string-searches the source for
`"price": round(price, 4)`. `tests/test_quote_freshness.py` covers
`quote_provenance` in isolation. Nothing on main actually ran `build()`, so a
regression in deployable pinning, quote unwrap, or the empty-book fail-closed
would ship as a silently wrong `live_portfolio_snapshot.json`.

F-05 (a missing quote priced at $0) is already the subject of open PR #248 —
these tests do not lock that bug in, and they do not require the fix.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import build_snapshot_from_mcp as B

NOW = datetime(2026, 8, 25, 14, 30, tzinfo=timezone.utc)


def _raw(
    *,
    buying_power: object = "1500.00",
    cash: object = "99999.00",
    pending: object = "100.00",
    total_value: object = "3600.00",
    positions: list | None = None,
    quotes: list | None = None,
    session_id: str = "sess-test",
) -> dict:
    return {
        "session_id": session_id,
        "portfolio": {
            "buying_power": {"buying_power": buying_power},
            "cash": cash,
            "pending_deposits": pending,
            "total_value": total_value,
        },
        "positions": positions
        if positions is not None
        else [
            {"symbol": "NVDA", "quantity": "10", "average_buy_price": "100.00"},
        ],
        "quotes": quotes
        if quotes is not None
        else [
            {
                "quote": {
                    "symbol": "NVDA",
                    "last_trade_price": "200.00",
                    "previous_close": "190.00",
                    "venue_last_trade_time": "2026-08-25T13:59:59Z",
                }
            }
        ],
    }


def _build(tmp_path: Path, raw: dict | None = None, **kwargs) -> dict:
    out = tmp_path / "live_portfolio_snapshot.json"
    snap = B.build(raw_data=raw if raw is not None else _raw(), output=out, now=NOW, **kwargs)
    return snap


def test_build_requires_raw_path_when_raw_data_missing():
    with pytest.raises(ValueError, match="raw_path"):
        B.build(output=Path("/tmp/unused-snapshot.json"))


def test_nested_quote_unwrap_and_holding_identity(tmp_path):
    snap = _build(tmp_path)
    assert snap["source"] == "robinhood_mcp"
    assert snap["fetched_at"] == NOW.isoformat()
    assert len(snap["holdings"]) == 1
    h = snap["holdings"][0]
    assert h["symbol"] == "NVDA"
    assert h["quantity"] == pytest.approx(10.0)
    assert h["price"] == pytest.approx(200.0)
    assert h["average_buy_price"] == pytest.approx(100.0)
    assert h["equity"] == pytest.approx(2000.0)
    assert h["percent_change"] == pytest.approx(100.0)
    assert "current_price" not in h, "writer contract is `price`, not `current_price`"


def test_flat_quote_shape_is_tolerated(tmp_path):
    raw = _raw(
        quotes=[
            {
                "symbol": "NVDA",
                "last_trade_price": "200.00",
                "previous_close": "190.00",
            }
        ]
    )
    snap = _build(tmp_path, raw)
    assert snap["holdings"][0]["price"] == pytest.approx(200.0)


def test_last_non_reg_trade_price_used_when_last_trade_missing(tmp_path):
    raw = _raw(
        quotes=[
            {
                "quote": {
                    "symbol": "NVDA",
                    "last_non_reg_trade_price": "199.50",
                    "previous_close": "190.00",
                }
            }
        ]
    )
    snap = _build(tmp_path, raw)
    assert snap["holdings"][0]["price"] == pytest.approx(199.50)
    assert snap["holdings"][0]["equity"] == pytest.approx(1995.0)


def test_zero_and_negative_quantity_positions_are_skipped(tmp_path):
    raw = _raw(
        positions=[
            {"symbol": "ZERO", "quantity": "0", "average_buy_price": "10"},
            {"symbol": "NEG", "quantity": "-2", "average_buy_price": "10"},
            {"symbol": "NVDA", "quantity": "10", "average_buy_price": "100.00"},
        ],
        quotes=[
            {"quote": {"symbol": "ZERO", "last_trade_price": "5"}},
            {"quote": {"symbol": "NEG", "last_trade_price": "5"}},
            {"quote": {"symbol": "NVDA", "last_trade_price": "200.00", "previous_close": "190.00"}},
        ],
    )
    snap = _build(tmp_path, raw)
    assert [h["symbol"] for h in snap["holdings"]] == ["NVDA"]


def test_zero_average_cost_does_not_invent_percent_change(tmp_path):
    raw = _raw(
        positions=[
            {"symbol": "NVDA", "quantity": "10", "average_buy_price": "0"},
        ]
    )
    snap = _build(tmp_path, raw)
    assert snap["holdings"][0]["percent_change"] == 0


def test_deployable_is_settled_unleveraged_buying_power_not_cash(tmp_path):
    """Gold-margin trap: pf.cash is the wrong figure; MCP buying_power is settled."""
    snap = _build(tmp_path)
    es = snap["equity_summary"]
    assert es["deployable"] == pytest.approx(1500.0)
    assert es["cash"] == pytest.approx(1500.0)
    assert es["settled_cash"] == pytest.approx(1500.0)
    assert es["buying_power"] == pytest.approx(1500.0)
    assert es["buying_power"] != pytest.approx(99999.0)
    # MCP exposes no Gold-margin figure — pin every leveraged alias to deployable
    # so sizing can never inflate on margin (CLAUDE.md snapshot contract).
    for key in ("buying_power_leveraged", "margin_buying_power", "withdrawable_amount"):
        assert es[key] == pytest.approx(1500.0), key


def test_missing_buying_power_dict_falls_back_to_portfolio_cash(tmp_path):
    raw = _raw()
    raw["portfolio"]["buying_power"] = None
    raw["portfolio"]["cash"] = "800.00"
    snap = _build(tmp_path, raw)
    assert snap["equity_summary"]["deployable"] == pytest.approx(800.0)


@pytest.mark.parametrize("zero", [0, 0.0, "0", "0.00"])
def test_zero_settled_buying_power_is_not_replaced_by_cash(tmp_path, zero):
    """Fully invested: measured $0 must not ``or``-fall through to pf.cash.

    The fixture's cash ($99999) is the documented wrong figure — the same
    Gold-margin trap ``test_deployable_is_settled_unleveraged_buying_power_not_cash``
    already pins when buying_power is $1500.
    """
    snap = _build(tmp_path, _raw(buying_power=zero))
    es = snap["equity_summary"]
    assert es["deployable"] == pytest.approx(0.0)
    assert es["cash"] == pytest.approx(0.0)
    assert es["settled_cash"] == pytest.approx(0.0)
    assert es["buying_power"] != pytest.approx(99999.0)


def test_bool_buying_power_does_not_become_one_dollar(tmp_path):
    """JSON true must not become $1 of dry powder via float(True)."""
    snap = _build(tmp_path, _raw(buying_power=True, cash="800.00"))
    assert snap["equity_summary"]["deployable"] == pytest.approx(800.0)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), "inf", "nan"])
def test_non_finite_buying_power_falls_back_to_cash(tmp_path, bad):
    snap = _build(tmp_path, _raw(buying_power=bad, cash="800.00"))
    assert snap["equity_summary"]["deployable"] == pytest.approx(800.0)


def test_scalar_buying_power_does_not_attributeerror(tmp_path):
    """A flat MCP number must not crash the daily snapshot on ``.get``."""
    raw = _raw()
    raw["portfolio"]["buying_power"] = "1500.00"
    snap = _build(tmp_path, raw)
    assert snap["equity_summary"]["deployable"] == pytest.approx(1500.0)


def test_scalar_zero_buying_power_is_kept(tmp_path):
    raw = _raw()
    raw["portfolio"]["buying_power"] = 0
    snap = _build(tmp_path, raw)
    assert snap["equity_summary"]["deployable"] == pytest.approx(0.0)
    assert snap["equity_summary"]["buying_power"] != pytest.approx(99999.0)


def test_pending_deposits_are_not_treated_as_deployable(tmp_path):
    snap = _build(tmp_path)
    es = snap["equity_summary"]
    assert es["pending_deposits"] == pytest.approx(100.0)
    assert es["uncleared_deposits"] == pytest.approx(100.0)
    assert es["deployable"] == pytest.approx(1500.0)


def test_total_value_is_preferred_over_computed_positions_plus_cash(tmp_path):
    snap = _build(tmp_path)
    es = snap["equity_summary"]
    assert es["portfolio_equity"] == pytest.approx(2000.0)  # positions only (raw file)
    assert es["total"] == pytest.approx(3600.0)
    assert es["account_equity"] == pytest.approx(3600.0)
    # prior close of 190 * 10 + deployable 1500
    assert es["adjusted_equity_previous_close"] == pytest.approx(3400.0)


def test_missing_total_value_uses_positions_plus_deployable(tmp_path):
    raw = _raw(total_value="")
    snap = _build(tmp_path, raw)
    assert snap["equity_summary"]["total"] == pytest.approx(3500.0)
    assert snap["equity_summary"]["account_equity"] == pytest.approx(3500.0)


def test_previous_close_falls_back_to_last_price(tmp_path):
    raw = _raw(
        quotes=[
            {
                "quote": {"symbol": "NVDA", "last_trade_price": "200.00"},
            }
        ]
    )
    snap = _build(tmp_path, raw)
    # prev_eq_positions = 200 * 10; + deployable 1500
    assert snap["equity_summary"]["adjusted_equity_previous_close"] == pytest.approx(3500.0)


def test_quote_provenance_is_keyed_by_symbol_and_never_uses_transport_time(tmp_path):
    snap = _build(tmp_path)
    meta = snap["quote_provenance"]["NVDA"]
    assert meta["quote_as_of"] == "2026-08-25T13:59:59Z"
    assert meta["provider"] == "robinhood_mcp"
    assert meta["session"] == "sess-test"
    assert meta["timestamp_basis"] == "venue_last_trade_time"
    assert "retrieved_at" not in meta


def test_empty_holdings_exits_before_writing_the_snapshot(tmp_path):
    out = tmp_path / "live_portfolio_snapshot.json"
    raw = _raw(positions=[{"symbol": "NVDA", "quantity": "0", "average_buy_price": "1"}])
    with pytest.raises(SystemExit) as exc:
        B.build(raw_data=raw, output=out, now=NOW)
    assert exc.value.code == 1
    assert not out.exists(), "a 0-holding dump must not persist a snapshot"


def test_build_writes_json_to_the_requested_output_path(tmp_path):
    out = tmp_path / "nested" / "snap.json"
    out.parent.mkdir()
    snap = B.build(raw_data=_raw(), output=out, now=NOW)
    written = json.loads(out.read_text())
    assert written["holdings"] == snap["holdings"]
    assert written["equity_summary"]["deployable"] == pytest.approx(1500.0)


def test_build_reads_raw_path(tmp_path):
    raw_path = tmp_path / "mcp_raw.json"
    raw_path.write_text(json.dumps(_raw()))
    out = tmp_path / "snap.json"
    snap = B.build(raw_path=raw_path, output=out, now=NOW)
    assert snap["holdings"][0]["symbol"] == "NVDA"
    assert out.is_file()
