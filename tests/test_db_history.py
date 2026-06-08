"""
tests/test_db_history.py — get_analysis_by_id must survive malformed result_json.

After the 2026-06-02 DB corruption + .recover, ~80 of 122 analysis_runs rows have
malformed JSON. db.get_analysis_by_id() did an unguarded json.loads → the
/api/history/{id} endpoint returned HTTP 500 for ~66% of history rows. It must
instead degrade to a parse_error marker so the endpoint stays 200 and the UI can
show a clean "run unavailable" state.

No network. Uses a temp SQLite file.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import db as dbmod


@pytest.fixture(autouse=True)
def temp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    p = tmp_path / "t.db"
    monkeypatch.setattr(dbmod, "DB_PATH", p)
    dbmod.init_db()
    yield p


def _insert_raw(db_path: Path, *, payload_json: str, result_json: str) -> int:
    con = sqlite3.connect(db_path)
    cur = con.execute(
        "INSERT INTO analysis_runs (run_at, model, payload_json, result_json)"
        " VALUES ('2026-06-02T00:00:00+00:00', 'test', ?, ?)",
        (payload_json, result_json),
    )
    con.commit()
    rid = cur.lastrowid
    con.close()
    return rid


class TestGetAnalysisByIdRobustness:
    def test_valid_json_parses(self, temp_db):
        rid = dbmod.log_analysis("sonnet", {"a": 1}, {"portfolio_score": 80})
        out = dbmod.get_analysis_by_id(rid)
        assert out["result"]["portfolio_score"] == 80
        assert out["payload"]["a"] == 1
        assert out.get("parse_error") is not True

    def test_malformed_result_json_does_not_raise(self, temp_db):
        rid = _insert_raw(temp_db, payload_json="{}", result_json='{"portfolio_score": 80, BROKEN')
        out = dbmod.get_analysis_by_id(rid)          # must NOT raise
        assert out is not None
        assert out["result"] == {}                    # degraded, not crashed
        assert out["parse_error"] is True

    def test_malformed_payload_json_does_not_raise(self, temp_db):
        rid = _insert_raw(temp_db, payload_json="NOT JSON {", result_json="{}")
        out = dbmod.get_analysis_by_id(rid)
        assert out is not None
        assert out["payload"] == {}
        assert out["parse_error"] is True

    def test_nonexistent_id_returns_none(self, temp_db):
        assert dbmod.get_analysis_by_id(999999) is None

    def test_null_json_columns_are_empty_dicts(self, temp_db):
        rid = _insert_raw(temp_db, payload_json="", result_json="")
        out = dbmod.get_analysis_by_id(rid)
        assert out["result"] == {}
        assert out["payload"] == {}
        assert out.get("parse_error") is not True     # empty != malformed
