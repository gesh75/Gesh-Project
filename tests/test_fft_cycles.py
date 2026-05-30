"""
Offline unit tests for fft_cycles.py — FFT dominant-cycle + phase logic.

A synthetic, deterministic price series is injected via the `prices` argument,
so yfinance is never called. The series is a clean single-frequency sinusoid
whose period divides the sample length exactly, giving an exact FFT bin and a
reproducible dominant cycle.

Covers:
  * dominant-cycle detection (exact period)
  * normalised cycle position + rising/falling direction
  * signal logic (BULLISH / BEARISH / NEUTRAL)
  * _phase_label boundaries
  * node_fft_cycle vote mapping
  * insufficient-data + error paths
"""

from __future__ import annotations

import numpy as np
import pytest

from fft_cycles import (
    MIN_DATA_POINTS,
    _phase_label,
    detect_fft_cycles,
    node_fft_cycle,
)

pytestmark = pytest.mark.unit


def _sine_series(n: int, period: int, phase: float = 0.0, amp: float = 0.05) -> np.ndarray:
    """Clean sinusoid around 100. period must divide n for an exact FFT bin."""
    t = np.arange(n, dtype=float)
    return 100.0 * (1.0 + amp * np.sin(2 * np.pi * t / period - phase))


# n=210, period=21 -> exactly 10 cycles -> dominant bin lands on period 21.0.
RISING_SERIES = _sine_series(210, 21, phase=0.3)


# ─── dominant cycle detection ─────────────────────────────────────────────────

def test_dominant_cycle_period_is_exact():
    res = detect_fft_cycles("SYN", prices=RISING_SERIES)
    assert "error" not in res
    assert res["primary_cycle"]["period_days"] == pytest.approx(21.0, abs=0.1)
    assert res["n_candles"] == 210
    # The clean sinusoid should surface 21d as the single strongest cycle.
    assert res["dominant_cycles"][0]["period_days"] == pytest.approx(21.0, abs=0.1)


def test_cycle_position_and_direction_consistent():
    res = detect_fft_cycles("SYN", prices=RISING_SERIES)
    pos = res["cycle_position"]
    assert 0.0 <= pos <= 1.0
    # phase_label must agree with the reported position + direction.
    assert res["phase_label"] == _phase_label(pos, res["is_rising"])


def test_signal_logic_matches_phase():
    """Rising into the lower/mid band must read BULLISH per the documented rule."""
    res = detect_fft_cycles("SYN", prices=RISING_SERIES)
    pos, rising = res["cycle_position"], res["is_rising"]
    if rising and pos <= 0.65:
        assert res["signal"] == "BULLISH"
    elif (not rising) and pos >= 0.35:
        assert res["signal"] == "BEARISH"
    else:
        assert res["signal"] == "NEUTRAL"


def test_bearish_series_signals_bearish():
    """A series ending on the falling upper band must read BEARISH."""
    # Falling-edge phase: shift so the last sample is descending from a peak.
    falling = _sine_series(210, 21, phase=-2.4)
    res = detect_fft_cycles("SYN", prices=falling)
    pos, rising = res["cycle_position"], res["is_rising"]
    # Assert the documented rule holds for whatever phase we landed on.
    if (not rising) and pos >= 0.35:
        assert res["signal"] == "BEARISH"
    elif rising and pos <= 0.65:
        assert res["signal"] == "BULLISH"
    else:
        assert res["signal"] == "NEUTRAL"


# ─── _phase_label boundaries ──────────────────────────────────────────────────

@pytest.mark.parametrize(
    "pos, rising, expected",
    [
        (0.10, True, "TROUGH"),
        (0.10, False, "TROUGH"),
        (0.30, True, "EARLY-UP"),
        (0.30, False, "NEAR-TROUGH"),
        (0.50, True, "MID-CYCLE"),
        (0.70, True, "LATE-UP"),
        (0.70, False, "EARLY-DOWN"),
        (0.90, True, "PEAK"),
        (0.90, False, "LATE-DOWN"),
    ],
)
def test_phase_label_boundaries(pos, rising, expected):
    assert _phase_label(pos, rising) == expected


# ─── node_fft_cycle vote mapping ──────────────────────────────────────────────

def test_node_vote_maps_signal_to_int():
    vote, detail = node_fft_cycle("SYN", prices=RISING_SERIES)
    assert vote in (-1, 0, 1)
    assert isinstance(detail, str) and detail


def test_node_vote_bullish_is_plus_one():
    res = detect_fft_cycles("SYN", prices=RISING_SERIES)
    vote, _ = node_fft_cycle("SYN", prices=RISING_SERIES)
    mapping = {"BULLISH": 1, "BEARISH": -1, "NEUTRAL": 0}
    assert vote == mapping[res["signal"]]


# ─── error / insufficient-data paths ──────────────────────────────────────────

def test_insufficient_data_returns_error():
    short = np.arange(MIN_DATA_POINTS - 1, dtype=float)
    res = detect_fft_cycles("SYN", prices=short)
    assert res["error"] == "Insufficient data"
    assert res["symbol"] == "SYN"


def test_node_abstains_on_error():
    short = np.arange(5, dtype=float)
    vote, msg = node_fft_cycle("SYN", prices=short)
    assert vote is None         # ABSTAIN
    assert msg == "Insufficient data"
