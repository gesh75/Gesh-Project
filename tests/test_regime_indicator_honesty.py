"""M-09: a too-short series has NO RSI — it must not read as a neutral 50."""

from __future__ import annotations

import numpy as np

import regime


def test_rsi_short_series_is_unknown_not_neutral_fifty():
    assert regime._rsi(np.array([100.0, 101.0, 102.0])) is None


def test_rsi_exactly_period_plus_one_points_is_computed():
    close = 100.0 + np.arange(regime.RSI_PERIOD + 1, dtype=float)
    assert regime._rsi(close) == 100.0


def test_classify_regime_tolerates_unknown_rsi():
    # Unknown RSI must neither trigger nor suppress anything by pretending to be 50.
    label = regime._classify_regime(30.0, 4.0, None, [4.0] * 30)
    assert isinstance(label, str) and label
