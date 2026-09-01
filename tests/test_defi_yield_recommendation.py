"""DeFi yield landscape must never recommend Tier 3 as the primary.

`_recommend_safe_yield` is the picker used by `get_yield_landscape` and
injected into Claude via `yield_summary_for_claude`. A regression that
sorts DeFi APYs into the recommendation would tell the daily chain to
park idle cash in a smart-contract pool — the module's own contract
forbids that.

yfinance is stubbed before import so this file is stdlib + pytest only.
"""

from __future__ import annotations

import inspect
import sys
import types

sys.modules.setdefault("yfinance", types.SimpleNamespace(Ticker=lambda *a, **k: None))

import defi_yield as dy


def _inst(name: str, apy: float) -> dict:
    return {"name": name, "apy": apy, "risk": "x"}


def test_picker_only_sees_tier1_and_tier2():
    """The extracted picker has no Tier 3 argument — DeFi cannot win here.

    The landscape-level pin (88% Aave still informational) lives in
    test_landscape_recommendation_ignores_tier3_even_when_mocked_highest.
    """
    rec, _proj = dy._recommend_safe_yield(
        tier1=[_inst("3-Month T-Bill (^IRX)", 5.0)],
        tier2=[_inst("SGOV (0-3mo T-Bill ETF)", 4.8)],
        cash_usd=10_000.0,
        tbill=5.0,
    )
    assert rec["instrument"] == "3-Month T-Bill (^IRX)"
    assert rec["apy"] == 5.0
    assert "tier3" not in inspect.signature(dy._recommend_safe_yield).parameters


def test_best_of_tier1_and_tier2_wins():
    rec, _proj = dy._recommend_safe_yield(
        tier1=[_inst("HYSA (est.)", 4.1), _inst("Money Market (est.)", 4.4)],
        tier2=[_inst("SGOV (0-3mo T-Bill ETF)", 5.1), _inst("SHY (1-3yr Tsy ETF)", 3.9)],
        cash_usd=1_200.0,
        tbill=4.0,
    )
    assert rec["instrument"] == "SGOV (0-3mo T-Bill ETF)"
    assert rec["apy"] == 5.1
    # 1200 * 5.1 / 100 / 12 = 5.1
    assert rec["monthly_income_usd"] == 5.1
    assert rec["annual_income_usd"] == 61.2


def test_zero_cash_monthly_income_and_projection_are_zero():
    rec, proj = dy._recommend_safe_yield([_inst("T-Bill", 5.0)], [], 0.0, 5.0)
    assert rec["monthly_income_usd"] == 0.0
    assert rec["annual_income_usd"] == 0.0
    assert all(p["income_usd"] == 0.0 and p["cumulative"] == 0.0 for p in proj)


def test_negative_cash_monthly_income_is_zero_projection_uses_raw():
    """The monthly-income guard is `cash_usd > 0`; projection is not clamped."""
    rec, proj = dy._recommend_safe_yield([_inst("T-Bill", 5.0)], [], -50.0, 5.0)
    assert rec["monthly_income_usd"] == 0.0
    assert rec["annual_income_usd"] == 0.0
    assert proj[0]["income_usd"] < 0.0


def test_empty_safe_tiers_fall_back_to_tbill_name():
    rec, _proj = dy._recommend_safe_yield([], [], cash_usd=2_000.0, tbill=4.25)
    assert rec["instrument"] == "T-Bill"
    assert rec["apy"] == 4.25
    assert rec["monthly_income_usd"] == 7.08  # 2000 * 4.25 / 100 / 12


def test_projection_is_six_months_and_compounds():
    rec, proj = dy._recommend_safe_yield([_inst("MM", 12.0)], [], 1_000.0, 12.0)
    assert [p["month"] for p in proj] == [1, 2, 3, 4, 5, 6]
    # Simple monthly income row is linear; cumulative compounds.
    assert proj[0]["income_usd"] == 10.0  # 1000 * 12 / 100 / 12
    assert proj[5]["income_usd"] == 60.0
    assert proj[0]["cumulative"] == 10.0
    assert proj[5]["cumulative"] > proj[0]["cumulative"]
    assert rec["monthly_income_usd"] == 10.0


def test_landscape_recommendation_ignores_tier3_even_when_mocked_highest(monkeypatch):
    monkeypatch.setattr(dy, "get_tbill_rate", lambda: 4.0)
    monkeypatch.setattr(dy, "get_fed_funds_rate", lambda: 4.5)
    monkeypatch.setattr(dy, "get_etf_yield", lambda _sym: 3.5)
    monkeypatch.setattr(
        dy,
        "get_defi_yields",
        lambda: {
            "USDC_AAVE_V3": {"apy": 88.0, "tvl_usd": 1e9, "chain": "eth", "risk_note": "sc"},
        },
    )
    out = dy.get_yield_landscape(5_000.0)
    assert out["recommendation"]["apy"] < 10.0
    assert out["recommendation"]["instrument"] != "USDC_AAVE_V3"
    assert any(t["name"] == "USDC_AAVE_V3" and t["apy"] == 88.0 for t in out["tiers"]["tier3"])
    assert "INFORMATIONAL ONLY" in out["disclaimer"]
    assert out["cash_usd"] == 5_000.0


def test_yield_summary_skips_sub_100_cash_without_fetching(monkeypatch):
    monkeypatch.setattr(
        dy,
        "get_yield_landscape",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("landscape must not run below $100")
        ),
    )
    assert "insufficient cash" in dy.yield_summary_for_claude(99.99).lower()
    assert "unavailable" not in dy.yield_summary_for_claude(0).lower()


def test_yield_summary_exception_is_unavailable_not_raised(monkeypatch):
    monkeypatch.setattr(
        dy,
        "get_yield_landscape",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("fred down")),
    )
    msg = dy.yield_summary_for_claude(500.0)
    assert msg.startswith("Cash yield analysis unavailable:")
    assert "fred down" in msg
