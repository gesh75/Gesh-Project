"""First-execution tests for the sandbox portfolio source chain.

``fetch_live_portfolio.load_portfolio`` is the fallback book for scheduled
sandbox jobs when :9000 is unreachable. A stale, empty, or malformed
``portfolio_live.json`` must not become "live" holdings, and a dict-shaped
``holdings`` blob must not be treated as a book (the DB path already rejected
that; the JSON path now matches).

Fully offline: every path is tmp_path. Robinhood is never reached.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import fetch_live_portfolio as loader

NOW = datetime(2026, 9, 18, 14, 0, tzinfo=timezone.utc)


@pytest.fixture
def isolated_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(loader, "LIVE_JSON", tmp_path / "portfolio_live.json")
    monkeypatch.setattr(loader, "SESSION_PICKLE", tmp_path / "missing.pickle")
    monkeypatch.setattr(loader, "MAIN_DB", tmp_path / "missing.db")
    monkeypatch.setattr(loader, "TMP_DB", tmp_path / "tmp_copy.db")
    monkeypatch.setattr(loader, "_load_from_robinhood", lambda: None)
    return tmp_path


def _write_json(
    path: Path,
    *,
    age_min: float = 5.0,
    holdings=None,
    equity_summary=None,
    exported_at: str | None = None,
    naive: bool = False,
) -> None:
    if exported_at is None:
        stamp = NOW - timedelta(minutes=age_min)
        exported_at = stamp.replace(tzinfo=None).isoformat() if naive else stamp.isoformat()
    payload = {
        "exported_at": exported_at,
        "holdings": [{"symbol": "HOOD", "equity": 100.0}] if holdings is None else holdings,
        "equity_summary": {"portfolio_equity": 100.0, "cash": 25.0}
        if equity_summary is None
        else equity_summary,
    }
    path.write_text(json.dumps(payload))


# ── JSON source ──────────────────────────────────────────────────────────────


def test_json_missing_file_is_none(isolated_paths):
    assert loader._load_from_json() is None


def test_json_parse_error_is_none(isolated_paths):
    loader.LIVE_JSON.write_text("{not json")
    assert loader._load_from_json() is None


def test_json_empty_holdings_is_none(isolated_paths):
    _write_json(loader.LIVE_JSON, holdings=[])
    assert loader._load_from_json() is None


def test_json_dict_holdings_is_none_not_a_book(isolated_paths):
    """A symbol→row dict must not be treated as a list of holdings."""
    _write_json(loader.LIVE_JSON, holdings={"HOOD": {"equity": 100.0}})
    assert loader._load_from_json() is None


def test_json_non_object_equity_summary_is_none(isolated_paths):
    _write_json(loader.LIVE_JSON, equity_summary=["not", "an", "object"])
    assert loader._load_from_json() is None


def test_json_stale_over_max_age_is_none(isolated_paths, monkeypatch):
    monkeypatch.setattr(loader, "datetime", _FrozenDateTime)
    _write_json(loader.LIVE_JSON, age_min=loader.MAX_JSON_AGE_MINUTES + 1)
    assert loader._load_from_json() is None


def test_json_exactly_max_age_is_accepted(isolated_paths, monkeypatch):
    monkeypatch.setattr(loader, "datetime", _FrozenDateTime)
    _write_json(loader.LIVE_JSON, age_min=loader.MAX_JSON_AGE_MINUTES)
    result = loader._load_from_json()
    assert result is not None
    holdings, equity, age = result
    assert holdings[0]["symbol"] == "HOOD"
    assert equity["cash"] == 25.0
    assert age == pytest.approx(loader.MAX_JSON_AGE_MINUTES)


def test_json_unparseable_exported_at_is_stale_skip(isolated_paths):
    _write_json(loader.LIVE_JSON, exported_at="not-a-date")
    assert loader._load_from_json() is None


def test_json_naive_exported_at_is_treated_as_utc(isolated_paths, monkeypatch):
    monkeypatch.setattr(loader, "datetime", _FrozenDateTime)
    _write_json(loader.LIVE_JSON, age_min=10.0, naive=True)
    result = loader._load_from_json()
    assert result is not None
    assert result[2] == pytest.approx(10.0)


def test_json_fresh_returns_book(isolated_paths, monkeypatch):
    monkeypatch.setattr(loader, "datetime", _FrozenDateTime)
    _write_json(loader.LIVE_JSON, age_min=12.0)
    holdings, equity, age = loader._load_from_json()
    assert [h["symbol"] for h in holdings] == ["HOOD"]
    assert equity["portfolio_equity"] == 100.0
    assert age == pytest.approx(12.0)


# ── DB source ────────────────────────────────────────────────────────────────


def _write_analysis_db(path: Path, *, payload, run_at: str = "2026-09-18T13:00:00+00:00") -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE analysis_runs (run_at TEXT, payload_json TEXT)")
    conn.execute(
        "INSERT INTO analysis_runs (run_at, payload_json) VALUES (?, ?)",
        (run_at, json.dumps(payload) if not isinstance(payload, str) else payload),
    )
    conn.commit()
    conn.close()


def test_db_missing_is_none(isolated_paths):
    assert loader._load_from_db() is None


def test_db_empty_table_is_none(isolated_paths):
    conn = sqlite3.connect(str(loader.TMP_DB))
    conn.execute("CREATE TABLE analysis_runs (run_at TEXT, payload_json TEXT)")
    conn.commit()
    conn.close()
    assert loader._load_from_db() is None


def test_db_payload_not_object_is_none(isolated_paths):
    _write_analysis_db(loader.TMP_DB, payload=[1, 2, 3])
    assert loader._load_from_db() is None


def test_db_holdings_not_list_is_none(isolated_paths):
    _write_analysis_db(loader.TMP_DB, payload={"holdings": {"HOOD": {}}, "equity_summary": {}})
    assert loader._load_from_db() is None


def test_db_unparseable_run_at_still_returns_with_sentinel_age(isolated_paths):
    """Unlike JSON, a bad stamp does not drop the last-resort snapshot — age is 9999."""
    _write_analysis_db(
        loader.TMP_DB,
        payload={"holdings": [{"symbol": "HOOD"}], "equity_summary": {"cash": 1}},
        run_at="not-a-date",
    )
    result = loader._load_from_db()
    assert result is not None
    holdings, equity, age = result
    assert holdings[0]["symbol"] == "HOOD"
    assert equity["cash"] == 1
    assert age == 9999


def test_db_readable_snapshot_returns_book(isolated_paths, monkeypatch):
    monkeypatch.setattr(loader, "datetime", _FrozenDateTime)
    _write_analysis_db(
        loader.TMP_DB,
        payload={"holdings": [{"symbol": "MU", "equity": 50}], "equity_summary": {"cash": 9}},
        run_at=(NOW - timedelta(minutes=90)).isoformat(),
    )
    holdings, equity, age = loader._load_from_db()
    assert holdings[0]["symbol"] == "MU"
    assert equity["cash"] == 9
    assert age == pytest.approx(90.0)


# ── public fallback chain ────────────────────────────────────────────────────


def test_load_portfolio_prefers_fresh_json(isolated_paths, monkeypatch):
    monkeypatch.setattr(loader, "datetime", _FrozenDateTime)
    _write_json(loader.LIVE_JSON, age_min=3.0)
    called = {"rh": False, "db": False}
    monkeypatch.setattr(loader, "_load_from_robinhood", lambda: called.__setitem__("rh", True))
    monkeypatch.setattr(loader, "_load_from_db", lambda: called.__setitem__("db", True))

    holdings, equity, source, age = loader.load_portfolio()
    assert source == "json_live"
    assert holdings[0]["symbol"] == "HOOD"
    assert called["rh"] is False and called["db"] is False
    assert age == pytest.approx(3.0)


def test_load_portfolio_falls_through_to_db(isolated_paths, monkeypatch):
    monkeypatch.setattr(loader, "datetime", _FrozenDateTime)
    _write_analysis_db(
        loader.TMP_DB,
        payload={"holdings": [{"symbol": "BAC"}], "equity_summary": {}},
        run_at=(NOW - timedelta(minutes=40)).isoformat(),
    )
    holdings, _equity, source, age = loader.load_portfolio()
    assert source == "db_snapshot"
    assert holdings[0]["symbol"] == "BAC"
    assert age == pytest.approx(40.0)


def test_load_portfolio_all_sources_fail_raises(isolated_paths):
    with pytest.raises(RuntimeError, match="All portfolio data sources failed"):
        loader.load_portfolio()


def test_persist_to_json_fail_soft_on_unwritable(isolated_paths, monkeypatch):
    monkeypatch.setattr(loader, "LIVE_JSON", isolated_paths / "missing_dir" / "out.json")
    loader._persist_to_json([{"symbol": "HOOD"}], {"cash": 1})  # must not raise


def test_print_staleness_warning_only_for_old_db_snapshot(capsys):
    loader.print_staleness_warning("db_snapshot", 90.0)
    out = capsys.readouterr().out
    assert "STALE DATA WARNING" in out
    loader.print_staleness_warning("json_live", 90.0)
    assert capsys.readouterr().out == ""


class _FrozenDateTime(datetime):
    """Pin ``datetime.now`` so age math is deterministic."""

    @classmethod
    def now(cls, tz=None):
        return NOW if tz is None else NOW.astimezone(tz)

    @classmethod
    def fromisoformat(cls, value):
        return datetime.fromisoformat(value)
