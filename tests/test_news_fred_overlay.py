"""FRED macro overlay on scan_news — first execution tests for the blend.

market_mood is what the daily chain and customer intelligence panes consume.
The overlay must not invent a mood from empty news, must lean risk-off on
disagreement, and must keep score-band edges honest (20 / -20 are NEUTRAL).
"""

from __future__ import annotations

import math

import pytest

import news_agent


@pytest.mark.parametrize(
    "news_mood,fred_signal,expected",
    [
        ("UNAVAILABLE", "RISK_ON", "UNAVAILABLE"),
        ("UNAVAILABLE", "RISK_OFF", "UNAVAILABLE"),
        ("UNAVAILABLE", "NEUTRAL", "UNAVAILABLE"),
        ("RISK_ON", "RISK_OFF", "NEUTRAL"),
        ("RISK_ON", "RISK_ON", "RISK_ON"),
        ("RISK_ON", "NEUTRAL", "RISK_ON"),
        ("NEUTRAL", "RISK_OFF", "RISK_OFF"),
        ("NEUTRAL", "RISK_ON", "RISK_ON"),
        ("NEUTRAL", "NEUTRAL", "NEUTRAL"),
        ("RISK_OFF", "RISK_ON", "RISK_OFF"),
        ("RISK_OFF", "RISK_OFF", "RISK_OFF"),
        ("RISK_OFF", "NEUTRAL", "RISK_OFF"),
        ("NEUTRAL", None, "NEUTRAL"),
        ("RISK_ON", None, "RISK_ON"),
    ],
)
def test_blend_news_fred_mood_matrix(news_mood, fred_signal, expected):
    assert news_agent.blend_news_fred_mood(news_mood, fred_signal) == expected


@pytest.mark.parametrize(
    "value,usable",
    [
        (21, True),
        (0, True),
        (-20.0, True),
        (True, False),
        (False, False),
        (None, False),
        ("12", False),
        (float("nan"), False),
        (float("inf"), False),
        (float("-inf"), False),
    ],
)
def test_finite_sentiment_score_rejects_bool_nan_and_non_numeric(value, usable):
    assert news_agent._finite_sentiment_score(value) is usable
    if usable:
        assert math.isfinite(float(value))


def _scored_scan(monkeypatch, score, fred="NEUTRAL", *, explode_fred=False):
    monkeypatch.setattr(news_agent, "_fresh_sentiment", lambda *_a, **_k: None)
    monkeypatch.setattr(news_agent, "_fetch_all_news", lambda *_a, **_k: [{"title": "t"}])
    monkeypatch.setattr(
        news_agent,
        "analyze_news_sentiment",
        lambda *_a, **_k: {
            "availability": "OK",
            "overall_sentiment": "POSITIVE",
            "sentiment_score": score,
            "impact_level": "LOW",
        },
    )
    monkeypatch.setattr(news_agent, "_save_sentiment", lambda *_a, **_k: None)
    monkeypatch.setattr(news_agent, "_compute_rolling_score", lambda *_a, **_k: score)

    import regime

    if explode_fred:

        def _boom():
            raise RuntimeError("fred down")

        monkeypatch.setattr(regime, "get_macro_context", _boom)
    else:
        monkeypatch.setattr(regime, "get_macro_context", lambda: {"macro_signal": fred})
    return news_agent.scan_news(["AAPL"])


def test_scan_news_score_band_edges_are_neutral(monkeypatch):
    """avg == 20 and avg == -20 are NEUTRAL — only strict inequalities flip."""
    high = _scored_scan(monkeypatch, 20, "NEUTRAL")
    assert high["news_mood"] == "NEUTRAL"
    assert high["market_mood"] == "NEUTRAL"
    assert high["avg_score"] == 20.0

    low = _scored_scan(monkeypatch, -20, "NEUTRAL")
    assert low["news_mood"] == "NEUTRAL"
    assert low["market_mood"] == "NEUTRAL"
    assert low["avg_score"] == -20.0


def test_scan_news_score_just_outside_band_flips(monkeypatch):
    on = _scored_scan(monkeypatch, 20.1, "NEUTRAL")
    assert on["news_mood"] == "RISK_ON"
    assert on["market_mood"] == "RISK_ON"

    off = _scored_scan(monkeypatch, -20.1, "NEUTRAL")
    assert off["news_mood"] == "RISK_OFF"
    assert off["market_mood"] == "RISK_OFF"


def test_scan_news_conflict_risk_on_news_fred_off_is_neutral(monkeypatch):
    out = _scored_scan(monkeypatch, 40, "RISK_OFF")
    assert out["news_mood"] == "RISK_ON"
    assert out["fred_signal"] == "RISK_OFF"
    assert out["market_mood"] == "NEUTRAL"


def test_scan_news_neutral_news_follows_fred(monkeypatch):
    off = _scored_scan(monkeypatch, 0, "RISK_OFF")
    assert off["news_mood"] == "NEUTRAL"
    assert off["market_mood"] == "RISK_OFF"

    on = _scored_scan(monkeypatch, 0, "RISK_ON")
    assert on["news_mood"] == "NEUTRAL"
    assert on["market_mood"] == "RISK_ON"


def test_scan_news_risk_off_news_not_upgraded_by_fred_on(monkeypatch):
    out = _scored_scan(monkeypatch, -40, "RISK_ON")
    assert out["news_mood"] == "RISK_OFF"
    assert out["fred_signal"] == "RISK_ON"
    assert out["market_mood"] == "RISK_OFF"


def test_scan_news_fred_exception_leaves_news_mood(monkeypatch):
    out = _scored_scan(monkeypatch, 40, explode_fred=True)
    assert out["fred_signal"] == "NEUTRAL"
    assert out["news_mood"] == "RISK_ON"
    assert out["market_mood"] == "RISK_ON"


def test_scan_news_missing_macro_signal_defaults_neutral(monkeypatch):
    monkeypatch.setattr(news_agent, "_fresh_sentiment", lambda *_a, **_k: None)
    monkeypatch.setattr(news_agent, "_fetch_all_news", lambda *_a, **_k: [{"title": "t"}])
    monkeypatch.setattr(
        news_agent,
        "analyze_news_sentiment",
        lambda *_a, **_k: {
            "availability": "OK",
            "sentiment_score": 0,
            "overall_sentiment": "NEUTRAL",
            "impact_level": "LOW",
        },
    )
    monkeypatch.setattr(news_agent, "_save_sentiment", lambda *_a, **_k: None)
    monkeypatch.setattr(news_agent, "_compute_rolling_score", lambda *_a, **_k: 0)
    import regime

    monkeypatch.setattr(regime, "get_macro_context", lambda: {})
    out = news_agent.scan_news(["AAPL"])
    assert out["fred_signal"] == "NEUTRAL"
    assert out["news_mood"] == "NEUTRAL"
    assert out["market_mood"] == "NEUTRAL"


def test_scan_news_bool_score_is_not_a_mood(monkeypatch):
    """True would coerce to 1 and invent NEUTRAL if bools were accepted."""
    monkeypatch.setattr(news_agent, "_fresh_sentiment", lambda *_a, **_k: None)
    monkeypatch.setattr(news_agent, "_fetch_all_news", lambda *_a, **_k: [{"title": "t"}])
    monkeypatch.setattr(
        news_agent,
        "analyze_news_sentiment",
        lambda *_a, **_k: {
            "availability": "OK",
            "sentiment_score": True,
            "overall_sentiment": "POSITIVE",
            "impact_level": "LOW",
        },
    )
    monkeypatch.setattr(news_agent, "_save_sentiment", lambda *_a, **_k: None)
    import regime

    monkeypatch.setattr(regime, "get_macro_context", lambda: {"macro_signal": "RISK_ON"})
    out = news_agent.scan_news(["AAPL"])
    assert out["news_mood"] == "UNAVAILABLE"
    assert out["market_mood"] == "UNAVAILABLE"
    assert out["avg_score"] is None
    assert out["fred_signal"] == "RISK_ON"
