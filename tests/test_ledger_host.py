"""Tests for ledger_host.py — Decision Ledger Phase 4B host integration.

All tests are offline. Mocks used only for unit tests; real adapter used for SQLite integration.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# Single source of truth for the audited gesh-core commit, so this test and the
# dependency-sanity suite can never disagree about what the pin should be.
from test_dependency_sanity import GESH_CORE_SHA

import ledger_host


def test_requirements_pins_gesh_core_immutably_and_no_editable():
    """Exact dependency pin requirement (active lines only).

    Originally asserted a literal release tag. v0.6.1 is an ANNOTATED tag:
    7af8fad is the tag-object SHA and f11c460 is the peeled commit SHA, both
    identifying the same release content (the tag has not moved). The pin is now
    that peeled commit, which expresses the same requirement more explicitly —
    the install path names the exact object built, so no reader has to peel a
    tag to know what shipped. A version bump is a reviewable edit to this SHA.
    """
    req = Path(__file__).parent.parent / "requirements.txt"
    active_lines = []
    for line in req.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        active_lines.append(stripped)

    active_text = "\n".join(active_lines)
    assert (f"gesh-core @ git+https://github.com/gesh75/gesh-core.git@{GESH_CORE_SHA}"
            in active_text)
    assert "v0.5.3" not in active_text
    # The install path must name the commit, not the tag it is reachable from.
    assert "@v0.6.0" not in active_text, "symbolic tag ref back in the install path"
    assert "@v0.6.1" not in active_text, "symbolic tag ref back in the install path"
    assert "-e ../gesh-core" not in active_text
    assert "--editable" not in active_text
    assert "main" not in active_text or "gesh-core.git@main" not in active_text
    gesh_core_lines = [line for line in active_lines if "gesh-core" in line]
    assert len(gesh_core_lines) == 1


def test_owner_tenant_id_missing_or_blank_skips_nonfatally_and_does_not_call_adapter():
    """OWNER_TENANT_ID missing/blank causes skip, no adapter call, return None, explicit log."""
    mock_adapter = MagicMock()
    with patch("ledger_host.record_daily_analysis_decision", mock_adapter):
        result = ledger_host.record_daily_analysis_ledger_nonfatal(
            engine_results={"snapshot": {}},
            analysis_run_id=42,
            ai_model="claude-sonnet-4-6",
            db_path=":memory:",
            environ={},
        )
        assert result is None
        mock_adapter.assert_not_called()

        result2 = ledger_host.record_daily_analysis_ledger_nonfatal(
            engine_results={"snapshot": {}},
            analysis_run_id=42,
            ai_model="claude-sonnet-4-6",
            db_path=":memory:",
            environ={"OWNER_TENANT_ID": "   "},
        )
        assert result2 is None
        mock_adapter.assert_not_called()


def test_successful_call_passes_exact_arguments_and_same_engine_results_object():
    """Exact successful adapter arguments including identity of engine_results."""
    eng = {"snapshot": {"quote_provenance": {"AAPL": {"provider": "yahoo"}}}, "run_manifest": {}}
    mock_recording = SimpleNamespace(
        recording=SimpleNamespace(
            portfolio_snapshot_id=123,
            recommendation_ids=[1, 2],
            inserted_ids=[1],
            reused_ids=[2],
        ),
        skipped=[],
    )
    mock_adapter = MagicMock(return_value=mock_recording)
    with patch("ledger_host.record_daily_analysis_decision", mock_adapter):
        result = ledger_host.record_daily_analysis_ledger_nonfatal(
            engine_results=eng,
            analysis_run_id=99,
            ai_model="claude-sonnet-4-6",
            db_path=Path("/tmp/test.db"),
            environ={"OWNER_TENANT_ID": "tenant-abc"},
        )
        assert result is mock_recording
        mock_adapter.assert_called_once()
        call_kwargs = mock_adapter.call_args.kwargs
        assert call_kwargs["tenant_id"] == "tenant-abc"
        assert call_kwargs["source"] == "daily_analysis_chain"
        assert call_kwargs["engine_results"] is eng
        assert call_kwargs["analysis_run_id"] == 99
        assert call_kwargs["ai_provider"] == "anthropic"
        assert call_kwargs["ai_model"] == "claude-sonnet-4-6"
        assert call_kwargs["quote_provider"] == "yahoo"
        assert call_kwargs["db_path"] == Path("/tmp/test.db")


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"snapshot": {"quote_provenance": {"AAPL": {"provider": "yahoo"}}}}, "yahoo"),
        (
            {
                "snapshot": {
                    "quote_provenance": {
                        "AAPL": {"provider": "yahoo"},
                        "MSFT": {"provider": "yahoo"},
                    }
                }
            },
            "yahoo",
        ),
        (
            {
                "snapshot": {
                    "quote_provenance": {
                        "AAPL": {"provider": "yahoo"},
                        "MSFT": {"provider": "alpha"},
                    }
                }
            },
            None,
        ),
        ({"snapshot": {"source": "snapshot_src"}, "run_manifest": {}}, "snapshot_src"),
        ({"run_manifest": {"input_source": "manifest_src"}}, "manifest_src"),
        ({}, None),
    ],
)
def test_quote_provider_derivation_rules(payload, expected):
    """Quote-provider derivation per spec."""
    assert ledger_host._derive_quote_provider(payload) == expected


def test_nonfatal_ledger_errors_are_caught_and_logged(capsys):
    """LedgerValidationError, LedgerConflictError, LedgerWriteError, RuntimeError are non-fatal."""
    from gesh_core.ledger.integration import (
        LedgerConflictError,
        LedgerValidationError,
        LedgerWriteError,
    )

    errors = [
        LedgerValidationError("bad data"),
        LedgerConflictError("conflict"),
        LedgerWriteError("write fail"),
        RuntimeError("boom"),
    ]
    for err in errors:
        mock_adapter = MagicMock(side_effect=err)
        with patch("ledger_host.record_daily_analysis_decision", mock_adapter):
            eng = {"snapshot": {}, "SECRET_MARKER": "sk-abc123"}
            result = ledger_host.record_daily_analysis_ledger_nonfatal(
                engine_results=eng,
                analysis_run_id=1,
                ai_model="m",
                db_path=":memory:",
                environ={"OWNER_TENANT_ID": "t"},
            )
            assert result is None
            captured = capsys.readouterr()
            assert "Ledger save failed (non-fatal)" in captured.out
            assert err.__class__.__name__ in captured.out
            assert "SECRET_MARKER" not in captured.out
            assert "sk-abc123" not in captured.out


@pytest.mark.parametrize(
    "exc",
    [
        TypeError("unexpected kwarg"),
        sqlite3.OperationalError("unable to open database file"),
        AttributeError("module has no attribute"),
        ValueError("unexpected value"),
    ],
)
def test_unexpected_integration_failures_are_also_nonfatal(exc, capsys):
    """Non-ledger exception types must be caught by the broad host boundary.

    The helper must never propagate: an unexpected integration failure (bad
    kwarg after a core upgrade, unopenable DB file, attribute error) has to
    degrade to a logged skip so the daily chain reaches log_equity_snapshot.
    """
    mock_adapter = MagicMock(side_effect=exc)
    with patch("ledger_host.record_daily_analysis_decision", mock_adapter):
        result = ledger_host.record_daily_analysis_ledger_nonfatal(
            engine_results={"snapshot": {}, "SECRET_MARKER": "sk-abc123"},
            analysis_run_id=1,
            ai_model="m",
            db_path=":memory:",
            environ={"OWNER_TENANT_ID": "t"},
        )
    assert result is None
    out = capsys.readouterr().out
    assert "Ledger save failed (non-fatal)" in out
    assert exc.__class__.__name__ in out
    assert "SECRET_MARKER" not in out
    assert "sk-abc123" not in out


def test_success_logging_contains_only_compact_counts_and_no_sensitive_data(capsys):
    """Success log has exact compact fields, skipped-reason aggregation, no holdings/raw/ai_result/secret."""
    mock_recording = SimpleNamespace(
        recording=SimpleNamespace(
            portfolio_snapshot_id=777,
            recommendation_ids=[10, 11, 12],
            inserted_ids=[10],
            reused_ids=[11, 12],
        ),
        skipped=[
            SimpleNamespace(decision_type="rec", reason="duplicate"),
            SimpleNamespace(decision_type="rec", reason="duplicate"),
            SimpleNamespace(decision_type="pos", reason="low_conf"),
        ],
    )
    mock_adapter = MagicMock(return_value=mock_recording)
    eng = {"snapshot": {}, "ai_result": {"secret": "x"}, "holdings": [1, 2], "SECRET": "no"}
    with patch("ledger_host.record_daily_analysis_decision", mock_adapter):
        ledger_host.record_daily_analysis_ledger_nonfatal(
            engine_results=eng,
            analysis_run_id=5,
            ai_model="m",
            db_path=":memory:",
            environ={"OWNER_TENANT_ID": "t"},
        )
    out = capsys.readouterr().out
    assert "portfolio=777" in out
    assert "recs=3" in out
    assert "inserted=1" in out
    assert "reused=2" in out
    assert "skipped=3" in out
    assert "rec:duplicate=2" in out or "rec:duplicate=2" in out
    assert "pos:low_conf=1" in out
    assert "holdings" not in out
    assert "ai_result" not in out
    assert "SECRET" not in out
    assert "secret" not in out.lower()


def test_real_temporary_sqlite_integration_uses_supplied_db_path_and_reuses_on_retry(tmp_path):
    """Real integration test using only tmp_path and the actual gesh-core v0.6.1 adapter.

    Creates analysis_runs row, calls real record_daily_analysis_ledger_nonfatal,
    verifies tenant/analysis_run_id, retry reuses IDs, no duplicates.
    """
    db_path = tmp_path / "test_ledger.db"
    tenant = "tenant-xyz"
    analysis_run_id = 1001

    # Initialize legacy analysis_runs table and a real row (direct SQLite as allowed)
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS analysis_runs (
            id INTEGER PRIMARY KEY,
            tenant_id TEXT,
            analysis_run_id INTEGER
        );
        """
    )
    conn.execute(
        "INSERT INTO analysis_runs (tenant_id, analysis_run_id) VALUES (?, ?)",
        (tenant, analysis_run_id),
    )
    conn.commit()
    conn.close()

    # Realistic minimal payload compatible with released daily-analysis adapter
    # (production run_ai2.py supplies ai_result_ran_at before ledger call)
    eng = {
        "snapshot": {
            "source": "test-snapshot",
            "quote_provenance": {"AAPL": {"provider": "yahoo"}},
            "fetched_at": "2026-07-22T10:29:00+00:00",
        },
        "run_manifest": {"input_source": "test-manifest"},
        "holdings": [{"symbol": "AAPL", "qty": 10, "current_price": 180.0}],
        "ai_result_ran_at": "2026-07-22T10:30:00+00:00",
        "consensus": {
            "AAPL": {
                "verdict": "HOLD",
                "confidence": 0.8,
                "conviction": 0.6,
            }
        },
    }

    # First call — real adapter
    res1 = ledger_host.record_daily_analysis_ledger_nonfatal(
        engine_results=eng,
        analysis_run_id=analysis_run_id,
        ai_model="claude-sonnet-4-6",
        db_path=db_path,
        environ={"OWNER_TENANT_ID": tenant},
    )
    assert res1 is not None

    # Verify through direct DB queries on supplied tmp_path only.
    # The ledger table names are unchanged in released gesh-core v0.6.1:
    # portfolio_snapshots, market_quote_snapshots, recommendations. Only
    # `recommendations` carries analysis_run_id — the two snapshot tables are
    # content-addressed and have no run-provenance column
    # (gesh_core/ledger/schema.py).
    c = sqlite3.connect(db_path)
    assert c.execute("SELECT COUNT(*) FROM portfolio_snapshots").fetchone()[0] == 1
    assert c.execute("SELECT COUNT(*) FROM market_quote_snapshots").fetchone()[0] == 1
    rec_count = c.execute("SELECT COUNT(*) FROM recommendations").fetchone()[0]
    assert rec_count >= 1

    # Tenant isolation holds across every ledger table.
    for table in ("portfolio_snapshots", "market_quote_snapshots", "recommendations"):
        tenants = {r[0] for r in c.execute(f"SELECT tenant_id FROM {table}").fetchall()}
        assert tenants == {tenant}, f"{table} tenant mismatch: {tenants}"

    # Explicit host-owned provenance reached storage unmodified, and the legacy
    # analysis_run_id was preserved as optional metadata on the recommendation.
    rec_rows = c.execute(
        "SELECT analysis_run_id, source, ai_provider, ai_model, symbol, action FROM recommendations"
    ).fetchall()
    for run_id, source, provider, model, symbol, action in rec_rows:
        assert run_id == analysis_run_id
        assert source == "daily_analysis_chain"
        assert provider == "anthropic"
        assert model == "claude-sonnet-4-6"
        assert symbol == "AAPL"
        assert action == "HOLD"

    # The derived quote provider was propagated onto the quote snapshot.
    provider, last, quality = c.execute(
        "SELECT provider, last, quality_state FROM market_quote_snapshots"
    ).fetchone()
    assert provider == "yahoo"
    assert last == 180.0
    assert quality == "FRESH"
    c.close()

    # Second call with identical inputs — must reuse
    res2 = ledger_host.record_daily_analysis_ledger_nonfatal(
        engine_results=eng,
        analysis_run_id=analysis_run_id,
        ai_model="claude-sonnet-4-6",
        db_path=db_path,
        environ={"OWNER_TENANT_ID": tenant},
    )
    assert res2 is not None
    recording2 = getattr(res2, "recording", res2)

    c = sqlite3.connect(db_path)
    assert c.execute("SELECT COUNT(*) FROM recommendations").fetchone()[0] == rec_count
    # Snapshots are content-addressed: an identical retry must not duplicate them.
    assert c.execute("SELECT COUNT(*) FROM portfolio_snapshots").fetchone()[0] == 1
    assert c.execute("SELECT COUNT(*) FROM market_quote_snapshots").fetchone()[0] == 1
    c.close()

    inserted2 = getattr(recording2, "inserted_ids", []) or []
    assert len(inserted2) == 0, "identical retry must insert nothing"
    reused2 = getattr(recording2, "reused_ids", []) or []
    assert len(reused2) >= 1, "identical retry must report reused recommendation IDs"
    # The reused IDs are the same deterministic identities recorded on pass one.
    recording1 = getattr(res1, "recording", res1)
    assert set(reused2) == set(getattr(recording1, "recommendation_ids", ()))

    # Only the supplied db_path (plus its own SQLite WAL sidecars) was written —
    # no production database and no stray file was created. init_ledger_tables
    # sets PRAGMA journal_mode=WAL, so -wal/-shm sidecars are expected.
    assert db_path.exists()
    stray = [p.name for p in tmp_path.iterdir() if not p.name.startswith(db_path.name)]
    assert stray == [], f"unexpected files written outside the supplied db_path: {stray}"
