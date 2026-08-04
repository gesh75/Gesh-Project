import json
from datetime import datetime, timezone

import pytest

pytest.importorskip("anthropic")
import ai_analyzer


def test_daily_budget_counts_same_day_usage_beyond_one_mib(tmp_path, monkeypatch):
    usage_log = tmp_path / "llm_usage.jsonl"
    today = datetime.now(timezone.utc).date().isoformat()
    row = json.dumps(
        {
            "ts": f"{today}T00:00:00+00:00",
            "est_cost_usd": 0.01,
            "padding": "x" * 180,
        }
    )
    usage_log.write_text((row + "\n") * 10_000, encoding="utf-8")

    monkeypatch.setattr(ai_analyzer, "_USAGE_LOG", usage_log)
    monkeypatch.setenv("LLM_DAILY_BUDGET_USD", "80")

    assert usage_log.stat().st_size > 1_048_576
    assert ai_analyzer._spent_today_usd() == pytest.approx(100.0)
    with pytest.raises(RuntimeError, match="daily budget exhausted"):
        ai_analyzer._budget_guard("test_call")
