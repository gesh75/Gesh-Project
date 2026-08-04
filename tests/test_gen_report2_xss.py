import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_generated_report_escapes_engine_and_ai_content(tmp_path):
    payload = "<script>window.pwned=true</script>"
    engine_results = {
        "holdings": [],
        "equity_summary": {"total": 1000, "cash": 1000, "portfolio_equity": 0},
        "ai_result": {
            "portfolio_type": payload,
            "portfolio_verdict": payload,
            "market_context": payload,
            "portfolio_recommendations": [
                {"priority": "High", "action": payload, "rationale": payload}
            ],
            "portfolio_risks": [{"severity": "High", "text": payload}],
        },
        "monte_carlo": {
            "buckets": [{"label": "</script><script>window.pwned=true</script>",
                         "pct": 1, "center": 1000}]
        },
        "yield_data": {
            "tiers": {"tier1": [{"name": payload, "apy": 4, "risk": payload}]}
        },
    }
    source = tmp_path / "engine_results.json"
    output = tmp_path / "report.html"
    source.write_text(json.dumps(engine_results))

    env = os.environ.copy()
    env.update(GESH_ENGINE_RESULTS=str(source), GESH_REPORT_OUTPUT=str(output))
    subprocess.run(
        [sys.executable, "gen_report2.py"], cwd=ROOT, env=env, check=True,
        capture_output=True, text=True,
    )

    report = output.read_text()
    assert payload not in report
    assert "&lt;script&gt;window.pwned=true&lt;/script&gt;" in report
    assert r'\u003c/script\u003e\u003cscript\u003ewindow.pwned=true' in report
