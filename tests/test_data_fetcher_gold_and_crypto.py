"""Gold-cash + crypto holdings first-execution tests for data_fetcher.

Two stop-ship contracts that lived only in comments / CLAUDE.md:

  * ``get_total_equity`` must never surface Robinhood Gold
    ``account["buying_power"]`` (leveraged margin) as cash or as the
    ``buying_power`` alias. Deployable cash is ``margin_balances.portfolio_cash``.
    ``withdrawable_amount`` reads $0 while a deposit clears and is last-resort only.
  * ``get_portfolio_holdings`` crypto path had no tests. A failed quote must tag
    ``price_source=unavailable`` instead of silently becoming a $0 holding that
    drops out of weights. A crypto outage must not blank the stock book.

Offline: robin_stocks is stubbed at the import boundary when the package is
absent, then monkeypatched on ``data_fetcher.rh``.
"""

from __future__ import annotations

import math
import sys
from types import ModuleType

import pytest

if "robin_stocks.robinhood" not in sys.modules:
    _pkg = ModuleType("robin_stocks")
    _mod = ModuleType("robin_stocks.robinhood")
    _pkg.robinhood = _mod
    sys.modules["robin_stocks"] = _pkg
    sys.modules["robin_stocks.robinhood"] = _mod

import data_fetcher as df  # noqa: E402


def _rh(monkeypatch, **fns):
    """Patch robin_stocks seams. raising=False so the import stub needs no attrs."""
    for name, fn in fns.items():
        monkeypatch.setattr(df.rh, name, fn, raising=False)


GOLD_LEVERAGED_BP = 17_000.0
SETTLED_CASH = 535.44
ACCOUNT_EQUITY = 35_000.0


def _stock(
    symbol: str = "NVDA",
    *,
    qty: str = "10",
    equity: str = "1000",
    price: str = "100",
    pe: str = "30",
    percentage: str = "99",
) -> dict:
    return {
        symbol: {
            "quantity": qty,
            "average_buy_price": "90",
            "equity": equity,
            "percent_change": "5",
            "equity_change": "50",
            "price": price,
            "type": "stock",
            "pe_ratio": pe,
            "percentage": percentage,
        }
    }


def _crypto_pos(code: str = "BTC", qty: str = "2", cost: str = "20000") -> dict:
    return {
        "quantity": qty,
        "currency": {"code": code},
        "cost_bases": [{"direct_cost_basis": cost}],
    }


# ── Gold cash / buying_power trap ─────────────────────────────────────────────


def test_gold_leveraged_buying_power_is_never_returned_as_cash(monkeypatch):
    """CLAUDE.md: Gold buying_power is leveraged margin. Size from settled cash."""
    _rh(
        monkeypatch,
        load_portfolio_profile=lambda: {
            "equity": ACCOUNT_EQUITY,
            "extended_hours_equity": ACCOUNT_EQUITY,
            "adjusted_equity_previous_close": 34_000.0,
            "withdrawable_amount": 0,  # Gold: $0 while a deposit clears
        },
        load_account_profile=lambda: {
            "buying_power": GOLD_LEVERAGED_BP,
            "margin_balances": {"portfolio_cash": SETTLED_CASH},
        },
    )
    eq = df.get_total_equity()
    assert eq["cash"] == pytest.approx(SETTLED_CASH)
    assert eq["buying_power"] == pytest.approx(SETTLED_CASH)
    assert eq["buying_power"] != pytest.approx(GOLD_LEVERAGED_BP)
    assert eq["portfolio_equity"] == pytest.approx(ACCOUNT_EQUITY)


def test_withdrawable_zero_does_not_win_over_portfolio_cash(monkeypatch):
    _rh(
        monkeypatch,
        load_portfolio_profile=lambda: {"equity": ACCOUNT_EQUITY, "withdrawable_amount": 0},
        load_account_profile=lambda: {"margin_balances": {"portfolio_cash": SETTLED_CASH}},
    )
    eq = df.get_total_equity()
    assert eq["cash"] == pytest.approx(SETTLED_CASH)


def test_unallocated_margin_cash_used_when_portfolio_cash_missing(monkeypatch):
    _rh(
        monkeypatch,
        load_portfolio_profile=lambda: {"equity": ACCOUNT_EQUITY, "withdrawable_amount": 0},
        load_account_profile=lambda: {"margin_balances": {"unallocated_margin_cash": 400.0}},
    )
    eq = df.get_total_equity()
    assert eq["cash"] == pytest.approx(400.0)
    assert eq["buying_power"] == pytest.approx(400.0)


def test_withdrawable_is_last_resort_when_margin_cash_absent(monkeypatch):
    _rh(
        monkeypatch,
        load_portfolio_profile=lambda: {"equity": ACCOUNT_EQUITY, "withdrawable_amount": 200.0},
        load_account_profile=lambda: {"margin_balances": {}},
    )
    eq = df.get_total_equity()
    assert eq["cash"] == pytest.approx(200.0)


def test_last_resort_withdrawable_still_ignores_account_buying_power(monkeypatch):
    """Withdrawable is last-resort cash, not a license to read Gold margin."""
    _rh(
        monkeypatch,
        load_portfolio_profile=lambda: {"equity": ACCOUNT_EQUITY, "withdrawable_amount": 200.0},
        load_account_profile=lambda: {
            "buying_power": GOLD_LEVERAGED_BP,
            "margin_balances": {},
        },
    )
    eq = df.get_total_equity()
    assert eq["cash"] == pytest.approx(200.0)
    assert eq["buying_power"] == pytest.approx(200.0)
    assert eq["buying_power"] != pytest.approx(GOLD_LEVERAGED_BP)


def test_zero_portfolio_cash_is_kept_not_replaced_by_unallocated(monkeypatch):
    """Fully invested: measured $0 portfolio_cash must not ``or``-fall through.

    Robinhood returns cash as strings. ``float("0.0000" or 0)`` is 0.0, then
    ``0.0 or unallocated`` invented Gold leftover as deployable dry powder.
    """
    leftover = 1_500.0
    _rh(
        monkeypatch,
        load_portfolio_profile=lambda: {
            "equity": ACCOUNT_EQUITY,
            "withdrawable_amount": 800.0,
        },
        load_account_profile=lambda: {
            "buying_power": GOLD_LEVERAGED_BP,
            "margin_balances": {
                "portfolio_cash": "0.0000",
                "unallocated_margin_cash": leftover,
            },
        },
    )
    eq = df.get_total_equity()
    assert eq["cash"] == pytest.approx(0.0)
    assert eq["buying_power"] == pytest.approx(0.0)
    assert eq["deployable"] == pytest.approx(0.0)
    assert eq["cash"] != pytest.approx(leftover)
    assert eq["buying_power"] != pytest.approx(GOLD_LEVERAGED_BP)


def test_numeric_zero_portfolio_cash_is_kept(monkeypatch):
    _rh(
        monkeypatch,
        load_portfolio_profile=lambda: {
            "equity": ACCOUNT_EQUITY,
            "withdrawable_amount": 200.0,
        },
        load_account_profile=lambda: {
            "margin_balances": {
                "portfolio_cash": 0.0,
                "unallocated_margin_cash": 400.0,
            },
        },
    )
    eq = df.get_total_equity()
    assert eq["cash"] == pytest.approx(0.0)
    assert eq["deployable"] == pytest.approx(0.0)


def test_bool_portfolio_cash_is_unknown_not_one_dollar(monkeypatch):
    """JSON true must not become $1 of settled cash and skip the fallback."""
    _rh(
        monkeypatch,
        load_portfolio_profile=lambda: {"equity": ACCOUNT_EQUITY, "withdrawable_amount": 0},
        load_account_profile=lambda: {
            "margin_balances": {
                "portfolio_cash": True,
                "unallocated_margin_cash": 400.0,
            },
        },
    )
    eq = df.get_total_equity()
    assert eq["cash"] == pytest.approx(400.0)
    assert eq["deployable"] == pytest.approx(400.0)


def test_nan_portfolio_cash_falls_through_to_unallocated(monkeypatch):
    """A poisoned portfolio_cash is unknown, not $0 — the next finite field wins."""
    _rh(
        monkeypatch,
        load_portfolio_profile=lambda: {"equity": ACCOUNT_EQUITY, "withdrawable_amount": 0},
        load_account_profile=lambda: {
            "margin_balances": {
                "portfolio_cash": float("nan"),
                "unallocated_margin_cash": 400.0,
            },
        },
    )
    eq = df.get_total_equity()
    assert eq["cash"] == pytest.approx(400.0)
    assert eq["deployable"] == pytest.approx(400.0)


def test_all_unusable_cash_sources_stay_non_finite(monkeypatch):
    """Every cash field NaN/inf must not invent $0 dry powder."""
    _rh(
        monkeypatch,
        load_portfolio_profile=lambda: {
            "equity": ACCOUNT_EQUITY,
            "withdrawable_amount": float("inf"),
        },
        load_account_profile=lambda: {
            "buying_power": GOLD_LEVERAGED_BP,
            "margin_balances": {
                "portfolio_cash": float("nan"),
                "unallocated_margin_cash": float("nan"),
            },
        },
    )
    eq = df.get_total_equity()
    assert not math.isfinite(eq["cash"])
    assert not math.isfinite(eq["deployable"])
    assert eq["cash"] != pytest.approx(GOLD_LEVERAGED_BP)


def test_empty_cash_sources_do_not_fall_back_to_account_buying_power(monkeypatch):
    """If every cash field is 0, return $0 — never Gold leveraged buying_power.

    The first Gold test still has portfolio_cash, so a later 'helpful' fallback
    to account['buying_power'] when cash is empty would pass it. $0 is honest
    empty; $17k of margin credit would resize Kelly and the risk gates.
    """
    _rh(
        monkeypatch,
        load_portfolio_profile=lambda: {"equity": ACCOUNT_EQUITY, "withdrawable_amount": 0},
        load_account_profile=lambda: {
            "buying_power": GOLD_LEVERAGED_BP,
            "margin_balances": {},
        },
    )
    eq = df.get_total_equity()
    assert eq["cash"] == pytest.approx(0.0)
    assert eq["buying_power"] == pytest.approx(0.0)
    assert eq["cash"] != pytest.approx(GOLD_LEVERAGED_BP)
    assert eq["buying_power"] != pytest.approx(GOLD_LEVERAGED_BP)


# ── Crypto holdings path ──────────────────────────────────────────────────────


def test_crypto_and_stock_weights_recalculated_from_combined_equity(monkeypatch):
    _rh(
        monkeypatch,
        build_holdings=lambda: _stock(),
        get_crypto_positions=lambda: [_crypto_pos()],
        get_crypto_quote=lambda s: {"mark_price": "50000", "open_price": "40000"},
    )
    out = df.get_portfolio_holdings()
    btc = out.loc[out["symbol"] == "BTC"].iloc[0]
    nvda = out.loc[out["symbol"] == "NVDA"].iloc[0]
    assert btc["type"] == "cryptocurrency"
    assert btc["equity"] == pytest.approx(100_000.0)  # 2 × 50_000
    assert btc["average_buy_price"] == pytest.approx(10_000.0)  # 20_000 / 2
    assert btc["percent_change"] == pytest.approx(25.0)  # (50k-40k)/40k
    # broker "percentage" is ignored — combined equity is 101_000
    assert btc["portfolio_percentage"] + nvda["portfolio_percentage"] == pytest.approx(100.0)
    assert out.iloc[0]["symbol"] == "BTC"  # sorted equity DESC


def test_unpriced_crypto_is_tagged_unavailable_not_silent_zero(monkeypatch):
    _rh(
        monkeypatch,
        build_holdings=lambda: {},
        get_crypto_positions=lambda: [_crypto_pos()],
        get_crypto_quote=lambda s: {"mark_price": "0", "bid_price": "0", "open_price": "40000"},
    )
    out = df.get_portfolio_holdings()
    row = out.iloc[0]
    assert row["symbol"] == "BTC"
    assert row["quantity"] == pytest.approx(2.0)
    assert row["current_price"] == pytest.approx(0.0)
    assert row["equity"] == pytest.approx(0.0)
    assert row["price_source"] == "unavailable"


def test_zero_open_price_does_not_invent_infinite_change(monkeypatch):
    _rh(
        monkeypatch,
        build_holdings=lambda: {},
        get_crypto_positions=lambda: [_crypto_pos()],
        get_crypto_quote=lambda s: {"mark_price": "50", "open_price": "0"},
    )
    row = df.get_portfolio_holdings().iloc[0]
    assert row["current_price"] == pytest.approx(50.0)
    assert row["percent_change"] == pytest.approx(0.0)


def test_crypto_quote_exception_tags_unavailable(monkeypatch):
    def _boom(_symbol):
        raise TimeoutError("quote timeout")

    _rh(
        monkeypatch,
        build_holdings=lambda: {},
        get_crypto_positions=lambda: [_crypto_pos()],
        get_crypto_quote=_boom,
    )
    row = df.get_portfolio_holdings().iloc[0]
    assert row["symbol"] == "BTC"
    assert row["quantity"] == pytest.approx(2.0)
    assert row["current_price"] == pytest.approx(0.0)
    assert row["price_source"] == "unavailable"


def test_crypto_outage_keeps_stock_book(monkeypatch):
    def _boom():
        raise ConnectionError("crypto feed down")

    _rh(monkeypatch, build_holdings=lambda: _stock(), get_crypto_positions=_boom)
    out = df.get_portfolio_holdings()
    assert list(out["symbol"]) == ["NVDA"]
    assert out.iloc[0]["portfolio_percentage"] == pytest.approx(100.0)


def test_zero_qty_and_blank_code_crypto_rows_are_skipped(monkeypatch):
    _rh(
        monkeypatch,
        build_holdings=lambda: {},
        get_crypto_positions=lambda: [
            _crypto_pos(qty="0"),
            {"quantity": "1", "currency": {"code": ""}, "cost_bases": []},
            _crypto_pos(code="ETH", qty="1", cost="3000"),
        ],
        get_crypto_quote=lambda s: {"mark_price": "3000", "open_price": "3000"},
    )
    out = df.get_portfolio_holdings()
    assert list(out["symbol"]) == ["ETH"]


def test_stock_nan_pe_is_none_not_a_number(monkeypatch):
    _rh(
        monkeypatch,
        build_holdings=lambda: _stock(pe="nan"),
        get_crypto_positions=lambda: [],
    )
    row = df.get_portfolio_holdings().iloc[0]
    assert row["pe_ratio"] is None
