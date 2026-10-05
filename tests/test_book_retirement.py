"""Item 14: retire the daily-analysis paper book; disclose totals first; flag Kelly to customers."""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from test_proof_page import _make_daily_analysis_db, _overlapping_unequal_notional_db

import book_registry
import kelly_quality
import proof_page
import run_paper_gate2
import server


def _plan():
    return [
        {"symbol": "AAA", "action": "BUY", "shares": 5, "current_price": 10.0, "signal_reason": "Kelly ADD"},
        {"symbol": "BBB", "action": "TRIM", "shares": 2, "current_price": 20.0, "signal_reason": "Kelly TRIM"},
        {"symbol": "CCC", "action": "HOLD", "shares": 0, "current_price": 30.0, "signal_reason": "kept"},
    ]


# ── registry ────────────────────────────────────────────────────────────────

def test_daily_analysis_is_retired_and_other_sources_are_not():
    assert book_registry.is_retired("daily-analysis") is True
    assert book_registry.is_retired("consensus") is False
    assert book_registry.retired_book("daily-analysis").retired_on == "2026-10-04"


@pytest.mark.parametrize("junk", [None, 5, ["daily-analysis"], {"a": 1}, ""])
def test_registry_lookup_is_total(junk):
    assert book_registry.is_retired(junk) is False
    assert book_registry.retired_book(junk) is None


def test_relaunch_requires_a_new_pre_registered_experiment():
    reqs = " ".join(book_registry.retired_book("daily-analysis").relaunch_requirements)
    assert "kill rule" in reqs and "DSR registry" in reqs and "sample-size" in reqs


# ── plan rewrite ────────────────────────────────────────────────────────────

def test_retired_book_opens_nothing_new():
    plan = _plan()
    out = book_registry.hold_if_retired(plan, "daily-analysis")
    assert [r["action"] for r in out] == ["HOLD", "HOLD", "HOLD"]
    assert all(r["shares"] == 0 for r in out)
    assert out[0]["signal_reason"].startswith(book_registry.RETIRED_HOLD_PREFIX)
    assert out[2]["signal_reason"] == "kept"  # an existing HOLD is left untouched
    assert not [r for r in out if r["action"] in ("BUY", "TRIM") and r["shares"] > 0]


def test_hold_if_retired_does_not_mutate_input():
    plan = _plan()
    book_registry.hold_if_retired(plan, "daily-analysis")
    assert plan[0]["action"] == "BUY" and plan[0]["shares"] == 5


def test_active_source_plan_passes_through_equal():
    plan = _plan()
    assert book_registry.hold_if_retired(plan, "consensus") == plan


def test_run_paper_gate2_wires_the_registry():
    """The daily chain must route its plan through the registry before executing."""
    src = inspect.getsource(run_paper_gate2.main)
    assert "hold_if_retired" in src
    assert src.index("hold_if_retired") < src.index("executable =")
    # auto-close must run BEFORE the rewrite, or a retired book could trap its exits
    assert src.index("check_and_close_paper_positions") < src.index("hold_if_retired")


# ── Kelly quality reaches the customer cache ────────────────────────────────

def _equal_weight_rows():
    return [{"symbol": f"S{i}", "optimal_pct": 4.9, "action": "ADD"} for i in range(8)]


def test_holdings_list_shape_is_recognised_as_degenerate():
    """run_kelly_analysis returns {"holdings": [rows]}; it was previously invisible."""
    assert kelly_quality.kelly_degeneracy({"holdings": _equal_weight_rows()})["degenerate"] is True


def test_holdings_list_with_junk_rows_does_not_raise():
    q = kelly_quality.kelly_degeneracy({"holdings": [None, "x", 3, {"optimal_pct": None}]})
    assert q["degenerate"] is False and q["sized"] == 0


def test_quality_block_carries_note_only_when_degenerate():
    assert kelly_quality.quality_block({"holdings": _equal_weight_rows()})["note"]
    healthy = {"holdings": [{"optimal_pct": v} for v in (1.1, 2.4, 3.7, 4.9, 6.2, 7.5)]}
    assert kelly_quality.quality_block(healthy)["note"] is None


def test_cached_kelly_stamps_quality_inside_data(monkeypatch):
    per_symbol = {f"S{i}": {"optimal_pct": 4.9, "action": "ADD"} for i in range(8)}
    monkeypatch.setattr(
        server, "_load_engine_results", lambda: {"kelly": per_symbol, "kelly_ran_at": None}
    )
    out = server._cached_response("kelly")
    assert out["kelly_quality"]["degenerate"] is True
    assert out["data"]["kelly_quality"]["degenerate"] is True  # renderKelly reads `data`
    assert out["data"]["kelly_quality"]["note"]


def test_cached_non_kelly_keys_are_untouched(monkeypatch):
    monkeypatch.setattr(
        server, "_load_engine_results", lambda: {"consensus": {"A": {"score": 1}}, "consensus_ran_at": None}
    )
    assert "kelly_quality" not in server._cached_response("consensus")


def test_dashboard_renders_the_uninformative_banner():
    html = (Path(__file__).resolve().parent.parent / "templates" / "dashboard.html").read_text(
        encoding="utf-8"
    )
    assert 'data-k="kelly-uninformative"' in html
    assert "kellyUninformativeBanner" in html
    assert "/api/kelly/cached" in html
    assert "loadBookKellyNote" in html


# ── proof page ──────────────────────────────────────────────────────────────

@pytest.fixture
def rendered(tmp_path, monkeypatch):
    monkeypatch.setattr(proof_page, "_spy_return_since", lambda _start: None)
    daily_db = _make_daily_analysis_db(
        tmp_path,
        [
            {
                "symbol": "DA1",
                "shares": 10,
                "entry_price": 100,
                "exit_price": 90,
                "exit_time": "2026-07-03T20:00:00+00:00",
                "realized_pnl": -100,
                "pnl_pct": -10.0,
                "outcome": "LOSS",
            },
        ],
    )
    data = proof_page.load_track_record(
        _overlapping_unequal_notional_db(tmp_path),
        daily_analysis_db_path=daily_db,
        launch_date="2026-05-21",
        today="2026-07-04",
    )
    return proof_page.render_html(data)


def test_total_automated_record_is_shown_before_the_engine_subset(rendered):
    combined = rendered.index('data-book="combined-automated"')
    headline = rendered.index("Headline — autonomous engine cohort")
    assert combined < headline, "the total must precede the flattering subset"
    assert "subset of this record" in rendered


def test_daily_analysis_is_labelled_retired_with_relaunch_terms(rendered):
    assert 'data-k="daily-analysis-retired"' in rendered
    assert "RETIRED 2026-10-04" in rendered
    note = rendered[rendered.index('data-k="daily-analysis-retired-note"'):]
    assert "equal-weight" in note
    assert "pre-registered" in note
    assert "DSR registry" in note


def test_existing_disclosure_phrases_survive(rendered):
    assert "arithmetic context only" in rendered
    assert "not included in the headline or autonomy gate" in rendered
    assert "not a portfolio return" in rendered
