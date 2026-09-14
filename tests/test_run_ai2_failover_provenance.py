"""Write-side failover provenance for the daily chain (run_ai2.py + llm_failover).

PR #236 already tests the adapters. PR #243 covers the report READ-side banners.
These tests lock the WRITE side: engine_results + analysis_runs.model must
disclose a Grok/Gemini-authored run, and a clean Anthropic run must stay quiet.

run_ai2.py executes its pipeline at import, so the script is pinned by source
and the decision function is tested directly — never by importing the script.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import llm_failover

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _reset_failover_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_failover, "FAILOVER_COUNT", 0, raising=False)
    monkeypatch.setattr(llm_failover, "LAST_FAILOVER", None, raising=False)


def test_current_provenance_clean_run_stays_anthropic():
    provider, count = llm_failover.current_provenance()
    assert provider == "anthropic"
    assert count == 0


def test_current_provenance_reads_last_failover_provider(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(llm_failover, "FAILOVER_COUNT", 2, raising=False)
    monkeypatch.setattr(
        llm_failover, "LAST_FAILOVER", {"operation": "deep", "provider": "grok"}, raising=False
    )
    provider, count = llm_failover.current_provenance()
    assert provider == "grok"
    assert count == 2


def test_current_provenance_missing_provider_key_is_unknown(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(llm_failover, "FAILOVER_COUNT", 1, raising=False)
    monkeypatch.setattr(llm_failover, "LAST_FAILOVER", {"operation": "deep"}, raising=False)
    provider, count = llm_failover.current_provenance()
    assert provider == "unknown"
    assert count == 1


def test_current_provenance_empty_provider_is_unknown(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(llm_failover, "FAILOVER_COUNT", 1, raising=False)
    monkeypatch.setattr(llm_failover, "LAST_FAILOVER", {"provider": ""}, raising=False)
    provider, _count = llm_failover.current_provenance()
    assert provider == "unknown"


def test_current_provenance_count_without_last_keeps_anthropic(
    monkeypatch: pytest.MonkeyPatch,
):
    """Preserve the original inline contract: count can be >0 while LAST is None."""
    monkeypatch.setattr(llm_failover, "FAILOVER_COUNT", 1, raising=False)
    monkeypatch.setattr(llm_failover, "LAST_FAILOVER", None, raising=False)
    provider, count = llm_failover.current_provenance()
    assert provider == "anthropic"
    assert count == 1


def test_current_provenance_garbage_count_fails_safe(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(llm_failover, "FAILOVER_COUNT", "not-a-number", raising=False)
    monkeypatch.setattr(llm_failover, "LAST_FAILOVER", {"provider": "gemini"}, raising=False)
    assert llm_failover.current_provenance() == ("anthropic", 0)


def test_analysis_model_label_rewrites_failover(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(llm_failover, "FAILOVER_COUNT", 1, raising=False)
    monkeypatch.setattr(llm_failover, "LAST_FAILOVER", {"provider": "gemini"}, raising=False)
    assert llm_failover.analysis_model_label("claude-sonnet-4-6") == (
        "gemini (FAILOVER, anthropic down)"
    )


def test_analysis_model_label_keeps_claude_on_clean_run():
    assert llm_failover.analysis_model_label("claude-sonnet-4-6") == "claude-sonnet-4-6"


def test_run_ai2_stamps_engine_results_and_rewrites_db_model():
    """The chain script must actually call the provenance helpers and persist them.

    Importing run_ai2.py is unsafe (it runs the pipeline at import). Pin the
    write-side contract in source so a silent revert cannot hide a failover.
    """
    src = (REPO / "run_ai2.py").read_text()
    assert "current_provenance()" in src
    assert "analysis_model_label(" in src
    assert "eng['ai_provider']" in src or 'eng["ai_provider"]' in src
    assert "eng['ai_failover_count']" in src or 'eng["ai_failover_count"]' in src
    assert "WARNING: ANTHROPIC UNAVAILABLE" in src
    assert "(FAILOVER, anthropic down)" in src
    # Fail-safe: a broken import must not invent a failover.
    # Accept either quote style so a formatter cannot hide a real revert.
    assert (
        "_ai_provider, _failovers = 'anthropic', 0" in src
        or '_ai_provider, _failovers = "anthropic", 0' in src
    )
