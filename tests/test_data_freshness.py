"""One freshness model per response.

Audit BUG-07 (2026-07-22): the same page carried `offline: true` alongside
`stale_snapshot: false`, a header reading "CACHED · offline — not live prices
(1h 2xm old)", section badges reading "LIVE MARKET DATA", and per-card
timestamps reading "just now" — one of which was NEWER than the snapshot it was
derived from. Four independent notions of freshness, mutually contradictory.
"""

from __future__ import annotations

import pytest

import server


def _payload(**kw):
    base = {"as_of": "2026-07-23T00:00:00+00:00", "holdings": [],
            "equity_summary": {"portfolio_equity": 0.0, "cash": 0.0}}
    base.update(kw)
    return base


def test_live_payload_is_labelled_live():
    p = server._attach_freshness(_payload(offline=False))
    f = p["data_freshness"]
    assert f["state"] == "live" and f["is_live"] is True
    assert p["offline"] is False and p["stale_snapshot"] is False


def test_recent_snapshot_is_cached_not_live_and_not_stale():
    p = server._attach_freshness(_payload(offline=True, snapshot_age_seconds=600))
    f = p["data_freshness"]
    assert f["state"] == "cached"
    assert f["is_live"] is False
    assert "not live" in f["label"]
    assert p["offline"] is True and p["stale_snapshot"] is False


def test_old_snapshot_is_stale():
    p = server._attach_freshness(_payload(offline=True, snapshot_age_seconds=90_000))
    assert p["data_freshness"]["state"] == "stale"
    assert p["stale_snapshot"] is True


def test_unknown_age_is_not_reported_as_fresh():
    p = server._attach_freshness(_payload(offline=True))
    f = p["data_freshness"]
    assert f["state"] == "unknown" and f["is_live"] is False
    assert f["age_seconds"] is None


@pytest.mark.parametrize("kw", [
    {"offline": False},
    {"offline": True, "snapshot_age_seconds": 10},
    {"offline": True, "snapshot_age_seconds": 999_999},
    {"offline": True},
])
def test_legacy_flags_can_never_contradict_the_state(kw):
    """`offline`/`stale_snapshot` are derived from `state`, so the combination
    the audit found (offline=true + stale_snapshot=false + 'LIVE') cannot be
    produced for any input."""
    f = server._attach_freshness(_payload(**kw))
    st = f["data_freshness"]["state"]
    assert f["offline"] is (st != "live")
    assert f["stale_snapshot"] is (st == "stale")
    assert not (f["data_freshness"]["is_live"] and f["offline"])


def test_freshness_block_carries_everything_a_ui_needs():
    f = server._attach_freshness(
        _payload(offline=True, snapshot_age_seconds=120, source="robinhood_mcp"))["data_freshness"]
    for key in ("state", "label", "data_as_of", "age_seconds",
                "max_expected_age_seconds", "source", "is_live"):
        assert key in f
    assert f["source"] == "robinhood_mcp"
