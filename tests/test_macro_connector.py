"""Guard tests for consensus Node 11 (macro_connector).

The consensus engine stubs this node in every suite — the scoring, cache, and
abstain rules were never executed. A threshold drift or a "missing data →
NEUTRAL vote" regression silently moves every daily consensus result.

No network: fetchers are stubbed; the cache file is pointed at tmp_path.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

import macro_connector as mc


@pytest.fixture(autouse=True)
def _isolate_macro_cache(tmp_path, monkeypatch):
    cache = tmp_path / "macro_cache.json"
    monkeypatch.setattr(mc, "CACHE_FILE", cache)
    monkeypatch.setattr(mc, "FRED_API_KEY", None)
    return cache


def _stub_series(monkeypatch, *, fed=None, curve=None, credit=None, vix=None):
    monkeypatch.setattr(mc, "_fetch_fed_funds", lambda: fed)
    monkeypatch.setattr(mc, "_fetch_yield_curve", lambda: curve)
    monkeypatch.setattr(mc, "_fetch_credit_spread", lambda: credit)
    monkeypatch.setattr(mc, "_fetch_vix", lambda: vix)


def test_parse_ts_treats_legacy_naive_stamp_as_utc():
    """Aware-minus-naive raises; a pre-migration cache must stay usable."""
    naive = mc._parse_ts("2026-08-21T10:00:00")
    aware = mc._parse_ts("2026-08-21T10:00:00+00:00")
    assert naive.tzinfo is not None and aware.tzinfo is not None
    assert naive == aware
    assert (mc._utc_now() - naive).total_seconds() > 0


def test_cache_valid_false_when_missing_corrupt_or_expired(_isolate_macro_cache, monkeypatch):
    cache = _isolate_macro_cache
    assert mc._cache_valid() is False

    cache.write_text("{not-json")
    assert mc._cache_valid() is False
    assert mc._load_cache() is None

    stale = (datetime.now(timezone.utc) - timedelta(hours=mc.CACHE_TTL_H + 1)).isoformat()
    cache.write_text(json.dumps({"fetched_at": stale}))
    assert mc._cache_valid() is False


def test_fresh_naive_cache_is_still_valid(_isolate_macro_cache):
    cache = _isolate_macro_cache
    cache.write_text(json.dumps({
        "fetched_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        "macro_score": 0.4,
    }))
    assert mc._cache_valid() is True


def test_fresh_cache_is_served_without_refetch(_isolate_macro_cache, monkeypatch):
    cache = _isolate_macro_cache
    payload = {
        "fed_funds": 4.3,
        "yield_curve": 0.2,
        "credit_spread": 3.8,
        "vix": 16.0,
        "macro_score": 0.05,
        "macro_regime": "NEUTRAL",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    cache.write_text(json.dumps(payload))

    def _fail():
        raise AssertionError("fresh cache must not refetch")

    monkeypatch.setattr(mc, "_fetch_fed_funds", _fail)
    monkeypatch.setattr(mc, "_fetch_yield_curve", _fail)
    monkeypatch.setattr(mc, "_fetch_credit_spread", _fail)
    monkeypatch.setattr(mc, "_fetch_vix", _fail)

    got = mc.get_macro_regime()
    assert got["macro_score"] == pytest.approx(0.05)
    assert got["macro_regime"] == "NEUTRAL"


@pytest.mark.parametrize(
    "fed,curve,credit,vix,regime,score",
    [
        (1.0, 0.8, 3.0, 12.0, "RISK-ON", 0.325),   # 0.4+0.3+0.3+0.3
        (5.25, -0.4, 6.5, 35.0, "RISK-OFF", -0.45),  # -0.4-0.5-0.5-0.4
        (2.5, 0.2, 4.0, 16.0, "NEUTRAL", 0.075),  # 0.1+0.1+0.0+0.1
        (2.5, 0.8, None, None, "RISK-ON", 0.2),   # 0.1+0.3 — inclusive 0.2
        (None, -0.3, None, 16.0, "RISK-OFF", -0.2),  # -0.5+0.1 — inclusive -0.2
    ],
)
def test_get_macro_regime_score_and_regime_thresholds(
    monkeypatch, fed, curve, credit, vix, regime, score
):
    _stub_series(monkeypatch, fed=fed, curve=curve, credit=credit, vix=vix)
    got = mc.get_macro_regime(force_refresh=True)
    assert got["macro_regime"] == regime
    assert got["macro_score"] == pytest.approx(score)
    assert "unavailable" not in got["summary"].lower()


def test_all_series_missing_is_neutral_score_not_invented(monkeypatch):
    _stub_series(monkeypatch)
    got = mc.get_macro_regime(force_refresh=True)
    assert got["macro_score"] == 0.0
    assert got["macro_regime"] == "NEUTRAL"
    assert got["fed_funds"] is None
    assert "unavailable" in got["summary"].lower()


def test_node_macro_abstains_when_every_series_is_missing(monkeypatch):
    """Missing data must not count as a NEUTRAL vote — that would move consensus."""
    monkeypatch.setattr(
        mc,
        "get_macro_regime",
        lambda: {
            "macro_score": 0.0,
            "macro_regime": "NEUTRAL",
            "vix": None,
            "yield_curve": None,
            "credit_spread": None,
            "fed_funds": None,
        },
    )
    vote, detail = mc.node_macro()
    assert vote is mc.ABSTAIN
    assert "unavailable" in detail.lower()


def test_node_macro_maps_score_to_vote(monkeypatch):
    def _regime(score, regime):
        return {
            "macro_score": score,
            "macro_regime": regime,
            "vix": 16.0,
            "yield_curve": 0.2,
            "credit_spread": 3.8,
            "fed_funds": 4.3,
        }

    monkeypatch.setattr(mc, "get_macro_regime", lambda: _regime(0.2, "RISK-ON"))
    assert mc.node_macro()[0] == mc.VOTE_BULLISH

    monkeypatch.setattr(mc, "get_macro_regime", lambda: _regime(-0.2, "RISK-OFF"))
    assert mc.node_macro()[0] == mc.VOTE_BEARISH

    monkeypatch.setattr(mc, "get_macro_regime", lambda: _regime(0.0, "NEUTRAL"))
    assert mc.node_macro()[0] == mc.VOTE_NEUTRAL


def test_node_macro_exception_abstains_instead_of_raising(monkeypatch):
    def _boom():
        raise RuntimeError("FRED timeout")

    monkeypatch.setattr(mc, "get_macro_regime", _boom)
    vote, detail = mc.node_macro()
    assert vote is mc.ABSTAIN
    assert "FRED timeout" in detail


def test_fred_json_skips_missing_dot_observations(monkeypatch):
    """FRED encodes a missing print as '.' — that must not become 0.0."""
    monkeypatch.setattr(mc, "FRED_API_KEY", "test-key-not-used")
    monkeypatch.setattr(mc, "_HAS_FREDAPI", False)

    class _Resp:
        status_code = 200

        def json(self):
            return {"observations": [
                {"value": "."},
                {"value": "4.33"},
            ]}

    monkeypatch.setattr(mc.requests, "get", lambda *a, **k: _Resp())
    assert mc._fred_fetch("DFF") == pytest.approx(4.33)
