"""First-execution tests for security/api_verifier.py.

This module sanitises external API payloads, rate-limits callers, and masks
credentials before they hit logs. It powered GET /api/security/health at 0%
coverage (Codex 2026-07-01). A NaN/Inf quote, an impossible volume, or a
leaked api_key= in an error string would look like a measured market fact.

No network. SQLite writes stay in tmp. In-memory rate/latency maps reset
per test so cases stay independent.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from security import api_verifier as av


@pytest.fixture(autouse=True)
def _isolate_verifier(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(av, "DB_PATH", tmp_path / "security.db")
    monkeypatch.setattr(av, "_FALLBACK_DB", tmp_path / "security_fallback.db")
    av._call_log.clear()
    av._rate_limit_hits.clear()
    av._last_errors.clear()
    av._latencies.clear()
    yield
    av._call_log.clear()
    av._rate_limit_hits.clear()
    av._last_errors.clear()
    av._latencies.clear()


# ── sanitise_financial_data ───────────────────────────────────────────────────


def test_sanitise_replaces_nan_and_inf_with_none():
    cleaned, anomalies = av.sanitise_financial_data(
        {"close": float("nan"), "open": float("inf"), "low": float("-inf")}
    )
    assert cleaned == {"close": None, "open": None, "low": None}
    assert len(anomalies) == 3
    assert all("NaN/Inf" in a for a in anomalies)


def test_sanitise_flags_price_outside_bounds_and_keeps_the_value():
    cleaned, anomalies = av.sanitise_financial_data({"last_price": 0.0001, "ask": 1_000_001.0})
    assert cleaned["last_price"] == 0.0001
    assert cleaned["ask"] == 1_000_001.0
    assert any("Suspicious price 0.0001" in a for a in anomalies)
    assert any("Suspicious price 1000001.0" in a for a in anomalies)


def test_sanitise_inclusive_price_bounds_and_zero_are_not_anomalies():
    cleaned, anomalies = av.sanitise_financial_data(
        {"price": av.PRICE_MIN, "close": av.PRICE_MAX, "open": 0.0}
    )
    assert cleaned == {"price": av.PRICE_MIN, "close": av.PRICE_MAX, "open": 0.0}
    assert anomalies == []


def test_sanitise_flags_impossible_volume_only_on_volume_keys():
    cleaned, anomalies = av.sanitise_financial_data(
        {
            "volume": float(av.VOLUME_MAX + 1),
            "shares": float(av.VOLUME_MAX + 1),
        }
    )
    assert cleaned["volume"] == float(av.VOLUME_MAX + 1)
    assert any("Impossible volume" in a for a in anomalies)
    assert all("shares" not in a for a in anomalies)


def test_sanitise_volume_at_max_is_accepted():
    _cleaned, anomalies = av.sanitise_financial_data({"volume": float(av.VOLUME_MAX)})
    assert anomalies == []


def test_sanitise_walks_nested_dicts_and_lists():
    cleaned, anomalies = av.sanitise_financial_data(
        {"holdings": [{"close": float("nan")}, {"bid": 12.5}]}
    )
    assert cleaned["holdings"][0]["close"] is None
    assert cleaned["holdings"][1]["bid"] == 12.5
    assert anomalies == ["NaN/Inf at .holdings[0].close"]


def test_sanitise_leaves_non_float_scalars_unchanged():
    payload = {"symbol": "NVDA", "qty": 10, "note": None, "ok": True}
    cleaned, anomalies = av.sanitise_financial_data(payload)
    assert cleaned == payload
    assert anomalies == []


def test_sanitise_non_price_finite_float_is_not_range_checked():
    cleaned, anomalies = av.sanitise_financial_data({"beta": 1.5, "rsi": 0.0001})
    assert cleaned == {"beta": 1.5, "rsi": 0.0001}
    assert anomalies == []


# ── verify_response ───────────────────────────────────────────────────────────


def test_verify_response_none_is_not_ok():
    result = av.verify_response("yfinance", None)
    assert result.ok is False
    assert result.sanitised_data is None
    assert any("response is None" in i for i in result.issues)


def test_verify_response_error_key_is_not_ok_and_masks_secrets():
    result = av.verify_response(
        "robinhood",
        {"error": "login failed api_key=rh-test-not-a-real-key"},
    )
    assert result.ok is False
    assert "rh-test-not-a-real-key" not in result.issues[0]
    assert "***" in result.issues[0]


def test_verify_response_missing_required_keys_is_not_ok():
    result = av.verify_response(
        "yfinance",
        {"symbol": "AAPL"},
        required_keys=["symbol", "close"],
    )
    assert result.ok is False
    assert any("missing required keys" in i and "close" in i for i in result.issues)


def test_verify_response_clean_payload_is_ok_and_sanitised():
    result = av.verify_response(
        "yfinance",
        {"symbol": "AAPL", "close": 190.0, "volume": 1_000_000.0},
        required_keys=["symbol", "close"],
    )
    assert result.ok is True
    assert result.issues == []
    assert result.anomalies == []
    assert result.sanitised_data["close"] == 190.0


def test_verify_response_anomaly_does_not_fail_schema():
    result = av.verify_response("yfinance", {"close": float("nan")})
    assert result.ok is True
    assert result.sanitised_data["close"] is None
    assert result.anomalies


def test_verify_result_to_dict_omits_payload():
    result = av.VerifyResult(
        ok=False,
        issues=["x"],
        sanitised_data={"secret": 1},
        anomalies=["y"],
    )
    assert result.to_dict() == {"ok": False, "issues": ["x"], "anomalies": ["y"]}


# ── rate_limit_guard ──────────────────────────────────────────────────────────


def test_rate_limit_guard_allows_under_cap_and_records_the_call():
    assert av.rate_limit_guard("yfinance", max_per_minute=2) is True
    assert av.rate_limit_guard("yfinance", max_per_minute=2) is True
    assert len(av._call_log["yfinance"]) == 2


def test_rate_limit_guard_throttles_at_inclusive_cap():
    assert av.rate_limit_guard("telegram", max_per_minute=1) is True
    assert av.rate_limit_guard("telegram", max_per_minute=1) is False
    assert av._rate_limit_hits["telegram"] == 1
    # Throttled call is not appended — the next window slot stays available
    # only after expiry, not because we over-counted.
    assert len(av._call_log["telegram"]) == 1


def test_rate_limit_guard_purges_entries_older_than_60s():
    av._call_log["fred"].append(0.0)  # monotonic epoch, far older than 60s
    assert av.rate_limit_guard("fred", max_per_minute=1) is True
    assert len(av._call_log["fred"]) == 1
    assert av._call_log["fred"][0] > 0.0


def test_rate_limit_guard_unknown_source_uses_default_cap():
    cap = av.RATE_LIMITS["default"]
    for _ in range(cap):
        assert av.rate_limit_guard("unknown-feed") is True
    assert av.rate_limit_guard("unknown-feed") is False


# ── health + latency ──────────────────────────────────────────────────────────


def test_check_api_health_starts_healthy_with_zero_latency():
    report = av.check_api_health("alpaca")
    assert report.healthy is True
    assert report.avg_latency_ms == 0.0
    assert report.rate_limit_hits == 0
    assert report.last_error == ""
    assert report.calls_last_60s == 0


def test_record_error_masks_secrets_and_marks_unhealthy():
    av.record_error("robinhood", "refresh_token=rh-refresh-test-not-real")
    report = av.check_api_health("robinhood")
    assert report.healthy is False
    assert "rh-refresh-test-not-real" not in report.last_error
    assert "***" in report.last_error


def test_rate_limit_hit_marks_source_unhealthy():
    av.rate_limit_guard("reddit", max_per_minute=1)
    av.rate_limit_guard("reddit", max_per_minute=1)
    report = av.check_api_health("reddit")
    assert report.healthy is False
    assert report.rate_limit_hits == 1
    assert report.calls_last_60s == 1


def test_record_latency_averages_recent_window_and_trims():
    av.record_latency("yfinance", 10.0)
    av.record_latency("yfinance", 30.0)
    report = av.check_api_health("yfinance")
    assert report.avg_latency_ms == 20.0
    av._latencies["yfinance"] = list(range(120))
    av.record_latency("yfinance", 999.0)
    assert len(av._latencies["yfinance"]) == 100
    assert av._latencies["yfinance"][-1] == 999.0


def test_all_api_health_skips_default_and_includes_known_sources():
    reports = av.all_api_health()
    sources = [r["source"] for r in reports]
    assert "default" not in sources
    assert "yfinance" in sources
    assert "robinhood" in sources
    assert all(set(r) >= {"source", "healthy", "avg_latency_ms"} for r in reports)


# ── secret masking ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,secret",
    [
        ("failed api_key=sk-test-not-a-real-key", "sk-test-not-a-real-key"),
        ('{"token": "tok-test-not-real"}', "tok-test-not-real"),
        ("Authorization Bearer bear-test-not-real", "bear-test-not-real"),
        ("password=pw-test-not-real", "pw-test-not-real"),
    ],
)
def test_mask_secrets_redacts_credential_values(raw: str, secret: str):
    masked = av._mask_secrets(raw)
    assert secret not in masked
    assert "***" in masked


def test_mask_secrets_leaves_ordinary_errors_intact():
    text = "quote missing for NVDA"
    assert av._mask_secrets(text) == text


# ── logging fail-soft + DB fallback ───────────────────────────────────────────


def test_log_api_event_persists_and_truncates_detail():
    av.log_api_event("yfinance", "ANOMALY", "x" * 800)
    with sqlite3.connect(str(av.DB_PATH)) as conn:
        row = conn.execute("SELECT source, event_type, detail FROM security_log").fetchone()
    assert row == ("yfinance", "ANOMALY", "x" * 500)


def test_log_api_event_never_raises_when_db_unwritable(tmp_path: Path, monkeypatch):
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    monkeypatch.setattr(av, "DB_PATH", blocked)
    monkeypatch.setattr(av, "_FALLBACK_DB", blocked)
    av.log_api_event("yfinance", "ERROR", "should not raise")


def test_db_falls_back_when_primary_path_is_a_directory(tmp_path: Path, monkeypatch):
    primary = tmp_path / "not-a-db"
    primary.mkdir()
    fallback = tmp_path / "ok.db"
    monkeypatch.setattr(av, "DB_PATH", primary)
    monkeypatch.setattr(av, "_FALLBACK_DB", fallback)
    assert av._db() == fallback
