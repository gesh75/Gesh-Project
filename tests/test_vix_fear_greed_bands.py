"""First execution tests for VIX / Fear & Greed score bands.

`vix_regime_score` and `fear_greed_score` feed consensus Node 12 (long-term
macro). Inclusive edges matter: VIX 30.0 is documented as NOT high (>30),
F&G 25.0 is NOT Extreme Fear (<25). Missing / short history must score 0
with level None — never invent a print.
"""

from __future__ import annotations

import pandas as pd
import pytest

import long_term_macro as ltm


def _vix(*levels: float) -> pd.Series:
    return pd.Series(list(levels), dtype=float)


def _fg(*levels: float) -> pd.DataFrame:
    return pd.DataFrame({"fear_greed": list(levels)})


def test_vix_regime_score_insufficient_data_is_zero_not_invented():
    empty = ltm.vix_regime_score(None)
    assert empty == {"score": 0, "detail": "VIX insufficient data", "level": None}

    short = ltm.vix_regime_score(_vix(12.0, 13.0, 14.0, 15.0))  # 4 < 5
    assert short["score"] == 0
    assert short["level"] is None
    assert "insufficient" in short["detail"].lower()


@pytest.mark.parametrize(
    "latest,score,needle",
    [
        (30.01, 1, "high"),
        (30.0, 0, "normal"),  # >30 is high; 30.0 is not
        (15.0, 0, "normal"),  # <15 is low; 15.0 is not
        (14.99, -1, "low"),
        (22.0, 0, "normal"),
    ],
)
def test_vix_regime_score_inclusive_bands(latest, score, needle):
    out = ltm.vix_regime_score(_vix(18.0, 19.0, 20.0, 21.0, latest))
    assert out["score"] == score
    assert out["level"] == pytest.approx(latest)
    assert needle in out["detail"].lower()


def test_fear_greed_score_missing_frame_is_zero_not_invented():
    assert ltm.fear_greed_score(None) == {
        "score": 0,
        "detail": "F&G no data",
        "level": None,
    }
    assert ltm.fear_greed_score(pd.DataFrame({"fear_greed": []}))["level"] is None
    assert ltm.fear_greed_score(pd.DataFrame({"fear_greed": []}))["score"] == 0


@pytest.mark.parametrize(
    "latest,score,needle",
    [
        (24.9, 1, "Extreme Fear"),
        (25.0, 0.5, "Fear"),  # <25 is extreme; 25.0 is Fear
        (39.9, 0.5, "Fear"),
        (40.0, 0, "Neutral"),
        (60.0, 0, "Neutral"),  # >60 is Greed; 60.0 is Neutral
        (60.1, -0.5, "Greed"),
        (75.0, -0.5, "Greed"),  # >75 is extreme; 75.0 is Greed
        (75.1, -1, "Extreme Greed"),
    ],
)
def test_fear_greed_score_inclusive_bands(latest, score, needle):
    out = ltm.fear_greed_score(_fg(50.0, latest))
    assert out["score"] == score
    assert out["level"] == pytest.approx(latest)
    assert needle in out["detail"]
