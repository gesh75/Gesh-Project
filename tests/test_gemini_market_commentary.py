"""Gemini market-commentary fail-soft.

`gemini_market_commentary` is the daily-briefing wrapper. An outage or a
parse miss must not invent RISK_ON / HOLD / themes — the same honesty
contract as second-opinion and quick-analysis (those wrappers already
have tests; this one did not).
"""

from __future__ import annotations

import pytest

import gemini_agent as ga


def test_market_commentary_failure_is_unavailable_not_neutral(monkeypatch):
    monkeypatch.setattr(
        ga, "_generate", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("429 quota"))
    )
    out = ga.gemini_market_commentary("TRENDING", "curve NORMAL", "cash 8%")
    assert out["available"] is False
    assert "error" in out
    assert "market_stance" not in out
    assert "action_bias" not in out
    assert "commentary" not in out
    assert "top_themes" not in out


def test_market_commentary_parse_error_is_unavailable(monkeypatch):
    monkeypatch.setattr(ga, "_generate", lambda *a, **k: "not-json {")
    monkeypatch.setattr(
        ga, "_extract_json", lambda _text: (_ for _ in ()).throw(ValueError("no object"))
    )
    out = ga.gemini_market_commentary(regime_summary="RANGING")
    assert out["available"] is False
    assert "error" in out
    assert out.get("market_stance") is None


def test_market_commentary_success_returns_parsed_fields(monkeypatch):
    monkeypatch.setattr(ga, "_generate", lambda *a, **k: '{"ok": true}')
    monkeypatch.setattr(
        ga,
        "_extract_json",
        lambda _text: {
            "commentary": "Curve is steep.",
            "market_stance": "RISK_ON",
            "top_themes": ["breadth"],
            "action_bias": "BUY_DIPS",
        },
    )
    out = ga.gemini_market_commentary("TRENDING", "STEEP", "HOOD 12%")
    assert out["available"] is True
    assert out["model"] == ga.GEMINI_FLASH_MODEL
    assert out["commentary"] == "Curve is steep."
    assert out["market_stance"] == "RISK_ON"
    assert out["top_themes"] == ["breadth"]
    assert out["action_bias"] == "BUY_DIPS"
    assert out["as_of"]


def test_market_commentary_missing_keys_do_not_invent_risk_on(monkeypatch):
    monkeypatch.setattr(ga, "_generate", lambda *a, **k: "{}")
    monkeypatch.setattr(ga, "_extract_json", lambda _text: {})
    out = ga.gemini_market_commentary("TRENDING")
    assert out["available"] is True
    # Omitted keys stay the documented blanks — never RISK_ON / BUY_DIPS.
    assert out["commentary"] == ""
    assert out["market_stance"] == "NEUTRAL"
    assert out["top_themes"] == []
    assert out["action_bias"] == "HOLD"


def test_market_commentary_empty_context_still_calls_generate(monkeypatch):
    calls = []

    def _generate(prompt, **kwargs):
        calls.append(prompt)
        return "{}"

    monkeypatch.setattr(ga, "_generate", _generate)
    monkeypatch.setattr(ga, "_extract_json", lambda _text: {"commentary": "general"})
    out = ga.gemini_market_commentary()
    assert out["available"] is True
    assert calls and "No specific context provided" in calls[0]
