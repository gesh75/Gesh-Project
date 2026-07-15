"""
Offline unit tests for long_term_macro margin-debt integrity (STOP-SHIP class).

Guards three data-integrity rules for the FINRA margin-debt feed:
  * Month labels ("May-26") MUST be parsed with an explicit %b-%y format —
    an inferring parser produced year-0001 dates that sorted Dec-25 after
    May-26, so month-over-month math ran on the wrong rows.
  * FINRA publishes debit balances in $ MILLIONS — values stay in official
    units (the old code divided by 1,000 a second time).
  * Retrieval failure returns {"status": "unavailable"} — a synthetic series
    is NEVER generated (fabricated market data violates the project's #1 rule).
Payloads must carry source / as_of / quality; a cache serve is labeled
quality="cached" and keeps the cached as_of.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

import long_term_macro as ltm

pytestmark = pytest.mark.unit

FIXTURE = Path(__file__).parent / "fixtures" / "finra_margin_statistics.html"


@pytest.fixture
def margin_cache(tmp_path, monkeypatch):
    """Point the module cache at a throwaway path so tests never read/write
    the repo-root .margin_debt_cache.json."""
    cache = tmp_path / ".margin_debt_cache.json"
    monkeypatch.setattr(ltm, "MARGIN_CACHE", cache)
    return cache


@pytest.fixture
def live_finra(margin_cache, monkeypatch):
    """FINRA 'up': the network seam serves the checked-in fixture tables."""
    tables = pd.read_html(FIXTURE, header=0)
    monkeypatch.setattr(ltm, "_fetch_finra_tables", lambda: tables, raising=False)
    return margin_cache


@pytest.fixture
def finra_down(margin_cache, monkeypatch):
    """FINRA 'down': the network seam raises."""
    def _boom():
        raise ConnectionError("FINRA unreachable")
    monkeypatch.setattr(ltm, "_fetch_finra_tables", _boom, raising=False)
    return margin_cache


def _write_cache(path: Path, *, as_of: str, age_hours: float, series: list[dict]) -> None:
    # Deliberately writes the LEGACY *naive* stamp (what caches on disk hold from before
    # the utcnow()→now(timezone.utc) migration) so these tests keep exercising
    # _parse_ts's naive tolerance. Built from an aware now to avoid the deprecated call.
    naive = (datetime.now(timezone.utc) - timedelta(hours=age_hours)).replace(tzinfo=None)
    path.write_text(json.dumps({
        "schema": 2,
        "fetched_at": naive.isoformat(),
        "source": "FINRA margin statistics (finra.org)",
        "as_of": as_of,
        "series": series,
    }))


# ── (a) explicit %b-%y date parsing ──────────────────────────────────────────

def test_dates_parsed_with_explicit_month_year_format(live_finra):
    result = ltm.fetch_margin_debt()
    assert result["status"] == "ok"
    # May-26 means 2026-05 — and it is the LATEST row, after Dec-25 (2025-12).
    assert result["as_of"] == "2026-05"
    dates = [row["date"] for row in result["series"]]
    assert dates == sorted(dates)
    assert dates[0] == "2025-05"
    assert dates[-1] == "2026-05"
    assert "2025-12" in dates


# ── (b) official units: $ millions, no /1000 ─────────────────────────────────

def test_values_kept_in_official_finra_millions(live_finra):
    result = ltm.fetch_margin_debt()
    by_date = {row["date"]: row["margin_debt_millions"] for row in result["series"]}
    # FINRA publishes 1,415,557 ($M) for May-26 — NOT 1415.557.
    assert by_date["2026-05"] == pytest.approx(1_415_557.0)
    assert by_date["2025-05"] == pytest.approx(920_960.0)


# ── (c) retrieval failure → unavailable, NEVER synthetic ─────────────────────

def test_retrieval_failure_returns_unavailable(finra_down):
    result = ltm.fetch_margin_debt()
    assert result["status"] == "unavailable"
    # No invented data points, under any key.
    assert not result.get("series")
    assert not result.get("data")


def test_retrieval_failure_never_scores_synthetic_series(finra_down):
    score = ltm.margin_debt_score(ltm.fetch_margin_debt())
    assert score["score"] == 0
    assert score["level"] is None
    assert score["status"] == "unavailable"


# ── (d) source / as_of / quality fields ──────────────────────────────────────

def test_payload_includes_source_as_of_quality(live_finra):
    result = ltm.fetch_margin_debt()
    assert "finra" in result["source"].lower()
    assert result["as_of"] == "2026-05"
    assert result["quality"] == "live"


def test_score_carries_provenance_fields(live_finra):
    score = ltm.margin_debt_score(ltm.fetch_margin_debt())
    # May-26 1,415,557 vs Apr-26 1,304,281 → +8.5% MoM → leverage caution.
    assert score["score"] == -1
    assert score["level"] == pytest.approx(1_415_557.0)
    assert score["status"] == "ok"
    assert "finra" in score["source"].lower()
    assert score["as_of"] == "2026-05"
    assert score["quality"] == "live"


def test_sentiment_overlay_exposes_margin_provenance(live_finra, monkeypatch):
    monkeypatch.setattr(ltm, "fetch_vix", lambda *a, **k: None)
    monkeypatch.setattr(ltm, "fetch_fear_greed", lambda *a, **k: None)
    sent = ltm.get_long_term_macro_sentiment()
    md = sent["margin_debt"]
    assert md["as_of"] == "2026-05"
    assert md["quality"] == "live"
    assert "finra" in md["source"].lower()


# ── cache semantics ──────────────────────────────────────────────────────────

def test_parse_ts_accepts_legacy_naive_and_aware():
    """Codex #7 (utcnow → now(timezone.utc)): caches written before the migration hold
    NAIVE stamps. Both forms must parse to AWARE UTC — otherwise aware-minus-naive
    raises TypeError and every legacy cache silently looks invalid forever."""
    naive = ltm._parse_ts("2026-07-15T00:00:00")
    aware = ltm._parse_ts("2026-07-15T00:00:00+00:00")
    assert naive.tzinfo is not None and aware.tzinfo is not None
    assert naive == aware                       # a legacy naive stamp WAS UTC
    # the age subtraction against an aware now is exactly what used to blow up
    assert (ltm._utc_now() - naive).total_seconds() > 0


def test_successful_fetch_writes_versioned_cache(live_finra):
    ltm.fetch_margin_debt()
    cached = json.loads(live_finra.read_text())
    assert cached["schema"] == 2
    assert cached["as_of"] == "2026-05"
    assert cached["series"][-1]["margin_debt_millions"] == pytest.approx(1_415_557.0)


def test_fresh_cache_served_without_hitting_finra(margin_cache, monkeypatch):
    def _fail():
        raise AssertionError("network seam must not be called on a fresh cache")
    monkeypatch.setattr(ltm, "_fetch_finra_tables", _fail, raising=False)
    series = [
        {"date": "2026-02", "margin_debt_millions": 1_253_192.0},
        {"date": "2026-03", "margin_debt_millions": 1_220_922.0},
        {"date": "2026-04", "margin_debt_millions": 1_304_281.0},
    ]
    _write_cache(margin_cache, as_of="2026-04", age_hours=1, series=series)
    result = ltm.fetch_margin_debt()
    assert result["status"] == "ok"
    assert result["quality"] == "cached"
    assert result["as_of"] == "2026-04"
    assert result["series"] == series


def test_stale_cache_served_as_cached_when_finra_down(finra_down):
    series = [
        {"date": "2026-02", "margin_debt_millions": 1_253_192.0},
        {"date": "2026-03", "margin_debt_millions": 1_220_922.0},
        {"date": "2026-04", "margin_debt_millions": 1_304_281.0},
    ]
    _write_cache(finra_down, as_of="2026-04", age_hours=48, series=series)
    result = ltm.fetch_margin_debt()
    assert result["status"] == "ok"
    assert result["quality"] == "cached"
    assert result["as_of"] == "2026-04"
    assert result["series"] == series


def test_legacy_corrupt_cache_is_rejected_not_served(finra_down):
    # Pre-fix cache format: year-0001 dates + /1000 values, no schema/as_of.
    finra_down.write_text(json.dumps({
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "data": [
            {"date": "0001-05-26 00:00:00", "margin_debt_millions": 1415.557},
            {"date": "0001-04-26 00:00:00", "margin_debt_millions": 1304.281},
            {"date": "0001-03-26 00:00:00", "margin_debt_millions": 1220.922},
        ],
    }))
    result = ltm.fetch_margin_debt()
    assert result["status"] == "unavailable"
    assert not result.get("series")
