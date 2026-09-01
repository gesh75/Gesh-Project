"""Owner export cash math — Gold buying_power is never deployable cash.

`export_portfolio._build_equity_summary` is the LaunchAgent writer of
`portfolio_live.json`. A regression that treats RH Gold `buying_power`
(leveraged margin) as cash mis-sizes every consumer of that artifact.
Holdings with unparseable fields must be skipped, not abort the export.

dotenv is stubbed before import so this file runs without that package.
`os.makedirs` for `.rh_session` now happens inside `export()`, not at
import, so collecting this module does not create a session dir.
"""

from __future__ import annotations

import sys
import types

sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

import export_portfolio as ep


def test_gold_leveraged_buying_power_is_not_cash():
    """$100k equity, $80k invested, $200k Gold BP → cash is $20k, not $200k."""
    holdings = [
        {"equity": 50_000.0, "equity_change": 100.0},
        {"equity": 30_000.0, "equity_change": -40.0},
    ]
    summary = ep._build_equity_summary(
        portfolio_equity=100_000.0,
        holdings=holdings,
        raw_buying_power=200_000.0,
        raw_withdrawable=0.0,
    )
    assert summary["cash"] == 20_000.0
    assert summary["total_invested"] == 80_000.0
    assert summary["portfolio_equity"] == 100_000.0
    # withdrawable is 0 → fall through to raw buying_power (diagnostic field)
    assert summary["buying_power"] == 200_000.0
    assert summary["today_pnl"] == 60.0


def test_pending_deposit_withdrawable_zero_does_not_invent_cash():
    holdings = [{"equity": 10_000.0, "equity_change": 0.0}]
    summary = ep._build_equity_summary(
        portfolio_equity=10_000.0,
        holdings=holdings,
        raw_buying_power=50_000.0,
        raw_withdrawable=0.0,
    )
    assert summary["cash"] == 0.0
    assert summary["buying_power"] == 50_000.0  # diagnostic fallback, not cash


def test_withdrawable_wins_over_leveraged_buying_power_for_bp_field():
    holdings = [{"equity": 4_000.0, "equity_change": 0.0}]
    summary = ep._build_equity_summary(
        portfolio_equity=5_000.0,
        holdings=holdings,
        raw_buying_power=80_000.0,
        raw_withdrawable=900.0,
    )
    assert summary["cash"] == 1_000.0
    assert summary["buying_power"] == 900.0


def test_today_pnl_pct_zero_when_cost_basis_is_zero():
    """total_invested - today_pnl == 0 must not ZeroDivision."""
    holdings = [{"equity": 10.0, "equity_change": 10.0}]  # 100% gain from $0
    summary = ep._build_equity_summary(10.0, holdings, 0.0, 0.0)
    assert summary["today_pnl_pct"] == 0
    assert summary["today_pnl"] == 10.0


def test_today_pnl_pct_uses_prior_invested():
    holdings = [{"equity": 110.0, "equity_change": 10.0}]
    summary = ep._build_equity_summary(110.0, holdings, 0.0, 0.0)
    assert summary["today_pnl_pct"] == 10.0  # 10 / (110-10) * 100


def test_empty_book_cash_equals_equity():
    summary = ep._build_equity_summary(
        portfolio_equity=1_234.56,
        holdings=[],
        raw_buying_power=0.0,
        raw_withdrawable=0.0,
    )
    assert summary["cash"] == 1_234.56
    assert summary["total_invested"] == 0.0
    assert summary["portfolio_equity"] == 1_234.56
    assert summary["buying_power"] == 1_234.56  # last fallback is cash
    assert summary["today_pnl"] == 0.0
    assert summary["today_pnl_pct"] == 0


def test_invested_above_equity_clamps_cash_at_zero():
    holdings = [{"equity": 12_000.0, "equity_change": 0.0}]
    summary = ep._build_equity_summary(10_000.0, holdings, 0.0, 0.0)
    assert summary["cash"] == 0.0


def test_missing_portfolio_equity_falls_back_to_invested():
    holdings = [{"equity": 8_000.0, "equity_change": 25.0}]
    summary = ep._build_equity_summary(0.0, holdings, 0.0, 0.0)
    assert summary["cash"] == 0.0
    assert summary["portfolio_equity"] == 8_000.0
    assert summary["today_pnl"] == 25.0


def test_normalize_holding_skips_unparseable_quantity():
    assert ep._normalize_holding("BAD", {"quantity": "n/a", "price": "10"}) is None


def test_normalize_holding_equity_defaults_to_qty_times_price():
    row = ep._normalize_holding(
        "AAA",
        {
            "quantity": "2",
            "price": "5.5",
            # equity omitted
            "average_buy_price": "5",
            "percent_change": "10",
            "equity_change": "1.0",
        },
    )
    assert row is not None
    assert row["symbol"] == "AAA"
    assert row["quantity"] == 2.0
    assert row["price"] == 5.5
    assert row["equity"] == 11.0


def test_normalize_holding_blank_fields_become_zero():
    row = ep._normalize_holding("Z", {})
    assert row is not None
    assert row["quantity"] == 0.0
    assert row["price"] == 0.0
    assert row["equity"] == 0.0
    assert row["equity_change"] == 0.0
