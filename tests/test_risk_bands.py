"""Tests for risk_bands.py — the canonical exit-risk math shared with agentic-rh-test."""

from risk_bands import exit_analytics, exit_bands, trailing_stop


# --- exit_bands ------------------------------------------------------------
def test_exit_bands_medium_is_base_anchor():
    b = exit_bands("MEDIUM")
    assert b == {"take_profit_pct": 8.0, "stop_loss_pct": 5.0}


def test_exit_bands_low_tightens():
    b = exit_bands("LOW")
    # 8*0.6=4.8 ; 5*0.6=3.0
    assert b == {"take_profit_pct": 4.8, "stop_loss_pct": 3.0}


def test_exit_bands_high_widens():
    b = exit_bands("HIGH")
    # 8*1.5=12 ; 5*1.5=7.5
    assert b == {"take_profit_pct": 12.0, "stop_loss_pct": 7.5}


def test_exit_bands_extreme_widest_then_clamped():
    b = exit_bands("EXTREME")
    # 8*2=16 -> clamped to TP max 15 ; 5*2=10 -> SL max 10
    assert b["take_profit_pct"] == 15.0
    assert b["stop_loss_pct"] == 10.0


def test_exit_bands_unknown_tier_falls_back_to_medium():
    assert exit_bands("WAT") == exit_bands("MEDIUM")
    assert exit_bands(None) == exit_bands("MEDIUM")


def test_exit_bands_case_insensitive():
    assert exit_bands("high") == exit_bands("HIGH")


def test_exit_bands_clamps_runaway_config():
    # Absurd base values get clamped to the documented ceilings/floors.
    b = exit_bands("HIGH", base_tp=1000.0, base_sl=1000.0)
    assert b["take_profit_pct"] == 15.0
    assert b["stop_loss_pct"] == 10.0
    b2 = exit_bands("LOW", base_tp=0.1, base_sl=0.1)
    assert b2["take_profit_pct"] == 4.0
    assert b2["stop_loss_pct"] == 3.0


# --- trailing_stop ---------------------------------------------------------
def test_trailing_inactive_before_tp_band_hit():
    # entry 100, MEDIUM TP band = +8% -> trigger at 108. peak 105 < 108.
    r = trailing_stop(entry=100.0, peak=105.0, volatility_tier="MEDIUM")
    assert r["active"] is False
    # rides the fixed vol-adaptive stop: 100*(1-5%) = 95
    assert r["stop_price"] == 95.0


def test_trailing_activates_after_tp_band():
    # peak 110 >= 108 trigger -> trail engages. MEDIUM trail = 4% below peak.
    r = trailing_stop(entry=100.0, peak=110.0, volatility_tier="MEDIUM")
    assert r["active"] is True
    assert r["trail_pct"] == 4.0
    # 110*(1-4%) = 105.6 ; floored at fixed stop 95 -> 105.6
    assert r["stop_price"] == 105.6


def test_trailing_floored_at_fixed_stop_never_loosens():
    # Even active, trail can't drop below the fixed stop. Construct a case where
    # the raw trail would be below the fixed stop by using a peak barely at trigger.
    # entry 100, HIGH: TP +12% trigger=112, fixed stop 7.5% -> 92.5, trail 6%.
    r = trailing_stop(entry=100.0, peak=113.0, volatility_tier="HIGH")
    assert r["active"] is True
    # 113*(1-6%)=106.22 > 92.5 -> 106.22
    assert r["stop_price"] == 106.22
    assert r["stop_price"] >= 92.5


def test_trailing_activates_at_exact_tp_boundary():
    # entry=100, HIGH TP=12% -> trigger = 100*1.12 = 112.00000000000001 (float wart).
    # A peak exactly at the band must ARM, not stay inactive.
    r = trailing_stop(entry=100.0, peak=112.0, volatility_tier="HIGH")
    assert r["active"] is True


def test_trailing_high_tier_wider_trail_than_low():
    hi = trailing_stop(entry=100.0, peak=200.0, volatility_tier="HIGH")
    lo = trailing_stop(entry=100.0, peak=200.0, volatility_tier="LOW")
    assert hi["trail_pct"] > lo["trail_pct"]


def test_trailing_invalid_entry_falls_back_to_fixed():
    r = trailing_stop(entry=0.0, peak=50.0, volatility_tier="MEDIUM")
    assert r["active"] is False
    assert r["reason"] == "invalid_entry_or_peak"


def test_trailing_none_peak_falls_back_to_fixed():
    r = trailing_stop(entry=100.0, peak=None, volatility_tier="MEDIUM")
    assert r["active"] is False
    assert r["stop_price"] == 95.0


def test_trailing_bool_entry_or_peak_is_not_one_dollar():
    """True is an int subclass — must not bind entry/peak to $1 and invent a trail."""
    for entry, peak in ((True, 110.0), (100.0, True), (True, True), (False, 110.0)):
        r = trailing_stop(entry=entry, peak=peak, volatility_tier="MEDIUM")
        assert r["active"] is False
        assert r["reason"] == "invalid_entry_or_peak"
        assert r["trail_pct"] is None


def test_trailing_nan_or_inf_cannot_arm_a_stop():
    """NaN comparisons are all False, so an unguarded peak would silently activate."""
    for entry in (float("nan"), float("inf"), float("-inf")):
        invalid_entry = trailing_stop(entry=entry, peak=110.0, volatility_tier="MEDIUM")
        assert invalid_entry["active"] is False
        assert invalid_entry["reason"] == "invalid_entry_or_peak"
        assert invalid_entry["stop_price"] == 0.0

    # Valid entry + missing peak rides the fixed stop — never a NaN trail price.
    for peak in (float("nan"), float("inf"), float("-inf")):
        r = trailing_stop(entry=100.0, peak=peak, volatility_tier="MEDIUM")
        assert r["active"] is False
        assert r["reason"] == "invalid_entry_or_peak"
        assert r["trail_pct"] is None
        assert r["stop_price"] == 95.0


# --- exit_analytics --------------------------------------------------------
def _row(**kw):
    base = {
        "agent_pnl": "", "exit_reason": "", "volatility_tier": "",
        "tp_pct": "", "sl_pct": "", "mfe_pct": "", "mae_pct": "",
    }
    base.update(kw)
    return base


def test_analytics_ignores_open_trades():
    rows = [_row(), _row(agent_pnl="")]
    a = exit_analytics(rows)
    assert a["exit_reasons"] == {}
    assert a["n_excursion"] == 0


def test_analytics_counts_reasons_and_excursions():
    rows = [
        _row(agent_pnl="5", exit_reason="take_profit", volatility_tier="HIGH",
             tp_pct="12", sl_pct="7.5", mfe_pct="10", mae_pct="-3"),
        _row(agent_pnl="-2", exit_reason="trailing_stop", volatility_tier="MEDIUM",
             tp_pct="8", sl_pct="5", mfe_pct="6", mae_pct="-4"),
    ]
    a = exit_analytics(rows)
    assert a["exit_reasons"] == {"take_profit": 1, "trailing_stop": 1}
    assert a["avg_mfe_pct"] == 8.0
    assert a["avg_mae_pct"] == -3.5
    assert a["n_excursion"] == 2
    assert a["n_untracked_closed"] == 0


def test_analytics_excludes_untracked_from_averages():
    rows = [
        _row(agent_pnl="5", exit_reason="take_profit", volatility_tier="HIGH",
             tp_pct="12", sl_pct="7.5", mfe_pct="10", mae_pct="-3"),
        # untracked: blank tier -> counted in reasons, excluded from MFE/MAE
        _row(agent_pnl="-2", exit_reason="manual", mfe_pct="99", mae_pct="-99"),
    ]
    a = exit_analytics(rows)
    assert a["n_untracked_closed"] == 1
    assert a["avg_mfe_pct"] == 10.0  # the 99 is excluded
    assert a["n_excursion"] == 1


def test_analytics_inf_pnl_is_not_a_closed_trade():
    """'inf' parses as a float and must not enter the closed-trade denominator."""
    rows = [
        _row(agent_pnl="inf", exit_reason="take_profit", volatility_tier="HIGH",
             tp_pct="12", sl_pct="7.5", mfe_pct="10", mae_pct="-3"),
        _row(agent_pnl="nan", exit_reason="stop", volatility_tier="HIGH",
             tp_pct="12", sl_pct="7.5", mfe_pct="99", mae_pct="-99"),
    ]
    a = exit_analytics(rows)
    assert a["exit_reasons"] == {}
    assert a["n_excursion"] == 0
    assert a["avg_mfe_pct"] is None


def test_analytics_normalizes_reason_typos():
    rows = [
        _row(agent_pnl="1", exit_reason="stop ", volatility_tier="LOW",
             tp_pct="4.8", sl_pct="3", mfe_pct="1", mae_pct="-1"),
        _row(agent_pnl="1", exit_reason="GESH-Bearish", volatility_tier="LOW",
             tp_pct="4.8", sl_pct="3", mfe_pct="1", mae_pct="-1"),
        _row(agent_pnl="1", exit_reason="bogus", volatility_tier="LOW",
             tp_pct="4.8", sl_pct="3", mfe_pct="1", mae_pct="-1"),
    ]
    a = exit_analytics(rows)
    assert a["exit_reasons"]["stop"] == 1
    assert a["exit_reasons"]["gesh_bearish"] == 1
    assert a["exit_reasons"]["invalid"] == 1
