"""Gemini transport fallback + JSON recovery.

`_generate` is the only place the 429 → `gemini-2.0-flash-lite` hop lives.
News / second-opinion / commentary wrappers already have honesty tests, but
they stub `_generate` entirely — a regression that listed the fallback model
and never retried would still look green.

`_extract_json` is what those wrappers parse; a fence/prefix miss fabricates
UNAVAILABLE on a good Flash answer.
"""

from __future__ import annotations

import json
import sys
import types
from types import SimpleNamespace

import pytest

import gemini_agent as ga


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(ga.time, "sleep", lambda *_a, **_k: None)


@pytest.fixture
def _fake_genai(monkeypatch):
    """`_generate` imports `google.genai.types` inside the function."""
    google = types.ModuleType("google")
    genai = types.ModuleType("google.genai")

    class GenerateContentConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    genai_types = SimpleNamespace(GenerateContentConfig=GenerateContentConfig)
    genai.types = genai_types
    google.genai = genai
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    return GenerateContentConfig


class _FakeModels:
    def __init__(self, effects):
        self.effects = list(effects)
        self.calls: list[dict] = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        effect = self.effects.pop(0)
        if isinstance(effect, BaseException):
            raise effect
        return SimpleNamespace(text=effect)


def _client(effects):
    models = _FakeModels(effects)
    return SimpleNamespace(models=models), models


# ── _extract_json ────────────────────────────────────────────────────────────


def test_extract_json_strips_markdown_fence():
    raw = '```json\n{"score": 12, "mood": "POSITIVE"}\n```'
    assert ga._extract_json(raw) == {"score": 12, "mood": "POSITIVE"}


def test_extract_json_recovers_object_after_prose_prefix():
    raw = 'Sure — here you go:\n{"ok": true, "n": 1}\nThanks.'
    assert ga._extract_json(raw) == {"ok": True, "n": 1}


def test_extract_json_raises_on_prose_only():
    with pytest.raises(ValueError, match="No JSON found"):
        ga._extract_json("no object in this reply")


def test_extract_json_raises_on_unbalanced_or_invalid_object():
    with pytest.raises(ValueError, match="No JSON found"):
        ga._extract_json("{not-json")
    with pytest.raises(json.JSONDecodeError):
        ga._extract_json("{not: json}")


# ── _generate quota hop ──────────────────────────────────────────────────────


def test_generate_retries_429_on_flash_lite(_fake_genai, monkeypatch):
    client, models = _client(
        [RuntimeError("429 RESOURCE_EXHAUSTED: quota"), "fallback-ok"]
    )
    monkeypatch.setattr(ga, "_get_client", lambda: client)

    text = ga._generate("prompt", model=ga.GEMINI_FLASH_MODEL)
    assert text == "fallback-ok"
    assert [c["model"] for c in models.calls] == [
        ga.GEMINI_FLASH_MODEL,
        ga.GEMINI_FALLBACK,
    ]
    assert ga.GEMINI_FALLBACK == "gemini-2.0-flash-lite"


def test_generate_retries_resource_exhausted_without_429_token(_fake_genai, monkeypatch):
    client, models = _client(
        [RuntimeError("RESOURCE_EXHAUSTED"), "lite-ok"]
    )
    monkeypatch.setattr(ga, "_get_client", lambda: client)
    assert ga._generate("p") == "lite-ok"
    assert len(models.calls) == 2


def test_generate_non_quota_error_does_not_retry(_fake_genai, monkeypatch):
    client, models = _client([ValueError("PERMISSION_DENIED: bad key")])
    monkeypatch.setattr(ga, "_get_client", lambda: client)
    with pytest.raises(ValueError, match="PERMISSION_DENIED"):
        ga._generate("p")
    assert [c["model"] for c in models.calls] == [ga.GEMINI_FLASH_MODEL]


def test_generate_empty_response_does_not_look_like_quota(_fake_genai, monkeypatch):
    """Empty text is a hard failure — must not burn the lite hop as if it were 429."""
    client, models = _client([""])
    monkeypatch.setattr(ga, "_get_client", lambda: client)
    with pytest.raises(ValueError, match="Empty response"):
        ga._generate("p")
    assert len(models.calls) == 1


def test_generate_all_quota_failures_reraise_last(_fake_genai, monkeypatch):
    last = RuntimeError("429 still exhausted on lite")
    client, models = _client(
        [RuntimeError("429 on flash"), last]
    )
    monkeypatch.setattr(ga, "_get_client", lambda: client)
    with pytest.raises(RuntimeError, match="still exhausted"):
        ga._generate("p")
    assert len(models.calls) == 2


def test_generate_retry_on_quota_false_never_adds_fallback(_fake_genai, monkeypatch):
    client, models = _client([RuntimeError("429 no hop")])
    monkeypatch.setattr(ga, "_get_client", lambda: client)
    with pytest.raises(RuntimeError, match="429 no hop"):
        ga._generate("p", retry_on_quota=False)
    assert [c["model"] for c in models.calls] == [ga.GEMINI_FLASH_MODEL]


def test_generate_does_not_duplicate_fallback_when_already_on_lite(_fake_genai, monkeypatch):
    client, models = _client([RuntimeError("429 on lite")])
    monkeypatch.setattr(ga, "_get_client", lambda: client)
    with pytest.raises(RuntimeError, match="429 on lite"):
        ga._generate("p", model=ga.GEMINI_FALLBACK)
    assert [c["model"] for c in models.calls] == [ga.GEMINI_FALLBACK]


def test_second_opinion_failure_is_unavailable_not_neutral(monkeypatch):
    monkeypatch.setattr(ga, "_generate", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    out = ga.gemini_second_opinion("NVDA", "Claude says STRONG BUY", verdict="STRONG BUY")
    assert out["available"] is False
    assert out["symbol"] == "NVDA"
    assert "error" in out
    assert "second_opinion" not in out
    assert out.get("agreement_level") is None


def test_quick_analysis_no_key_is_unavailable(monkeypatch):
    monkeypatch.setattr(ga, "_GEMINI_KEY", "")
    out = ga.gemini_quick_analysis("cash 10%")
    assert out["available"] is False
    assert "GEMINI_API_KEY" in out["error"]
    assert "analysis" not in out
