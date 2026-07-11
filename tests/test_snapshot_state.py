"""Tiered freshness gate for the daily-analysis chain (snapshot_state.classify).

The chain must NOT hard-abort when a live fetch fails but a recent-enough
snapshot exists — it should run degraded on last-known holdings so
engine_results.json / GESH signals keep refreshing. Only a genuinely stale or
missing snapshot is fatal. These tests pin the tier boundaries + fail-safe.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

import snapshot_state as ss

pytestmark = pytest.mark.unit

NOW = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)
SERVICEABLE_MAX = 1440.0  # 24h


def _write_snap(tmp_path, *, age_min: float, source: str = "robinhood_mcp", holdings: int = 10,
                base_now: datetime | None = None):
    # classify()-level tests inject now=NOW and default base_now=NOW (deterministic).
    # The CLI (main) has no now injection and uses wall-clock by design, so its test
    # must anchor the fixture to REAL now — otherwise a NOW-frozen fetched_at reads as
    # hours/days old against wall-clock and the token drifts FRESH→SERVICEABLE→STALE.
    fetched = ((base_now or NOW) - timedelta(minutes=age_min)).isoformat()
    p = tmp_path / "live_portfolio_snapshot.json"
    p.write_text(json.dumps({
        "fetched_at": fetched,
        "source": source,
        "holdings": [{"symbol": f"S{i}"} for i in range(holdings)],
    }))
    return str(p)


def _state(tmp_path, **kw):
    path = _write_snap(tmp_path, **kw)
    state, detail = ss.classify(path, SERVICEABLE_MAX, now=NOW)
    return state, detail


# --- tiers -----------------------------------------------------------------
def test_fresh_when_under_30_min(tmp_path):
    assert _state(tmp_path, age_min=5)[0] == "FRESH"


def test_fresh_at_exact_30_min_boundary(tmp_path):
    assert _state(tmp_path, age_min=30)[0] == "FRESH"


def test_serviceable_between_30_min_and_max(tmp_path):
    assert _state(tmp_path, age_min=6 * 60)[0] == "SERVICEABLE"


def test_serviceable_at_exact_max_boundary(tmp_path):
    assert _state(tmp_path, age_min=SERVICEABLE_MAX)[0] == "SERVICEABLE"


def test_stale_past_serviceable_max(tmp_path):
    assert _state(tmp_path, age_min=SERVICEABLE_MAX + 1)[0] == "STALE"


# --- fail-safe -------------------------------------------------------------
def test_missing_file_is_missing(tmp_path):
    state, detail = ss.classify(str(tmp_path / "nope.json"), SERVICEABLE_MAX, now=NOW)
    assert state == "MISSING"
    assert "unreadable" in detail


def test_malformed_json_is_missing(tmp_path):
    p = tmp_path / "live_portfolio_snapshot.json"
    p.write_text("{not json")
    assert ss.classify(str(p), SERVICEABLE_MAX, now=NOW)[0] == "MISSING"


def test_absent_fetched_at_is_missing(tmp_path):
    p = tmp_path / "live_portfolio_snapshot.json"
    p.write_text(json.dumps({"source": "robinhood_mcp", "holdings": []}))
    assert ss.classify(str(p), SERVICEABLE_MAX, now=NOW)[0] == "MISSING"


# --- source assertion is carried but never changes the tier ----------------
def test_non_mcp_source_warns_but_still_classifies_by_age(tmp_path):
    state, detail = _state(tmp_path, age_min=5, source="robinhood_live")
    assert state == "FRESH"
    assert "WARNING" in detail and "robinhood_mcp" in detail


def test_mcp_source_has_no_warning(tmp_path):
    _, detail = _state(tmp_path, age_min=5, source="robinhood_mcp")
    assert "WARNING" not in detail


# --- CLI contract the shell relies on --------------------------------------
def test_cli_prints_state_token_to_stdout(tmp_path, capsys):
    # main() classifies against wall-clock (no now injection) — anchor to real now.
    path = _write_snap(tmp_path, age_min=5, base_now=datetime.now(timezone.utc))
    rc = ss.main(["snapshot_state.py", str(SERVICEABLE_MAX), path])
    out = capsys.readouterr()
    assert rc == 0
    assert out.out.strip() == "FRESH"          # stdout = the single token the shell captures
    assert "snapshot age" in out.err           # detail goes to stderr (the chain log)
