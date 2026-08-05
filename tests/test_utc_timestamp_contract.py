from datetime import datetime, timedelta

import fft_cycles
import gemini_agent
import market_data


def test_as_of_aware_utc_timestamp_is_parseable(monkeypatch):
    monkeypatch.setattr(gemini_agent, "_GEMINI_KEY", "")

    payloads = [
        fft_cycles.batch_fft_cycles([]),
        market_data.get_all_market_data([]),
        gemini_agent.get_gemini_status(),
    ]

    for payload in payloads:
        timestamp = payload["as_of"]
        parsed = datetime.fromisoformat(timestamp)

        assert parsed.utcoffset() == timedelta(0)
        assert not timestamp.endswith("+00:00Z")
