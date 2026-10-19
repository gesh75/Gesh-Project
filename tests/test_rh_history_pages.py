"""History paging and statement overrides. The fetch function is a fake."""

import json
from datetime import date

import pytest

from rh_history_pages import collect_pages, load_statement_overrides


def test_cursor_is_followed_until_it_is_gone():
    def fetch(start: str, end: str, symbol: str | None, cursor: str | None) -> dict:
        if cursor is None:
            return {
                "results": [{"id": "a", "executed_at": "2026-01-02"}],
                "next_cursor": "page-2",
                "raw_bytes": 100,
            }
        assert cursor == "page-2"
        return {
            "results": [{"id": "b", "executed_at": "2026-01-03"}],
            "raw_bytes": 100,
        }

    got = collect_pages(fetch, "2026-01-01", "2026-01-31")
    assert [row["id"] for row in got.orders] == ["a", "b"]
    assert got.gaps == []


def test_truncated_window_splits_and_duplicate_ids_collapse():
    def fetch(start: str, end: str, symbol: str | None, cursor: str | None) -> dict:
        s = date.fromisoformat(start)
        e = date.fromisoformat(end)
        if (e - s).days > 10:
            return {"results": [{"id": "cut", "executed_at": start}], "raw_bytes": 20_000, "truncated": True}
        return {
            "results": [{"id": f"{start}", "executed_at": start}, {"id": "shared", "executed_at": start}],
            "raw_bytes": 400,
        }

    got = collect_pages(fetch, "2026-01-01", "2026-03-01")
    assert len(got.orders) >= 2
    assert got.gaps == []
    assert sum(1 for row in got.orders if row["id"] == "shared") == 1


def test_single_day_truncation_is_a_gap_and_stops():
    calls = {"n": 0}

    def fetch(start: str, end: str, symbol: str | None, cursor: str | None) -> dict:
        calls["n"] += 1
        return {"results": [], "truncated": True, "raw_bytes": 20_000}

    got = collect_pages(fetch, "2026-05-01", "2026-05-01")
    assert calls["n"] == 1
    assert got.orders == []
    assert len(got.gaps) == 1
    assert got.gaps[0].reason == "truncated"
    assert got.gaps[0].start == date(2026, 5, 1)


def test_symbols_are_fetched_separately():
    seen: list[str | None] = []

    def fetch(start: str, end: str, symbol: str | None, cursor: str | None) -> dict:
        seen.append(symbol)
        return {"results": [{"id": symbol, "executed_at": start, "symbol": symbol}], "raw_bytes": 10}

    got = collect_pages(fetch, "2026-01-01", "2026-01-02", symbols=["aaa", "bbb"])
    assert seen == ["AAA", "BBB"]
    assert {row["id"] for row in got.orders} == {"AAA", "BBB"}


def test_unset_statement_env_is_empty(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("GESH_STATEMENT_OVERRIDES", raising=False)
    assert load_statement_overrides() == []


def test_statement_json_round_trip(tmp_path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "flows.json"
    path.write_text(
        json.dumps(
            [{"date": "2024-02-15", "type": "deposit", "amount": 100.0, "source": "statement"}]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("GESH_STATEMENT_OVERRIDES", str(path))
    rows = load_statement_overrides()
    assert rows == [
        {"date": "2024-02-15", "type": "deposit", "amount": 100.0, "source": "statement"}
    ]


def test_missing_statement_file_raises(tmp_path):
    missing = tmp_path / "nope.json"
    with pytest.raises(FileNotFoundError):
        load_statement_overrides(missing)
