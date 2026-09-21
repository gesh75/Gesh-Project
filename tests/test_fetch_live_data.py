import json
import runpy
import stat
import sys
from datetime import datetime, timedelta, timezone

import pytest

import fetch_live_data

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)


def test_write_cache_typical_umask_creates_private_file(tmp_path, monkeypatch):
    cache_file = tmp_path / "gesh_live_cache.json"
    monkeypatch.setattr(fetch_live_data, "CACHE_FILE", cache_file)

    previous_umask = fetch_live_data.os.umask(0o022)
    try:
        fetch_live_data.write_cache({"holdings": [{"symbol": "FAKE"}]})
    finally:
        fetch_live_data.os.umask(previous_umask)

    assert stat.S_IMODE(cache_file.stat().st_mode) == 0o600
    assert json.loads(cache_file.read_text())["holdings"] == [{"symbol": "FAKE"}]


def test_snapshot_cli_without_runtime_input_fails_closed(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", [fetch_live_data.__file__, "--snapshot"])

    with pytest.raises(SystemExit, match="2"):
        runpy.run_path(fetch_live_data.__file__, run_name="__main__")

    assert "has no embedded portfolio data" in capsys.readouterr().err


# ── _money / apply_day_change ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        (12.5, 12.5),
        ("12.5", 12.5),
        (0, 0.0),
        (None, 0.0),
        (True, 0.0),
        (False, 0.0),
        (float("nan"), 0.0),
        (float("inf"), 0.0),
        (float("-inf"), 0.0),
        ("not-a-number", 0.0),
        ({}, 0.0),
    ],
)
def test_money_rejects_bool_nan_and_unparseable(raw, expected):
    assert fetch_live_data._money(raw) == expected


def test_money_overflow_is_default_not_crash():
    assert fetch_live_data._money(10**10000) == 0.0


def test_day_change_zero_prev_is_zero_pct_not_inf():
    eq = fetch_live_data.apply_day_change(
        {"portfolio_equity": 1000.0, "adjusted_equity_previous_close": 0}
    )
    assert eq["day_change"] == 1000.0
    assert eq["day_change_pct"] == 0.0


def test_day_change_missing_prev_is_zero_pct_not_inf():
    eq = fetch_live_data.apply_day_change({"portfolio_equity": 1000.0})
    assert eq["day_change"] == 1000.0
    assert eq["day_change_pct"] == 0.0


def test_day_change_bool_prev_is_not_one_dollar():
    # True must not become prev=$1 and invent a 99,900% day move.
    eq = fetch_live_data.apply_day_change(
        {"portfolio_equity": 1000.0, "adjusted_equity_previous_close": True}
    )
    assert eq["day_change"] == 1000.0
    assert eq["day_change_pct"] == 0.0


def test_day_change_known_prev_is_percent_of_prev():
    eq = fetch_live_data.apply_day_change(
        {"portfolio_equity": 108.0, "adjusted_equity_previous_close": 100.0}
    )
    assert eq["day_change"] == 8.0
    assert eq["day_change_pct"] == 8.0


# ── load_cache ────────────────────────────────────────────────────────────────


def _write_cache_file(path, *, age_sec=60, extra=None, cached_at=None):
    payload = {"holdings": [{"symbol": "FAKE"}], "equity_summary": {"portfolio_equity": 1}}
    if extra:
        payload.update(extra)
    payload["_cached_at"] = cached_at or (NOW - timedelta(seconds=age_sec)).isoformat()
    payload["_cache_version"] = 2
    path.write_text(json.dumps(payload))


def test_load_cache_missing_file_is_none(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_live_data, "CACHE_FILE", tmp_path / "gesh_live_cache.json")
    assert fetch_live_data.load_cache() is None


def test_load_cache_fresh_hit(tmp_path, monkeypatch):
    cache = tmp_path / "gesh_live_cache.json"
    monkeypatch.setattr(fetch_live_data, "CACHE_FILE", cache)

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz else NOW.replace(tzinfo=None)

    monkeypatch.setattr(fetch_live_data, "datetime", _Frozen)
    _write_cache_file(cache, age_sec=60)
    hit = fetch_live_data.load_cache(max_age_sec=1800)
    assert hit is not None
    assert hit["holdings"][0]["symbol"] == "FAKE"


def test_load_cache_exact_max_age_is_still_a_hit(tmp_path, monkeypatch):
    cache = tmp_path / "gesh_live_cache.json"
    monkeypatch.setattr(fetch_live_data, "CACHE_FILE", cache)

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz else NOW.replace(tzinfo=None)

    monkeypatch.setattr(fetch_live_data, "datetime", _Frozen)
    _write_cache_file(cache, age_sec=1800)
    assert fetch_live_data.load_cache(max_age_sec=1800) is not None


def test_load_cache_stale_is_none(tmp_path, monkeypatch):
    cache = tmp_path / "gesh_live_cache.json"
    monkeypatch.setattr(fetch_live_data, "CACHE_FILE", cache)

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz else NOW.replace(tzinfo=None)

    monkeypatch.setattr(fetch_live_data, "datetime", _Frozen)
    _write_cache_file(cache, age_sec=1801)
    assert fetch_live_data.load_cache(max_age_sec=1800) is None


def test_load_cache_naive_stamp_fail_softs_to_none(tmp_path, monkeypatch):
    """Aware-minus-naive raises TypeError; load_cache must not crash — DB fallback."""
    cache = tmp_path / "gesh_live_cache.json"
    monkeypatch.setattr(fetch_live_data, "CACHE_FILE", cache)
    _write_cache_file(cache, cached_at="2026-09-19T12:00:00")  # naive
    assert fetch_live_data.load_cache() is None


def test_load_cache_malformed_json_is_none(tmp_path, monkeypatch):
    cache = tmp_path / "gesh_live_cache.json"
    monkeypatch.setattr(fetch_live_data, "CACHE_FILE", cache)
    cache.write_text("{not json")
    assert fetch_live_data.load_cache() is None


def test_load_cache_missing_cached_at_is_none(tmp_path, monkeypatch):
    cache = tmp_path / "gesh_live_cache.json"
    monkeypatch.setattr(fetch_live_data, "CACHE_FILE", cache)
    cache.write_text(json.dumps({"holdings": []}))
    assert fetch_live_data.load_cache() is None


@pytest.mark.parametrize("payload", ["[]", "null", '"stale"', "12"])
def test_load_cache_non_object_json_is_none(tmp_path, monkeypatch, payload):
    cache = tmp_path / "gesh_live_cache.json"
    monkeypatch.setattr(fetch_live_data, "CACHE_FILE", cache)
    cache.write_text(payload)
    assert fetch_live_data.load_cache() is None


# ── write_snapshot_from_screenshot ────────────────────────────────────────────


def test_snapshot_equity_is_price_times_qty_not_headline(tmp_path, monkeypatch):
    cache = tmp_path / "gesh_live_cache.json"
    monkeypatch.setattr(fetch_live_data, "CACHE_FILE", cache)
    payload = fetch_live_data.write_snapshot_from_screenshot(
        holdings=[{"symbol": "FAKE", "quantity": 10, "price": 8.0}],
        total_value=1000.0,  # headline must not invent holding dollars
        buying_power=50.0,
        day_change=5.0,
        day_chg_pct=0.5,
    )
    assert payload["holdings"][0]["equity"] == 80.0
    assert payload["equity_summary"]["portfolio_equity"] == 80.0
    assert payload["equity_summary"]["portfolio_value"] == 1000.0
    assert payload["equity_summary"]["adjusted_equity_previous_close"] == 995.0
    assert payload["equity_summary"]["withdrawable_amount"] == 50.0


def test_snapshot_bool_price_is_not_one_dollar(tmp_path, monkeypatch):
    cache = tmp_path / "gesh_live_cache.json"
    monkeypatch.setattr(fetch_live_data, "CACHE_FILE", cache)
    payload = fetch_live_data.write_snapshot_from_screenshot(
        holdings=[{"symbol": "FAKE", "quantity": 10, "price": True}],
        total_value=100.0,
        buying_power=0,
        day_change=0,
        day_chg_pct=0,
    )
    assert payload["holdings"][0]["equity"] == 0.0
    assert payload["equity_summary"]["portfolio_equity"] == 0.0


def test_snapshot_zero_price_stays_zero_equity(tmp_path, monkeypatch):
    cache = tmp_path / "gesh_live_cache.json"
    monkeypatch.setattr(fetch_live_data, "CACHE_FILE", cache)
    payload = fetch_live_data.write_snapshot_from_screenshot(
        holdings=[{"symbol": "FAKE", "quantity": 4, "price": 0}],
        total_value=0,
        buying_power=0,
        day_change=0,
        day_chg_pct=0,
    )
    assert payload["holdings"][0]["equity"] == 0.0
    assert payload["_source"] == "screenshot"
